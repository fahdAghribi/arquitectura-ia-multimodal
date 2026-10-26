import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

# Entorno hermético: sin red, sin claves, sin Keycloak.
os.environ.update(
    {
        "EMBEDDINGS_BACKEND": "hash",
        "VECTOR_DB_URL": ":memory:",
        "AUTH_MODE": "disabled",
        "LLM_PROVIDER_ORDER": "mock",
        "ALLOW_MOCK_LLM": "true",
        "MCP_APPROVAL_SECRET": "test-secret",
        "ANTHROPIC_API_KEY": "",
    }
)

# Documento SINTÉTICO de prueba (contenido ficticio, no normativa real).
PAGES = [
    ("Modelo 303. Autoliquidación del IVA",
     "El modelo 303 es la autoliquidación periódica del IVA. Los empresarios y profesionales lo presentan "
     "trimestralmente indicando el IVA devengado y el IVA soportado deducible del periodo. "
     "Documento de prueba sintético para los tests automáticos."),
    ("Deducciones autonómicas › Andalucía",
     "Deducción por alquiler de vivienda habitual (prueba): los contribuyentes menores de 35 años podrán "
     "deducir un porcentaje de las cantidades satisfechas por el alquiler. Requisitos: contrato de alquiler y "
     "justificantes de pago mediante transferencia. Documento de prueba sintético."),
    ("Renta WEB › Acceso", "Pulse en Accede a la campaña y seleccione el servicio de tramitación de borrador."),
]


def build_test_pdf(path: Path) -> None:
    import pymupdf
    from PIL import Image, ImageDraw

    doc = pymupdf.open()
    for title, body in PAGES:
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=14)
        page.insert_textbox(pymupdf.Rect(72, 100, 520, 400), body, fontsize=11)
        page.insert_text((72, 800), "Documento de prueba - Asistente tributario", fontsize=8)  # pie repetido
    table_page = doc[0]
    rows = [["Modelo", "Periodicidad"], ["303", "Trimestral"], ["390", "Anual"]]
    x0, y0, w, h = 72, 420, 160, 24
    for i in range(len(rows) + 1):
        table_page.draw_line((x0, y0 + i * h), (x0 + 2 * w, y0 + i * h), color=(0, 0, 0), width=0.8)
    for j in range(3):
        table_page.draw_line((x0 + j * w, y0), (x0 + j * w, y0 + len(rows) * h), color=(0, 0, 0), width=0.8)
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            table_page.insert_text((x0 + j * w + 6, y0 + i * h + 16), cell, fontsize=10)
    img = Image.new("RGB", (640, 400), (240, 244, 250))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 600, 120), fill=(0, 82, 155))
    d.rectangle((60, 200, 300, 260), fill=(255, 255, 255), outline=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc[2].insert_image(pymupdf.Rect(72, 200, 520, 480), stream=buf.getvalue())
    doc[2].insert_text((72, 500), "Captura: pantalla de acceso a la campaña de Renta", fontsize=10)
    doc.set_toc([[1, "Modelo 303", 1], [1, "Deducciones autonómicas", 2], [2, "Andalucía", 2], [1, "Renta WEB", 3]])
    doc.save(path)


@pytest.fixture(scope="session")
def mcp_server(tmp_path_factory):
    os.environ["EXPEDIENTES_DB_PATH"] = str(tmp_path_factory.mktemp("mcp") / "expedientes.db")
    spec = importlib.util.spec_from_file_location("expedientes_server", ROOT / "mcp-servers" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def indexed(tmp_path_factory):
    """PDF sintético ingerido en un Qdrant en memoria con el embedder determinista."""
    import ingest_documents as ing
    from qdrant_client import QdrantClient

    from app.config import get_settings
    from app.embeddings import get_embedder
    from app.rag import ensure_collection

    work = tmp_path_factory.mktemp("docs")
    pdf = work / "prueba.pdf"
    build_test_pdf(pdf)
    meta = {"doc_id": "TEST-IVA-2026", "titulo": "Documento de prueba", "impuesto": "IVA", "ejercicio": 2026, "tipo": "manual_practico", "vigente": True}
    old = meta | {"doc_id": "TEST-OLD-2019", "titulo": "Documento antiguo de prueba", "ejercicio": 2019, "vigente": False}
    client = QdrantClient(location=":memory:")
    ensure_collection(client, get_settings().qdrant_collection, get_embedder(), recreate=True)
    chunks = ing.extract_document(str(pdf), meta, str(work / "media"), "/static/extracted_images")
    ing.upsert(chunks, meta, client, get_settings().qdrant_collection)
    old_chunks = ing.extract_document(str(pdf), old, str(work / "media"), "/static/extracted_images")
    ing.upsert(old_chunks, old, client, get_settings().qdrant_collection)
    (work / "catalogo.json").write_text(json.dumps({"documentos": [meta, old]}))
    return {"client": client, "chunks": chunks, "media": work / "media"}
