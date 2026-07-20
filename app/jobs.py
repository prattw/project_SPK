from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingest import ingest_path
from app.token_usage import get_tracking, start_tracking
from app.usage import record_error, record_query_finish, record_query_start

_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}


@dataclass
class Job:
    id: str
    kind: str  # ingest | query
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    # ingest
    filename: str = ""
    pages_total: int = 0
    pages_done: int = 0
    chunks_indexed: int = 0
    warnings: list[str] = field(default_factory=list)
    extra_meta: dict[str, str] | None = None
    # query
    result: dict[str, Any] | None = None
    query_email: str | None = None
    query_session_id: str | None = None
    query_question: str = ""

    @property
    def elapsed_ms(self) -> int | None:
        if not self.started_at:
            return None
        end = self.finished_at or time.time()
        return max(0, int((end - self.started_at) * 1000))


def create_ingest_job(
    filename: str,
    pages_total: int = 0,
    extra_meta: dict[str, str] | None = None,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        kind="ingest",
        filename=filename,
        pages_total=pages_total,
        extra_meta=extra_meta,
    )
    with _lock:
        _jobs[job.id] = job
    return job


def create_query_job(
    *,
    question: str,
    email: str | None,
    session_id: str | None,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        kind="query",
        query_question=question,
        query_email=email,
        query_session_id=session_id,
    )
    with _lock:
        _jobs[job.id] = job
    record_query_start(
        job_id=job.id,
        email=email,
        session_id=session_id,
        question=question,
    )
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def _update(job_id: str, **kwargs: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)


def run_ingest_job(
    job_id: str,
    path: Path,
    source_name: str,
    extra_meta: dict[str, str] | None = None,
) -> None:
    def on_progress(done: int, total: int) -> None:
        _update(job_id, pages_done=done, pages_total=total, status="running")

    try:
        _update(job_id, status="running", started_at=time.time(), message="Indexing…")
        result = ingest_path(
            path,
            source_name,
            progress_callback=on_progress,
            extra_meta=extra_meta,
        )
        if result.get("files_processed"):
            pages = int(result.get("pages_indexed") or 0)
            existing = get_job(job_id)
            _update(
                job_id,
                status="done",
                finished_at=time.time(),
                chunks_indexed=int(result.get("chunks_indexed", 0)),
                message=str(result.get("message", "Done.")),
                warnings=list(result.get("warnings", [])),
                pages_done=pages,
                pages_total=pages or (existing.pages_total if existing else 0),
            )
        else:
            _update(
                job_id,
                status="error",
                finished_at=time.time(),
                message=str(result.get("message", "Indexing failed.")),
            )
            _record_ingest_error(job_id, str(result.get("message", "Indexing failed.")))
    except Exception as exc:  # noqa: BLE001 — surface to client
        _update(job_id, status="error", finished_at=time.time(), message=str(exc))
        _record_ingest_error(job_id, str(exc))


def _record_ingest_error(job_id: str, message: str) -> None:
    job = get_job(job_id)
    meta = (job.extra_meta or {}) if job else {}
    record_error(
        email=meta.get("uploaded_by"),
        session_id=meta.get("session_id"),
        source="upload",
        message=message,
        detail=job.filename if job else None,
    )


def run_query_job(job_id: str, query_kwargs: dict[str, Any]) -> None:
    from app.rag import get_rag

    try:
        _update(job_id, status="running", started_at=time.time(), message="Working on your answer…")
        start_tracking()
        result = get_rag().query(**query_kwargs)
        tokens = get_tracking()
        _update(
            job_id,
            status="done",
            finished_at=time.time(),
            result=result,
            message="Complete.",
        )
        record_query_finish(job_id=job_id, status="done", tokens=tokens)
    except Exception as exc:  # noqa: BLE001 — surface to client
        tokens = get_tracking()
        _update(
            job_id,
            status="error",
            finished_at=time.time(),
            message=str(exc),
        )
        record_query_finish(job_id=job_id, status="error", tokens=tokens, error=str(exc))
        job = get_job(job_id)
        record_error(
            email=job.query_email if job else None,
            session_id=job.query_session_id if job else None,
            source="query",
            message=str(exc),
            detail=job.query_question if job else None,
        )


def start_background_ingest(
    path: Path,
    source_name: str,
    pages_total: int,
    extra_meta: dict[str, str] | None = None,
) -> Job:
    job = create_ingest_job(source_name, pages_total=pages_total, extra_meta=extra_meta)
    thread = threading.Thread(
        target=run_ingest_job,
        args=(job.id, path, source_name, extra_meta),
        daemon=True,
    )
    thread.start()
    return job


def start_background_query(
    *,
    question: str,
    email: str | None,
    session_id: str | None,
    query_kwargs: dict[str, Any],
) -> Job:
    job = create_query_job(question=question, email=email, session_id=session_id)
    thread = threading.Thread(
        target=run_query_job,
        args=(job.id, query_kwargs),
        daemon=True,
    )
    thread.start()
    return job
