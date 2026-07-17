import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    ApiKeyMiddleware,
    auth_required,
    authenticated_email,
    email_on_roster,
    issue_login_token,
    require_api_key,
    roster_enabled,
)
from app.config import settings
from app.downloads import document_link_url, guess_media_type, resolve_data_file
from app.ingest import INGESTABLE_EXTENSIONS, ingest_directory, ingest_path, pdf_needs_background, save_upload
from app.jobs import get_job, start_background_ingest, start_background_library_ingest, start_background_query
from app.library_ingest import (
    extract_incoming_zip,
    library_incoming_path,
    save_incoming_upload,
)
from app.publication_sync import check_publication_sites
from app.rag import get_rag
from app.usage import (
    init_usage_db,
    is_usage_admin,
    record_error,
    record_login,
    record_upload,
    usage_summary,
)

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
    # Warm the vector index in a background thread so the first user request
    # doesn't pay the multi-second cold-load cost. Running it off-thread (never
    # inline in lifespan) keeps startup instant so the deploy health check passes.
    if settings.warm_index_on_startup:
        threading.Thread(target=_warm_index, name="index-warmup", daemon=True).start()
    yield


app = FastAPI(
    title="Project SPK",
    description="Construction document RAG — upload, compare, and ask questions.",
    version="0.8.0",
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
