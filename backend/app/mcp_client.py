"""Cliente de los servidores FastMCP remotos (telemetría / control)."""

from __future__ import annotations

import json
import logging
import time

from fastmcp import Client

from .llm import ToolSpec

log = logging.getLogger(__name__)


class MCPTools:
    def __init__(self, url: str, cache_ttl_s: int = 60):
        self.url = url
        self._tools: list = []
        self._tools_at = 0.0
        self._ttl = cache_ttl_s

    async def list_tools(self) -> list:
        if not self._tools or time.time() - self._tools_at > self._ttl:
            async with Client(self.url, timeout=10) as client:
                self._tools = await client.list_tools()
                self._tools_at = time.time()
        return self._tools

    async def tool_specs(self, tag: str) -> list[ToolSpec]:
        """Herramientas expuestas al LLM filtradas por etiqueta (el agente nunca ve las de control)."""
        try:
            tools = await self.list_tools()
        except Exception as exc:  # noqa: BLE001
            log.warning("Servidor MCP no disponible (%s): el agente continúa sin telemetría", exc)
            return []
        return [
            ToolSpec(name=t.name, description=t.description or "", parameters=t.input_schema)
            for t in tools
            if tag in ((t.meta or {}).get("fastmcp", {}).get("tags", []))
        ]

    async def call(self, name: str, arguments: dict) -> tuple[bool, object]:
        try:
            async with Client(self.url, timeout=20) as client:
                result = await client.call_tool(name, arguments, raise_on_error=False)
        except Exception as exc:  # noqa: BLE001
            return False, f"Servidor MCP inaccesible: {exc}"
        if result.is_error:
            return False, " ".join(getattr(c, "text", "") for c in result.content)
        return True, result.data if result.data is not None else result.structured_content

    async def ping(self) -> bool:
        try:
            await self.list_tools()
            return True
        except Exception:  # noqa: BLE001
            return False


def to_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
