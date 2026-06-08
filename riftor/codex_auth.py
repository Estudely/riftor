"""Read-only status for the Codex CLI's ~/.codex/auth.json token file.

riftor never writes this file — the official ``codex login`` does. We only peek
at it to tell the operator whether they're logged in (and roughly when the token
expires) so /config and --doctor can guide them. Every failure degrades to a
sensible status; this module never raises.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodexAuthStatus:
    logged_in: bool
    expires_in_s: int | None
    detail: str


def codex_home() -> Path:
    """``$CODEX_HOME`` if set, else ``~/.codex`` (matches the official CLI)."""
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


def _auth_file() -> Path:
    return codex_home() / "auth.json"


def _jwt_exp(token: str) -> int | None:
    """Read the ``exp`` claim from a JWT payload (no signature check)."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    claims = json.loads(raw)
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def auth_status() -> CodexAuthStatus:
    """Best-effort login status. Never raises."""
    try:
        path = _auth_file()
    except Exception:  # noqa: BLE001 — Path.home() raises RuntimeError in homedir-less envs
        return CodexAuthStatus(False, None, "cannot resolve auth path — run `codex login`")
    if not path.exists():
        return CodexAuthStatus(False, None, "not logged in — run `codex login`")
    try:
        data = json.loads(path.read_text())
        token = (data.get("tokens") or {}).get("access_token")
    except Exception:  # noqa: BLE001 — a bad token file must never crash riftor
        return CodexAuthStatus(False, None, "unreadable auth.json — run `codex login`")
    if not token:
        return CodexAuthStatus(False, None, "no token — run `codex login`")
    try:
        exp = _jwt_exp(token)
    except Exception:  # noqa: BLE001 — unparseable JWT => token present, expiry unknown
        return CodexAuthStatus(True, None, "token present")
    if exp is None:
        return CodexAuthStatus(True, None, "token present")
    remaining = int(exp - time.time())
    if remaining <= 0:
        return CodexAuthStatus(False, 0, "token expired — run `codex login`")
    return CodexAuthStatus(True, remaining, f"logged in ({remaining // 60}m left)")
