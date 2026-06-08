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
from importlib import resources
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


# --- instructions prompt (Task 4b) -----------------------------------------
#
# The Codex backend rejects requests whose ``instructions`` field isn't the
# canonical Codex system prompt, so the handler must supply it. We BUNDLE a
# pinned copy (``prompts/codex/default.md``) and *opportunistically* refresh it
# from GitHub with a short timeout, falling back to the bundle on any failure.
#
# v1 scope cut (deliberate): the refreshed prompt is cached in-process for the
# lifetime of the process only — there is NO disk / ETag cache. A long-running
# process picks up an upstream prompt change on its next fresh start, which is
# good enough for now.

# Bundled prompt resource (loaded via importlib.resources, mirroring how
# ``context.py`` loads ``prompts/system.md``).
_BUNDLED_INSTRUCTIONS_RESOURCE = "prompts/codex/default.md"

# Remote source: the canonical Codex prompt in the openai/codex repo. Fetched
# opportunistically; any failure degrades to the bundled copy.
_REMOTE_INSTRUCTIONS_URLS = {
    "codex": (
        "https://raw.githubusercontent.com/openai/codex/main/"
        "codex-rs/core/gpt_5_codex_prompt.md"
    ),
}
_REMOTE_FETCH_TIMEOUT_S = 3.0

# Process-level cache keyed by family (no disk cache — see scope note above).
_INSTRUCTIONS_CACHE: dict[str, str] = {}


def _bundled_instructions() -> str:
    """Return the pinned, bundled Codex instructions prompt.

    Loaded as package data via ``importlib.resources`` (same mechanism as
    ``context._load_system_prompt``), so it works from an installed wheel.
    """
    return (
        resources.files("riftor.agent")
        .joinpath(_BUNDLED_INSTRUCTIONS_RESOURCE)
        .read_text(encoding="utf-8")
    )


def _model_family(model: str) -> str:
    """Map a model id to a prompt family.

    For v1 everything maps to the single bundled "codex" family; this helper
    exists so future families (e.g. distinct prompts per model) are a one-line
    change here rather than threaded through the call sites.
    """
    return "codex"


def _fetch_remote_instructions(family: str) -> str | None:
    """Fetch the latest instructions prompt for ``family`` from GitHub.

    This is the SINGLE network seam — tests monkeypatch *this*, never the
    network. Uses a short timeout and returns ``None`` on any failure; it never
    raises.
    """
    url = _REMOTE_INSTRUCTIONS_URLS.get(family)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(  # noqa: S310 — fixed https raw.githubusercontent URL
            req, timeout=_REMOTE_FETCH_TIMEOUT_S
        ) as resp:
            text = resp.read().decode("utf-8")
        return text or None
    except Exception:  # noqa: BLE001 — opportunistic refresh; any failure falls back to the bundle
        return None


def instructions_for(model: str) -> str:
    """Return the Codex ``instructions`` prompt for ``model``.

    Never raises on network/remote failure; always returns a non-empty string.
    Tries the (cached) opportunistic remote fetch first, then falls back to the
    bundled copy.
    """
    family = _model_family(model)
    cached = _INSTRUCTIONS_CACHE.get(family)
    if cached:
        return cached

    try:
        remote = _fetch_remote_instructions(family)
    except Exception:  # noqa: BLE001 — a misbehaving seam must still degrade to the bundle
        remote = None
    if isinstance(remote, str) and remote:
        _INSTRUCTIONS_CACHE[family] = remote
        return remote

    bundled = _bundled_instructions()
    _INSTRUCTIONS_CACHE[family] = bundled
    return bundled


# --- Responses request-body builder (Task 4c) ------------------------------
#
# Pure data transformation: Chat-Completions ``messages`` + ``tools`` -> the
# undocumented Codex *Responses API* request body. No network, no I/O. See the
# CLAUDE.md/task spec for the authoritative mapping.


def _extract_text(content: object) -> str:
    """Coerce a Chat-Completions ``content`` value into plain text.

    ``content`` may be a string, ``None``, or a list of parts. For a list we
    concatenate the text of ``{"type": "text"|"input_text", "text": ...}`` parts
    (joined with ""), ignoring non-text parts. Anything else stringifies to "".
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _message_item(role: str, text: str) -> dict:
    """A Responses ``message`` input item wrapping ``text`` for ``role``."""
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def _map_message(msg: dict) -> list[dict]:
    """Map one Chat-Completions message to zero or more Responses input items.

    System messages are handled by the caller (collected into a single leading
    ``developer`` item), so they yield nothing here.
    """
    role = msg.get("role")
    if role == "system":
        return []  # handled by build_request_body as a leading developer item
    if role == "tool":
        return [
            {
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id"),
                "output": _extract_text(msg.get("content")),
            }
        ]
    if role == "assistant":
        items: list[dict] = []
        text = _extract_text(msg.get("content"))
        if text:
            items.append(_message_item("assistant", text))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            items.append(
                {
                    "type": "function_call",
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments"),
                    "call_id": call.get("id"),
                }
            )
        return items
    # Default: a plain user (or other) message.
    text = _extract_text(msg.get("content"))
    if not text:
        return []
    return [_message_item("user" if role is None else role, text)]


def _map_tool(tool: dict) -> dict:
    """Flatten a Chat-Completions function tool to the Responses tool shape."""
    fn = tool.get("function") or {}
    out: dict = {
        "type": "function",
        "name": fn.get("name"),
        "description": fn.get("description"),
        "parameters": fn.get("parameters"),
    }
    if "strict" in fn:
        out["strict"] = fn["strict"]
    return out


def build_request_body(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    instructions: str,
    reasoning_effort: str = "medium",
    verbosity: str = "medium",
) -> dict:
    """Build the Codex *Responses API* request body from Chat-Completions inputs.

    Pure function — no network, no I/O. ``model`` is passed through as-is (the
    caller has already stripped any provider prefix). ``instructions`` is the
    canonical Codex system prompt and is placed verbatim in the top-level
    ``instructions`` field (riftor's own RIFT system message is NOT placed there;
    it rides along as a leading ``developer`` item in ``input`` instead).

    Sampling/length params (``max_tokens``, ``max_output_tokens``,
    ``max_completion_tokens``, ``temperature``, ``top_p``) are never emitted.
    """
    input_items: list[dict] = []

    # riftor's RIFT system prompt(s) become a single leading developer item.
    system_texts = [
        _extract_text(m.get("content"))
        for m in messages
        if m.get("role") == "system"
    ]
    system_texts = [t for t in system_texts if t]
    if system_texts:
        input_items.append(_message_item("developer", "\n\n".join(system_texts)))

    for msg in messages:
        input_items.extend(_map_message(msg))

    body: dict = {
        "model": model,
        "input": input_items,
        "instructions": instructions,
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": reasoning_effort, "summary": "auto"},
        "text": {"verbosity": verbosity},
    }

    if tools:
        body["tools"] = [_map_tool(t) for t in tools]

    return body
