"""Comment records for one review, stored on this Mac only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent / "data" / "reviews.json"


class ReviewStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_PATH
        self.records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for record in data.get("comments") or []:
            if record.get("id"):
                self.records[str(record["id"])] = record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"comments": list(self.records.values())}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def replace_file(self, source_file: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for key, record in list(self.records.items()):
            if record.get("source_file") == source_file:
                del self.records[key]
        for record in records:
            self.records[str(record["id"])] = record
        self.save()
        return records

    def list_comments(self) -> list[dict[str, Any]]:
        return sorted(self.records.values(), key=lambda record: str(record.get("id")))

    def get(self, comment_id: str) -> dict[str, Any] | None:
        return self.records.get(comment_id)
