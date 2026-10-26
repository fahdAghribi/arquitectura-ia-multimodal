FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

RUN useradd --create-home --uid 10001 mcp && mkdir -p /app/data && chown -R mcp /app/data
USER mcp

EXPOSE 9000
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -fs http://localhost:9000/health || exit 1
CMD ["python", "server.py"]
