from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LLM (Anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_max_tokens: int = 4096

    # Embeddings: voyage (recommended with Claude) or openai
    embedding_provider: str = "voyage"
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-3"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    host: str = "0.0.0.0"
    port: int = 8000
    # Set when hosting online — clients send X-API-Key (UI stores in session)
    app_api_key: str = ""
    max_upload_mb: int = 300

    chroma_persist_dir: str = "./chroma_db"
    data_dir: str = "./data"

    collection_name: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 150

    # Retrieval → LLM (only a small, relevant slice is sent to Claude)
    retrieval_top_k: int = 8
    max_retrieval_candidates: int = 40
    max_context_chars: int = 120_000  # ~30k tokens of context + room for Q&A
    max_chunks_per_source: int = 8

    # Large construction PDFs (2000+ pages)
    max_extract_chars_per_file: int = 12_000_000
    max_chunks_per_file: int = 6_000
    max_pdf_pages: int = 2_500
    pdf_background_page_threshold: int = 75
    pdf_progress_every_pages: int = 25
    pdf_embed_flush_chunks: int = 250
    embedding_batch_size: int = 128

    @property
    def chroma_path(self) -> Path:
        return Path(self.chroma_persist_dir)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
