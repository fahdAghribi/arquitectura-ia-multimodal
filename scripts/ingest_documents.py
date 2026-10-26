"""Ingestión de la documentación tributaria (PDF) en Qdrant.

Por cada documento del catálogo (data/catalogo.json):
  - Texto: se agrupa por sección del índice del PDF (marcadores), se eliminan cabeceras y
    pies repetidos y se trocea con el SentenceSplitter de LlamaIndex. Cada fragmento guarda
    su página y su ruta de sección ("Capítulo 5 › Deducciones › Alquiler").
  - Tablas: Markdown con cabeceras; las tablas largas se parten repitiendo la cabecera.
  - Capturas y figuras: PNG en el volumen de medios (image_name / folder_path). Se descartan
    las imágenes repetidas (logos, cabeceras) y las demasiado pequeñas.

La extracción se paraleliza por documento; los embeddings se calculan por lotes.

Uso:
    python scripts/ingest_documents.py --qdrant_url http://vector-db:6333
    python scripts/ingest_documents.py --only AEAT-MP-IVA-2026 --qdrant_url http://vector-db:6333
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import multiprocessing
import os
import pickle
import re
import sys
import time
import unicodedata
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" if (ROOT / "backend").exists() else ROOT))

from qdrant_client import QdrantClient, models  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.embeddings import get_embedder  # noqa: E402
from app.rag import ensure_collection  # noqa: E402

log = logging.getLogger("ingest")
MIN_IMAGE_PX = 150
TABLE_ROWS_PER_CHUNK = 25
CAPTION_RE = re.compile(r"^\s*(Figura|Fig\.|Imagen|Captura|Pantalla|Ilustración)\b", re.IGNORECASE)


@dataclass
class Chunk:
    chunk_id: str
    chunk_type: str
    page_number: int
    section: str
    content_text: str
    seq: int
    metadata: dict = field(default_factory=dict)
    image_bytes: bytes | None = None


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def section_paths(doc: pymupdf.Document) -> dict[int, str]:
    """Ruta de sección vigente en cada página según los marcadores (TOC) del PDF."""
    toc = doc.get_toc(simple=True)
    by_page: dict[int, list[tuple[int, str]]] = collections.defaultdict(list)
    for level, title, page in toc:
        if page >= 1:
            by_page[page].append((level, _clean(title)))
    stack: list[str] = []
    paths = {}
    for pno in range(1, len(doc) + 1):
        for level, title in by_page.get(pno, []):
            stack = stack[: level - 1] + [title]
        paths[pno] = " › ".join(stack[-3:])
    return paths


MARGIN = 0.08  # franja superior/inferior donde viven cabeceras y pies
PAGE_NUMBER_RE = re.compile(r"(p[áa]g(ina)?\.?\s*)?\d+(\s*(de|/)\s*\d+)?", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", _clean(text))


def _in_margin(block, page: pymupdf.Page) -> bool:
    h = page.rect.height
    return block[3] < h * MARGIN or block[1] > h * (1 - MARGIN)


def boilerplate_lines(doc: pymupdf.Document) -> set[str]:
    """Bloques de cabecera/pie (por posición) que se repiten en muchas páginas: título del manual, numeración…"""
    counts: collections.Counter[str] = collections.Counter()
    for page in doc:
        seen = {_norm(b[4]) for b in page.get_text("blocks") if b[6] == 0 and _clean(b[4]) and _in_margin(b, page)}
        counts.update(seen)
    threshold = max(3, int(len(doc) * 0.3))
    return {line for line, c in counts.items() if c >= threshold}


def _is_boilerplate(block, page: pymupdf.Page, boiler: set[str]) -> bool:
    if not _in_margin(block, page):
        return False
    text = _clean(block[4])
    return _norm(text) in boiler or bool(PAGE_NUMBER_RE.fullmatch(text))


def _caption(page: pymupdf.Page, bbox: pymupdf.Rect, blocks: list) -> str:
    below = [b for b in blocks if b[1] >= bbox.y1 - 2 and b[1] - bbox.y1 < 90]
    above = [b for b in blocks if b[3] <= bbox.y0 + 2 and bbox.y0 - b[3] < 90]
    pick = [b for b in below if CAPTION_RE.match(b[4])] or above[-1:] or below[:1]
    return _clean(pick[0][4])[:400] if pick else ""


def extract_document(pdf_path: str, meta: dict, media_dir: str, public_prefix: str) -> list[Chunk]:
    doc = pymupdf.open(pdf_path)
    doc_id = meta["doc_id"]
    paths = section_paths(doc)
    boiler = boilerplate_lines(doc)
    splitter = SentenceSplitter(chunk_size=380, chunk_overlap=60)
    image_dir = Path(media_dir) / doc_id
    image_dir.mkdir(parents=True, exist_ok=True)
    folder_path = f"{public_prefix.rstrip('/')}/{doc_id}/"

    digest_count: collections.Counter = collections.Counter()
    for page in doc:
        for info in page.get_image_info(hashes=True):
            digest_count[info["digest"]] += 1

    chunks: list[Chunk] = []
    seq = 0

    def add(kind: str, page: int, section: str, text: str, metadata: dict | None = None, image: bytes | None = None):
        nonlocal seq
        chunks.append(Chunk(f"{doc_id}-{kind}-{seq:05d}", {"txt": "text", "tbl": "structured_table", "img": "image_description"}[kind],
                            page, section, text, seq, metadata or {}, image))
        seq += 1

    # Texto agrupado por sección: (sección, [(página, texto)])
    groups: list[tuple[str, list[tuple[int, str]]]] = []
    for page in doc:
        pno = page.number + 1
        section = paths.get(pno, "")
        blocks = [b for b in page.get_text("blocks") if b[6] == 0 and _clean(b[4])]

        table_rects = []
        try:
            tables = page.find_tables().tables
        except Exception:  # noqa: BLE001 - páginas con geometría corrupta
            tables = []
        for table in tables:
            rows = [[_clean(page.get_textbox(c)) if c else "" for c in row.cells] for row in table.rows]
            rows = [r for r in rows if any(r)]
            if len(rows) < 2 or len(rows[0]) < 2:
                continue
            table_rects.append(pymupdf.Rect(table.bbox))
            headers, body = rows[0], rows[1:]
            for start in range(0, len(body), TABLE_ROWS_PER_CHUNK):
                part = body[start : start + TABLE_ROWS_PER_CHUNK]
                md = "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + ["| " + " | ".join(r) + " |" for r in part])
                add("tbl", pno, section, md, {"table_headers": headers, "row_count": len(part)})

        for idx, info in enumerate(page.get_image_info(xrefs=True, hashes=True), start=1):
            if digest_count[info["digest"]] > 2 or min(info["width"], info["height"]) < MIN_IMAGE_PX or not info["xref"]:
                continue
            bbox = pymupdf.Rect(info["bbox"])
            if bbox.width * bbox.height < 0.02 * page.rect.width * page.rect.height:
                continue
            pix = pymupdf.Pixmap(doc, info["xref"])
            if pix.alpha or pix.n - pix.alpha != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            png = pix.tobytes("png")
            image_name = f"img_p{pno}_{idx}.png"
            (image_dir / image_name).write_bytes(png)
            caption = _caption(page, bbox, blocks)
            text = " — ".join(x for x in (section, caption) if x) or f"Captura de la página {pno}"
            add("img", pno, section, text, {"image_name": image_name, "folder_path": folder_path}, png)

        narrative = "\n".join(
            b[4].strip() for b in blocks
            if not _is_boilerplate(b, page, boiler) and not any(pymupdf.Rect(b[:4]).intersects(t) for t in table_rects)
        )
        if narrative.strip():
            if groups and groups[-1][0] == section:
                groups[-1][1].append((pno, narrative))
            else:
                groups.append((section, [(pno, narrative)]))

    for section, pages in groups:
        offsets, parts, pos = [], [], 0
        for pno, text in pages:
            offsets.append((pos, pno))
            parts.append(text)
            pos += len(text) + 2
        full = "\n\n".join(parts)
        for node in splitter.get_nodes_from_documents([Document(text=full)]):
            start = node.start_char_idx or full.find(node.text[:60])
            pno = next((p for off, p in reversed(offsets) if off <= max(start, 0)), pages[0][0])
            if len(_clean(node.text)) >= 40:
                add("txt", pno, section, node.text)
    return chunks


def _embed_text(chunk: Chunk, meta: dict) -> str:
    """El texto a vectorizar incluye documento y sección: mejora mucho la recuperación."""
    head = " › ".join(x for x in (meta["titulo"], chunk.section) if x)
    return f"[{head}]\n{chunk.content_text}"


def upsert(chunks: list[Chunk], meta: dict, client: QdrantClient, collection: str, batch: int = 64) -> None:
    embedder = get_embedder()
    client.delete(collection, points_selector=models.FilterSelector(
        filter=models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=meta["doc_id"]))])
    ))
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        texts = [_embed_text(c, meta) for c in part]
        dense = embedder.embed_dense(texts)
        sparse = embedder.embed_sparse(texts)
        img_idx = [j for j, c in enumerate(part) if c.image_bytes]
        clip = dict(zip(img_idx, embedder.embed_clip_image([part[j].image_bytes for j in img_idx]), strict=True)) if img_idx else {}
        points = []
        for j, c in enumerate(part):
            vector = {"dense": dense[j], "bm25": models.SparseVector(indices=sparse[j].indices, values=sparse[j].values)}
            if j in clip:
                vector["clip"] = clip[j]
            points.append(models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.chunk_id)),
                vector=vector,
                payload={
                    "chunk_id": c.chunk_id, "seq": c.seq, "doc_id": meta["doc_id"], "titulo": meta["titulo"],
                    "impuesto": meta["impuesto"], "ejercicio": meta["ejercicio"], "tipo": meta["tipo"], "vigente": meta["vigente"],
                    "page_number": c.page_number, "section": c.section, "chunk_type": c.chunk_type,
                    "content_text": c.content_text, "metadata": c.metadata,
                },
            ))
        client.upsert(collection, points=points, wait=True)
        if len(chunks) > batch and (i // batch) % 8 == 7:
            log.info("          %-28s %5d / %d", meta["doc_id"], i + len(part), len(chunks))


def load_catalog(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["documentos"]


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs_dir", type=Path, default=ROOT / "data/documentos")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalogo.json")
    parser.add_argument("--qdrant_url", default=settings.qdrant_location)
    parser.add_argument("--collection", default=settings.qdrant_collection)
    parser.add_argument("--media_dir", type=Path, default=Path(os.getenv("MEDIA_DIR", ROOT / "data/extracted_images")))
    parser.add_argument("--public_prefix", default="/static/extracted_images")
    parser.add_argument("--only", nargs="*", help="Ingerir solo estos doc_id")
    parser.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    parser.add_argument("--recreate", action="store_true", help="Borrar y recrear la colección")
    parser.add_argument("--force", action="store_true", help="Reindexar también los documentos ya presentes")
    parser.add_argument("--cache_dir", type=Path, default=ROOT / "data/.cache", help="Caché de extracción y estado de la ingestión")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    files = {_nfc(f.name): f for f in args.docs_dir.glob("*.pdf")}
    catalog = [d for d in load_catalog(args.catalog) if not args.only or d["doc_id"] in args.only]
    missing = [d["archivo"] for d in catalog if _nfc(d["archivo"]) not in files]
    if missing:
        log.warning("No encontrados en %s: %s", args.docs_dir, missing)
    catalog = [d for d in catalog if _nfc(d["archivo"]) in files]

    client = QdrantClient(location=":memory:") if args.qdrant_url == ":memory:" else QdrantClient(url=args.qdrant_url, timeout=60)
    cache_dir = args.cache_dir
    (cache_dir / "extract").mkdir(parents=True, exist_ok=True)
    state_file = cache_dir / "ingest_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}

    def indexed_count(doc_id: str) -> int:
        if not client.collection_exists(args.collection):
            return 0
        flt = models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])
        return client.count(args.collection, count_filter=flt).count

    # Un documento solo cuenta como hecho si terminó de indexarse completo (marcador + recuento en Qdrant).
    if not args.force and not args.recreate:
        done = [d["doc_id"] for d in catalog if state.get(d["doc_id"]) and indexed_count(d["doc_id"]) == state[d["doc_id"]]]
        if done:
            log.info("Ya indexados por completo (use --force para rehacerlos): %s", done)
        catalog = [d for d in catalog if d["doc_id"] not in done]
    log.info("Documentos a procesar: %d", len(catalog))
    t0 = time.time()

    # Fase 1: extracción en paralelo, sin modelos cargados ("spawn" evita heredar memoria del proceso padre).
    pending = [d for d in catalog if args.force or not (cache_dir / "extract" / f"{d['doc_id']}.pkl").exists()]
    if pending:
        log.info("Fase 1 · extracción de %d documento(s) con %d proceso(s)", len(pending), args.workers)
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(extract_document, str(files[_nfc(d["archivo"])]), d, str(args.media_dir), args.public_prefix): d for d in pending}
            for fut in as_completed(futures):
                meta = futures[fut]
                try:
                    chunks = fut.result()
                except Exception:
                    log.exception("Fallo extrayendo %s", meta["doc_id"])
                    continue
                (cache_dir / "extract" / f"{meta['doc_id']}.pkl").write_bytes(pickle.dumps(chunks))
                log.info("extraído  %-28s %5d fragmentos", meta["doc_id"], len(chunks))

    # Fase 2: embeddings documento a documento con todos los hilos para un único proceso.
    log.info("Fase 2 · embeddings (%s)", get_settings().text_embedding_model)
    ensure_collection(client, args.collection, get_embedder(), recreate=args.recreate)
    totals: collections.Counter = collections.Counter()
    for meta in catalog:
        cached = cache_dir / "extract" / f"{meta['doc_id']}.pkl"
        if not cached.exists():
            continue
        chunks = pickle.loads(cached.read_bytes())
        t = time.time()
        upsert(chunks, meta, client, args.collection)
        state[meta["doc_id"]] = len(chunks)
        state_file.write_text(json.dumps(state, indent=1))
        kinds = collections.Counter(c.chunk_type for c in chunks)
        totals.update(kinds)
        log.info("indexado  %-28s %5d fragmentos %s (%.0fs)", meta["doc_id"], len(chunks), dict(kinds), time.time() - t)
    log.info("Total: %d fragmentos %s en %.0fs", sum(totals.values()), dict(totals), time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
