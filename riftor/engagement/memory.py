"""Per-engagement memory — free-form durable notes in .riftor/memory.json.

Mirrors LessonStore, but scoped to a single engagement (the workdir) and free-form
(no required trigger). The agent writes via the `remember` tool; entries auto-inject
into the system prompt (see engagement/injection.py) so recall is automatic.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class MemoryItem:
    id: str
    text: str
    tag: str = ""
    source: str = "agent"  # agent | operator
    ts: float = 0.0

    def __post_init__(self):
        if not self.ts:
            self.ts = time.time()
        if not self.id:
            self.id = uuid.uuid4().hex[:8]


class MemoryStore:
    """Read/write per-engagement memory. Same shape as LessonStore."""

    def __init__(self, workdir: Path | str) -> None:
        self.path = Path(workdir) / ".riftor" / "memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, rows: list[dict]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, text: str, tag: str = "", source: str = "agent") -> MemoryItem:
        """Add a memory item. Deduplicates by normalized text+tag."""
        text = (text or "").strip()
        tag = (tag or "").strip()
        if not text:
            raise ValueError("memory needs text")
        rows = self._read()
        for r in rows:
            if (r.get("text", "").strip().lower() == text.lower()
                    and r.get("tag", "").strip().lower() == tag.lower()):
                return MemoryItem(**{k: r[k] for k in r
                                     if k in MemoryItem.__dataclass_fields__})
        entry = MemoryItem(id=uuid.uuid4().hex[:8], text=text, tag=tag,
                           source=source, ts=time.time())
        rows.append(asdict(entry))
        self._write(rows)
        return entry

    def list(self) -> list[dict]:
        return self._read()

    def remove(self, item_id: str) -> bool:
        rows = self._read()
        before = len(rows)
        rows = [r for r in rows if r.get("id") != item_id]
        if len(rows) == before:
            return False
        self._write(rows)
        return True

    def clear(self) -> None:
        self._write([])

    def format_for_prompt(self, max_items: int = 50) -> str:
        """Format memory as a prompt section for injection into system context."""
        rows = self._read()[-max_items:]
        if not rows:
            return ""
        lines = ["## MEMORY (durable notes for this engagement)"]
        for r in rows:
            text = r.get("text", "")
            tag = r.get("tag", "")
            if not text:
                continue
            lines.append(f"- [{tag}] {text}" if tag else f"- {text}")
        return "\n".join(lines)
