FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/models \
    MEDIA_ROOT=/app/static/extracted_images \
    MEDIA_DIR=/app/static/extracted_images

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modelos de embeddings (denso multilingüe, BM25 en español, CLIP visión) y datos NLTK de LlamaIndex
# horneados en la imagen: el contenedor arranca sin depender de descargas externas.
RUN python -c "from fastembed import TextEmbedding, SparseTextEmbedding, ImageEmbedding; \
TextEmbedding('sentence-transformers/paraphrase-multilingual-mpnet-base-v2'); \
SparseTextEmbedding('Qdrant/bm25', language='spanish'); ImageEmbedding('Qdrant/clip-ViT-B-32-vision')" \
 && python -c "from llama_index.core.node_parser import SentenceSplitter; SentenceSplitter().split_text('Hola. Mundo.')"

COPY app ./app

RUN useradd --create-home --uid 10001 api \
 && mkdir -p /app/static/extracted_images /app/scripts /app/data \
 && chown -R api /app/static /app/models
USER api

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
