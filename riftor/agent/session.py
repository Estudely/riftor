"""Session persistence: save/resume conversations per engagement (workdir).

Sessions are JSON files under ``<workdir>/.riftor/sessions/<id>.json`` holding
the message history plus light metadata. Resuming restores the conversation so
the agent keeps its memory across runs.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
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
    # Second-resolution timestamp + a short random suffix so two sessions started
    # in the same clock second (e.g. /new then immediately tasking, or two
    # windows) don't collide onto the same file (issue #112).
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


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
    # atomic write: unique tmp + replace, so a crash never leaves a half-written
    # file AND two processes writing the same session don't clobber each other's
    # tmp file (issue #112). mkstemp gives a per-writer name in the same dir so
    # the final os.replace is atomic on the same filesystem.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{session_id}.", suffix=".json.tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
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
