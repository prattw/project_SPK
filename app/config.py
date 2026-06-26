from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # OpenAI — chat (GPT) and embeddings with one API key
    openai_api_key: str = ""
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

    # WebAuthn / YubiKey access control (enabled only when rp_id + origin are set,
    # so local dev stays open and the hosted deployment is gated).
    webauthn_rp_id: str = ""  # e.g. "projectspk-production.up.railway.app"
    webauthn_rp_name: str = "Project SPK"
    webauthn_origin: str = ""  # e.g. "https://projectspk-production.up.railway.app"
    webauthn_enroll_code: str = ""  # admin shares with authorized users to enroll a key
    # Comma-separated label:role pairs, e.g. "Admin:admin,User_1:user". Labels must match
    # enrollment names exactly. WebAuthn cannot read YubiKey serial numbers — use fixed labels.
    webauthn_role_map: str = "Admin:admin,User_1:user"
    session_secret: str = ""  # signs session/challenge cookies; set a long random value
    session_max_age_hours: int = 12
    auth_db_path: str = "./auth.db"

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
    allow_index_reset: bool = False  # set true only for local admin; never in production UI

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

    @property
    def webauthn_enabled(self) -> bool:
        return bool(self.webauthn_rp_id and self.webauthn_origin)

    @property
    def webauthn_origins(self) -> list[str]:
        """Allowed origins for WebAuthn verification (comma-separated in env)."""
        if not self.webauthn_origin:
            return []
        return [o.strip() for o in self.webauthn_origin.split(",") if o.strip()]

    @property
    def webauthn_roles(self) -> dict[str, str]:
        """Map enrollment label -> role (admin or user)."""
        out: dict[str, str] = {}
        raw = (self.webauthn_role_map or "").strip()
        if not raw:
            return out
        for part in raw.split(","):
            piece = part.strip()
            if not piece or ":" not in piece:
                continue
            label, role = piece.split(":", 1)
            label = label.strip()
            role = role.strip().lower()
            if label and role in ("admin", "user"):
                out[label] = role
        return out


settings = Settings()
