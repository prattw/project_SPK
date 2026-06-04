FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY data ./data

RUN mkdir -p chroma_db

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

# Railway/Fly set $PORT at runtime
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
