from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

_lock = threading.Lock()


@dataclass
class TokenTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.embedding_tokens


def _db_path() -> Path:
    path = Path(settings.usage_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_usage_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS logins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    logged_in_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    email TEXT,
                    session_id TEXT,
                    question TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    duration_ms INTEGER,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    embedding_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    session_id TEXT,
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    uploaded_at REAL NOT NULL,
                    chunks_indexed INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    job_id TEXT
                );

                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    session_id TEXT,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail TEXT,
                    occurred_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_logins_email ON logins(email);
                CREATE INDEX IF NOT EXISTS idx_queries_email ON queries(email);
                CREATE INDEX IF NOT EXISTS idx_uploads_email ON uploads(email);
                CREATE INDEX IF NOT EXISTS idx_errors_email ON errors(email);
                """
            )
            conn.commit()
        finally:
            conn.close()


def record_login(email: str, expires_at: int) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO logins (email, logged_in_at, expires_at) VALUES (?, ?, ?)",
                (email, time.time(), float(expires_at)),
            )
            conn.commit()
        finally:
            conn.close()


def record_query_start(
    *,
    job_id: str,
    email: str | None,
    session_id: str | None,
    question: str,
) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO queries (
                    job_id, email, session_id, question, started_at, status
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (job_id, email, session_id, question[:4000], time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def record_query_finish(
    *,
    job_id: str,
    status: str,
    tokens: TokenTotals,
    error: str | None = None,
) -> None:
    finished = time.time()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT started_at FROM queries WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            started_at = float(row["started_at"]) if row else finished
            duration_ms = max(0, int((finished - started_at) * 1000))
            conn.execute(
                """
                UPDATE queries
                SET finished_at = ?, duration_ms = ?, prompt_tokens = ?,
                    completion_tokens = ?, embedding_tokens = ?, total_tokens = ?,
                    status = ?, error = ?
                WHERE job_id = ?
                """,
                (
                    finished,
                    duration_ms,
                    tokens.prompt_tokens,
                    tokens.completion_tokens,
                    tokens.embedding_tokens,
                    tokens.total_tokens,
                    status,
                    error,
                    job_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_upload(
    *,
    email: str | None,
    session_id: str | None,
    filename: str,
    size_bytes: int,
    status: str,
    chunks_indexed: int = 0,
    job_id: str | None = None,
) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO uploads (
                    email, session_id, filename, size_bytes, uploaded_at,
                    chunks_indexed, status, job_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    email,
                    session_id,
                    filename,
                    size_bytes,
                    time.time(),
                    chunks_indexed,
                    status,
                    job_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_error(
    *,
    email: str | None,
    session_id: str | None,
    source: str,
    message: str,
    detail: str | None = None,
) -> None:
    """Log an error a user experienced (login, upload, query, or client-side)."""
    try:
        with _lock:
            conn = _connect()
            try:
                conn.execute(
                    """
                    INSERT INTO errors (email, session_id, source, message, detail, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (email, session_id, source[:50], message[:1000], (detail or "")[:4000], time.time()),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        # Error logging must never break the request that triggered it.
        pass


def _email_filter(email: str | None) -> str:
    return (email or "").strip().lower()


def usage_summary() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            users = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        COALESCE(q.email, 'unknown') AS email,
                        COUNT(q.id) AS query_count,
                        COALESCE(SUM(q.duration_ms), 0) AS active_prompting_ms,
                        COALESCE(SUM(q.total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(q.prompt_tokens), 0) AS prompt_tokens,
                        COALESCE(SUM(q.completion_tokens), 0) AS completion_tokens,
                        COALESCE(SUM(q.embedding_tokens), 0) AS embedding_tokens,
                        COALESCE(AVG(q.duration_ms), 0) AS avg_response_ms,
                        MAX(q.finished_at) AS last_query_at
                    FROM queries q
                    WHERE q.status = 'done'
                    GROUP BY COALESCE(q.email, 'unknown')
                    ORDER BY query_count DESC
                    """
                ).fetchall()
            ]

            logins = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT email, logged_in_at, expires_at
                    FROM logins
                    ORDER BY logged_in_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]

            uploads = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT email, session_id, filename, size_bytes, uploaded_at,
                           chunks_indexed, status, job_id
                    FROM uploads
                    ORDER BY uploaded_at DESC
                    LIMIT 200
                    """
                ).fetchall()
            ]

            recent_queries = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT job_id, email, question, started_at, finished_at,
                           duration_ms, total_tokens, status
                    FROM queries
                    ORDER BY started_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]

            errors = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT email, session_id, source, message, detail, occurred_at
                    FROM errors
                    ORDER BY occurred_at DESC
                    LIMIT 200
                    """
                ).fetchall()
            ]

            error_counts = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT COALESCE(email, 'unknown') AS email,
                           source,
                           COUNT(*) AS error_count
                    FROM errors
                    GROUP BY COALESCE(email, 'unknown'), source
                    ORDER BY error_count DESC
                    """
                ).fetchall()
            ]
        finally:
            conn.close()

    return {
        "users": users,
        "logins": logins,
        "uploads": uploads,
        "recent_queries": recent_queries,
        "errors": errors,
        "error_counts": error_counts,
    }


def is_usage_admin(email: str | None) -> bool:
    normalized = _email_filter(email)
    if not normalized:
        return False
    return normalized in settings.usage_admin_emails_set
