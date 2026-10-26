"""Directorio A2A (Agent-to-Agent): tarjetas de capacidades de los sub-agentes.

El Supervisor resuelve la intención consultando el directorio en lugar de tener el enrutado
cableado. Las tarjetas remotas se descubren en {url}/.well-known/agent-card.json y quedan
listadas; su invocación remota (JSON-RPC message/send) queda fuera del PoC.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class Skill:
    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    skills: list[Skill]
    version: str = "0.1.0"
    protocolVersion: str = "0.3.0"  # noqa: N815 - nombres de campo del estándar A2A
    capabilities: dict = field(default_factory=lambda: {"streaming": True, "pushNotifications": False})
    defaultInputModes: list[str] = field(default_factory=lambda: ["text/plain", "image/jpeg", "image/webp"])  # noqa: N815
    defaultOutputModes: list[str] = field(default_factory=lambda: ["application/json"])  # noqa: N815
    tools: list[str] = field(default_factory=list)
    local_node: str | None = None  # nodo del StateGraph que lo implementa (None = remoto)

    def public(self) -> dict:
        data = asdict(self)
        data.pop("local_node")
        return data


def local_cards(base_url: str) -> list[AgentCard]:
    return [
        AgentCard(
            name="agente-tributario",
            description="Responde consultas sobre trámites e impuestos con la documentación de la AEAT indexada, citando documento y página.",
            url=f"{base_url}/a2a/agente-tributario",
            skills=[Skill("consulta", "Consulta tributaria", "IRPF, IVA, Sociedades, Patrimonio, No Residentes, censos y facturación.",
                          ["irpf", "iva", "is", "rag"], ["¿Qué deducción autonómica por alquiler hay en Andalucía?"])],
            tools=["buscar_documentacion", "leer_paginas", "listar_expedientes", "obtener_expediente", "historial_expediente", "responder_consulta"],
            local_node="agente_tributario",
        ),
        AgentCard(
            name="agente-expedientes",
            description="Informa del estado de los expedientes del cliente y de la documentación pendiente.",
            url=f"{base_url}/a2a/agente-expedientes",
            skills=[Skill("expediente", "Estado de expedientes", "Trámites abiertos, estado y documentación pendiente.",
                          ["expedientes"], ["¿Qué me falta por entregar?"])],
            tools=["listar_expedientes", "obtener_expediente"],
            local_node="agente_expedientes",
        ),
        AgentCard(
            name="agente-seguridad",
            description="Filtra entradas no permitidas, enmascara datos personales, valida las respuestas y las retiene para revisión del gestor.",
            url=f"{base_url}/a2a/agente-seguridad",
            skills=[Skill("revision", "Revisión por gestor", "Human-in-the-Loop antes de entregar respuestas a clientes.", ["hitl", "rgpd"])],
            tools=["registrar_respuesta"],
            local_node=None,
        ),
    ]


class AgentDirectory:
    def __init__(self, base_url: str, remote_urls: list[str] | None = None):
        self.cards = local_cards(base_url)
        self.remote_urls = remote_urls or []

    async def discover_remote(self) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            for url in self.remote_urls:
                try:
                    data = (await client.get(f"{url.rstrip('/')}/.well-known/agent-card.json")).json()
                    skills = [Skill(**{k: s.get(k) for k in ("id", "name", "description")}) for s in data.get("skills", [])]
                    self.cards.append(AgentCard(name=data["name"], description=data.get("description", ""), url=data.get("url", url), skills=skills))
                    log.info("Agente A2A remoto descubierto: %s", data["name"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se pudo descubrir el agente A2A %s: %s", url, exc)

    def resolve(self, skill_id: str) -> AgentCard:
        """Primer agente local que anuncia la skill; por defecto, el agente tributario."""
        for card in self.cards:
            if card.local_node and any(s.id == skill_id for s in card.skills):
                return card
        return next(c for c in self.cards if c.local_node == "agente_tributario")
