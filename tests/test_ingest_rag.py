from app.config import get_settings
from app.rag import Retriever


def test_extraction_sections_tables_images(indexed):
    chunks = indexed["chunks"]
    kinds = {c.chunk_type for c in chunks}
    assert kinds == {"text", "structured_table", "image_description"}

    text_p2 = next(c for c in chunks if c.chunk_type == "text" and c.page_number == 2)
    assert text_p2.section == "Deducciones autonómicas › Andalucía"

    table = next(c for c in chunks if c.chunk_type == "structured_table")
    assert table.metadata["table_headers"] == ["Modelo", "Periodicidad"]
    assert "| 303 | Trimestral |" in table.content_text

    img = next(c for c in chunks if c.chunk_type == "image_description")
    assert img.page_number == 3 and (indexed["media"] / "TEST-IVA-2026" / img.metadata["image_name"]).is_file()
    assert "Renta WEB" in img.content_text


def test_boilerplate_removed(indexed):
    assert not any("Documento de prueba - Asistente tributario" in c.content_text for c in indexed["chunks"])


def test_hybrid_search_exact_terms_and_filters(indexed):
    r = Retriever(client=indexed["client"])
    hits = r.search("modelo 303 trimestral", top_k=5, impuesto="IVA", ejercicio=2026)
    assert hits and all(h.payload["doc_id"] == "TEST-IVA-2026" for h in hits)
    assert any("303" in h.payload["content_text"] for h in hits)

    vigentes = r.search("alquiler vivienda", top_k=10, solo_vigentes=True)
    assert vigentes and all(h.payload["vigente"] for h in vigentes)


def test_old_documents_flagged(indexed):
    hits = Retriever(client=indexed["client"]).search("alquiler vivienda", top_k=10, ejercicio=2019)
    assert hits and all("aviso" in h.for_llm() for h in hits)


def test_read_pages_and_catalog(indexed):
    r = Retriever(client=indexed["client"])
    page = r.read_pages("TEST-IVA-2026", 2)
    assert page and all(h.payload["page_number"] == 2 for h in page)
    docs = {d["doc_id"]: d for d in r.catalog()}
    assert set(docs) == {"TEST-IVA-2026", "TEST-OLD-2019"}
    assert docs["TEST-OLD-2019"]["vigente"] is False


def test_payload_contract(indexed):
    points, _ = indexed["client"].scroll(get_settings().qdrant_collection, limit=100, with_payload=True)
    for p in points:
        assert {"chunk_id", "seq", "doc_id", "titulo", "impuesto", "ejercicio", "vigente", "page_number", "section", "chunk_type", "content_text"} <= set(p.payload)


def test_current_documents_rank_before_old_ones(indexed):
    hits = Retriever(client=indexed["client"]).search("alquiler vivienda habitual deducción", top_k=6)
    flags = [h.payload["vigente"] for h in hits]
    assert flags and flags[0] is True
    assert flags == sorted(flags, reverse=True)  # ningún documento antiguo por delante de uno vigente
    assert flags.count(False) <= 2
