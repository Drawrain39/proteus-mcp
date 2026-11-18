from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceStore:
    def __init__(self, root: Path):
        self.path = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:10])
        self.path.mkdir(parents=True, exist_ok=False)

    def write(self, name: str, value: dict) -> Path:
        path = self.path / name
        if path.parent != self.path or path.exists():
            raise ValueError("Evidence must be a new file directly in this run directory")
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return path

    def event(self, op: str, **details) -> None:
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": utc_now(), "op": op, **details}, ensure_ascii=False) + "\n")

