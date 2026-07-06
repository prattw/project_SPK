from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # OpenAI — chat (GPT) and embeddings with one API key
    openai_api_key: str = ""
    # Override the API root for Azure OpenAI, a proxy, or a local gateway.
    # Leave blank to use the OpenAI SDK default (https://api.openai.com/v1).
    openai_base_url: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 4096
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "openai"
    embedding_batch_size: int = 128

    # Optional: Voyage embeddings only (if you switch EMBEDDING_PROVIDER=voyage)
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-3"

    host: str = "0.0.0.0"
    port: int = 8000
    app_api_key: str = ""
    max_upload_mb: int = 300

    chroma_persist_dir: str = "./chroma_db"
    data_dir: str = "./data"

    collection_name: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 150

    retrieval_top_k: int = 8
    max_retrieval_candidates: int = 40
    max_retrieval_candidates_with_library: int = 80
    max_context_chars: int = 120_000
    max_chunks_per_source: int = 8
    max_focus_chunks_per_source: int = 4

    max_extract_chars_per_file: int = 12_000_000
    max_chunks_per_file: int = 6_000
    max_pdf_pages: int = 2_500
    pdf_background_page_threshold: int = 75
    pdf_progress_every_pages: int = 25
    pdf_embed_flush_chunks: int = 250
    # Pages whose embedded text is shorter than this are treated as scanned/image
    # pages and sent through vision OCR (so scanned PDFs become searchable).
    pdf_ocr_min_chars: int = 24
    max_ocr_pages: int = 60  # cap OCR calls per document to bound time/cost
    allow_index_reset: bool = False  # set true only for local admin; never in production UI
    warm_index_on_startup: bool = True  # background-load the vector index at boot so the first request isn't slow

    # Retrieval tuning for spec/submittal comparison sessions
    library_retrieval_slots: int = 24
    section_search_limit: int = 40
    min_library_chunks_in_context: int = 12
    library_subquery_slots: int = 8

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
