# Codex (ChatGPT subscription) Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let riftor users run inference through their ChatGPT/Codex subscription by reusing the token file written by the official `codex login`, exposed as a first-class `codex/` provider.

**Architecture:** Codex appears as an ordinary litellm model id (`codex/gpt-5.5-codex`). The undocumented Codex endpoint machinery is delegated to the `litellm-codex-oauth-provider` package, registered into litellm via `custom_provider_map` inside riftor's existing lazy-litellm seam. riftor supplies no API key — the package reads `~/.codex/auth.json` itself. A small read-only helper surfaces login status in `/config` and `--doctor`.

**Tech Stack:** Python 3.11+, litellm, pydantic, Textual, `uv`. New dep: `litellm-codex-oauth-provider`.

**Spec:** `docs/superpowers/specs/2026-06-08-codex-chatgpt-login-design.md`

**Conventions to honor (from CLAUDE.md):**
- All tests run **offline** — no network, no real `~/.codex`. Use `monkeypatch` and `tmp_path`.
- `pytest` runs in `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed).
- Bad config/auth must **never crash startup** — degrade silently to defaults.
- Run lint+types before committing each task: `uv run ruff check riftor dev tests && uv run pyright riftor`.

---

## File Structure

| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `riftor/providers.py` | Modify | Register `codex` in `PROVIDERS` + `PROVIDER_DEFAULTS`; `fetch_models` handles `list_kind="codex"` |
| `riftor/config.py` | Modify | `creds_for`/`has_credentials` treat `codex/` as token-file-based (no key) |
| `riftor/codex_auth.py` | Create | Read-only `~/.codex/auth.json` status helper (never raises) |
| `riftor/agent/provider.py` | Modify | Register `litellm.custom_provider_map` for `codex` in `_get_litellm()` |
| `riftor/tui/config_screen.py` | Modify | Hide key/base/fetch for Codex; show login-status line |
| `riftor/tui/app.py` | Modify | Add Codex to context-window estimate (128k) |
| `riftor/engagement/doctor.py` | Modify | Add Codex login line to the doctor report |
| `riftor/__main__.py` | Modify | `--doctor` prints the Codex status line |
| `pyproject.toml` | Modify | Add `litellm-codex-oauth-provider` runtime dep |
| `docs/configuration.md` | Modify | Document Codex login |
| `tests/test_codex_auth.py` | Create | Unit tests for the auth-status helper |
| `tests/test_providers.py` | Modify | Codex registry/prefix/fetch tests |
| `tests/test_config.py` | Modify | Codex creds/has_credentials tests |
| `dev/smoke.py` | Modify | Drive a Codex-model turn offline |

---

## Task 1: Register the Codex provider in the registry

**Files:**
- Modify: `riftor/providers.py:26-45` (PROVIDERS table), `:48-61` (PROVIDER_DEFAULTS), `:130-143` (fetch_models)
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_providers.py`:

```python
def test_codex_provider_registered():
    assert "codex" in pv.PROVIDERS
    meta = pv.PROVIDERS["codex"]
    assert meta.prefix == "codex/"
    assert meta.env is None          # no API key env var
    assert meta.list_kind == "codex"
    assert meta.default_base is None
    assert "codex" in pv.PROVIDER_DEFAULTS


def test_codex_provider_key_and_prefix():
    assert pv.provider_key_for_model("codex/gpt-5.5-codex") == "codex"
    assert pv.apply_prefix("codex", "gpt-5.5-codex") == "codex/gpt-5.5-codex"
    # an id that already carries the prefix passes through unchanged
    assert pv.apply_prefix("codex", "codex/gpt-5.5") == "codex/gpt-5.5"


def test_fetch_codex_returns_curated_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not hit the network for list_kind=codex")
    monkeypatch.setattr(pv.urllib.request, "urlopen", boom)
    monkeypatch.setattr(pv, "_http_get_json", boom)
    res = pv.fetch_models("codex", None, None)
    assert res.source == "curated"
    assert res.error is None
    assert res.models == pv.PROVIDER_DEFAULTS["codex"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -k codex -v`
Expected: FAIL — `KeyError: 'codex'` / `assert 'codex' in {...}`.

- [ ] **Step 3: Add the provider to the registry**

In `riftor/providers.py`, add this entry to the `PROVIDERS` dict (place it just before the `"ollama"` entry so cloud providers stay grouped before local):

```python
    "codex": ProviderMeta("codex", "Codex (ChatGPT)", "codex/", None,
                          "codex", None),
```

Add to `PROVIDER_DEFAULTS` (place before `"ollama": []`):

```python
    "codex": ["gpt-5.5-codex", "gpt-5.5", "gpt-5.4-codex"],
```

In `fetch_models`, add a guard right after the existing `if meta.list_kind == "none":` block (around line 122), so a Codex fetch never touches the network and never needs a base URL:

```python
    if meta.list_kind == "codex":
        # Codex backend has no public /models list; always use curated defaults.
        return FetchResult(models=curated, source="curated")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -k codex -v`
Expected: PASS (3 tests).

Also run the full provider suite to confirm no regression (the existing `test_registry_has_expected_providers` asserts a subset, so it still passes):

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/providers.py tests/test_providers.py
git commit -m "feat: register codex (ChatGPT subscription) provider in registry"
```

---

## Task 2: Credential resolution treats Codex as token-file-based

**Files:**
- Modify: `riftor/config.py:120-134` (`has_credentials`), `:136-155` (`creds_for`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_codex_creds_are_none(monkeypatch):
    # Codex supplies no api_key/api_base from riftor; the litellm handler reads
    # ~/.codex/auth.json itself. A stale global key must never leak to it.
    cfg = Config(model="codex/gpt-5.5-codex", api_key="leftover-global")
    assert cfg.creds_for("codex/gpt-5.5-codex") == (None, None)


def test_codex_has_credentials_like_ollama(monkeypatch):
    # Treated like ollama: never block the UI on a key. Real login validity is
    # surfaced as status, not a hard gate.
    cfg = Config(model="codex/gpt-5.5-codex")
    assert cfg.has_credentials()


def test_codex_provider_env_is_none():
    assert Config(model="codex/gpt-5.5-codex").provider_env() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k codex -v`
Expected: FAIL — `creds_for` returns `("leftover-global", None)` and `has_credentials()` is `False`.

- [ ] **Step 3: Implement the Codex branches**

In `riftor/config.py`, `has_credentials()` — add a Codex branch alongside the existing Ollama one. The current block is:

```python
        if self.model.startswith(("ollama/", "ollama_chat/")):
            return True
```

Change it to also cover Codex:

```python
        if self.model.startswith(("ollama/", "ollama_chat/", "codex/")):
            return True
```

In `creds_for()`, add an explicit early return at the very top of the method body (before the per-provider table lookup), so a stale global `api_key`/env never reaches the Codex handler:

```python
        if model.startswith("codex/"):
            # Codex auth lives in ~/.codex/auth.json, read by the litellm handler.
            return None, None
```

`provider_env()` already returns `None` for Codex because `PROVIDERS["codex"].env is None` — no change needed; the new test pins it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k codex -v`
Expected: PASS (3 tests).

Run the full config suite to confirm no regression:

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/config.py tests/test_config.py
git commit -m "feat: resolve codex creds from token file (no api key)"
```

---

## Task 3: Read-only Codex auth-status helper

**Files:**
- Create: `riftor/codex_auth.py`
- Test: `tests/test_codex_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_codex_auth.py`:

```python
"""Codex auth-status helper: reads ~/.codex/auth.json, never raises."""

from __future__ import annotations

import base64
import json
import time

import riftor.codex_auth as ca


def _make_jwt(exp: int) -> str:
    """A minimal unsigned JWT with the given exp claim (header.payload.sig)."""
    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


def _write_auth(tmp_path, access_token: str) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": access_token}})
    )


def test_missing_file_is_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    status = ca.auth_status()
    assert status.logged_in is False
    assert "codex login" in status.detail


def test_valid_future_token_is_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, _make_jwt(int(time.time()) + 3600))
    status = ca.auth_status()
    assert status.logged_in is True
    assert status.expires_in_s is not None
    assert 0 < status.expires_in_s <= 3600


def test_garbage_token_degrades_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, "not-a-jwt")
    status = ca.auth_status()           # must NOT raise
    assert status.logged_in is True     # token present, just unparseable
    assert status.expires_in_s is None


def test_malformed_json_degrades_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text("{ this is not json")
    status = ca.auth_status()           # must NOT raise
    assert status.logged_in is False


def test_codex_home_defaults_to_home(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    from pathlib import Path
    assert ca.codex_home() == Path.home() / ".codex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_codex_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.codex_auth'`.

- [ ] **Step 3: Create the helper**

Create `riftor/codex_auth.py`:

```python
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
    path = _auth_file()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_codex_auth.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/codex_auth.py tests/test_codex_auth.py
git commit -m "feat: read-only codex auth.json login-status helper"
```

---

## Task 4: Add the runtime dependency and register the litellm handler

**Files:**
- Modify: `pyproject.toml:19-23` (dependencies), `uv.lock` (relock)
- Modify: `riftor/agent/provider.py:27-37` (`_get_litellm`)

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` block to:

```toml
dependencies = [
    "textual>=0.79",
    "litellm>=1.55",
    "pydantic>=2.6",
    "litellm-codex-oauth-provider>=0.1",
]
```

- [ ] **Step 2: Relock and install**

Run: `uv sync --extra dev`
Expected: resolves and installs `litellm-codex-oauth-provider`; `uv.lock` updated. If the exact `>=0.1` floor doesn't resolve, run `uv add litellm-codex-oauth-provider` instead (it picks a valid floor and updates both files), then re-run `uv sync --extra dev`.

- [ ] **Step 3: Verify the import path of the handler**

Run: `uv run python -c "from litellm_codex_oauth_provider import codex_auth_provider; print(type(codex_auth_provider))"`
Expected: prints a class/instance (no ImportError). If the import name differs in the installed version, run `uv run python -c "import litellm_codex_oauth_provider as m; print([n for n in dir(m) if not n.startswith('_')])"` and use the exported handler name in Step 4.

- [ ] **Step 4: Register the handler in the lazy-litellm seam**

In `riftor/agent/provider.py`, the current `_get_litellm()` is:

```python
def _get_litellm():
    global _litellm
    if _litellm is None:
        os.environ.setdefault("LITELLM_LOG", "ERROR")
        import litellm

        litellm.telemetry = False
        litellm.drop_params = True
        litellm.suppress_debug_info = True
        _litellm = litellm
    return _litellm
```

Replace the body with one that also registers the Codex custom provider (guarded so a missing/renamed handler never breaks startup of non-Codex users):

```python
def _get_litellm():
    global _litellm
    if _litellm is None:
        os.environ.setdefault("LITELLM_LOG", "ERROR")
        import litellm

        litellm.telemetry = False
        litellm.drop_params = True
        litellm.suppress_debug_info = True
        _register_codex_provider(litellm)
        _litellm = litellm
    return _litellm


def _register_codex_provider(litellm) -> None:
    """Register the `codex/` custom provider that reads ~/.codex/auth.json.

    Guarded: if the package is absent or its API changed, Codex simply won't
    work, but every other provider still does — consistent with riftor's
    never-crash-on-an-optional-thing ethos.
    """
    try:
        from litellm_codex_oauth_provider import codex_auth_provider
    except Exception:  # noqa: BLE001 — Codex optional at runtime; never break the loop
        return
    existing = list(getattr(litellm, "custom_provider_map", None) or [])
    if any(entry.get("provider") == "codex" for entry in existing):
        return
    existing.append({"provider": "codex", "custom_handler": codex_auth_provider})
    litellm.custom_provider_map = existing
```

- [ ] **Step 5: Verify registration works without a token file**

Run: `uv run python -c "from riftor.agent.provider import _get_litellm; m=_get_litellm(); print([e['provider'] for e in m.custom_provider_map])"`
Expected: output includes `'codex'`, and the command does not require `~/.codex/auth.json` to exist (no crash).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check riftor && uv run pyright riftor
git add pyproject.toml uv.lock riftor/agent/provider.py
git commit -m "feat: register litellm codex custom provider; add runtime dep"
```

---

## Task 5: `/config` UI — hide key/base/fetch for Codex, show login status

**Files:**
- Modify: `riftor/tui/config_screen.py` — imports (`:14-22`), compose Model section (`:114-129`), `on_select_changed` (`:216-223`)
- Manual verification (Textual UI; covered end-to-end by the smoke test in Task 8)

- [ ] **Step 1: Import the auth helper**

In `riftor/tui/config_screen.py`, add after the existing `from riftor.config import REASONING_EFFORTS` import (line 22):

```python
from riftor.codex_auth import auth_status
```

- [ ] **Step 2: Add a status label to the Model section**

In `compose()`, the Model section currently ends with the API-key row and the Fetch button (lines 125-129). Add a Codex status label right after the API-key row and before the Fetch-button `Horizontal`. Insert:

```python
                        yield _row("Codex login", Label(
                            self._codex_status_text(), id="cfg-codex-status"))
```

- [ ] **Step 3: Add the status-text helper and initial visibility**

Add this method to `ConfigScreen` (next to `_set_model_options`):

```python
    def _codex_status_text(self) -> str:
        st = auth_status()
        mark = "✓" if st.logged_in else "⚠"
        return f"{mark} {st.detail}"

    def _set_codex_mode(self, on: bool) -> None:
        """Codex has no API key/base/model-list: hide those rows, show status."""
        for wid in ("cfg-key", "cfg-base", "cfg-fetch"):
            row = self.query_one(f"#{wid}").parent
            row.set_class(on, "hidden")
        status_row = self.query_one("#cfg-codex-status").parent
        status_row.set_class(not on, "hidden")
        if on:
            self.query_one("#cfg-codex-status", Label).update(self._codex_status_text())
```

In `on_mount()` (currently just focuses `#cfg-provider`), add a call so the initial state is correct when the saved provider is already Codex:

```python
    def on_mount(self) -> None:
        self.query_one("#cfg-provider", Select).focus()
        self._set_codex_mode(self._provider == "codex")
```

- [ ] **Step 4: Toggle on provider change**

In `on_select_changed`, the `cfg-provider` branch currently sets `self._provider`, updates the base URL, and refreshes model options. Add the Codex-mode toggle. The branch becomes:

```python
        if event.select.id == "cfg-provider" and isinstance(event.value, str):
            self._provider = event.value
            self._set_codex_mode(event.value == "codex")
            meta = PROVIDERS[event.value]
            self.query_one("#cfg-base", Input).value = meta.default_base or ""
            if self._provider_initialized:
                self._set_model_options(_model_options(event.value))
            else:
                self._provider_initialized = True
```

- [ ] **Step 5: Manual smoke (optional here; automated in Task 8)**

Run: `uv run riftor` → open `/config` → switch Provider to "Codex (ChatGPT)".
Expected: API key, Base URL, and Fetch button disappear; a "Codex login" status line shows `⚠ not logged in — run \`codex login\`` (or `✓ logged in (…m left)` if you've run `codex login`). Switching back to Anthropic restores the key/base/fetch rows.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check riftor && uv run pyright riftor
git add riftor/tui/config_screen.py
git commit -m "feat: /config adapts to codex (login status, no key/base/fetch)"
```

---

## Task 6: Context-window estimate for Codex

**Files:**
- Modify: `riftor/tui/app.py:44-49` (`_CONTEXT_WINDOWS`)

- [ ] **Step 1: Add the Codex window**

In `riftor/tui/app.py`, the `_CONTEXT_WINDOWS` dict is matched by prefix in `_context_window()`. Add a `codex/` entry (128k, matching the GPT-5 class default):

```python
_CONTEXT_WINDOWS = {
    "anthropic/": 200_000, "openai/": 128_000, "openrouter/": 128_000,
    "gemini/": 1_000_000, "groq/": 128_000, "ollama": 8_192,
    "codex/": 128_000,
}
```

- [ ] **Step 2: Verify the lookup resolves**

Run: `uv run python -c "import riftor.tui.app as a; print(next(w for p,w in a._CONTEXT_WINDOWS.items() if 'codex/gpt-5.5-codex'.startswith(p)))"`
Expected: `128000`.

- [ ] **Step 3: Lint, type-check, commit**

```bash
uv run ruff check riftor && uv run pyright riftor
git add riftor/tui/app.py
git commit -m "feat: context-window estimate for codex models (128k)"
```

---

## Task 7: Surface Codex login in `--doctor`

**Files:**
- Modify: `riftor/engagement/doctor.py:105-115` (`render_plain`)
- Modify: `riftor/__main__.py:55-59` (the `--doctor` branch already calls `render_plain`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_codex_auth.py` (it already imports `riftor.codex_auth`):

```python
def test_doctor_plain_includes_codex_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # no auth.json => not logged in
    from riftor.engagement.doctor import check_toolchain, render_plain
    out = render_plain(check_toolchain())
    assert "Codex" in out
    assert "codex login" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_codex_auth.py::test_doctor_plain_includes_codex_line -v`
Expected: FAIL — `assert "Codex" in out` (the doctor report has no Codex line yet).

- [ ] **Step 3: Append a Codex line in `render_plain`**

In `riftor/engagement/doctor.py`, import the helper at the top (after the `import shutil` line):

```python
from riftor.codex_auth import auth_status
```

In `render_plain`, before the final `return "\n".join(lines)`, append a Codex auth line:

```python
    st = auth_status()
    mark = "ok " if st.logged_in else "MISSING"
    lines.append(f"  [{mark}] {'codex login':<10} {st.detail}")
```

(`render_markdown` is the TUI surface; the spec only requires the CLI `--doctor` line, which goes through `render_plain`. Leave `render_markdown` unchanged to keep this task focused.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_codex_auth.py::test_doctor_plain_includes_codex_line -v`
Expected: PASS.

- [ ] **Step 5: Verify the CLI end-to-end**

Run: `CODEX_HOME=/nonexistent uv run riftor --doctor`
Expected: the report ends with a line like `[MISSING] codex login  not logged in — run \`codex login\``.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/engagement/doctor.py tests/test_codex_auth.py
git commit -m "feat: --doctor reports codex login status"
```

---

## Task 8: Smoke test — drive a Codex-model turn offline

**Files:**
- Modify: `dev/smoke.py`

- [ ] **Step 1: Read the existing demo-turn block**

Open `dev/smoke.py` and find the block that sets `RIFTOR_DEMO_RESPONSE` and drives a turn (around line 114-119, where `_saved_demo`, `_saved_key`, `_saved_chakla_model` are captured). This is the offline turn-driving pattern to reuse.

- [ ] **Step 2: Add a Codex-model offline check**

After the existing offline demo-turn block completes and restores its globals, add a self-contained Codex check that proves: (a) switching to a `codex/` model works, (b) `custom_provider_map` registration is harmless under the demo mock, and (c) no token file is required. Insert:

```python
        # --- Codex (ChatGPT subscription) model: offline, mocked ---
        _saved_model = app.config.model
        _saved_demo2 = os.environ.get("RIFTOR_DEMO_RESPONSE")
        _saved_codex_home = os.environ.get("CODEX_HOME")
        os.environ["RIFTOR_DEMO_RESPONSE"] = "codex offline ok"
        os.environ["CODEX_HOME"] = workdir  # guarantee NO real auth.json is read
        try:
            inp.value = "/model codex/gpt-5.5-codex"
            await pilot.press("enter")
            await pilot.pause()
            assert app.status.model == "codex/gpt-5.5-codex", "codex model not selected"
            inp.value = "hello codex"
            await pilot.press("enter")
            await pilot.pause()
            # The mocked turn must complete without crashing; the demo text appears.
            assert "codex offline ok" in app.context.last_assistant_text(), \
                "expected mocked codex turn to complete"
        finally:
            app.config.model = _saved_model
            if _saved_demo2 is None:
                os.environ.pop("RIFTOR_DEMO_RESPONSE", None)
            else:
                os.environ["RIFTOR_DEMO_RESPONSE"] = _saved_demo2
            if _saved_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = _saved_codex_home
```

> **Note on `last_assistant_text()`:** if `riftor.agent.context.Context` has no such method, replace that assertion with the same mechanism the existing demo block uses to read the assistant's reply (mirror it exactly — read the existing block first in Step 1). The goal is only "the mocked turn completed without crashing."

- [ ] **Step 3: Run the smoke test**

Run: `uv run python dev/smoke.py`
Expected: exits 0; prints its existing success output with no traceback. The Codex block runs without needing a real `~/.codex/auth.json`.

- [ ] **Step 4: Commit**

```bash
git add dev/smoke.py
git commit -m "test: smoke-drive a codex model turn offline"
```

---

## Task 9: Document Codex login

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Read the current doc structure**

Open `docs/configuration.md` and find where providers / model ids / credentials are documented, to place the new section consistently (follow the existing heading style).

- [ ] **Step 2: Add the Codex section**

Add a section (match the surrounding heading level):

```markdown
## Codex / ChatGPT subscription login

riftor can run inference through your **ChatGPT Plus/Pro/Team subscription**
instead of an API key, by reusing the credentials from OpenAI's official Codex CLI.

**Setup:**

1. Install the official Codex CLI and run `codex login` once. This performs the
   OAuth browser flow and writes `~/.codex/auth.json` (override the location with
   `$CODEX_HOME`). riftor reads this file — it never writes it.
2. In riftor, open `/config`, set **Provider** to **Codex (ChatGPT)**, and pick a
   model such as `codex/gpt-5.5-codex`. No API key is needed — the "Codex login"
   line shows whether you're authenticated and roughly when the token expires.
3. Or set it directly: `riftor --model codex/gpt-5.5-codex`.

Check status any time with `riftor --doctor`, which reports whether
`~/.codex/auth.json` is present and logged in.

**Notes:**

- Billing goes to your ChatGPT subscription, not OpenAI API credits.
- This uses an **undocumented** ChatGPT backend endpoint via the
  `litellm-codex-oauth-provider` package; OpenAI may change it without notice.
- If a call fails with an auth error, re-run `codex login`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: document codex (ChatGPT subscription) login"
```

---

## Task 10: Full CI gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full gate**

Run: `make check`
Expected: lint → typecheck → test → smoke all pass (exit 0). This is the same gate CI runs on 3.11 + 3.12.

- [ ] **Step 2: If anything fails**

Fix the specific failure in its owning task's files, re-run `make check`, and amend/commit. Do not mark the plan complete until `make check` is green.

- [ ] **Step 3: Final commit (only if fixes were needed)**

```bash
git add -A
git commit -m "chore: green CI for codex login feature"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Architecture (registration in `_get_litellm`, creds `(None,None)`) → Task 4, Task 2 ✓
- `providers.py` registry + `PROVIDER_DEFAULTS` + `fetch_models` codex kind → Task 1 ✓
- `config.py` `creds_for`/`has_credentials`/`provider_env` → Task 2 ✓
- `codex_auth.py` helper → Task 3 ✓
- `config_screen.py` (hide key/base/fetch, status line) → Task 5 ✓
- `app.py` context window → Task 6 ✓
- `doctor.py` + `--doctor` → Task 7 ✓
- `pyproject.toml` / `uv.lock` hard dep → Task 4 ✓
- `docs/configuration.md` → Task 9 ✓
- Tests (providers, config, codex_auth) + smoke → Tasks 1,2,3,7,8 ✓
- Error handling (auth errors via `classify_error`, never-crash) → covered by Task 3 (helper never raises), Task 4 (guarded registration); `classify_error` already maps auth/validation/unknown — no change needed ✓
- Out-of-scope items (no OAuth, no shell-out, no CLI flag) → respected; completions untouched ✓

**Placeholder scan:** No TBD/TODO. Every code step shows full code. The two adaptive notes (handler import name in Task 4 Step 3, `last_assistant_text` in Task 8) give an explicit verification command + fallback rather than a vague "fill in" — they exist because the exact external symbol can vary by installed version.

**Type/name consistency:** `CodexAuthStatus(logged_in, expires_in_s, detail)`, `auth_status()`, `codex_home()`, `_register_codex_provider`, `_set_codex_mode`, `_codex_status_text` are used consistently across Tasks 3/5/7/8. Provider key `"codex"`, prefix `"codex/"`, `list_kind="codex"` consistent across Tasks 1/2/4/6. Model id `codex/gpt-5.5-codex` consistent throughout.
