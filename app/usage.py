from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

_lock = threading.Lock()
PACIFIC = ZoneInfo("America/Los_Angeles")
WEEKLY_REPORT_HOUR = 17  # 5:00 PM Pacific
WEEKLY_REPORT_MINUTE = 0


@dataclass
class TokenTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.embedding_tokens


def _default_usage_db_path() -> Path:
    """Always keep usage metrics on the data volume so deploys cannot wipe them."""
    configured = (settings.usage_db_path or "").strip()
    if configured and configured not in {"./data/usage.db", "data/usage.db"}:
        return Path(configured)
    return settings.data_path / "usage.db"


def _db_path() -> Path:
    path = _default_usage_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def usage_reports_dir() -> Path:
    path = settings.data_path / "usage-reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_legacy_db_if_needed() -> None:
    """Copy usage.db from common legacy locations onto the data volume once."""
    dest = _db_path()
    if dest.exists() and dest.stat().st_size > 0:
        return

    candidates = [
        Path("./data/usage.db"),
        Path("/app/data/usage.db"),
        Path(settings.usage_db_path) if settings.usage_db_path else None,
    ]
    for src in candidates:
        if not src or not src.exists() or src.resolve() == dest.resolve():
            continue
        if src.stat().st_size <= 0:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"Migrated usage database from {src} → {dest}")
        return


def init_usage_db() -> None:
    _migrate_legacy_db_if_needed()
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

                CREATE TABLE IF NOT EXISTS weekly_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_ending TEXT NOT NULL UNIQUE,
                    period_start REAL NOT NULL,
                    period_end REAL NOT NULL,
                    generated_at REAL NOT NULL,
                    report_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_logins_email ON logins(email);
                CREATE INDEX IF NOT EXISTS idx_logins_at ON logins(logged_in_at);
                CREATE INDEX IF NOT EXISTS idx_queries_email ON queries(email);
                CREATE INDEX IF NOT EXISTS idx_queries_started ON queries(started_at);
                CREATE INDEX IF NOT EXISTS idx_uploads_email ON uploads(email);
                CREATE INDEX IF NOT EXISTS idx_uploads_at ON uploads(uploaded_at);
                CREATE INDEX IF NOT EXISTS idx_errors_email ON errors(email);
                CREATE INDEX IF NOT EXISTS idx_errors_at ON errors(occurred_at);
                """
            )
            conn.commit()
        finally:
            conn.close()
    print(f"Usage metrics database: {_db_path()}")


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


def _fmt_iso_pacific(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, PACIFIC).isoformat()


def friday_week_bounds(as_of: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Return (period_start, period_end, week_ending_date) in Pacific time.

    Weekly report window: previous Friday 5:00 PM PT → this Friday 5:00 PM PT.
    If `as_of` is before Friday 5pm, the previous completed week is used.
    """
    now = as_of.astimezone(PACIFIC) if as_of else datetime.now(PACIFIC)
    # weekday: Mon=0 … Fri=4 … Sun=6
    days_since_friday = (now.weekday() - 4) % 7
    this_friday = (now - timedelta(days=days_since_friday)).replace(
        hour=WEEKLY_REPORT_HOUR,
        minute=WEEKLY_REPORT_MINUTE,
        second=0,
        microsecond=0,
    )
    if now < this_friday:
        this_friday -= timedelta(days=7)
    period_end = this_friday
    period_start = this_friday - timedelta(days=7)
    week_ending = period_end.date().isoformat()
    return period_start, period_end, week_ending


def weekly_usage_report(as_of: datetime | None = None) -> dict[str, Any]:
    """Aggregate retained metrics for one Friday-to-Friday Pacific week."""
    period_start, period_end, week_ending = friday_week_bounds(as_of)
    start_ts = period_start.timestamp()
    end_ts = period_end.timestamp()

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
                        COALESCE(SUM(CASE WHEN q.status = 'done' THEN 1 ELSE 0 END), 0) AS queries_done,
                        COALESCE(SUM(CASE WHEN q.status = 'error' THEN 1 ELSE 0 END), 0) AS queries_error,
                        COALESCE(SUM(q.duration_ms), 0) AS active_prompting_ms,
                        COALESCE(SUM(q.total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(q.prompt_tokens), 0) AS prompt_tokens,
                        COALESCE(SUM(q.completion_tokens), 0) AS completion_tokens,
                        COALESCE(SUM(q.embedding_tokens), 0) AS embedding_tokens,
                        COALESCE(AVG(CASE WHEN q.status = 'done' THEN q.duration_ms END), 0) AS avg_response_ms,
                        MAX(q.finished_at) AS last_query_at
                    FROM queries q
                    WHERE q.started_at >= ? AND q.started_at < ?
                    GROUP BY COALESCE(q.email, 'unknown')
                    ORDER BY query_count DESC
                    """,
                    (start_ts, end_ts),
                ).fetchall()
            ]

            login_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT email, COUNT(*) AS login_count, MAX(logged_in_at) AS last_login_at
                    FROM logins
                    WHERE logged_in_at >= ? AND logged_in_at < ?
                    GROUP BY email
                    ORDER BY login_count DESC
                    """,
                    (start_ts, end_ts),
                ).fetchall()
            ]

            upload_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        COALESCE(email, 'unknown') AS email,
                        COUNT(*) AS upload_count,
                        COALESCE(SUM(size_bytes), 0) AS total_bytes,
                        COALESCE(SUM(chunks_indexed), 0) AS chunks_indexed
                    FROM uploads
                    WHERE uploaded_at >= ? AND uploaded_at < ?
                    GROUP BY COALESCE(email, 'unknown')
                    ORDER BY upload_count DESC
                    """,
                    (start_ts, end_ts),
                ).fetchall()
            ]

            recent_queries = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT job_id, email, question, started_at, finished_at,
                           duration_ms, total_tokens, status
                    FROM queries
                    WHERE started_at >= ? AND started_at < ?
                    ORDER BY started_at DESC
                    LIMIT 500
                    """,
                    (start_ts, end_ts),
                ).fetchall()
            ]

            errors = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT email, session_id, source, message, detail, occurred_at
                    FROM errors
                    WHERE occurred_at >= ? AND occurred_at < ?
                    ORDER BY occurred_at DESC
                    LIMIT 200
                    """,
                    (start_ts, end_ts),
                ).fetchall()
            ]

            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS query_count,
                    COALESCE(SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END), 0) AS queries_done,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), 0) AS queries_error,
                    COALESCE(SUM(duration_ms), 0) AS active_prompting_ms,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM queries
                WHERE started_at >= ? AND started_at < ?
                """,
                (start_ts, end_ts),
            ).fetchone()

            login_total = conn.execute(
                "SELECT COUNT(*) AS c FROM logins WHERE logged_in_at >= ? AND logged_in_at < ?",
                (start_ts, end_ts),
            ).fetchone()["c"]
            upload_total = conn.execute(
                "SELECT COUNT(*) AS c FROM uploads WHERE uploaded_at >= ? AND uploaded_at < ?",
                (start_ts, end_ts),
            ).fetchone()["c"]
        finally:
            conn.close()

    # Merge per-user activity into one roster-friendly table
    by_email: dict[str, dict[str, Any]] = {}
    for row in users:
        email = row["email"]
        by_email[email] = {
            "email": email,
            "query_count": int(row["query_count"] or 0),
            "queries_done": int(row["queries_done"] or 0),
            "queries_error": int(row["queries_error"] or 0),
            "active_prompting_ms": int(row["active_prompting_ms"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "embedding_tokens": int(row["embedding_tokens"] or 0),
            "avg_response_ms": float(row["avg_response_ms"] or 0),
            "last_query_at": row["last_query_at"],
            "last_query_at_pacific": _fmt_iso_pacific(row["last_query_at"]),
            "login_count": 0,
            "last_login_at": None,
            "last_login_at_pacific": None,
            "upload_count": 0,
            "upload_bytes": 0,
        }
    for row in login_rows:
        email = row["email"]
        entry = by_email.setdefault(
            email,
            {
                "email": email,
                "query_count": 0,
                "queries_done": 0,
                "queries_error": 0,
                "active_prompting_ms": 0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "embedding_tokens": 0,
                "avg_response_ms": 0.0,
                "last_query_at": None,
                "last_query_at_pacific": None,
                "login_count": 0,
                "last_login_at": None,
                "last_login_at_pacific": None,
                "upload_count": 0,
                "upload_bytes": 0,
            },
        )
        entry["login_count"] = int(row["login_count"] or 0)
        entry["last_login_at"] = row["last_login_at"]
        entry["last_login_at_pacific"] = _fmt_iso_pacific(row["last_login_at"])
    for row in upload_rows:
        email = row["email"]
        entry = by_email.setdefault(
            email,
            {
                "email": email,
                "query_count": 0,
                "queries_done": 0,
                "queries_error": 0,
                "active_prompting_ms": 0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "embedding_tokens": 0,
                "avg_response_ms": 0.0,
                "last_query_at": None,
                "last_query_at_pacific": None,
                "login_count": 0,
                "last_login_at": None,
                "last_login_at_pacific": None,
                "upload_count": 0,
                "upload_bytes": 0,
            },
        )
        entry["upload_count"] = int(row["upload_count"] or 0)
        entry["upload_bytes"] = int(row["total_bytes"] or 0)

    users_merged = sorted(
        by_email.values(),
        key=lambda u: (-u["query_count"], -u["login_count"], u["email"]),
    )

    for q in recent_queries:
        q["started_at_pacific"] = _fmt_iso_pacific(q.get("started_at"))
        q["finished_at_pacific"] = _fmt_iso_pacific(q.get("finished_at"))
    for e in errors:
        e["occurred_at_pacific"] = _fmt_iso_pacific(e.get("occurred_at"))

    return {
        "timezone": "America/Los_Angeles",
        "week_ending": week_ending,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_start_ts": start_ts,
        "period_end_ts": end_ts,
        "generated_at": datetime.now(PACIFIC).isoformat(),
        "usage_db_path": str(_db_path()),
        "totals": {
            "unique_users": len(users_merged),
            "logins": int(login_total or 0),
            "queries": int(totals["query_count"] or 0),
            "queries_done": int(totals["queries_done"] or 0),
            "queries_error": int(totals["queries_error"] or 0),
            "uploads": int(upload_total or 0),
            "active_prompting_ms": int(totals["active_prompting_ms"] or 0),
            "total_tokens": int(totals["total_tokens"] or 0),
            "errors": len(errors),
        },
        "users": users_merged,
        "recent_queries": recent_queries,
        "errors": errors,
    }


def save_weekly_snapshot(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a weekly report to SQLite + JSON on the data volume (idempotent per week)."""
    report = report or weekly_usage_report()
    week_ending = report["week_ending"]
    generated_at = time.time()
    payload = json.dumps(report, indent=2)

    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO weekly_reports (week_ending, period_start, period_end, generated_at, report_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(week_ending) DO UPDATE SET
                    period_start = excluded.period_start,
                    period_end = excluded.period_end,
                    generated_at = excluded.generated_at,
                    report_json = excluded.report_json
                """,
                (
                    week_ending,
                    report["period_start_ts"],
                    report["period_end_ts"],
                    generated_at,
                    payload,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    out = usage_reports_dir() / f"weekly-{week_ending}.json"
    out.write_text(payload + "\n", encoding="utf-8")
    report = dict(report)
    report["snapshot_path"] = str(out)
    report["saved"] = True
    return report


def list_weekly_snapshots(limit: int = 52) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT week_ending, period_start, period_end, generated_at
                FROM weekly_reports
                ORDER BY week_ending DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "week_ending": row["week_ending"],
            "period_start": _fmt_iso_pacific(row["period_start"]),
            "period_end": _fmt_iso_pacific(row["period_end"]),
            "generated_at": _fmt_iso_pacific(row["generated_at"]),
        }
        for row in rows
    ]


def get_weekly_snapshot(week_ending: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT report_json FROM weekly_reports WHERE week_ending = ?",
                (week_ending,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        path = usage_reports_dir() / f"weekly-{week_ending}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None
    return json.loads(row["report_json"])


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
                    LIMIT 500
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
                    LIMIT 500
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
                    LIMIT 500
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
                    LIMIT 500
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

            retention = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM logins) AS login_rows,
                    (SELECT COUNT(*) FROM queries) AS query_rows,
                    (SELECT COUNT(*) FROM uploads) AS upload_rows,
                    (SELECT COUNT(*) FROM errors) AS error_rows,
                    (SELECT COUNT(*) FROM weekly_reports) AS weekly_report_rows,
                    (SELECT MIN(logged_in_at) FROM logins) AS earliest_login,
                    (SELECT MIN(started_at) FROM queries) AS earliest_query
                """
            ).fetchone()
        finally:
            conn.close()

    return {
        "usage_db_path": str(_db_path()),
        "retention": {
            "login_rows": int(retention["login_rows"] or 0),
            "query_rows": int(retention["query_rows"] or 0),
            "upload_rows": int(retention["upload_rows"] or 0),
            "error_rows": int(retention["error_rows"] or 0),
            "weekly_report_rows": int(retention["weekly_report_rows"] or 0),
            "earliest_login_pacific": _fmt_iso_pacific(retention["earliest_login"]),
            "earliest_query_pacific": _fmt_iso_pacific(retention["earliest_query"]),
            "policy": "indefinite — rows are never deleted; weekly snapshots retained on the data volume",
        },
        "users": users,
        "logins": logins,
        "uploads": uploads,
        "recent_queries": recent_queries,
        "errors": errors,
        "error_counts": error_counts,
        "weekly_snapshots": list_weekly_snapshots(),
    }


def is_usage_admin(email: str | None) -> bool:
    normalized = _email_filter(email)
    if not normalized:
        return False
    return normalized in settings.usage_admin_emails_set


def format_weekly_report_text(report: dict[str, Any]) -> str:
    """Human-readable Friday report for email/terminal."""
    totals = report.get("totals") or {}
    lines = [
        "Project SPK — Weekly Usage Report",
        f"Week ending: {report.get('week_ending')} (Friday 5:00 PM Pacific)",
        f"Period: {report.get('period_start')} → {report.get('period_end')}",
        f"Generated: {report.get('generated_at')}",
        "",
        "Totals",
        f"  Unique users: {totals.get('unique_users', 0)}",
        f"  Logins:       {totals.get('logins', 0)}",
        f"  Queries:      {totals.get('queries', 0)} "
        f"(done {totals.get('queries_done', 0)}, error {totals.get('queries_error', 0)})",
        f"  Uploads:      {totals.get('uploads', 0)}",
        f"  Tokens:       {int(totals.get('total_tokens', 0)):,}",
        f"  Active time:  {int(totals.get('active_prompting_ms', 0)) / 1000:.0f}s",
        f"  Errors:       {totals.get('errors', 0)}",
        "",
        "By user",
    ]
    users = report.get("users") or []
    if not users:
        lines.append("  (no user activity this week)")
    for u in users:
        lines.append(
            f"  {u['email']}: "
            f"{u.get('query_count', 0)} queries, "
            f"{u.get('login_count', 0)} logins, "
            f"{u.get('upload_count', 0)} uploads, "
            f"{int(u.get('total_tokens', 0)):,} tokens"
        )
    lines.append("")
    return "\n".join(lines)
