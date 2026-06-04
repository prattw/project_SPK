from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingest import ingest_path

_lock = threading.Lock()
_jobs: dict[str, "IngestJob"] = {}


@dataclass
class IngestJob:
    id: str
    filename: str
    status: str = "queued"  # queued | running | done | error
    pages_total: int = 0
    pages_done: int = 0
    chunks_indexed: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def create_job(filename: str, pages_total: int = 0) -> IngestJob:
    job = IngestJob(id=str(uuid.uuid4()), filename=filename, pages_total=pages_total)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> IngestJob | None:
    with _lock:
        return _jobs.get(job_id)


def _update(job_id: str, **kwargs: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)


def run_ingest_job(job_id: str, path: Path, source_name: str) -> None:
    def on_progress(done: int, total: int) -> None:
        _update(job_id, pages_done=done, pages_total=total, status="running")

    try:
        _update(job_id, status="running", message="Indexing…")
        result = ingest_path(path, source_name, progress_callback=on_progress)
        if result.get("files_processed"):
            pages = int(result.get("pages_indexed") or 0)
            existing = get_job(job_id)
            _update(
                job_id,
                status="done",
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
                message=str(result.get("message", "Indexing failed.")),
            )
    except Exception as exc:  # noqa: BLE001 — surface to client
        _update(job_id, status="error", message=str(exc))


def start_background_ingest(path: Path, source_name: str, pages_total: int) -> IngestJob:
    job = create_job(source_name, pages_total=pages_total)
    thread = threading.Thread(
        target=run_ingest_job,
        args=(job.id, path, source_name),
        daemon=True,
    )
    thread.start()
    return job
