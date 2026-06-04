FROM python:3.12-slim-bookworm

WORKDIR /app

# chromadb / hnswlib need a compiler on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY data ./data

RUN mkdir -p chroma_db data

ENV HOST=0.0.0.0
ENV PORT=8000
ENV CHROMA_PERSIST_DIR=/app/chroma_db
ENV DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
