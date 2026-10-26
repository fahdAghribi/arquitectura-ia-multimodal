"""Recuperación híbrida sobre Qdrant: denso + BM25 (+ CLIP si hay imagen), fusionados con RRF."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import QdrantClient, models

from .config import Settings, get_settings
from .embeddings import Embedder, get_embedder

log = logging.getLogger(__name__)


@dataclass
class Hit:
    chunk_id: str
    score: float
    payload: dict

    @property
    def image_url(self) -> str | None:
        meta = self.payload.get("metadata", {})
        if not meta.get("image_name"):
            return None
        return f"{meta['folder_path'].rstrip('/')}/{meta['image_name']}"

    def for_llm(self) -> dict:
        """Representación compacta que el agente recibe como observación."""
        p = self.payload
        item = {
            "chunk_id": self.chunk_id,
            "documento": p.get("titulo"),
            "doc_id": p.get("doc_id"),
            "impuesto": p.get("impuesto"),
            "ejercicio": p.get("ejercicio"),
            "pagina": p.get("page_number"),
            "seccion": p.get("section") or None,
            "tipo": p.get("chunk_type"),
            "contenido": p.get("content_text", "")[:1800],
            "score": round(self.score, 4),
        }
        if not p.get("vigente", True):
            item["aviso"] = f"Documento de un ejercicio antiguo ({p.get('ejercicio')}): puede no reflejar la normativa vigente."
        if p.get("metadata", {}).get("image_name"):
            item["image_name"] = p["metadata"]["image_name"]
        return {k: v for k, v in item.items() if v is not None}


def ensure_collection(client: QdrantClient, name: str, embedder: Embedder, recreate: bool = False) -> None:
    if recreate and client.collection_exists(name):
        client.delete_collection(name)
    if client.collection_exists(name):
        return
    client.create_collection(
        name,
        vectors_config={
            "dense": models.VectorParams(size=embedder.dense_dim, distance=models.Distance.COSINE),
            "clip": models.VectorParams(size=embedder.clip_dim, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field, schema in (
        ("doc_id", "keyword"), ("impuesto", "keyword"), ("ejercicio", "integer"),
        ("chunk_type", "keyword"), ("page_number", "integer"), ("vigente", "bool"), ("seq", "integer"),
    ):
        client.create_payload_index(name, field, field_schema=schema)


@lru_cache
def get_qdrant(location: str) -> QdrantClient:
    if location == ":memory:":
        return QdrantClient(location=":memory:")
    return QdrantClient(url=location, timeout=30)


class Retriever:
    def __init__(self, settings: Settings | None = None, client: QdrantClient | None = None, embedder: Embedder | None = None):
        self.settings = settings or get_settings()
        self.client = client or get_qdrant(self.settings.qdrant_location)
        self.embedder = embedder or get_embedder(self.settings)
        self.collection = self.settings.qdrant_collection

    def ready(self) -> bool:
        try:
            return self.client.collection_exists(self.collection)
        except Exception:  # noqa: BLE001 - informe de salud, no propagar
            return False

    @staticmethod
    def _filter(impuesto: str | None, ejercicio: int | None, solo_vigentes: bool, doc_id: str | None = None) -> models.Filter | None:
        must = []
        if impuesto:
            must.append(models.FieldCondition(key="impuesto", match=models.MatchValue(value=impuesto)))
        if ejercicio:
            must.append(models.FieldCondition(key="ejercicio", match=models.MatchValue(value=int(ejercicio))))
        if solo_vigentes:
            must.append(models.FieldCondition(key="vigente", match=models.MatchValue(value=True)))
        if doc_id:
            must.append(models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)))
        return models.Filter(must=must) if must else None

    def search(
        self,
        query: str,
        image: bytes | None = None,
        top_k: int | None = None,
        impuesto: str | None = None,
        ejercicio: int | None = None,
        solo_vigentes: bool = False,
    ) -> list[Hit]:
        top_k = top_k or self.settings.rag_top_k
        if not self.ready():
            log.warning("Colección '%s' inexistente: ejecute scripts/ingest_documents.py", self.collection)
            return []
        dense = sparse = clip = None
        if query.strip():
            dense = self.embedder.embed_dense_query(query)
            sparse = self.embedder.embed_sparse_query(query)
        if image:
            clip = self.embedder.embed_clip_image([image])[0]
        if dense is None and clip is None:
            return []

        def run(flt: models.Filter | None, limit: int) -> list[Hit]:
            prefetch = []
            if dense is not None:
                prefetch.append(models.Prefetch(query=dense, using="dense", limit=limit * 4, filter=flt))
            if sparse is not None and sparse.indices:
                prefetch.append(models.Prefetch(query=models.SparseVector(indices=sparse.indices, values=sparse.values), using="bm25", limit=limit * 4, filter=flt))
            if clip is not None:
                prefetch.append(models.Prefetch(query=clip, using="clip", limit=limit, filter=flt))
            result = self.client.query_points(
                self.collection, prefetch=prefetch, query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit, with_payload=True
            )
            return [Hit(chunk_id=p.payload.get("chunk_id", str(p.id)), score=p.score, payload=p.payload) for p in result.points]

        if solo_vigentes or ejercicio:
            return run(self._filter(impuesto, ejercicio, solo_vigentes), top_k)
        # Sin ejercicio explícito, la documentación vigente va primero; la antigua solo se añade como apoyo al final.
        vigentes = run(self._filter(impuesto, None, True), top_k)
        antiguos_flt = self._filter(impuesto, None, False)
        antiguos_flt = models.Filter(must=[*(antiguos_flt.must if antiguos_flt else []), models.FieldCondition(key="vigente", match=models.MatchValue(value=False))])
        return vigentes + run(antiguos_flt, max(2, top_k // 4))

    def read_pages(self, doc_id: str, page: int, count: int = 1) -> list[Hit]:
        """Texto completo de páginas concretas, para que el agente amplíe el contexto de un fragmento."""
        flt = self._filter(None, None, False, doc_id)
        flt.must.append(models.FieldCondition(key="page_number", range=models.Range(gte=page, lt=page + max(1, min(count, 3)))))
        flt.must.append(models.FieldCondition(key="chunk_type", match=models.MatchAny(any=["text", "structured_table"])))
        points, _ = self.client.scroll(self.collection, scroll_filter=flt, limit=60, with_payload=True)
        points.sort(key=lambda p: (p.payload.get("page_number", 0), p.payload.get("seq", 0)))
        return [Hit(chunk_id=p.payload.get("chunk_id", str(p.id)), score=1.0, payload=p.payload) for p in points]

    def catalog(self) -> list[dict]:
        """Documentos indexados (una entrada por doc_id) para informar al agente y a la interfaz."""
        if not self.ready():
            return []
        seen: dict[str, dict] = {}
        offset = None
        flt = models.Filter(must=[models.FieldCondition(key="seq", match=models.MatchValue(value=0))])
        while True:
            points, offset = self.client.scroll(self.collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True)
            for p in points:
                pl = p.payload
                seen.setdefault(pl["doc_id"], {k: pl.get(k) for k in ("doc_id", "titulo", "impuesto", "ejercicio", "tipo", "vigente")})
            if offset is None:
                break
        return sorted(seen.values(), key=lambda d: (d["impuesto"] or "", d["doc_id"]))
