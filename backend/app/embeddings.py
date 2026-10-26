"""Embeddings para la búsqueda híbrida sobre la documentación tributaria.

Cada fragmento se indexa en Qdrant con hasta tres vectores con nombre:
  - "dense": embedding semántico multilingüe (512 tokens) del texto del fragmento.
  - "bm25":  vector disperso BM25 con stemming y stopwords en español. Imprescindible
             para coincidencias exactas ("modelo 303", "artículo 68", "casilla 0545").
  - "clip":  solo en capturas y figuras, con el codificador visual de CLIP, para que
             una foto o captura del cliente recupere la pantalla equivalente del manual.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from PIL import Image

from .config import Settings, get_settings


@dataclass
class Sparse:
    indices: list[int]
    values: list[float]


class Embedder(Protocol):
    dense_dim: int
    clip_dim: int

    def embed_dense(self, texts: list[str]) -> list[list[float]]: ...
    def embed_dense_query(self, text: str) -> list[float]: ...
    def embed_sparse(self, texts: list[str]) -> list[Sparse]: ...
    def embed_sparse_query(self, text: str) -> Sparse: ...
    def embed_clip_image(self, images: list[bytes]) -> list[list[float]]: ...


def _open(image: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image)).convert("RGB")


class FastEmbedEmbedder:
    def __init__(self, settings: Settings):
        from fastembed import ImageEmbedding, SparseTextEmbedding, TextEmbedding

        threads = settings.embedding_threads  # limitar hilos evita la sobresuscripción de CPU de ONNX Runtime
        self._dense = TextEmbedding(settings.text_embedding_model, threads=threads)
        self._sparse = SparseTextEmbedding(settings.sparse_embedding_model, language=settings.sparse_language)
        self._clip = ImageEmbedding(settings.clip_vision_model, threads=threads)
        self.dense_dim = self._dense.embedding_size
        self.clip_dim = self._clip.embedding_size

    def embed_dense(self, texts):
        return [v.tolist() for v in self._dense.embed(texts, batch_size=8)]

    def embed_dense_query(self, text):
        return next(iter(self._dense.query_embed(text))).tolist()

    def embed_sparse(self, texts):
        return [Sparse(v.indices.tolist(), v.values.tolist()) for v in self._sparse.embed(texts, batch_size=64)]

    def embed_sparse_query(self, text):
        v = next(iter(self._sparse.query_embed(text)))
        return Sparse(v.indices.tolist(), v.values.tolist())

    def embed_clip_image(self, images):
        return [v.tolist() for v in self._clip.embed([_open(i) for i in images])]


class HashEmbedder:
    """Embedder determinista sin modelos (hashing trick). Solo para pruebas y CI sin red."""

    dense_dim = 384
    clip_dim = 512

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    @staticmethod
    def _h(tok: str) -> int:
        return int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")

    def _vec(self, text: str, dim: int) -> list[float]:
        vec = [0.0] * dim
        toks = self._tokens(text)
        for tok in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]:
            h = self._h(tok)
            vec[h % dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_dense(self, texts):
        return [self._vec(t, self.dense_dim) for t in texts]

    def embed_dense_query(self, text):
        return self._vec(text, self.dense_dim)

    def embed_sparse(self, texts):
        out = []
        for t in texts:
            counts: dict[int, float] = {}
            for tok in self._tokens(t):
                idx = self._h(tok) % 2**31
                counts[idx] = counts.get(idx, 0.0) + 1.0
            out.append(Sparse(list(counts), list(counts.values())))
        return out

    def embed_sparse_query(self, text):
        return self.embed_sparse([text])[0]

    def embed_clip_image(self, images):
        out = []
        for raw in images:
            px = [c / 255.0 for p in _open(raw).resize((16, 16)).getdata() for c in p]
            vec = [px[i] - px[i + 256] for i in range(self.clip_dim // 2)] * 2
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


@lru_cache
def _build(backend: str) -> Embedder:
    return HashEmbedder() if backend == "hash" else FastEmbedEmbedder(get_settings())


def get_embedder(settings: Settings | None = None) -> Embedder:
    return _build((settings or get_settings()).embeddings_backend)
