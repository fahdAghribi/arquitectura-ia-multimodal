"""Ejecución del grafo por sesión: streaming de la traza, bandeja de revisión y avisos al cliente."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image, ImageOps

from .agents.graph import Deps, build_graph
from .auth import User
from .schemas import Captura, ConsultaResponse, Fuente, RespuestaOut, Revision, TraceStep

log = logging.getLogger(__name__)
MAX_SIDE = 1600


def prepare_image(raw: bytes) -> dict:
    """Normaliza la imagen (orientación EXIF, tamaño máximo, JPEG) antes de enviarla al modelo."""
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
    return {"media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}


class SessionNotFound(Exception):
    pass


def visible_step(step: dict, is_gestor: bool) -> dict:
    """El cliente ve el progreso, pero no el razonamiento ni el borrador antes de la revisión del gestor."""
    if is_gestor:
        return step
    if step["kind"] == "reasoning":
        return {**step, "detail": "Analizando la consulta…", "data": None}
    if step["kind"] == "action" and step["detail"].startswith("responder_consulta"):
        return {**step, "detail": "Preparando la respuesta", "data": None}
    return step


Listener = Callable[[dict], Awaitable[None]]


class ConsultaService:
    def __init__(self, deps: Deps):
        self.deps = deps
        # PoC: memoria en proceso. En producción, AsyncPostgresSaver y una tabla de revisiones.
        self.graph = build_graph(deps, InMemorySaver())
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.pending: dict[str, Revision] = {}  # session_id -> revisión pendiente
        self._listeners: dict[str, set[Listener]] = defaultdict(set)

    @staticmethod
    def _config(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 60}

    # ------------------------------------------------------------------ suscripción (WebSocket del cliente)
    def subscribe(self, session_id: str, listener: Listener) -> None:
        self._listeners[session_id].add(listener)

    def unsubscribe(self, session_id: str, listener: Listener) -> None:
        self._listeners[session_id].discard(listener)

    async def _notify(self, session_id: str, event: dict, skip: Listener | None = None) -> None:
        for listener in list(self._listeners.get(session_id, ())):
            if listener is skip:
                continue
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 - un socket caído no debe romper la revisión
                self.unsubscribe(session_id, listener)

    # ------------------------------------------------------------------ ejecución
    async def _stream(self, session_id: str, payload, viewer: User) -> AsyncIterator[dict]:
        async with self._locks[session_id]:
            async for chunk in self.graph.astream(payload, self._config(session_id), stream_mode="updates"):
                for node, update in chunk.items():
                    if node != "__interrupt__":
                        for item in (update or {}).get("trace", []):
                            yield {"type": "trace", "step": visible_step(item, viewer.has(self.deps.settings.role_gestor))}
            snap = await self.snapshot(session_id, viewer)
            if snap.status == "pending_review" and snap.revision:
                self.pending[session_id] = snap.revision
            else:
                self.pending.pop(session_id, None)
            yield {"type": "result", "data": snap.model_dump()}

    def consultar(self, session_id: str, user: User, pregunta: str, expediente_id: str | None, image: dict | None) -> AsyncIterator[dict]:
        inputs = {"session_id": session_id, "user": user.public(), "query": pregunta or "", "expediente_id": expediente_id or None, "image": image}
        return self._stream(session_id, inputs, user)

    async def revisar(self, session_id: str, gestor: User, aprobado: bool, nota: str, resumen_editado: str | None) -> ConsultaResponse:
        state = await self.graph.aget_state(self._config(session_id))
        if not state.values:
            raise SessionNotFound(session_id)
        if not state.interrupts:
            raise ValueError("La consulta no está pendiente de revisión")
        decision = {"aprobado": aprobado, "nota": nota, "resumen_editado": resumen_editado, "user": gestor.public()}
        result = None
        async for event in self._stream(session_id, Command(resume=decision), gestor):
            if event["type"] == "result":
                result = event["data"]
        owner = User(sub=state.values["user"]["sub"], username=state.values["user"]["username"], roles=set(state.values["user"]["roles"]))
        await self._notify(session_id, {"type": "result", "data": (await self.snapshot(session_id, owner)).model_dump()})
        return ConsultaResponse.model_validate(result)

    async def run(self, events: AsyncIterator[dict]) -> ConsultaResponse:
        result = None
        async for event in events:
            if event["type"] == "result":
                result = event["data"]
        return ConsultaResponse.model_validate(result)

    async def owner(self, session_id: str) -> str | None:
        state = await self.graph.aget_state(self._config(session_id))
        return (state.values.get("user") or {}).get("sub") if state.values else None

    # ------------------------------------------------------------------ respuesta estandarizada
    async def snapshot(self, session_id: str, viewer: User) -> ConsultaResponse:
        state = await self.graph.aget_state(self._config(session_id))
        v = state.values
        if not v:
            raise SessionNotFound(session_id)
        status = v.get("status", "error")
        if state.interrupts:
            status = "pending_review"
        is_gestor = viewer.has(self.deps.settings.role_gestor)
        retrieved = v.get("retrieved") or {}
        respuesta = None
        r = v.get("respuesta")
        # El cliente no ve el borrador hasta que el gestor lo aprueba; si se rechaza, solo ve la nota.
        if r and (is_gestor or status == "completed"):
            respuesta = RespuestaOut(
                **{k: r.get(k, []) for k in ("modelos", "requisitos", "documentacion", "pasos", "plazos", "advertencias", "preguntas_aclaratorias")},
                resumen=r["resumen"], respuesta_detallada=r.get("respuesta_detallada", ""), confianza=r["confianza"],
                requiere_asesor=r["requiere_asesor"], nota_gestor=r.get("nota_gestor"),
                fuentes=[
                    Fuente(chunk_id=f, doc_id=retrieved[f]["doc_id"], documento=retrieved[f].get("documento"), pagina=retrieved[f].get("pagina"),
                           seccion=retrieved[f].get("seccion"), extracto=retrieved[f]["contenido"][:400], vigente="aviso" not in retrieved[f])
                    for f in r.get("fuentes", []) if f in retrieved
                ],
                capturas=[
                    Captura(image_name=c["image_name"], url=c["image_url"], doc_id=c["doc_id"], documento=c.get("documento"),
                            pagina=c.get("pagina"), descripcion=c.get("contenido", "")[:300])
                    for name in r.get("capturas", []) for c in [next((x for x in retrieved.values() if x.get("image_name") == name and x.get("image_url")), None)] if c
                ],
            )
        rev = v.get("revision")
        return ConsultaResponse(
            session_id=v["session_id"],
            status=status if status in ("completed", "pending_review", "rejected", "blocked") else "error",
            pregunta=v.get("query"),
            cliente=(v.get("user") or {}).get("username"),
            expediente_id=v.get("expediente_id"),
            respuesta=respuesta,
            revision=Revision.model_validate(rev) if rev and status == "pending_review" else None,
            mensaje_gestor=v.get("mensaje_gestor"),
            blocked_reason=v.get("blocked_reason"),
            model_used=v.get("model_used"),
            agent=v.get("agent"),
            trace=[TraceStep.model_validate(visible_step(t, is_gestor)) for t in v.get("trace", [])[v.get("trace_start", 0):]],
        )
