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
    parent_id: str | None = None,
    branch_label: str | None = None,
) -> Path:
    """Persist a session atomically. ``complete=False`` marks a mid-run checkpoint
    so a crash mid-turn can be detected and offered for resume on next launch."""
    path = sessions_dir(workdir) / f"{session_id}.json"
    created = time.time()
    existing_parent_id: str | None = None
    existing_branch_label: str | None = None
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            created = prev.get("created", created)
            existing_parent_id = prev.get("parent_id")
            existing_branch_label = prev.get("branch_label")
        except Exception:  # noqa: BLE001
            pass
    resolved_parent = parent_id if parent_id is not None else existing_parent_id
    resolved_label = branch_label if branch_label is not None else existing_branch_label
    payload = {
        "id": session_id,
        "created": created,
        "updated": time.time(),
        "model": model,
        "complete": complete,
        "title": _title(messages),
        "messages": messages,
    }
    if resolved_parent is not None:
        payload["parent_id"] = resolved_parent
    if resolved_label is not None:
        payload["branch_label"] = resolved_label
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
                "parent_id": data.get("parent_id"),
                "branch_label": data.get("branch_label"),
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


def branch(
    workdir: Path,
    parent_id: str,
    *,
    at_index: int | None = None,
    label: str | None = None,
    model: str | None = None,
) -> str:
    """Fork a session: copy a message prefix into a new session linked to the parent."""
    parent = load(workdir, parent_id)
    if parent is None:
        raise ValueError(f"no such session: {parent_id}")
    messages = parent.get("messages", [])
    if at_index is not None:
        messages = messages[:at_index]
    new_session_id = new_id()
    save(
        workdir,
        new_session_id,
        messages,
        model or parent.get("model", ""),
        parent_id=parent_id,
        branch_label=label,
    )
    return new_session_id


def truncate(
    workdir: Path,
    session_id: str,
    at_index: int,
    model: str | None = None,
) -> dict | None:
    """Drop messages from ``at_index`` onward; preserve branch metadata."""
    data = load(workdir, session_id)
    if data is None:
        return None
    messages = data.get("messages", [])[:at_index]
    save(
        workdir,
        session_id,
        messages,
        model or data.get("model", ""),
        parent_id=data.get("parent_id"),
        branch_label=data.get("branch_label"),
    )
    return load(workdir, session_id)


def resolve_id(workdir: Path, query: str) -> str:
    """Resolve a session id by exact match, unique prefix, or unique suffix.

    Raises ``ValueError`` when nothing matches or the query is ambiguous.
    """
    q = query.strip()
    if not q:
        raise ValueError("empty session id")
    ids = [s["id"] for s in list_sessions(workdir)]
    if q in ids:
        return q
    prefix_hits = [i for i in ids if i.startswith(q)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    if len(prefix_hits) > 1:
        preview = ", ".join(f"`{i}`" for i in prefix_hits[:5])
        more = f" (+{len(prefix_hits) - 5} more)" if len(prefix_hits) > 5 else ""
        raise ValueError(f"ambiguous session id '{q}': {preview}{more}")
    suffix_hits = [i for i in ids if i.endswith(q)]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    if len(suffix_hits) > 1:
        preview = ", ".join(f"`{i}`" for i in suffix_hits[:5])
        more = f" (+{len(suffix_hits) - 5} more)" if len(suffix_hits) > 5 else ""
        raise ValueError(f"ambiguous session id '{q}': {preview}{more}")
    raise ValueError(f"no such session: {q}")


def delete(workdir: Path, session_id: str) -> bool:
    """Delete a session file. Returns True if a file was removed."""
    path = sessions_dir(workdir) / f"{session_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def prune_old(
    workdir: Path,
    *,
    max_age_days: float = 7.0,
    keep_ids: set[str] | None = None,
) -> list[str]:
    """Delete sessions whose ``updated`` stamp is older than ``max_age_days``.

    ``keep_ids`` are never deleted (e.g. the live session). Returns deleted ids
    newest-first among those removed.
    """
    keep = keep_ids or set()
    cutoff = time.time() - (max_age_days * 86400.0)
    removed: list[str] = []
    for row in list_sessions(workdir):
        sid = row["id"]
        if sid in keep:
            continue
        if float(row.get("updated") or 0) >= cutoff:
            continue
        if delete(workdir, sid):
            removed.append(sid)
    return removed


def index_before_last_user_turns(messages: list[dict], k: int) -> int:
    """Return the truncate index that drops the last ``k`` user turns.

    A "user turn" is a message with ``role == "user"``. Truncating at the
    returned index keeps everything before that turn (and drops the turn plus
    any following assistant/tool messages). ``k <= 0`` keeps the full list.
    """
    if k <= 0:
        return len(messages)
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idxs:
        return 0
    if k >= len(user_idxs):
        return 0
    return user_idxs[-k]
