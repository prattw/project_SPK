from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import ApiKeyMiddleware, require_api_key
from app.config import settings
from app.ingest import INGESTABLE_EXTENSIONS, ingest_directory, ingest_path, pdf_needs_background, save_upload
from app.jobs import get_job, start_background_ingest
from app.rag import get_rag

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
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


class JobStatusResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    pages_total: int
    pages_done: int
    chunks_indexed: int
    message: str
    warnings: list[str] = []


class FilesResponse(BaseModel):
    files: list[str]
    chunks_indexed: int


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
    documents_indexed: int
    data_dir: str
    llm: str
    embeddings: str
    context_limits: ContextLimits
    auth_required: bool
    llm_configured: bool
    embeddings_configured: bool


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.openai_api_key:
        print("Warning: OPENAI_API_KEY not set — get one at platform.openai.com.")
    yield


app = FastAPI(
    title="Project SPK",
    description="Construction document RAG — upload, compare, and ask questions.",
    version="0.3.0",
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
        return FileResponse(index)
    return {"message": "UI not found. API is running — see /docs"}


def _embeddings_configured() -> bool:
    if settings.embedding_provider.lower() == "voyage":
        return bool(settings.voyage_api_key)
    return bool(settings.openai_api_key)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    rag = get_rag()
    return HealthResponse(
        status="ok",
        documents_indexed=rag.document_count,
        data_dir=str(settings.data_path),
        llm=settings.openai_model,
        embeddings=f"{settings.embedding_provider}:{settings.openai_embedding_model if settings.embedding_provider == 'openai' else settings.voyage_embedding_model}",
        auth_required=bool(settings.app_api_key),
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
    return FilesResponse(files=rag.list_sources(), chunks_indexed=rag.document_count)


@app.post("/upload", response_model=UploadResponse)
async def upload_file(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    require_api_key(request)
    _require_keys()

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in INGESTABLE_EXTENSIONS:
        supported = ", ".join(sorted(INGESTABLE_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type '{suffix}'. Supported: {supported}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit.",
        )

    path = save_upload(content, file.filename)

    use_background, page_count = pdf_needs_background(path)
    if use_background:
        job = start_background_ingest(path, path.name, page_count)
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

    result = ingest_path(path, original_name=path.name)

    if not result.get("files_processed"):
        raise HTTPException(status_code=422, detail=str(result.get("message")))

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
    return JobStatusResponse(
        job_id=job.id,
        filename=job.filename,
        status=job.status,
        pages_total=job.pages_total,
        pages_done=job.pages_done,
        chunks_indexed=job.chunks_indexed,
        message=job.message,
        warnings=job.warnings,
    )


@app.post("/query", response_model=QueryResponse)
def query(request: Request, body: QueryRequest) -> QueryResponse:
    require_api_key(request)
    _require_keys()
    result = get_rag().query(body.question, top_k=body.top_k)
    return QueryResponse(**result)


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
    _require_keys()
    get_rag().reset_index()
    return {"message": "Vector index cleared."}
