"""Append-only JSONL audit log: every tool invocation riftor attempts.

Lives under ``$XDG_STATE_HOME/riftor/audit.jsonl`` (falls back to
``~/.local/state/riftor/audit.jsonl``). This is the engagement record.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "riftor"


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            d = _state_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / "audit.jsonl"
        self.path = path

    def record(
        self,
        tool: str,
        preview: str,
        *,
        allowed: bool,
        is_error: bool = False,
        duration: float = 0.0,
        result_len: int = 0,
    ) -> None:
        record = {
            "ts": round(time.time(), 3),
            "tool": tool,
            "preview": preview[:500],
            "allowed": allowed,
            "is_error": is_error,
            "duration_s": round(duration, 3),
            "result_len": result_len,
        }
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception:  # noqa: BLE001 — auditing must never crash the agent
            pass
