import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    ApiKeyMiddleware,
    auth_required,
    authenticated_email,
    email_on_roster,
    has_app_api_key,
    issue_login_token,
    require_api_key,
    roster_enabled,
)
from app.config import settings
from app.downloads import document_link_url, guess_media_type, resolve_data_file
from app.email_assistant import DEFAULT_TONE, REPLY_TONES, analyze_thread, draft_reply
from app.email_messages import (
    SUPPORTED_MESSAGE_SUFFIXES,
    EmailThread,
    msg_support_available,
    parse_message_file,
    parse_msg_file,
    parse_pasted_email,
)
from app.ingest import INGESTABLE_EXTENSIONS, ingest_directory, ingest_path, pdf_needs_background, save_upload
from app.jobs import (
    get_job,
    start_background_email_sweep,
    start_background_ingest,
    start_background_library_ingest,
    start_background_query,
)
from app.library_ingest import (
    extract_incoming_zip,
    library_incoming_path,
    save_incoming_upload,
)
from app.llm import model_endpoint_info
from app.outlook_connector import (
    MailboxUnavailable,
    can_read_mailbox,
    connector_status,
    get_connector,
)
from app.publication_sync import check_publication_sites
from app.rag import get_rag
from app.token_usage import get_tracking, start_tracking
from app.usage import (
    format_weekly_report_text,
    get_weekly_snapshot,
    init_usage_db,
    is_usage_admin,
    list_weekly_snapshots,
    record_email_usage,
    record_error,
    record_login,
    record_upload,
    save_weekly_snapshot,
    usage_summary,
    weekly_usage_report,
)
from app.usage_scheduler import start_weekly_usage_scheduler

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    focus_sources: list[str] | None = Field(default=None, max_length=50)
    session_id: str | None = Field(default=None, max_length=64)
    include_library: bool = True
    section_numbers: list[str] | None = Field(default=None, max_length=10)
    history: list[dict[str, str]] | None = Field(default=None, max_length=20)


class Citation(BaseModel):
    source: str
    doc_number: str | None = None
    doc_type: str | None = None
    page: int | None = None
    page_end: int | None = None
    label: str
    url: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    citations: list[Citation] = []
    chunks_used: int
    context_warnings: list[str] = []


class IngestResponse(BaseModel):
    chunks_indexed: int
    files_processed: list[str]
    message: str
    warnings: list[str] = []


class UploadResponse(BaseModel):
    filename: str
    chunks_indexed: int = 0
    message: str
    warnings: list[str] = []
    status: str = "complete"  # complete | processing
    job_id: str | None = None
    pages_total: int | None = None


class QueryJobResponse(BaseModel):
    job_id: str
    status: str
    message: str = "Query started."


class JobStatusResponse(BaseModel):
    job_id: str
    kind: str = "ingest"
    status: str
    phase: str = ""
    filename: str = ""
    pages_total: int = 0
    pages_done: int = 0
    chunks_indexed: int = 0
    files_total: int = 0
    files_done: int = 0
    message: str = ""
    warnings: list[str] = []
    elapsed_ms: int | None = None
    result: QueryResponse | None = None
    library_report: dict | None = None
    sweep_report: dict | None = None


class LibraryIngestRequest(BaseModel):
    purge_patterns: list[str] | None = Field(
        default=None,
        max_length=20,
        description='Remove existing indexed sources matching these substrings before ingest (e.g. ["UFC"]).',
    )


class LibraryUploadResponse(BaseModel):
    filename: str
    message: str
    incoming_count: int


class FilesResponse(BaseModel):
    files: list[str]
    documents: list[dict] = []
    chunks_indexed: int


class PublicationSyncResponse(BaseModel):
    status: str
    last_sync: str | None = None
    sites_checked: int = 0
    links_found: int = 0
    new_publications: list[dict] = []
    errors: list[str] = []
    message: str = ""


class ContextLimits(BaseModel):
    max_upload_mb: int
    max_extract_chars_per_file: int
    max_chunks_per_file: int
    max_pdf_pages: int
    pdf_background_page_threshold: int
    max_retrieval_candidates: int
    max_context_chars: int
    max_chunks_per_source: int
    chunk_size: int


class HealthResponse(BaseModel):
    status: str
    version: str
    documents_indexed: int
    data_dir: str
    llm: str
    embeddings: str
    context_limits: ContextLimits
    auth_required: bool
    llm_configured: bool
    embeddings_configured: bool


def _require_usage_admin(request: Request) -> str:
    # Durable machine credential for GitHub Actions / CLI automation.
    # Login session tokens expire in AUTH_TOKEN_HOURS and cannot be scheduled weekly.
    if has_app_api_key(request):
        return "app-api-key"
    email = authenticated_email(request)
    if not is_usage_admin(email):
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return email or ""


def _require_keys() -> None:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured. Add it in Railway Variables or .env.",
        )
    provider = settings.embedding_provider.lower()
    if provider == "voyage" and not settings.voyage_api_key:
        raise HTTPException(
            status_code=503,
            detail="VOYAGE_API_KEY is not configured (or set EMBEDDING_PROVIDER=openai).",
        )


def _warm_index() -> None:
    try:
        count = get_rag().warm()
        print(f"Vector index warmed: {count:,} chunks ready.")
    except Exception as exc:  # noqa: BLE001 — never let warm-up crash the app
        print(f"Index warm-up skipped: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.openai_api_key:
        print("Warning: OPENAI_API_KEY not set — get one at platform.openai.com.")
    init_usage_db()
    start_weekly_usage_scheduler()
    # Warm the vector index in a background thread so the first user request
    # doesn't pay the multi-second cold-load cost. Running it off-thread (never
    # inline in lifespan) keeps startup instant so the deploy health check passes.
    if settings.warm_index_on_startup:
        threading.Thread(target=_warm_index, name="index-warmup", daemon=True).start()
    yield


app = FastAPI(
    title="Project SPK",
    description="Construction document RAG — upload, compare, and ask questions.",
    version="0.9.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ApiKeyMiddleware)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def chat_ui():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    return {"message": "UI not found. API is running — see /docs"}


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class LoginResponse(BaseModel):
    token: str
    email: str
    expires_at: int


@app.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    email = body.email.strip().lower()
    if not roster_enabled():
        raise HTTPException(status_code=404, detail="Roster sign-in is not enabled.")
    if not email_on_roster(email):
        record_error(
            email=email,
            session_id=None,
            source="login",
            message="Sign-in rejected: email not on the access roster.",
        )
        raise HTTPException(
            status_code=403,
            detail="This email is not on the access roster. Contact the site administrator.",
        )
    token, expires_at = issue_login_token(email)
    record_login(email, expires_at)
    return LoginResponse(token=token, email=email, expires_at=expires_at)


def _embeddings_configured() -> bool:
    if settings.embedding_provider.lower() == "voyage":
        return bool(settings.voyage_api_key)
    return bool(settings.openai_api_key)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Do not open Chroma here — large indexes can OOM or stall Railway deploy checks.
    return HealthResponse(
        status="ok",
        version=app.version,
        documents_indexed=0,
        data_dir=str(settings.data_path),
        llm=settings.openai_model,
        embeddings=f"{settings.embedding_provider}:{settings.openai_embedding_model if settings.embedding_provider == 'openai' else settings.voyage_embedding_model}",
        auth_required=auth_required(),
        llm_configured=bool(settings.openai_api_key),
        embeddings_configured=_embeddings_configured(),
        context_limits=ContextLimits(
            max_upload_mb=settings.max_upload_mb,
            max_extract_chars_per_file=settings.max_extract_chars_per_file,
            max_chunks_per_file=settings.max_chunks_per_file,
            max_pdf_pages=settings.max_pdf_pages,
            pdf_background_page_threshold=settings.pdf_background_page_threshold,
            max_retrieval_candidates=settings.max_retrieval_candidates,
            max_context_chars=settings.max_context_chars,
            max_chunks_per_source=settings.max_chunks_per_source,
            chunk_size=settings.chunk_size,
        ),
    )


@app.get("/files", response_model=FilesResponse)
def list_files(request: Request) -> FilesResponse:
    require_api_key(request)
    rag = get_rag()
    documents = rag.list_documents()
    for doc in documents:
        doc["url"] = document_link_url(
            doc.get("doc_number"),
            doc.get("source"),
            upload_origin=doc.get("upload_origin"),
        )
    return FilesResponse(
        files=[d["source"] for d in documents],
        documents=documents,
        chunks_indexed=rag.document_count,
    )


@app.get("/download/{filename}")
def download_file(request: Request, filename: str) -> FileResponse:
    require_api_key(request)

    safe = Path(filename).name
    if not safe or safe != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    path = resolve_data_file(safe)
    if not path:
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(
        path,
        media_type=guess_media_type(path),
        filename=safe,
        content_disposition_type="attachment",
    )


@app.delete("/files/{filename}")
def delete_file(request: Request, filename: str) -> dict[str, str | int]:
    require_api_key(request)

    safe = Path(filename).name
    if not safe or safe != filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    rag = get_rag()
    doc = next((d for d in rag.list_documents() if d.get("source") == safe), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    origin = (doc.get("upload_origin") or "").lower()
    if origin != "user":
        raise HTTPException(status_code=403, detail="Only user uploads can be deleted from the app.")

    chunks = rag.delete_source(safe)
    path = resolve_data_file(safe)
    if path and path.is_file():
        path.unlink()

    return {"message": f"Deleted {safe}.", "chunks_removed": chunks}


@app.post("/sync/publications", response_model=PublicationSyncResponse)
def sync_publications(request: Request, force: bool = False) -> PublicationSyncResponse:
    require_api_key(request)
    result = check_publication_sites(force=force)
    return PublicationSyncResponse(**result)


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
) -> UploadResponse:
    require_api_key(request)
    _require_keys()

    uploader = authenticated_email(request)

    def _upload_error(status_code: int, detail: str) -> HTTPException:
        record_error(
            email=uploader,
            session_id=session_id,
            source="upload",
            message=detail,
            detail=file.filename,
        )
        return HTTPException(status_code=status_code, detail=detail)

    if not file.filename:
        raise _upload_error(400, "Missing filename.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in INGESTABLE_EXTENSIONS:
        supported = ", ".join(sorted(INGESTABLE_EXTENSIONS))
        raise _upload_error(400, f"Unsupported type '{suffix}'. Supported: {supported}")

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise _upload_error(413, f"File exceeds {settings.max_upload_mb} MB limit.")

    # Offload all blocking disk/CPU/index work to a worker thread. The /upload
    # handler is async, so calling these inline would freeze the single-worker
    # event loop (and every other request) for the whole ingest — which, on a
    # cold index, is minutes. run_in_threadpool keeps the server responsive.
    path = await run_in_threadpool(save_upload, content, file.filename)

    extra_meta: dict[str, str] = {"upload_origin": "user"}
    if session_id:
        extra_meta["session_id"] = session_id[:64]
    if uploader:
        extra_meta["uploaded_by"] = uploader

    use_background, page_count = await run_in_threadpool(pdf_needs_background, path)
    if use_background:
        job = start_background_ingest(path, path.name, page_count, extra_meta=extra_meta or None)
        record_upload(
            email=uploader,
            session_id=session_id,
            filename=path.name,
            size_bytes=len(content),
            status="processing",
            job_id=job.id,
        )
        return UploadResponse(
            filename=path.name,
            message=(
                f"Indexing {page_count:,} pages in the background. "
                "You can chat once status shows complete (large PDFs may take several minutes)."
            ),
            status="processing",
            job_id=job.id,
            pages_total=page_count,
        )

    result = await run_in_threadpool(
        ingest_path, path, source_name=path.name, extra_meta=extra_meta or None
    )

    if not result.get("files_processed"):
        raise _upload_error(422, str(result.get("message")))

    record_upload(
        email=uploader,
        session_id=session_id,
        filename=path.name,
        size_bytes=len(content),
        status="complete",
        chunks_indexed=int(result["chunks_indexed"]),
    )

    return UploadResponse(
        filename=path.name,
        chunks_indexed=int(result["chunks_indexed"]),
        message=str(result.get("message", "Indexed.")),
        warnings=list(result.get("warnings", [])),
        status="complete",
        pages_total=int(result.get("pages_indexed") or 0) or None,
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(request: Request, job_id: str) -> JobStatusResponse:
    require_api_key(request)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    # An email sweep result contains message bodies. 404 rather than 403 for
    # someone else's job, so job ids are not confirmable by probing.
    if job.owner_email and job.owner_email != (authenticated_email(request) or "").lower():
        raise HTTPException(status_code=404, detail="Job not found.")
    result = None
    if job.kind == "query" and job.result:
        result = QueryResponse(**job.result)
    return JobStatusResponse(
        job_id=job.id,
        kind=job.kind,
        filename=job.filename,
        status=job.status,
        phase=job.phase,
        pages_total=job.pages_total,
        pages_done=job.pages_done,
        chunks_indexed=job.chunks_indexed,
        files_total=job.files_total,
        files_done=job.files_done,
        message=job.message,
        warnings=job.warnings,
        elapsed_ms=job.elapsed_ms,
        result=result,
        library_report=job.library_report,
        sweep_report=job.sweep_report,
    )


@app.post("/query", response_model=QueryJobResponse)
def query(request: Request, body: QueryRequest) -> QueryJobResponse:
    require_api_key(request)
    _require_keys()
    email = authenticated_email(request)
    job = start_background_query(
        question=body.question,
        email=email,
        session_id=body.session_id,
        query_kwargs={
            "question": body.question,
            "top_k": body.top_k,
            "focus_sources": body.focus_sources,
            "session_id": body.session_id,
            "include_library": body.include_library,
            "explicit_sections": body.section_numbers,
            "history": body.history,
        },
    )
    return QueryJobResponse(job_id=job.id, status=job.status)


class EmailAnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000, description="Email text pasted from Outlook")
    session_id: str | None = Field(default=None, max_length=64)


class EmailDraftRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000, description="Email text pasted from Outlook")
    instructions: str = Field(default="", max_length=4_000, description="What the reply should say or do")
    tone: str = Field(default=DEFAULT_TONE, max_length=32)
    use_library: bool = Field(default=False, description="Ground the reply in the Document Library")
    session_id: str | None = Field(default=None, max_length=64)


def _require_email_assistant() -> None:
    if not settings.email_assistant_enabled:
        raise HTTPException(
            status_code=503,
            detail="The email assistant is disabled on this deployment (set EMAIL_ASSISTANT_ENABLED=true).",
        )


def _parse_email_request(text: str) -> EmailThread:
    if len(text) > settings.email_max_chars:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That email is {len(text):,} characters, over the "
                f"{settings.email_max_chars:,} character limit. Paste a shorter portion of the thread."
            ),
        )
    thread = parse_pasted_email(text, scrub=settings.email_scrub_pii)
    if not any(turn.body.strip() for turn in thread.turns):
        raise HTTPException(status_code=400, detail="No email text found. Paste the email body.")
    return thread


@app.get("/email/status")
def email_status(request: Request) -> dict:
    """What the email assistant can do on this deployment, and what it cannot."""
    require_api_key(request)
    return {
        "enabled": settings.email_assistant_enabled,
        "scrub_pii": settings.email_scrub_pii,
        "max_chars": settings.email_max_chars,
        "msg_upload_supported": msg_support_available(),
        "upload_suffixes": list(SUPPORTED_MESSAGE_SUFFIXES),
        "tones": [{"key": key, "description": value} for key, value in REPLY_TONES.items()],
        "mailbox": connector_status(),
        "model": model_endpoint_info(),
        "sweep": {
            "enabled": settings.email_sweep_enabled,
            "window_hours": settings.email_sweep_hours,
            "max_window_hours": settings.email_sweep_max_hours,
            "max_messages": settings.email_sweep_max_messages,
            # The UI only auto-starts when the source needs no files from the user.
            "autostart": settings.email_sweep_autostart and can_read_mailbox(),
            "can_read_mailbox": can_read_mailbox(),
            "timezone": settings.email_sweep_timezone,
            "drafts": settings.email_sweep_drafts,
            "notes": settings.email_sweep_notes,
            "invites": settings.email_sweep_invites,
        },
    }


class EmailSweepRequest(BaseModel):
    hours: int | None = Field(
        default=None, ge=1, le=336, description="How far back to sweep. Defaults to EMAIL_SWEEP_HOURS (72)."
    )
    draft_replies: bool | None = Field(default=None, description="Draft replies for mail that needs one")
    write_notes: bool | None = Field(default=None, description="Write a note for the record per message")
    build_invites: bool | None = Field(default=None, description="Build .ics appointments and invites")
    tone: str = Field(default=DEFAULT_TONE, max_length=32)
    use_library: bool = Field(default=False, description="Ground reply drafts in the Document Library")
    session_id: str | None = Field(default=None, max_length=64)

    def sweep_kwargs(self) -> dict:
        return {
            "draft_replies": self.draft_replies,
            "write_notes": self.write_notes,
            "build_invites": self.build_invites,
            "tone": self.tone,
            "use_library": self.use_library,
        }


def _require_email_sweep() -> None:
    _require_email_assistant()
    if not settings.email_sweep_enabled:
        raise HTTPException(
            status_code=503,
            detail="The autonomous email sweep is disabled on this deployment (set EMAIL_SWEEP_ENABLED=true).",
        )


@app.post("/email/sweep", response_model=QueryJobResponse)
def email_sweep(request: Request, body: EmailSweepRequest) -> QueryJobResponse:
    """Sweep the configured mail source for the recent window and prepare the work.

    Reads every message received in the window, analyzes it, and drafts the
    replies, notes, and calendar invites it calls for. Returns a job id; poll
    ``GET /jobs/{job_id}`` for progress and the report.

    Requires a source that can enumerate mail on its own — the ``local_folder``
    connector, or Graph once provisioned. With the default ``manual`` connector
    there is no mailbox to read, so this returns 503 with the setup steps and the
    client should use ``POST /email/sweep/upload`` instead.
    """
    require_api_key(request)
    _require_email_sweep()
    _require_keys()
    email = authenticated_email(request)

    if not can_read_mailbox():
        status = connector_status()
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Project SPK has no mail source it can read on its own, so it cannot sweep "
                    "automatically. Drag the last few days of email out of Outlook instead, or "
                    "configure a local mail folder."
                ),
                "requirements": status.get("requirements") or [],
                "connector": status.get("connector"),
            },
        )

    job = start_background_email_sweep(
        user_email=email,
        hours=body.hours,
        session_id=body.session_id,
        sweep_kwargs=body.sweep_kwargs(),
    )
    return QueryJobResponse(
        job_id=job.id,
        status=job.status,
        message="Email sweep started. Poll GET /jobs/{job_id} for progress.",
    )


@app.post("/email/sweep/upload", response_model=QueryJobResponse)
async def email_sweep_upload(
    request: Request,
    files: list[UploadFile] = File(..., description=".msg or .eml files to sweep"),
    hours: int = Form(default=0, description="Window in hours; 0 uses the default"),
    draft_replies: bool = Form(default=True),
    write_notes: bool = Form(default=True),
    build_invites: bool = Form(default=True),
    tone: str = Form(default=DEFAULT_TONE),
    use_library: bool = Form(default=False),
    session_id: str | None = Form(default=None),
) -> QueryJobResponse:
    """Sweep a batch of messages the user dragged out of Outlook.

    The path that needs no IT approvals: multi-select the last few days in
    Outlook, drag them in, and the same analysis runs over the batch. Files are
    parsed in a temp directory that is deleted before the job starts, and email
    content is never written to the document index.
    """
    require_api_key(request)
    _require_email_sweep()
    _require_keys()
    email = authenticated_email(request)

    if not files:
        raise HTTPException(status_code=400, detail="Select at least one .msg or .eml file.")
    limit = settings.email_sweep_max_messages
    if len(files) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That is {len(files)} files, over the {limit}-message limit for one sweep. "
                "Select fewer messages or raise EMAIL_SWEEP_MAX_MESSAGES."
            ),
        )

    threads: list[EmailThread] = []
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for upload in files:
            name = Path(upload.filename or "").name
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_MESSAGE_SUFFIXES:
                warnings.append(f"{name or 'file'}: not a .msg or .eml file.")
                continue
            if suffix == ".msg" and not msg_support_available():
                warnings.append(f"{name}: reading .msg needs the 'extract-msg' package on the server.")
                continue
            data = await upload.read()
            if not data:
                warnings.append(f"{name}: empty file.")
                continue
            if len(data) > settings.max_upload_bytes:
                warnings.append(f"{name}: too large.")
                continue
            path = Path(tmp) / name
            path.write_bytes(data)
            try:
                thread = await run_in_threadpool(
                    parse_message_file, path, scrub=settings.email_scrub_pii
                )
            except Exception as exc:  # noqa: BLE001 — one bad file should not fail the batch
                warnings.append(f"{name}: could not be read ({exc}).")
                continue
            threads.append(thread)

    if not threads:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "None of those files could be read as email.",
                "warnings": warnings,
            },
        )

    job = start_background_email_sweep(
        user_email=email,
        # An uploaded batch is the user's explicit selection, so the window only
        # filters it — it does not go looking for anything else.
        hours=hours or settings.email_sweep_max_hours,
        threads=threads,
        source="upload",
        session_id=session_id,
        sweep_kwargs={
            "draft_replies": draft_replies,
            "write_notes": write_notes,
            "build_invites": build_invites,
            "tone": tone,
            "use_library": use_library,
        },
    )
    return QueryJobResponse(
        job_id=job.id,
        status=job.status,
        message=(
            f"Sweeping {len(threads)} message(s). Poll GET /jobs/{{job_id}} for progress."
            + (f" {len(warnings)} file(s) skipped." if warnings else "")
        ),
    )


@app.get("/email/mailbox/messages")
def email_mailbox_messages(request: Request, hours: int = 0, limit: int = 25) -> dict:
    """Preview what a sweep would read, without running any model calls."""
    require_api_key(request)
    _require_email_assistant()

    from app.email_sweep import window_bounds

    since, _until, window_hours = window_bounds(hours or None)
    try:
        refs = get_connector().list_messages(
            user_email=authenticated_email(request) or "",
            since=since,
            limit=max(1, min(limit, settings.email_sweep_max_messages)),
        )
    except MailboxUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": str(exc), "requirements": exc.requirements},
        ) from exc

    return {
        "window_hours": window_hours,
        "since": since.isoformat(),
        "count": len(refs),
        "messages": [vars(ref) for ref in refs],
    }


@app.post("/email/analyze")
def email_analyze(request: Request, body: EmailAnalyzeRequest) -> dict:
    """Summarize and triage a pasted Outlook email in one pass."""
    require_api_key(request)
    _require_email_assistant()
    _require_keys()
    email = authenticated_email(request)
    thread = _parse_email_request(body.text)

    start_tracking()
    try:
        analysis = analyze_thread(thread, user_email=email)
    except ValueError as exc:
        record_error(
            email=email, session_id=body.session_id, source="email", message=str(exc), detail="analyze"
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface a readable message, log the rest
        record_error(
            email=email, session_id=body.session_id, source="email", message=str(exc), detail="analyze"
        )
        raise HTTPException(status_code=500, detail=f"Could not analyze that email: {exc}") from exc
    finally:
        record_email_usage(
            email=email,
            session_id=body.session_id,
            action="analyze",
            tokens=get_tracking(),
        )

    return {"email": thread.as_dict(), "analysis": analysis}


@app.post("/email/draft-reply")
def email_draft_reply(request: Request, body: EmailDraftRequest) -> dict:
    """Draft a reply for the user to review and send from Outlook themselves."""
    require_api_key(request)
    _require_email_assistant()
    _require_keys()
    email = authenticated_email(request)
    thread = _parse_email_request(body.text)

    start_tracking()
    try:
        draft = draft_reply(
            thread,
            instructions=body.instructions,
            tone=body.tone,
            use_library=body.use_library,
            user_email=email,
        )
    except Exception as exc:  # noqa: BLE001 — surface a readable message, log the rest
        record_error(
            email=email, session_id=body.session_id, source="email", message=str(exc), detail="draft"
        )
        raise HTTPException(status_code=500, detail=f"Could not draft a reply: {exc}") from exc
    finally:
        record_email_usage(
            email=email,
            session_id=body.session_id,
            action="draft",
            tokens=get_tracking(),
        )

    return {"email": thread.as_dict(), "draft": draft}


@app.post("/email/parse-msg")
async def email_parse_msg(request: Request, file: UploadFile = File(...)) -> dict:
    """Parse an Outlook .msg file dragged out of Outlook into thread text.

    Returns the extracted text so the client can review it before running an
    analysis or draft. The file is parsed in a temp directory and never indexed —
    email content does not enter the document search index.
    """
    require_api_key(request)
    _require_email_assistant()

    if not msg_support_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Reading .msg files requires the 'extract-msg' package on the server. "
                "Paste the email text instead."
            ),
        )

    name = Path(file.filename or "").name
    if not name.lower().endswith(".msg"):
        raise HTTPException(status_code=400, detail="Upload an Outlook .msg file.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That .msg file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="That .msg file is too large.")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        try:
            thread = await run_in_threadpool(
                parse_msg_file, path, scrub=settings.email_scrub_pii
            )
        except Exception as exc:  # noqa: BLE001 — bad .msg should not 500 silently
            record_error(
                email=authenticated_email(request),
                session_id=None,
                source="email",
                message=str(exc),
                detail="parse-msg",
            )
            raise HTTPException(
                status_code=400, detail=f"Could not read that .msg file: {exc}"
            ) from exc

    return {"email": thread.as_dict(), "text": thread.to_prompt_text()}


class ClientErrorReport(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=64)


@app.post("/log/client-error")
def log_client_error(request: Request, body: ClientErrorReport) -> dict[str, str]:
    require_api_key(request)
    record_error(
        email=authenticated_email(request),
        session_id=body.session_id,
        source="client",
        message=body.message,
        detail=body.context,
    )
    return {"status": "logged"}


@app.get("/usage/summary")
def usage_report(request: Request) -> dict:
    require_api_key(request)
    _require_usage_admin(request)
    return usage_summary()


@app.get("/usage/weekly")
def usage_weekly(
    request: Request,
    week_ending: str | None = None,
    save: bool = False,
) -> dict:
    """Friday-to-Friday Pacific weekly usage report (admin only).

    Defaults to the most recently completed week ending Friday 5:00 PM PT.
    Pass ``week_ending=YYYY-MM-DD`` to load a saved snapshot for that Friday.
    Pass ``save=true`` to persist the current week snapshot to the data volume.
    """
    require_api_key(request)
    _require_usage_admin(request)

    if week_ending:
        saved = get_weekly_snapshot(week_ending)
        if saved:
            return saved
        raise HTTPException(status_code=404, detail=f"No weekly snapshot for {week_ending}.")

    report = weekly_usage_report()
    if save:
        report = save_weekly_snapshot(report)
    return report


@app.get("/usage/weekly/text")
def usage_weekly_text(
    request: Request,
    week_ending: str | None = None,
    save: bool = False,
) -> PlainTextResponse:
    """Human-readable Friday weekly report (admin only)."""
    require_api_key(request)
    _require_usage_admin(request)
    if week_ending:
        report = get_weekly_snapshot(week_ending)
        if not report:
            raise HTTPException(status_code=404, detail=f"No weekly snapshot for {week_ending}.")
    else:
        report = weekly_usage_report()
        if save:
            report = save_weekly_snapshot(report)
    return PlainTextResponse(format_weekly_report_text(report))


@app.get("/usage/weekly/snapshots")
def usage_weekly_snapshots(request: Request) -> dict:
    require_api_key(request)
    _require_usage_admin(request)
    return {"snapshots": list_weekly_snapshots()}


@app.post("/admin/library/upload", response_model=LibraryUploadResponse)
async def admin_library_upload(request: Request, file: UploadFile = File(...)) -> LibraryUploadResponse:
    """Upload one library document to the production incoming folder (admin only)."""
    require_api_key(request)
    _require_usage_admin(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit.")

    try:
        dest = await run_in_threadpool(save_incoming_upload, content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    incoming = library_incoming_path()
    count = len(list(incoming.glob("*")))
    return LibraryUploadResponse(
        filename=dest.name,
        message=f"Saved to library-incoming. Upload remaining files, then POST /admin/library/ingest.",
        incoming_count=count,
    )


@app.post("/admin/library/upload-zip", response_model=LibraryUploadResponse)
async def admin_library_upload_zip(request: Request, file: UploadFile = File(...)) -> LibraryUploadResponse:
    """Extract supported files from a zip into library-incoming (admin only)."""
    require_api_key(request)
    _require_usage_admin(request)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a .zip file.")

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit.")

    try:
        names = await run_in_threadpool(extract_incoming_zip, content)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not names:
        raise HTTPException(status_code=400, detail="No supported files found in zip.")

    incoming = library_incoming_path()
    count = len(list(incoming.glob("*")))
    return LibraryUploadResponse(
        filename=file.filename,
        message=f"Extracted {len(names)} file(s) to library-incoming.",
        incoming_count=count,
    )


@app.post("/admin/library/ingest", response_model=QueryJobResponse)
def admin_library_ingest(
    request: Request,
    body: LibraryIngestRequest | None = Body(default=None),
) -> QueryJobResponse:
    """Start background indexing of all files in library-incoming (admin only)."""
    require_api_key(request)
    _require_usage_admin(request)
    _require_keys()

    incoming = library_incoming_path()
    pending = [p for p in incoming.iterdir() if p.is_file() and not p.name.startswith(".")]
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="No files in library-incoming. Upload documents first via POST /admin/library/upload.",
        )

    patterns = body.purge_patterns if body else None
    job = start_background_library_ingest(purge_patterns=patterns)
    return QueryJobResponse(
        job_id=job.id,
        status=job.status,
        message=f"Library ingest started for {len(pending)} file(s). Poll GET /jobs/{{job_id}} for progress.",
    )


@app.get("/admin/library/incoming")
def admin_library_incoming(request: Request) -> dict:
    """List files waiting in library-incoming (admin only)."""
    require_api_key(request)
    _require_usage_admin(request)
    incoming = library_incoming_path()
    files = sorted(
        (
            {
                "filename": p.name,
                "size_bytes": p.stat().st_size,
            }
            for p in incoming.iterdir()
            if p.is_file() and not p.name.startswith(".")
        ),
        key=lambda item: item["filename"].lower(),
    )
    return {"incoming_count": len(files), "files": files}


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: Request) -> IngestResponse:
    require_api_key(request)
    _require_keys()
    result = ingest_directory()
    return IngestResponse(
        chunks_indexed=int(result["chunks_indexed"]),
        files_processed=list(result.get("files_processed", [])),
        message=str(result.get("message", "")),
        warnings=list(result.get("warnings", [])),
    )


@app.post("/reset")
def reset_index(request: Request) -> dict[str, str]:
    require_api_key(request)
    if not settings.allow_index_reset:
        raise HTTPException(
            status_code=403,
            detail="Index reset is disabled. Use server-side admin tools if needed.",
        )
    _require_keys()
    get_rag().reset_index()
    return {"message": "Vector index cleared."}
