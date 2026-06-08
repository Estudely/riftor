"""Auth section of the vendored Codex/ChatGPT-backend handler (Task 4a).

riftor talks to OpenAI's undocumented Codex/ChatGPT backend by reusing the token
file written by the official ``codex login`` (``~/.codex/auth.json``). Unlike the
read-only status peek in :mod:`riftor.codex_auth`, this module also *refreshes*
the access token and writes it back — securely (0o600, tmp-file + os.replace).

This file covers ONLY token read, account-id resolution, and refresh. Request
building, SSE parsing, and the litellm ``CustomLLM`` class land in later
sub-tasks; the public names here are what those depend on.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from riftor.codex_auth import _jwt_claims, _jwt_exp, codex_home

# --- wire-protocol constants (undocumented endpoint — do not guess) ---------
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REFRESH_WINDOW_S = 300  # refresh when exp is within 5 minutes (or past)


def _http_post_json(url: str, body: dict, timeout: float = 30.0) -> dict:
    """POST ``body`` as JSON to ``url`` and return the parsed JSON response.

    This is the single network seam in the module — tests monkeypatch *this*,
    never the network itself.
    """
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https endpoint
        return json.loads(resp.read().decode("utf-8"))


def _auth_file() -> Path:
    return codex_home() / "auth.json"


def _read_auth() -> dict:
    """Parse auth.json into a dict, or raise the upstream-mappable auth error."""
    path = _auth_file()
    if not path.exists():
        raise RuntimeError("not logged in — run `codex login`")
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 — a bad token file maps to an auth error, not a crash
        raise RuntimeError("not logged in — run `codex login`") from exc


def read_tokens() -> tuple[str, str]:
    """Return ``(access_token, refresh_token)`` from auth.json.

    Raises ``RuntimeError`` (so upstream ``classify_error`` maps it to an auth
    error) when the file is missing or the tokens are absent.
    """
    data = _read_auth()
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access or not refresh:
        raise RuntimeError("not logged in — run `codex login`")
    return access, refresh


def account_id() -> str | None:
    """The ChatGPT account id, or None if it cannot be resolved (never raises).

    Prefers ``tokens.account_id`` when present and non-empty; otherwise decodes
    it from the access-token JWT (``https://api.openai.com/auth`` ->
    ``chatgpt_account_id``).
    """
    try:
        data = _read_auth()
    except RuntimeError:
        return None
    tokens = data.get("tokens") or {}
    acc = tokens.get("account_id")
    if isinstance(acc, str) and acc:
        return acc
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        return None
    try:
        claims = _jwt_claims(access)
    except Exception:  # noqa: BLE001 — an unparseable JWT just means "unresolvable", never crash
        return None
    if not claims:
        return None
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        chatgpt = auth.get("chatgpt_account_id")
        if isinstance(chatgpt, str) and chatgpt:
            return chatgpt
    return None


def should_refresh(access_token: str) -> bool:
    """True if the token has no readable ``exp`` or expires within the window."""
    try:
        exp = _jwt_exp(access_token)
    except Exception:  # noqa: BLE001 — unparseable token => refresh to be safe
        return True
    if exp is None:
        return True
    return exp - time.time() <= _REFRESH_WINDOW_S


def _write_auth_secure(data: dict) -> None:
    """Write auth.json atomically with owner-only perms (tmp-file + os.replace)."""
    path = _auth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            os.write(fd, json.dumps(data, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — re-raised after cleanup; just removes the stale tmp first
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def refresh_tokens() -> str:
    """Refresh the access token via the OAuth endpoint and persist it.

    Reads the current refresh_token, POSTs to :data:`AUTH_TOKEN_URL`, writes the
    new ``access_token``/``refresh_token``/``id_token`` back into auth.json
    (falling back to the old values when a field is absent), updates
    ``last_refresh``, and returns the new access token. Raises ``RuntimeError``
    if the response has no ``access_token``.
    """
    data = _read_auth()
    tokens = dict(data.get("tokens") or {})
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("not logged in — run `codex login`")

    try:
        resp = _http_post_json(
            AUTH_TOKEN_URL,
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
        )
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        # A revoked/expired refresh token (HTTPError, a URLError subclass) or a
        # non-JSON body becomes a clean auth error telling the user to re-login.
        raise RuntimeError("token refresh failed — run `codex login`") from exc

    new_access = resp.get("access_token")
    if not new_access:
        raise RuntimeError("token refresh failed — no access_token in response")

    tokens["access_token"] = new_access
    tokens["refresh_token"] = resp.get("refresh_token") or tokens.get("refresh_token")
    if resp.get("id_token"):
        tokens["id_token"] = resp["id_token"]

    data["tokens"] = tokens
    data["last_refresh"] = (
        _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    _write_auth_secure(data)
    return new_access
