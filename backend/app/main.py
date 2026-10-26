"""API Gateway (FastAPI): REST + WebSocket, validación OIDC/RBAC y acceso al core agéntico."""

from __future__ import annotations

import base64
import contextlib
import logging
import uuid

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles

from .a2a import AgentCard, AgentDirectory, Skill
from .agents.graph import Deps
from .auth import User, current_user, require_role, websocket_user
from .config import get_settings
from .llm import get_router
from .mcp_client import MCPTools
from .rag import Retriever
from .rag_mcp import rag_mcp
from .schemas import ConsultaResponse, RevisionDecision
from .service import ConsultaService, SessionNotFound, prepare_image

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")

settings.media_root.mkdir(parents=True, exist_ok=True)
rag_mcp_app = rag_mcp.http_app(path="/mcp")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    directory = AgentDirectory(settings.public_base_url, [u for u in settings.a2a_remote_agents.split(",") if u.strip()])
    await directory.discover_remote()
    log.info("Cargando modelos de embeddings (%s)…", settings.embeddings_backend)
    deps = Deps(settings=settings, router=get_router(), retriever=Retriever(settings), mcp=MCPTools(settings.mcp_endpoint), directory=directory)
    log.info("Modelos cargados")
    app.state.deps = deps
    app.state.service = ConsultaService(deps)
    async with rag_mcp_app.lifespan(app):
        yield


app = FastAPI(title="Asistente de trámites tributarios - API Gateway", version="0.1.0", lifespan=lifespan)
app.mount("/static/extracted_images", StaticFiles(directory=settings.media_root), name="extracted_images")
app.mount("/mcp-rag", rag_mcp_app)

any_role = require_role("role_cliente", "role_gestor")
gestor_role = require_role("role_gestor")


def service() -> ConsultaService:
    return app.state.service


def _is_gestor(user: User) -> bool:
    return user.has(settings.role_gestor)


async def _authorize_session(session_id: str, user: User) -> None:
    owner = await service().owner(session_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consulta no encontrada")
    if owner != user.sub and not _is_gestor(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La consulta pertenece a otro usuario")


async def _authorize_expediente(expediente_id: str | None, user: User) -> None:
    if not expediente_id:
        return
    args = {"expediente_id": expediente_id} | ({} if _is_gestor(user) else {"cliente_username": user.username})
    ok, _ = await app.state.deps.mcp.call("obtener_expediente", args)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Expediente '{expediente_id}' no encontrado")


@app.get("/health", tags=["ops"])
async def health() -> dict:
    deps: Deps = app.state.deps
    try:
        deps.retriever.client.get_collections()
        vector = "connected" if deps.retriever.ready() else "connected_empty"
    except Exception:  # noqa: BLE001
        vector = "unreachable"
    tools = "connected" if await deps.mcp.ping() else "unreachable"
    if settings.auth_mode == "disabled":
        auth = "disabled"
    else:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(settings.jwks_url)
            auth = "active" if r.status_code == 200 else f"http_{r.status_code}"
        except httpx.HTTPError:
            auth = "unreachable"
    llm = [p["provider"] for p in deps.router.catalog()]
    ok = vector == "connected" and tools == "connected" and auth in ("active", "disabled")
    return {"status": "ok" if ok else "degraded", "services": {"vector_db": vector, "expedientes_mcp": tools, "keycloak_auth": auth, "llm": llm}}


@app.get("/api/v1/me", tags=["auth"])
async def me(user: User = Depends(current_user)) -> dict:
    return user.public() | {"es_gestor": _is_gestor(user), "es_cliente": user.has(settings.role_cliente)}


@app.get("/api/v1/documentos", tags=["documentación"])
async def documentos(_: User = Depends(any_role)) -> list[dict]:
    return app.state.deps.catalog()


@app.get("/api/v1/modelos-ia", tags=["documentación"])
async def modelos_ia(_: User = Depends(any_role)) -> list[dict]:
    return app.state.deps.router.catalog()


@app.get("/api/v1/expedientes", tags=["expedientes"])
async def expedientes(user: User = Depends(any_role)) -> list[dict]:
    args = {} if _is_gestor(user) else {"cliente_username": user.username}
    ok, data = await app.state.deps.mcp.call("listar_expedientes", args)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(data))
    return data


@app.post("/api/v1/consultas", response_model=ConsultaResponse, tags=["consultas"])
async def consultar(
    pregunta: str = Form(""),
    expediente_id: str | None = Form(None),
    session_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: User = Depends(any_role),
) -> ConsultaResponse:
    """Consulta síncrona (texto + imagen opcional). Las respuestas a clientes quedan pendientes de revisión."""
    if session_id:
        await _authorize_session(session_id, user)
    await _authorize_expediente(expediente_id, user)
    image = None
    if file is not None and file.filename:
        try:
            image = prepare_image(await file.read())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El archivo no es una imagen válida") from exc
    sid = session_id or str(uuid.uuid4())
    return await service().run(service().consultar(sid, user, pregunta, expediente_id, image))


@app.get("/api/v1/sessions/{session_id}", response_model=ConsultaResponse, tags=["consultas"])
async def get_session(session_id: str, user: User = Depends(any_role)) -> ConsultaResponse:
    await _authorize_session(session_id, user)
    return await service().snapshot(session_id, user)


@app.get("/api/v1/revisiones", response_model=list[ConsultaResponse], tags=["revisión"])
async def revisiones(user: User = Depends(gestor_role)) -> list[ConsultaResponse]:
    """Bandeja del gestor: consultas de clientes con borrador pendiente de revisión."""
    return [await service().snapshot(sid, user) for sid in list(service().pending)]


@app.post("/api/v1/revisiones/{session_id}", response_model=ConsultaResponse, tags=["revisión"])
async def revisar(session_id: str, body: RevisionDecision, user: User = Depends(gestor_role)) -> ConsultaResponse:
    """Human-in-the-Loop: el gestor aprueba (opcionalmente editando) o rechaza el borrador; el cliente recibe el aviso."""
    try:
        return await service().revisar(session_id, user, body.aprobado, body.nota, body.resumen_editado)
    except SessionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consulta no encontrada") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.websocket("/ws/v1/consultas/{session_id}")
async def ws_consultas(websocket: WebSocket, session_id: str):
    """Canal en tiempo real: el cliente envía consultas, recibe la traza y el aviso cuando el gestor revisa."""
    offered = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    try:
        user = await websocket_user(websocket, settings)
    except HTTPException as exc:
        await websocket.close(code=4401 if exc.status_code == 401 else 4403, reason=str(exc.detail)[:120])
        return
    await websocket.accept(subprotocol="asesoria.v1" if "asesoria.v1" in offered else None)
    owner = await service().owner(session_id)
    if owner and owner != user.sub and not _is_gestor(user):
        await websocket.close(code=4403, reason="La consulta pertenece a otro usuario")
        return

    async def listener(event: dict) -> None:
        await websocket.send_json(event)

    service().subscribe(session_id, listener)
    await websocket.send_json({"type": "session", "session_id": session_id, "user": user.public()})
    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") != "consulta":
                await websocket.send_json({"type": "error", "detail": "Tipo de mensaje desconocido"})
                continue
            try:
                await _authorize_expediente(msg.get("expediente_id"), user)
                image = prepare_image(base64.b64decode(msg["image"]["data"])) if (msg.get("image") or {}).get("data") else None
                async for event in service().consultar(session_id, user, msg.get("pregunta", ""), msg.get("expediente_id"), image):
                    await websocket.send_json(event)
            except HTTPException as exc:
                await websocket.send_json({"type": "error", "detail": exc.detail})
            except Exception as exc:  # noqa: BLE001 - se informa al cliente y el canal sigue abierto
                log.exception("Error en la consulta %s", session_id)
                await websocket.send_json({"type": "error", "detail": f"Error interno: {exc}"})
    except WebSocketDisconnect:
        pass
    finally:
        service().unsubscribe(session_id, listener)


# --------------------------------------------------------------------------- Directorio A2A


@app.get("/a2a/agents", tags=["a2a"])
async def a2a_agents() -> list[dict]:
    return [c.public() for c in app.state.deps.directory.cards]


@app.get("/.well-known/agent-card.json", tags=["a2a"])
async def supervisor_card() -> dict:
    card = AgentCard(
        name="asesoria-supervisor",
        description="Supervisor del asistente de trámites tributarios: enruta consultas a sub-agentes especializados.",
        url=f"{settings.public_base_url}/api/v1/consultas",
        skills=[Skill(s.id, s.name, s.description, s.tags, s.examples) for c in app.state.deps.directory.cards for s in c.skills],
    )
    return card.public()

