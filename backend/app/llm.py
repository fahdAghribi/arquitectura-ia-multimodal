"""Catálogo de modelos (LLM/VLM) y enrutado con respaldo (fallback).

Orden por defecto (LLM_PROVIDER_ORDER): modelos europeos soberanos primero
(Mistral Large / Pixtral 12B), Llama 3.2 Vision local vía Ollama, y como respaldo
de alta capacidad Claude y GPT-4o. "mock" es un razonador heurístico sin LLM que
permite ejecutar el PoC de extremo a extremo sin claves de API.

La conversación se guarda en formato neutro (dicts serializables en el checkpoint
de LangGraph) y cada adaptador la traduce a su API nativa:
  {"role": "user", "text": str, "images": [{"media_type": str, "data": b64}]}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "arguments"}], "provider": str, "native": Any}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class LLMTurn:
    text: str
    tool_calls: list[dict]
    model: str
    provider: str
    native: Any = None


@dataclass
class ProviderError(Exception):
    provider: str
    reason: str

    def __str__(self) -> str:
        return f"{self.provider}: {self.reason}"


class Provider(ABC):
    name: str
    vision: bool = True

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def turn(self, system: str, messages: list[dict], tools: list[ToolSpec], has_images: bool) -> LLMTurn: ...

    async def describe_image(self, image: bytes, context: str = "") -> str:
        prompt = (
            "Describe en español, en 2-3 frases, el contenido de esta captura o figura de un manual de la AEAT "
            f"(pantalla, apartados, casillas o botones visibles). Contexto: {context}"
        )
        msg = {"role": "user", "text": prompt, "images": [{"media_type": _sniff(image), "data": base64.b64encode(image).decode()}]}
        result = await self.turn("Eres un experto en documentación tributaria española.", [msg], [], True)
        return result.text.strip()


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _data_url(img: dict) -> str:
    return f"data:{img['media_type']};base64,{img['data']}"


# --------------------------------------------------------------------------- OpenAI-style (Mistral, OpenAI, Ollama)


def _openai_messages(system: str, messages: list[dict], image_as_str: bool, id_map=lambda i: i) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            parts: list[dict] = [{"type": "text", "text": m.get("text", "")}]
            for img in m.get("images", []):
                url = _data_url(img)
                parts.append({"type": "image_url", "image_url": url if image_as_str else {"url": url}})
            out.append({"role": "user", "content": parts if m.get("images") else m.get("text", "")})
        elif m["role"] == "assistant":
            msg: dict = {"role": "assistant", "content": m.get("text") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": id_map(tc["id"]), "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": id_map(m["tool_call_id"]), "name": m["name"], "content": m["content"]})
    return out


def _openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


def _mistral_id(call_id: str) -> str:
    """Mistral exige ids de 9 caracteres alfanuméricos; se normalizan los de otros proveedores."""
    if re.fullmatch(r"[A-Za-z0-9]{9}", call_id):
        return call_id
    return hashlib.sha1(call_id.encode()).hexdigest()[:9]


class MistralProvider(Provider):
    name = "mistral"

    def available(self) -> bool:
        return bool(self.settings.mistral_api_key)

    async def turn(self, system, messages, tools, has_images):
        from mistralai.client import Mistral

        model = self.settings.mistral_vision_model if has_images else self.settings.mistral_text_model
        async with Mistral(api_key=self.settings.mistral_api_key) as client:
            try:
                resp = await client.chat.complete_async(
                    model=model,
                    messages=_openai_messages(system, messages, image_as_str=True, id_map=_mistral_id),
                    tools=_openai_tools(tools) or None,
                    tool_choice="auto" if tools else None,
                    temperature=self.settings.llm_temperature,
                    max_tokens=self.settings.llm_max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - cualquier fallo activa el siguiente proveedor
                raise ProviderError(self.name, repr(exc)) from exc
        msg = resp.choices[0].message
        calls = [{"id": tc.id, "name": tc.function.name, "arguments": _parse_args(tc.function.arguments)} for tc in msg.tool_calls or []]
        text = msg.content if isinstance(msg.content, str) else "".join(getattr(c, "text", "") for c in msg.content or [])
        return LLMTurn(text=text or "", tool_calls=calls, model=model, provider=self.name)


class OpenAICompatProvider(Provider):
    """GPT-4o (OpenAI) o Llama 3.2 Vision servido por Ollama mediante su API compatible."""

    def __init__(self, settings: Settings, name: str):
        super().__init__(settings)
        self.name = name

    def available(self) -> bool:
        return bool(self.settings.openai_api_key) if self.name == "openai" else bool(self.settings.ollama_base_url)

    async def turn(self, system, messages, tools, has_images):
        from openai import AsyncOpenAI

        if self.name == "openai":
            client, model = AsyncOpenAI(api_key=self.settings.openai_api_key), self.settings.openai_model
        else:
            client, model = AsyncOpenAI(api_key="ollama", base_url=self.settings.ollama_base_url), self.settings.ollama_vision_model
        try:
            kwargs: dict = {}
            if tools:
                kwargs = {"tools": _openai_tools(tools), "tool_choice": "auto"}
            resp = await client.chat.completions.create(
                model=model,
                messages=_openai_messages(system, messages, image_as_str=False),
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, repr(exc)) from exc
        finally:
            await client.close()
        msg = resp.choices[0].message
        calls = [{"id": tc.id, "name": tc.function.name, "arguments": _parse_args(tc.function.arguments)} for tc in msg.tool_calls or []]
        return LLMTurn(text=msg.content or "", tool_calls=calls, model=model, provider=self.name)


# --------------------------------------------------------------------------- Anthropic (Claude)


class AnthropicProvider(Provider):
    name = "anthropic"

    def available(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                content = [
                    {"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]}}
                    for img in m.get("images", [])
                ]
                content.append({"type": "text", "text": m.get("text", "")})
                out.append({"role": "user", "content": content})
            elif m["role"] == "assistant":
                if m.get("provider") == "anthropic" and m.get("native"):
                    # Se reenvía el contenido íntegro (incluidos bloques de thinking) sin modificar.
                    out.append({"role": "assistant", "content": m["native"]})
                    continue
                content = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                content += [{"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]} for tc in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if m.get("is_error"):
                    block["is_error"] = True
                # Todos los resultados de herramientas de un turno van en un único mensaje de usuario.
                if out and out[-1]["role"] == "user" and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    async def turn(self, system, messages, tools, has_images):
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        betas = ["server-side-fallback-2026-07-01"]
        thinking: dict = {"type": "adaptive"}
        if self.settings.anthropic_progress_updates:
            # Notas breves de progreso entre llamadas a herramientas: alimentan la traza en vivo.
            betas.append("thinking-display-updates-2026-08-18")
            thinking["display"] = "updates"
        try:
            # Los modelos Claude actuales no aceptan temperature: el determinismo se refuerza con
            # instrucciones del sistema y validación Pydantic de la salida estructurada.
            resp = await client.beta.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=self.settings.anthropic_max_tokens,
                system=system,
                messages=self._messages(messages),
                tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools] or anthropic.omit,
                thinking=thinking,
                output_config={"effort": self.settings.anthropic_effort},
                # Caché automático del prefijo estable (instrucciones + herramientas + historial).
                cache_control={"type": "ephemeral"},
                betas=betas,
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise ProviderError(self.name, "rate limit") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(self.name, f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(self.name, "error de conexión") from exc
        finally:
            await client.close()
        if resp.stop_reason == "refusal":
            category = resp.stop_details.category if resp.stop_details else None
            raise ProviderError(self.name, f"refusal ({category})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        progress = " ".join(b.thinking for b in resp.content if b.type == "thinking" and getattr(b, "thinking", ""))
        calls = [{"id": b.id, "name": b.name, "arguments": b.input} for b in resp.content if b.type == "tool_use"]
        return LLMTurn(
            text=text or progress, tool_calls=calls, model=resp.model, provider=self.name, native=[b.to_dict() for b in resp.content]
        )


# --------------------------------------------------------------------------- Offline (sin LLM)


class MockProvider(Provider):
    """Razonador heurístico y determinista: búsqueda -> expediente -> respuesta con los fragmentos recuperados.

    No es un modelo de lenguaje y no interpreta la normativa: existe para probar la orquestación,
    el RAG, MCP, el RBAC y la revisión del gestor sin claves de API. Resume literalmente los
    fragmentos más relevantes y siempre marca la respuesta como pendiente de estudio por el asesor.
    """

    name = "mock"

    def available(self) -> bool:
        return self.settings.allow_mock_llm

    async def describe_image(self, image: bytes, context: str = "") -> str:
        return ""

    async def turn(self, system, messages, tools, has_images):
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        query = messages[last_user].get("text", "")
        results = {m["name"]: m for m in messages[last_user:] if m["role"] == "tool" and not m.get("is_error")}
        expediente = re.search(r"Expediente seleccionado:\s*(\S+)", query)
        pregunta = query.split("\n")[0]
        names = {t.name for t in tools}

        def call(name: str, args: dict) -> LLMTurn:
            cid = "mock" + hashlib.sha1(f"{name}{len(messages)}".encode()).hexdigest()[:5]
            return LLMTurn(text=f"Consulto {name}.", tool_calls=[{"id": cid, "name": name, "arguments": args}], model="mock-heuristic", provider=self.name)

        if "buscar_documentacion" in names and "buscar_documentacion" not in results:
            return call("buscar_documentacion", {"consulta": pregunta, "top_k": 6})
        if expediente and "obtener_expediente" in names and "obtener_expediente" not in results:
            return call("obtener_expediente", {"expediente_id": expediente.group(1)})
        return call("responder_consulta", self._answer(results))

    @staticmethod
    def _answer(results: dict) -> dict:
        try:
            hits = json.loads(results["buscar_documentacion"]["content"]).get("resultados", [])
        except (KeyError, json.JSONDecodeError, AttributeError):
            hits = []
        texts = [h for h in hits if h.get("tipo") in ("text", "structured_table")]
        top = texts[:3]
        modelos = sorted({m for h in top for m in re.findall(r"[Mm]odelo\s+\d{3}", h["contenido"])})
        fuentes = [h["chunk_id"] for h in top]
        capturas = [h["image_name"] for h in hits if h.get("image_name")][:2]
        if not top:
            return {
                "resumen": "No he encontrado información sobre esta cuestión en la documentación disponible.",
                "respuesta_detallada": "", "fuentes": [], "confianza": 0.1, "requiere_asesor": True,
                "preguntas_aclaratorias": ["¿Puede indicar el impuesto, el modelo o el ejercicio al que se refiere?"],
            }
        first = " ".join(top[0]["contenido"].split())
        return {
            "resumen": f"Según {top[0]['documento']} (pág. {top[0]['pagina']}): {first[:280]}…",
            "respuesta_detallada": "\n\n".join(
                f"{h['documento']}, pág. {h['pagina']}{' · ' + h['seccion'] if h.get('seccion') else ''}:\n{' '.join(h['contenido'].split())[:700]}"
                for h in top
            ),
            "modelos": modelos,
            "fuentes": fuentes,
            "capturas": capturas,
            "advertencias": ["Respuesta generada sin modelo de lenguaje (modo offline): son extractos literales de la documentación."],
            "confianza": 0.3,
            "requiere_asesor": True,
        }


# --------------------------------------------------------------------------- Router


@dataclass
class ModelRouter:
    settings: Settings
    providers: list[Provider] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelRouter:
        registry = {
            "mistral": lambda: MistralProvider(settings),
            "ollama": lambda: OpenAICompatProvider(settings, "ollama"),
            "anthropic": lambda: AnthropicProvider(settings),
            "openai": lambda: OpenAICompatProvider(settings, "openai"),
            "mock": lambda: MockProvider(settings),
        }
        order = [p.strip() for p in settings.llm_provider_order.split(",") if p.strip() in registry]
        providers = [p for p in (registry[name]() for name in order) if p.available()]
        log.info("Proveedores LLM activos: %s", [p.name for p in providers])
        return cls(settings=settings, providers=providers)

    def catalog(self) -> list[dict]:
        s = self.settings
        models = {
            "mistral": [s.mistral_text_model, s.mistral_vision_model],
            "ollama": [s.ollama_vision_model],
            "anthropic": [s.anthropic_model],
            "openai": [s.openai_model],
            "mock": ["mock-heuristic"],
        }
        return [{"provider": p.name, "models": models[p.name], "vision": p.vision} for p in self.providers]

    def _chain(self, preferred: str | None, has_images: bool) -> list[Provider]:
        chain = [p for p in self.providers if p.vision or not has_images]
        if preferred:
            chain.sort(key=lambda p: p.name != preferred)
        return chain

    async def turn(self, system: str, messages: list[dict], tools: list[ToolSpec], has_images: bool, preferred: str | None = None) -> tuple[LLMTurn, list[str]]:
        errors: list[str] = []
        for provider in self._chain(preferred, has_images):
            try:
                return await provider.turn(system, messages, tools, has_images), errors
            except ProviderError as exc:
                log.warning("Fallback de modelo: %s", exc)
                errors.append(str(exc))
        raise RuntimeError("Ningún proveedor LLM disponible: " + "; ".join(errors or ["configure una clave de API o ALLOW_MOCK_LLM=true"]))

    async def describe_image(self, image: bytes, context: str = "") -> str:
        for provider in self._chain(None, True):
            try:
                return await provider.describe_image(image, context)
            except ProviderError as exc:
                log.warning("Descripción de imagen fallida en %s", exc)
        return ""


@lru_cache
def get_router() -> ModelRouter:
    return ModelRouter.from_settings(get_settings())
