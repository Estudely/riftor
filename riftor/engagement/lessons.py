"""Persistent lesson store — cross-session memory that survives forever.

The agent and operator teach lessons that persist across sessions. On every
session start, lessons are injected into the system prompt so the agent never
repeats the same mistake.

Storage: ~/.config/riftor/lessons.json (or XDG_CONFIG_HOME/riftor/lessons.json).
Format: JSON array of lesson objects.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Lesson:
    id: str
    trigger: str
    lesson: str
    source: str = "operator"  # operator | agent
    ts: float = 0.0

    def __post_init__(self):
        if not self.ts:
            self.ts = time.time()
        if not self.id:
            self.id = uuid.uuid4().hex[:8]


def _default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "riftor" / "lessons.json"


class LessonStore:
    """Read/write/search lessons. Thread-safe enough for a single-agent loop."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else _default_path()
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

    def add(self, trigger: str, lesson: str, source: str = "operator") -> Lesson:
        """Add a lesson. Deduplicates by trigger+lesson content."""
        trigger = (trigger or "").strip()
        lesson_text = (lesson or "").strip()
        if not trigger and not lesson_text:
            raise ValueError("lesson needs a trigger or lesson text")
        # dedup: skip if same trigger+lesson already exists
        rows = self._read()
        for r in rows:
            if (r.get("trigger", "").strip().lower() == trigger.lower()
                    and r.get("lesson", "").strip().lower() == lesson_text.lower()):
                return Lesson(**{k: r[k] for k in r if k in Lesson.__dataclass_fields__})

        entry = Lesson(id=uuid.uuid4().hex[:8], trigger=trigger,
                       lesson=lesson_text, source=source, ts=time.time())
        rows.append(asdict(entry))
        self._write(rows)
        return entry

    def list(self) -> list[dict]:
        return self._read()

    def remove(self, lesson_id: str) -> bool:
        rows = self._read()
        before = len(rows)
        rows = [r for r in rows if r.get("id") != lesson_id]
        if len(rows) == before:
            return False
        self._write(rows)
        return True

    def format_for_prompt(self, max_lessons: int = 50) -> str:
        """Format lessons as a prompt section for injection into system context."""
        rows = self._read()[-max_lessons:]
        if not rows:
            return ""
        lines = ["## LESSONS (follow these — they override your default behavior)"]
        for r in rows:
            trigger = r.get("trigger", "")
            lesson = r.get("lesson", "")
            source = r.get("source", "operator")
            if trigger and lesson:
                lines.append(f"- WHEN {trigger} → {lesson} ({source}-taught)")
            elif lesson:
                lines.append(f"- {lesson} ({source}-taught)")
            elif trigger:
                lines.append(f"- WHEN {trigger} — remember this ({source}-taught)")
        return "\n".join(lines)
