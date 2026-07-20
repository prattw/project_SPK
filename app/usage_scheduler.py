"""Background scheduler that snapshots weekly usage every Friday at 5pm Pacific."""

from __future__ import annotations

import threading
import time
from datetime import datetime

from app.usage import (
    PACIFIC,
    WEEKLY_REPORT_HOUR,
    WEEKLY_REPORT_MINUTE,
    friday_week_bounds,
    get_weekly_snapshot,
    save_weekly_snapshot,
)

_started = False
_lock = threading.Lock()


def _should_snapshot_now(now: datetime) -> bool:
    if now.weekday() != 4:  # Friday
        return False
    if now.hour != WEEKLY_REPORT_HOUR:
        return False
    # Run during the first 10 minutes of 5pm so a restart still catches it.
    return now.minute < 10


def _tick() -> None:
    now = datetime.now(PACIFIC)
    if not _should_snapshot_now(now):
        return
    _, _, week_ending = friday_week_bounds(now)
    if get_weekly_snapshot(week_ending):
        return
    report = save_weekly_snapshot()
    print(
        f"Weekly usage snapshot saved for week ending {report.get('week_ending')} "
        f"→ {report.get('snapshot_path')}"
    )


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001 — never crash the app from metrics
            print(f"Usage weekly scheduler error: {exc}")
        time.sleep(60)


def start_weekly_usage_scheduler() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_loop, name="usage-weekly-scheduler", daemon=True)
    thread.start()
    print("Weekly usage scheduler started (Friday 5:00 PM America/Los_Angeles).")
