"""Per-engagement context injection: memory + active-template methodology.

Given a workdir, returns prompt text appended to the system prompt by Context.
Reads memory from JSON and the active template name via a direct read-only SQLite
SELECT (no schema/migrate churn — this runs on every prompt assembly). Creates
nothing and never raises — degrades to "" on anything missing or malformed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def engagement_injection(workdir: Path | None) -> str:
    if workdir is None:
        return ""
    parts: list[str] = []

    # --- memory (.riftor/memory.json) ---
    # Guard on the file existing first so we never create .riftor/ as a side
    # effect (MemoryStore.__init__ would mkdir it otherwise).
    try:
        if (workdir / ".riftor" / "memory.json").exists():
            from riftor.engagement.memory import MemoryStore
            mem = MemoryStore(workdir).format_for_prompt()
            if mem:
                parts.append(mem)
    except Exception:  # noqa: BLE001
        pass

    # --- active template methodology (engagement.db meta + static dict) ---
    # Direct read-only SELECT of the one meta value, avoiding Store.__init__'s
    # schema/migrate/commit on this hot path.
    try:
        db_path = workdir / ".riftor" / "engagement.db"
        if db_path.exists():
            from riftor.engagement.templates import (
                ACTIVE_TEMPLATE_META_KEY,
                TEMPLATES,
            )
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA busy_timeout=5000")  # wait instead of SQLITE_BUSY
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key=?",
                    (ACTIVE_TEMPLATE_META_KEY,),
                ).fetchone()
            finally:
                conn.close()
            key = (row[0] if row else "") or ""
            tmpl = TEMPLATES.get(key)
            if tmpl:
                parts.append("## ENGAGEMENT TEMPLATE\n" + tmpl.methodology)
    except Exception:  # noqa: BLE001
        pass

    return "\n\n".join(parts)
