"""Session persistence: save/resume conversations per engagement (workdir).

Sessions are JSON files under ``<workdir>/.riftor/sessions/<id>.json`` holding
the message history plus light metadata. Resuming restores the conversation so
the agent keeps its memory across runs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def sessions_dir(workdir: Path) -> Path:
    path = Path(workdir) / ".riftor" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _title(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            text = msg["content"].strip().replace("\n", " ")
            if text:
                return text[:60]
    return "(empty session)"


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def save(
    workdir: Path,
    session_id: str,
    messages: list[dict],
    model: str,
    *,
    complete: bool = True,
) -> Path:
    """Persist a session atomically. ``complete=False`` marks a mid-run checkpoint
    so a crash mid-turn can be detected and offered for resume on next launch."""
    path = sessions_dir(workdir) / f"{session_id}.json"
    created = time.time()
    if path.exists():
        try:
            created = json.loads(path.read_text(encoding="utf-8")).get("created", created)
        except Exception:  # noqa: BLE001
            pass
    payload = {
        "id": session_id,
        "created": created,
        "updated": time.time(),
        "model": model,
        "complete": complete,
        "title": _title(messages),
        "messages": messages,
    }
    # atomic write: tmp + replace, so a crash never leaves a half-written file
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load(workdir: Path, session_id: str) -> dict | None:
    path = sessions_dir(workdir) / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def list_sessions(workdir: Path) -> list[dict]:
    """Session metadata (no messages), newest first."""
    out: list[dict] = []
    for path in sessions_dir(workdir).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "id": data.get("id", path.stem),
                "title": data.get("title", ""),
                "updated": data.get("updated", 0),
                "model": data.get("model", ""),
                "complete": data.get("complete", True),
                "messages": len(data.get("messages", [])),
            }
        )
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


def find_incomplete(workdir: Path) -> list[dict]:
    """Return metadata for incomplete (crashed) sessions, newest first."""
    return [s for s in list_sessions(workdir) if not s.get("complete", True)]


def latest(workdir: Path) -> dict | None:
    sessions = list_sessions(workdir)
    if not sessions:
        return None
    return load(workdir, sessions[0]["id"])
