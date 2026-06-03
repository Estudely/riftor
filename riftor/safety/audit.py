"""Append-only JSONL audit log: every tool invocation riftor attempts.

Lives under ``$XDG_STATE_HOME/riftor/audit.jsonl`` (falls back to
``~/.local/state/riftor/audit.jsonl``). This is the engagement record. When the
file grows past ``max_bytes`` it is rotated to ``audit.jsonl.1`` (gzip-compressed)
so the active log stays small and readable.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "riftor"


class AuditLog:
    def __init__(self, path: Path | None = None, max_bytes: int = 5_000_000) -> None:
        if path is None:
            d = _state_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / "audit.jsonl"
        self.path = path
        self.max_bytes = max_bytes

    def _maybe_rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
            archive = self.path.with_suffix(self.path.suffix + ".1.gz")
            with self.path.open("rb") as src, gzip.open(archive, "wb") as dst:
                dst.writelines(src)
            self.path.write_text("", encoding="utf-8")
        except Exception:  # noqa: BLE001 — rotation must never crash the agent
            pass

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
            self._maybe_rotate()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception:  # noqa: BLE001 — auditing must never crash the agent
            pass

    def tail(self, limit: int = 30) -> list[dict]:
        """Most recent audit entries (oldest-first), for the in-app /audit view."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
