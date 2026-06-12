"""Per-engagement context injection: memory + active-template methodology.

Given a workdir, returns prompt text appended to the system prompt by Context.
Opens the engagement DB directly (lightweight, no full Engagement) and reads memory
from JSON. Degrades to "" on anything missing or malformed — never raises, since it
runs on every prompt assembly.
"""

from __future__ import annotations

from pathlib import Path


def engagement_injection(workdir: Path | None) -> str:
    if workdir is None:
        return ""
    parts: list[str] = []

    # --- memory (.riftor/memory.json) ---
    try:
        from riftor.engagement.memory import MemoryStore
        mem = MemoryStore(workdir).format_for_prompt()
        if mem:
            parts.append(mem)
    except Exception:  # noqa: BLE001
        pass

    # --- active template methodology (engagement.db meta + static dict) ---
    try:
        db_path = Path(workdir) / ".riftor" / "engagement.db"
        if db_path.exists():
            from riftor.engagement.state import Store
            from riftor.engagement.templates import (
                ACTIVE_TEMPLATE_META_KEY,
                TEMPLATES,
            )
            key = Store(db_path).get_meta(ACTIVE_TEMPLATE_META_KEY, "") or ""
            tmpl = TEMPLATES.get(key)
            if tmpl:
                parts.append(
                    "## ENGAGEMENT TEMPLATE\n" + tmpl.methodology
                )
    except Exception:  # noqa: BLE001
        pass

    return "\n\n".join(parts)
