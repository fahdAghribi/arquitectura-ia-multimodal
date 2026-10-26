"""Búsqueda en la documentación expuesta como servidor FastMCP (montado en /mcp-rag/mcp del API Gateway).

Permite que otros agentes o clientes MCP consulten la documentación indexada con el mismo motor
que usa el Agente Tributario. Solo devuelve documentación pública, nunca datos de expedientes.
"""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from .rag import Retriever

rag_mcp = FastMCP(name="documentacion-tributaria", instructions="Búsqueda híbrida en publicaciones informativas de la AEAT indexadas.")


@rag_mcp.tool(tags={"rag"}, annotations={"readOnlyHint": True})
def buscar_documentacion(
    consulta: Annotated[str, Field(description="Pregunta, modelo, concepto o artículo a buscar")],
    impuesto: Annotated[str | None, Field(description="IRPF, IVA, IS, IP, IRNR, CENSAL, FACTURACION")] = None,
    ejercicio: Annotated[int | None, Field(description="Ejercicio fiscal, p. ej. 2025")] = None,
    top_k: Annotated[int, Field(ge=1, le=15)] = 8,
) -> dict:
    """Busca en la documentación tributaria. Devuelve fragmentos con documento, página y sección."""
    hits = Retriever().search(consulta, top_k=top_k, impuesto=impuesto, ejercicio=ejercicio)
    return {"resultados": [h.for_llm() | ({"image_url": h.image_url} if h.image_url else {}) for h in hits]}
