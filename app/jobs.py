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
    kind: str  # ingest | query | library_ingest | email_sweep
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    # Set for jobs whose result contains the user's own content. /jobs/{id}
    # refuses to return those to anyone else — an email sweep result holds
    # message bodies, so it must not be readable by another signed-in user.
    owner_email: str | None = None
    # ingest
    filename: str = ""
    pages_total: int = 0
    pages_done: int = 0
    chunks_indexed: int = 0
    warnings: list[str] = field(default_factory=list)
    extra_meta: dict[str, str] | None = None
    # library batch ingest
    phase: str = ""
    files_total: int = 0
    files_done: int = 0
    library_report: dict | None = None
    # query
    result: dict[str, Any] | None = None
    query_email: str | None = None
    query_session_id: str | None = None
    query_question: str = ""
    # email sweep
    sweep_report: dict | None = None

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


def list_jobs(*, kind: str | None = None) -> list[Job]:
    with _lock:
        jobs = list(_jobs.values())
    if kind:
        jobs = [job for job in jobs if job.kind == kind]
    jobs.sort(key=lambda job: job.created_at, reverse=True)
    return jobs


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


def create_library_ingest_job() -> Job:
    job = Job(id=str(uuid.uuid4()), kind="library_ingest")
    with _lock:
        _jobs[job.id] = job
    return job


def run_library_ingest_job(
    job_id: str,
    *,
    purge_patterns: list[str] | None = None,
) -> None:
    from app.library_ingest import library_incoming_path, run_library_ingest

    def on_progress(phase: str, done: int, total: int, detail: str) -> None:
        if phase == "ingest":
            _update(
                job_id,
                status="running",
                phase="ingest",
                files_total=total,
                files_done=done,
                filename=detail,
                message=f"Indexing library files ({done}/{total})…",
            )
        elif phase == "split":
            _update(
                job_id,
                status="running",
                phase="split",
                files_total=total,
                files_done=done,
                filename=detail,
                message=f"Splitting large PDFs ({done}/{total})…",
            )
        else:
            _update(job_id, status="running", phase=phase, message=detail)

    try:
        _update(
            job_id,
            status="running",
            phase="prepare",
            started_at=time.time(),
            message="Preparing library ingest…",
        )
        report = run_library_ingest(
            library_incoming_path(),
            purge_patterns=purge_patterns,
            progress=on_progress,
        )
        data = report.to_dict()
        status = "error" if report.files_failed else "done"
        message = (
            f"Indexed {report.files_indexed} file(s) ({report.chunks_indexed:,} chunks). "
            f"Failed: {report.files_failed}. Split oversized PDFs: {report.split_pdfs}."
        )
        if report.purged_sources:
            message += f" Purged {report.purged_sources:,} old chunk(s)."
        _update(
            job_id,
            status=status,
            phase="done",
            finished_at=time.time(),
            files_total=report.files_found,
            files_done=report.files_indexed + report.files_failed + report.files_skipped,
            chunks_indexed=report.chunks_indexed,
            warnings=report.warnings,
            library_report=data,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", finished_at=time.time(), message=str(exc))


def start_background_library_ingest(*, purge_patterns: list[str] | None = None) -> Job:
    job = create_library_ingest_job()
    thread = threading.Thread(
        target=run_library_ingest_job,
        args=(job.id,),
        kwargs={"purge_patterns": purge_patterns},
        daemon=True,
    )
    thread.start()
    return job


def create_email_sweep_job(*, owner_email: str | None) -> Job:
    job = Job(id=str(uuid.uuid4()), kind="email_sweep", owner_email=owner_email)
    with _lock:
        _jobs[job.id] = job
    return job


def run_email_sweep_job(
    job_id: str,
    *,
    user_email: str | None,
    user_name: str = "",
    hours: int | None = None,
    threads: list[Any] | None = None,
    source: str = "manual",
    session_id: str | None = None,
    sweep_kwargs: dict[str, Any] | None = None,
) -> None:
    """Sweep a window of email: collect, analyze, and build artifacts.

    ``threads`` is supplied when the user uploaded the messages. When it is None
    the configured mailbox connector is asked for the window instead, which is the
    autonomous path.
    """
    from app.email_sweep import run_sweep, window_bounds
    from app.outlook_connector import MailboxUnavailable, get_connector
    from app.usage import record_email_usage

    start_tracking()
    try:
        _update(
            job_id,
            status="running",
            phase="collect",
            started_at=time.time(),
            message="Collecting recent email…",
        )
        since, _until, window_hours = window_bounds(hours)

        if threads is None:
            connector = get_connector()
            threads = connector.recent_threads(
                user_email=user_email or "",
                since=since,
                limit=_sweep_limit(),
            )
            source = connector.name

        if not threads:
            _update(
                job_id,
                status="done",
                phase="done",
                finished_at=time.time(),
                files_total=0,
                files_done=0,
                message=f"No email found in the last {window_hours} hours.",
                sweep_report={"empty": True, "window_hours": window_hours, "source": source},
            )
            return

        _update(job_id, files_total=len(threads), message=f"Analyzing {len(threads)} message(s)…")

        def on_progress(phase: str, done: int, total: int, detail: str) -> None:
            label = "Drafting reply for" if phase == "draft" else "Analyzing"
            _update(
                job_id,
                status="running",
                phase=phase,
                files_total=total,
                files_done=done,
                filename=detail,
                message=(
                    f"{label} message {min(done + 1, total)} of {total}…"
                    if phase != "done"
                    else "Finishing up…"
                ),
            )

        report = run_sweep(
            threads,
            user_email=user_email,
            user_name=user_name,
            source=source,
            hours=hours,
            progress=on_progress,
            **(sweep_kwargs or {}),
        )
        data = report.to_dict()
        digest = data["digest"]
        message = (
            f"Swept {report.messages_analyzed} message(s) from the last {report.window_hours} hours: "
            f"{digest['priority_counts'].get('high', 0)} high priority, "
            f"{digest['replies_drafted']} reply draft(s), "
            f"{digest['invites_built']} calendar invite(s)."
        )
        if report.messages_failed:
            message += f" {report.messages_failed} message(s) could not be analyzed."
        _update(
            job_id,
            status="done",
            phase="done",
            finished_at=time.time(),
            files_total=report.messages_analyzed + report.messages_failed,
            files_done=report.messages_analyzed + report.messages_failed,
            warnings=report.warnings,
            sweep_report=data,
            message=message,
        )
    except MailboxUnavailable as exc:
        _update(
            job_id,
            status="error",
            phase="done",
            finished_at=time.time(),
            message=str(exc),
            sweep_report={"requirements": exc.requirements},
        )
    except Exception as exc:  # noqa: BLE001 — surface to the client
        _update(job_id, status="error", phase="done", finished_at=time.time(), message=str(exc))
        record_error(
            email=user_email,
            session_id=session_id,
            source="email",
            message=str(exc),
            detail="sweep",
        )
    finally:
        record_email_usage(
            email=user_email,
            session_id=session_id,
            action="sweep",
            tokens=get_tracking(),
        )


def _sweep_limit() -> int:
    from app.config import settings

    return settings.email_sweep_max_messages


def start_background_email_sweep(
    *,
    user_email: str | None,
    user_name: str = "",
    hours: int | None = None,
    threads: list[Any] | None = None,
    source: str = "manual",
    session_id: str | None = None,
    sweep_kwargs: dict[str, Any] | None = None,
) -> Job:
    job = create_email_sweep_job(owner_email=(user_email or "").lower() or None)
    thread = threading.Thread(
        target=run_email_sweep_job,
        args=(job.id,),
        kwargs={
            "user_email": user_email,
            "user_name": user_name,
            "hours": hours,
            "threads": threads,
            "source": source,
            "session_id": session_id,
            "sweep_kwargs": sweep_kwargs,
        },
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
