"""Contratos de datos: salida estructurada del agente y respuestas del API Gateway."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RespuestaTramite(BaseModel):
    """Salida estructurada que el Agente Tributario entrega con la herramienta responder_consulta."""

    model_config = ConfigDict(extra="forbid")

    resumen: str = Field(description="Respuesta directa en 2-4 frases, en lenguaje claro para el cliente")
    respuesta_detallada: str = Field(description="Explicación completa basada en la documentación citada")
    modelos: list[str] = Field(default_factory=list, description="Modelos o formularios aplicables, p. ej. 'Modelo 303'")
    requisitos: list[str] = Field(default_factory=list, description="Requisitos o condiciones que debe cumplir el contribuyente")
    documentacion: list[str] = Field(default_factory=list, description="Documentación que el cliente debe reunir")
    pasos: list[str] = Field(default_factory=list, description="Pasos ordenados del trámite (sede electrónica, Renta WEB…)")
    plazos: list[str] = Field(default_factory=list, description="Plazos SOLO si aparecen en la documentación recuperada")
    advertencias: list[str] = Field(default_factory=list, description="Riesgos, excepciones o avisos de vigencia")
    preguntas_aclaratorias: list[str] = Field(default_factory=list, description="Datos que faltan para responder con seguridad")
    fuentes: list[str] = Field(description="chunk_id de los fragmentos que respaldan la respuesta")
    capturas: list[str] = Field(default_factory=list, description="image_name de capturas del manual útiles para el trámite")
    confianza: float = Field(ge=0, le=1)
    requiere_asesor: bool = Field(description="True si el caso es complejo y conviene que el gestor lo estudie en detalle")


class Captura(BaseModel):
    image_name: str
    url: str
    doc_id: str
    documento: str | None = None
    pagina: int | None = None
    descripcion: str | None = None


class Fuente(BaseModel):
    chunk_id: str
    doc_id: str
    documento: str | None = None
    pagina: int | None = None
    seccion: str | None = None
    extracto: str
    vigente: bool = True


class RespuestaOut(BaseModel):
    resumen: str
    respuesta_detallada: str
    modelos: list[str]
    requisitos: list[str]
    documentacion: list[str]
    pasos: list[str]
    plazos: list[str]
    advertencias: list[str]
    preguntas_aclaratorias: list[str]
    fuentes: list[Fuente]
    capturas: list[Captura]
    confianza: float
    requiere_asesor: bool
    nota_gestor: str | None = None


class Revision(BaseModel):
    revision_id: str
    expediente_id: str | None
    cliente: str
    pregunta: str
    requested_at: int


class TraceStep(BaseModel):
    node: str
    kind: Literal["guardrail", "routing", "reasoning", "action", "observation", "expediente", "review", "delivery", "final", "privacy"]
    detail: str
    data: dict | None = None


class ConsultaResponse(BaseModel):
    session_id: str
    status: Literal["completed", "pending_review", "rejected", "blocked", "error"]
    pregunta: str | None = None
    cliente: str | None = None
    expediente_id: str | None = None
    respuesta: RespuestaOut | None = None
    revision: Revision | None = None
    mensaje_gestor: str | None = None
    blocked_reason: str | None = None
    model_used: str | None = None
    agent: str | None = None
    trace: list[TraceStep] = []


class RevisionDecision(BaseModel):
    aprobado: bool
    nota: str = Field("", description="Nota del gestor que verá el cliente")
    resumen_editado: str | None = Field(None, description="Resumen corregido por el gestor (opcional)")
