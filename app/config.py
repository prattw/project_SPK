from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

    # Access roster — only these emails can sign in. Comma-separated; override
    # with the ACCESS_ROSTER env var. Empty string disables the roster gate.
    access_roster: str = (
        "william.a.pratt@usace.army.mil,"
        "daniel.t.osborne@usace.army.mil,"
        "cameron.l.sessions@usace.army.mil,"
        "ike.m.ukachi@usace.army.mil,"
        "nicholas.j.ivy@usace.army.mil,"
        "jesse.j.schlunegger@usace.army.mil,"
        "matthew.parks@usace.army.mil,"
        "hans.w.fotta@usace.army.mil,"
        "nicole.a.castle@usace.army.mil,"
        "angela.c.delwiche@usace.army.mil,"
        "suzanne.monk@usace.army.mil,"
        "shakib.a.waheedi@usace.army.mil,"
        "anita.y.sie@usace.army.mil,"
        "robert.m.mctighe@usace.army.mil,"
        "chi.m.bui@usace.army.mil,"
        "richard.l.wells@usace.army.mil"
    )
    # Secret for signing login tokens. Set AUTH_SECRET in production so sessions
    # survive restarts; falls back to APP_API_KEY, then an ephemeral boot secret.
    auth_secret: str = ""
    auth_token_hours: int = 24  # users must sign in again after this long

    # Empty / default → {DATA_DIR}/usage.db so metrics stay on the Railway volume.
    # Override only if you intentionally use a different persistent path.
    usage_db_path: str = ""
    usage_admin_emails: str = "william.a.pratt@usace.army.mil"

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

    # --- Email assistant (Outlook) ---
    # Summarizing/drafting from an email the user pastes or uploads carries the
    # same risk profile as the document uploads the app already accepts, so it is
    # on by default. Reading a mailbox directly does not: outlook_connector stays
    # "manual" until Entra ID access is provisioned and security-reviewed.
    email_assistant_enabled: bool = True
    email_max_chars: int = 60_000
    email_scrub_pii: bool = True  # redact SSN/EDIPI/DOB/card numbers before the LLM sees the text
    email_library_top_k: int = 24  # retrieval budget when a reply cites the Document Library

    # --- Autonomous sweep ---
    # Opening the Email Assistant reads the recent window, analyzes every message,
    # and drafts replies/notes/invites for the user to review. It costs 1-2 model
    # calls per message, so the message cap bounds both time and spend.
    email_sweep_enabled: bool = True
    email_sweep_hours: int = 72
    email_sweep_max_messages: int = 40
    email_sweep_max_hours: int = 336  # 14 days — ceiling on a user-supplied window
    # Start a sweep automatically when the tab is opened, when the configured
    # source can read mail without the user supplying files.
    email_sweep_autostart: bool = True
    # Interpreting "Thursday at 10" from an email needs a timezone. SPK is the
    # Sacramento District, so Pacific matches both the users and the weekly report.
    email_sweep_timezone: str = "America/Los_Angeles"
    email_sweep_drafts: bool = True  # draft replies for mail that needs one
    email_sweep_notes: bool = True  # write a note for the record
    email_sweep_invites: bool = True  # build .ics appointments/invites

    outlook_connector: str = "manual"  # manual | local_folder | graph
    # Directory the local_folder connector reads .msg/.eml files from. This is the
    # connector that makes an autonomous sweep possible with no cloud access: an
    # Outlook rule or export drops recent mail here and Project SPK reads the files.
    outlook_local_folder: str = ""
    outlook_graph_cloud: str = "gcchigh"  # commercial | gcc | gcchigh | dod
    outlook_tenant_id: str = ""
    outlook_client_id: str = ""
    outlook_client_secret: str = ""

    @property
    def roster_emails(self) -> frozenset[str]:
        return frozenset(
            email.strip().lower()
            for email in self.access_roster.split(",")
            if email.strip()
        )

    @property
    def usage_admin_emails_set(self) -> frozenset[str]:
        return frozenset(
            email.strip().lower()
            for email in self.usage_admin_emails.split(",")
            if email.strip()
        )

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
    def sweep_tzinfo(self) -> tzinfo:
        """Timezone used to resolve relative meeting times out of email text."""
        try:
            return ZoneInfo(self.email_sweep_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return timezone.utc


settings = Settings()
