# /config Provider & Model Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider/model setup to `/config` — a provider dropdown with curated default-model suggestions, a "Fetch models" button that pulls the live model list from the provider's endpoint, and per-provider credential storage.

**Architecture:** A new `riftor/providers.py` holds the provider registry, curated defaults, and a network `fetch_models()` (no Textual/config coupling). `riftor/config.py` gains a nested `[providers]` creds table plus a model-aware `creds_for()` resolver (back-compatible). `riftor/tui/config_screen.py` gains the provider/model UI. Credentials are decoupled from the single active `model`, so a future multi-model feature is additive.

**Tech Stack:** Python 3.11+, pydantic, Textual TUI, litellm, `urllib.request` for fetches, pytest (`asyncio_mode=auto`), ruff (line-length 100).

---

## Conventions (read once before starting)

- All modules start with `"""docstring"""` then `from __future__ import annotations`.
- Tests live in `tests/test_*.py`, use `monkeypatch` and `tmp_path`; async tests just use `async def` (asyncio_mode=auto, no decorator needed but existing code uses `@pytest.mark.asyncio` — match the file you edit).
- Run a single test: `uv run pytest tests/test_x.py::test_name -v`
- Run all + lint before each commit: `uv run pytest -q && uv run ruff check riftor tests`
- Keep lines ≤ 100 chars.

## File Structure

- **Create `riftor/providers.py`** — `ProviderMeta` dataclass, `PROVIDERS` registry, `PROVIDER_DEFAULTS`, `FetchResult`, `fetch_models()`, helpers `provider_key_for_model()` and `apply_prefix()`.
- **Create `tests/test_providers.py`** — unit tests for the above (network monkeypatched).
- **Modify `riftor/config.py`** — `ProviderCreds` model, `Config.providers` field, load/save of `[providers]`, `creds_for()`, `has_credentials()` update.
- **Modify `riftor/agent/provider.py:147-150`** — use `creds_for()`.
- **Modify `riftor/tui/config_screen.py`** — provider/model picker UI + fetch worker.
- **Modify `riftor/tui/app.py`** (`_open_config`, ~lines 450-469) — persist per-provider creds.
- **Modify `tests/test_config.py`, `tests/test_config_screen.py`** — new behavior.
- **Modify `docs/configuration.md`** — document the feature.

---

## Task 1: Provider registry & curated defaults

**Files:**
- Create: `riftor/providers.py`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_providers.py`:

```python
"""Provider registry, curated defaults, prefix helpers, and dynamic model fetch."""

from __future__ import annotations

import riftor.providers as pv


def test_registry_has_expected_providers():
    keys = set(pv.PROVIDERS)
    assert {"anthropic", "openai", "openrouter", "gemini", "groq",
            "deepseek", "mistral", "ollama", "custom"} <= keys
    # every provider has a curated-defaults entry (possibly empty)
    for key in pv.PROVIDERS:
        assert key in pv.PROVIDER_DEFAULTS


def test_curated_anthropic_ids_are_dateless():
    assert pv.PROVIDER_DEFAULTS["anthropic"][0] == "claude-opus-4-8"
    assert all("-2026" not in m for m in pv.PROVIDER_DEFAULTS["anthropic"])


def test_provider_key_for_model():
    assert pv.provider_key_for_model("anthropic/claude-opus-4-8") == "anthropic"
    assert pv.provider_key_for_model("ollama_chat/llama3") == "ollama"
    assert pv.provider_key_for_model("openrouter/auto") == "openrouter"
    # unknown prefix -> "custom"
    assert pv.provider_key_for_model("weird/model") == "custom"


def test_apply_prefix():
    assert pv.apply_prefix("anthropic", "claude-opus-4-8") == "anthropic/claude-opus-4-8"
    # openrouter ids already contain a slash -> passed through unchanged
    assert pv.apply_prefix("openrouter", "openrouter/auto") == "openrouter/auto"
    assert pv.apply_prefix("openrouter", "anthropic/claude-opus-4.8") == "anthropic/claude-opus-4.8"
    # custom -> no prefix
    assert pv.apply_prefix("custom", "my-model") == "my-model"
    # ollama uses ollama_chat/ prefix
    assert pv.apply_prefix("ollama", "llama3") == "ollama_chat/llama3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.providers'`

- [ ] **Step 3: Write minimal implementation**

Create `riftor/providers.py`:

```python
"""Provider registry, curated default models, and dynamic model-list fetch.

Kept free of Textual and config coupling so it is cheap to import and trivial to
unit-test. ``fetch_models`` is the only function that touches the network; it
never raises into callers — failures degrade to the curated list.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderMeta:
    key: str
    label: str
    prefix: str            # litellm prefix; "" for custom
    env: str | None        # env var holding the key, if any
    list_kind: str         # "openai" | "ollama" | "none"
    default_base: str | None


# Order here = dropdown order in /config.
PROVIDERS: dict[str, ProviderMeta] = {
    "anthropic": ProviderMeta("anthropic", "Anthropic", "anthropic/", "ANTHROPIC_API_KEY",
                              "none", None),
    "openai": ProviderMeta("openai", "OpenAI", "openai/", "OPENAI_API_KEY",
                           "openai", "https://api.openai.com/v1"),
    "openrouter": ProviderMeta("openrouter", "OpenRouter", "openrouter/", "OPENROUTER_API_KEY",
                               "openai", "https://openrouter.ai/api/v1"),
    "gemini": ProviderMeta("gemini", "Gemini", "gemini/", "GEMINI_API_KEY", "none", None),
    "groq": ProviderMeta("groq", "Groq", "groq/", "GROQ_API_KEY",
                         "openai", "https://api.groq.com/openai/v1"),
    "deepseek": ProviderMeta("deepseek", "DeepSeek", "deepseek/", "DEEPSEEK_API_KEY",
                             "openai", "https://api.deepseek.com/v1"),
    "mistral": ProviderMeta("mistral", "Mistral", "mistral/", "MISTRAL_API_KEY",
                            "openai", "https://api.mistral.ai/v1"),
    "ollama": ProviderMeta("ollama", "Ollama", "ollama_chat/", None,
                           "ollama", "http://localhost:11434"),
    "custom": ProviderMeta("custom", "Custom…", "", None, "openai", None),
}

# Curated, best-first, bare ids (no provider prefix). Researched/verified 2026-06-03.
PROVIDER_DEFAULTS: dict[str, list[str]] = {
    "anthropic": ["claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"],
    "openai": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"],
    "openrouter": ["openrouter/auto", "anthropic/claude-opus-4.8",
                   "anthropic/claude-sonnet-4.6", "openai/gpt-5.5"],
    "gemini": ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
             "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
    "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "mistral": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    "ollama": [],
    "custom": [],
}


def provider_key_for_model(model: str) -> str:
    """Best-effort provider key from a litellm model id; 'custom' if unknown."""
    for key, meta in PROVIDERS.items():
        if meta.prefix and model.startswith(meta.prefix):
            return key
    return "custom"


def apply_prefix(provider_key: str, bare_id: str) -> str:
    """Build the full litellm model id from a provider + a (possibly bare) id.

    Ids that already contain a '/' (OpenRouter, or anything pre-prefixed) pass
    through unchanged so we never double-prefix.
    """
    if "/" in bare_id:
        return bare_id
    prefix = PROVIDERS[provider_key].prefix if provider_key in PROVIDERS else ""
    return f"{prefix}{bare_id}"


@dataclass
class FetchResult:
    models: list[str] = field(default_factory=list)
    source: str = "curated"          # "live" | "curated" | "merged"
    error: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add riftor/providers.py tests/test_providers.py
git commit -m "feat(providers): registry + curated default models + prefix helpers"
```

---

## Task 2: Dynamic model fetch + merge

**Files:**
- Modify: `riftor/providers.py` (add `_merge`, `fetch_models`)
- Test: `tests/test_providers.py` (add fetch tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers.py`:

```python
def test_merge_pins_curated_first_and_dedupes():
    merged = pv._merge(["claude-opus-4-8", "claude-sonnet-4-6"],
                       ["zzz-model", "claude-opus-4-8", "aaa-model"])
    # curated first, then live with curated removed, order otherwise preserved
    assert merged == ["claude-opus-4-8", "claude-sonnet-4-6", "zzz-model", "aaa-model"]


def test_fetch_none_kind_returns_curated_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not hit the network for list_kind=none")
    monkeypatch.setattr(pv.urllib.request, "urlopen", boom)
    res = pv.fetch_models("anthropic", None, None)
    assert res.source == "curated"
    assert res.error is None
    assert res.models == pv.PROVIDER_DEFAULTS["anthropic"]


def test_fetch_openai_parses_and_merges(monkeypatch):
    payload = {"data": [{"id": "gpt-5.5"}, {"id": "gpt-9-new"}]}
    monkeypatch.setattr(pv, "_http_get_json", lambda url, headers, timeout: payload)
    res = pv.fetch_models("openai", "https://api.openai.com/v1", "sk-x")
    assert res.source == "merged"
    assert res.error is None
    assert res.models[0] == "gpt-5.5"           # curated pinned first
    assert "gpt-9-new" in res.models            # live id surfaced


def test_fetch_ollama_parses_tags(monkeypatch):
    payload = {"models": [{"name": "llama3.3"}, {"name": "qwen3"}]}
    monkeypatch.setattr(pv, "_http_get_json", lambda url, headers, timeout: payload)
    res = pv.fetch_models("ollama", "http://localhost:11434", None)
    assert res.source == "live"                 # ollama has no curated list
    assert res.models == ["llama3.3", "qwen3"]


def test_fetch_network_error_falls_back_to_curated(monkeypatch):
    def boom(url, headers, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr(pv, "_http_get_json", boom)
    res = pv.fetch_models("openai", "https://api.openai.com/v1", "sk-x")
    assert res.error is not None
    assert res.models == pv.PROVIDER_DEFAULTS["openai"]   # curated fallback
    assert res.source == "curated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_providers.py -v`
Expected: FAIL — `AttributeError: module 'riftor.providers' has no attribute '_merge'`

- [ ] **Step 3: Write minimal implementation**

Append to `riftor/providers.py`:

```python
_FETCH_TIMEOUT = 4.0


def _merge(curated: list[str], live: list[str]) -> list[str]:
    """Curated favorites first, then live ids with curated removed (stable)."""
    seen = set(curated)
    return list(curated) + [m for m in live if m and m not in seen]


def _http_get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    """GET ``url`` and parse JSON. Isolated so tests can monkeypatch one seam."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_models(provider_key: str, api_base: str | None, api_key: str | None) -> FetchResult:
    """Fetch the live model list for a provider, merged with curated defaults.

    Never raises: any network/auth/parse failure degrades to the curated list
    with ``error`` set to a short human hint.
    """
    meta = PROVIDERS.get(provider_key)
    curated = PROVIDER_DEFAULTS.get(provider_key, [])
    if meta is None:
        return FetchResult(models=curated, source="curated")

    if meta.list_kind == "none":
        return FetchResult(models=curated, source="curated")

    base = (api_base or meta.default_base or "").rstrip("/")
    if not base:
        return FetchResult(models=curated, source="curated",
                           error="no base URL for this provider")

    try:
        if meta.list_kind == "ollama":
            data = _http_get_json(f"{base}/api/tags", {}, _FETCH_TIMEOUT)
            live = [m["name"] for m in data.get("models", []) if m.get("name")]
        else:  # "openai"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            data = _http_get_json(f"{base}/models", headers, _FETCH_TIMEOUT)
            live = [m["id"] for m in data.get("data", []) if m.get("id")]
    except Exception as exc:  # noqa: BLE001 — never let a fetch crash the UI
        return FetchResult(models=curated, source="curated", error=str(exc)[:160])

    if curated:
        return FetchResult(models=_merge(curated, live), source="merged")
    return FetchResult(models=live, source="live")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add riftor/providers.py tests/test_providers.py
git commit -m "feat(providers): dynamic model fetch with curated-first merge + safe fallback"
```

---

## Task 3: Per-provider credentials in Config

**Files:**
- Modify: `riftor/config.py` (add `ProviderCreds`, `providers` field, load/save)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_providers_table_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {
        "anthropic": cfgmod.ProviderCreds(api_key="sk-ant-x"),
        "ollama": cfgmod.ProviderCreds(api_base="http://localhost:11434"),
    }
    cfg.save()
    loaded = Config.load()
    assert loaded.providers["anthropic"].api_key == "sk-ant-x"
    assert loaded.providers["ollama"].api_base == "http://localhost:11434"


def test_old_flat_config_loads_without_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text(
        '[riftor]\nmodel = "openai/gpt-5.5"\ntemperature = 0.3\n'
    )
    loaded = Config.load()
    assert loaded.model == "openai/gpt-5.5"
    assert loaded.providers == {}


def test_providers_table_is_owner_only(tmp_path, monkeypatch):
    import os, stat
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {"anthropic": cfgmod.ProviderCreds(api_key="sk-secret-xyz")}
    cfg.save()
    mode = stat.S_IMODE(os.stat(cfgmod.CONFIG_PATH).st_mode)
    assert mode == 0o600, oct(mode)
    assert "sk-secret-xyz" in cfgmod.CONFIG_PATH.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_providers_table_roundtrips -v`
Expected: FAIL — `AttributeError: module 'riftor.config' has no attribute 'ProviderCreds'`

- [ ] **Step 3: Write minimal implementation**

In `riftor/config.py`, add the `ProviderCreds` model just above `class Config` (after line 49):

```python
class ProviderCreds(BaseModel):
    """Per-provider credentials, stored in the [providers.<key>] TOML table."""

    api_key: str | None = None
    api_base: str | None = None
```

Add the field to `Config` (after the `onboarded` field, ~line 69):

```python
    # Per-provider credentials, keyed by provider key (see riftor.providers.PROVIDERS).
    providers: dict[str, ProviderCreds] = {}
```

Replace `Config.load()` (lines 108-116) so it reads the sibling `[providers]` table:

```python
    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("rb") as fh:
                data = tomllib.load(fh)
            section = dict(data.get("riftor", data))
            section.pop("providers", None)  # never let a stray key shadow the table
            providers = data.get("providers", {})
            return cls(**section, providers=providers)
        cfg = cls.detect_defaults()
        cfg.save()
        return cfg
```

In `_to_toml()` (before the final `return` at line 174), append the providers tables:

```python
        for key, creds in self.providers.items():
            if not (creds.api_key or creds.api_base):
                continue
            lines.append("")
            lines.append(f"[providers.{key}]")
            if creds.api_key:
                lines.append(f'api_key = "{creds.api_key}"')
            if creds.api_base:
                lines.append(f'api_base = "{creds.api_base}"')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add riftor/config.py tests/test_config.py
git commit -m "feat(config): per-provider credentials in a [providers] table (back-compatible)"
```

---

## Task 4: `creds_for()` resolver + `has_credentials` update

**Files:**
- Modify: `riftor/config.py` (add `creds_for`, update `has_credentials`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_creds_for_precedence(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # 1. per-provider table wins
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="legacy-global")
    cfg.providers = {"anthropic": cfgmod.ProviderCreds(api_key="from-table",
                                                       api_base="https://t/")}
    assert cfg.creds_for("anthropic/claude-opus-4-8") == ("from-table", "https://t/")

    # 2. legacy global key, no table entry
    cfg2 = Config(model="anthropic/claude-opus-4-8", api_key="legacy-global",
                  api_base="https://legacy/")
    assert cfg2.creds_for("anthropic/claude-opus-4-8") == ("legacy-global", "https://legacy/")

    # 3. env var fallback for the key
    cfg3 = Config(model="anthropic/claude-opus-4-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert cfg3.creds_for("anthropic/claude-opus-4-8") == ("from-env", None)

    # 4. nothing
    cfg4 = Config(model="anthropic/claude-opus-4-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cfg4.creds_for("anthropic/claude-opus-4-8") == (None, None)


def test_has_credentials_via_provider_table(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(model="openai/gpt-5.5")
    assert not cfg.has_credentials()
    cfg.providers = {"openai": cfgmod.ProviderCreds(api_key="sk-x")}
    assert cfg.has_credentials()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_creds_for_precedence -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'creds_for'`

- [ ] **Step 3: Write minimal implementation**

In `riftor/config.py`, add this import near the top (after the existing imports, ~line 17):

```python
from riftor.providers import provider_key_for_model
```

Add the `creds_for` method to `Config` (after `has_credentials`, ~line 106):

```python
    def creds_for(self, model: str) -> tuple[str | None, str | None]:
        """Resolve (api_key, api_base) for ``model``.

        Precedence: per-provider table → legacy global fields → env var (key
        only) → (None, None). Model-keyed so a future multi-model feature can
        resolve each model's creds without touching this layer.
        """
        key_name = provider_key_for_model(model)
        entry = self.providers.get(key_name)
        if entry and (entry.api_key or entry.api_base):
            return entry.api_key, entry.api_base
        if self.api_key or self.api_base:
            return self.api_key, self.api_base
        env = self.provider_env()  # uses self.model; fine — same provider as `model`
        if env and os.environ.get(env):
            return os.environ[env], None
        return None, None
```

Update `has_credentials` (lines 96-106) to also check the provider table — add this block right after the `if self.api_key: return True` check (line 99):

```python
        key_name = provider_key_for_model(self.model)
        entry = self.providers.get(key_name)
        if entry and entry.api_key:
            return True
```

> NOTE on import cycles: `riftor/providers.py` does NOT import `riftor.config`, so
> `config` importing `providers` is safe (one-directional).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 5: Verify no import cycle / full suite still green**

Run: `uv run pytest -q && uv run ruff check riftor tests`
Expected: PASS, no lint errors

- [ ] **Step 6: Commit**

```bash
git add riftor/config.py tests/test_config.py
git commit -m "feat(config): model-aware creds_for() resolver; has_credentials checks table"
```

---

## Task 5: Provider uses `creds_for()`

**Files:**
- Modify: `riftor/agent/provider.py:147-150`
- Test: `tests/test_provider.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provider.py`:

```python
def test_kwargs_uses_provider_table_creds(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from riftor.config import ProviderCreds
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {"anthropic": ProviderCreds(api_key="sk-table",
                                                api_base="https://table/")}
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["api_key"] == "sk-table"
    assert kw["api_base"] == "https://table/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_provider.py::test_kwargs_uses_provider_table_creds -v`
Expected: FAIL — `api_key` not in kwargs (current code only reads `config.api_key`, which is None)

- [ ] **Step 3: Write minimal implementation**

In `riftor/agent/provider.py`, replace lines 147-150:

```python
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
```

with:

```python
        api_key, api_base = self.config.creds_for(self.config.model)
        if api_base:
            kwargs["api_base"] = api_base
        if api_key:
            kwargs["api_key"] = api_key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_provider.py -v`
Expected: PASS (existing tests + new one). The existing `Provider_for_test` uses
`api_key="sk-demo"` (legacy global) → `creds_for` returns it via the legacy branch, so
`test_kwargs_*` still behave the same.

- [ ] **Step 5: Commit**

```bash
git add riftor/agent/provider.py tests/test_provider.py
git commit -m "feat(provider): resolve api_key/api_base via Config.creds_for()"
```

---

## Task 6: Config screen — provider/model picker UI (no fetch yet)

**Files:**
- Modify: `riftor/tui/config_screen.py`
- Test: `tests/test_config_screen.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_screen.py`:

```python
@pytest.mark.asyncio
async def test_provider_pick_prefills_base_and_models():
    from textual.widgets import Select
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            # new widgets exist
            for fid in ("#cfg-provider", "#cfg-model-select", "#cfg-base", "#cfg-fetch"):
                assert screen.query_one(fid) is not None, fid
            # switching provider prefills the base URL + repopulates models
            screen.query_one("#cfg-provider", Select).value = "openai"
            await pilot.pause()
            assert screen.query_one("#cfg-base", Input).value == "https://api.openai.com/v1"
            opts = [v for _, v in screen.query_one("#cfg-model-select", Select)._options]
            assert "gpt-5.5" in opts
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_save_assembles_prefixed_model_and_writes_key():
    from textual.widgets import Select
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#cfg-provider", Select).value = "openai"
            await pilot.pause()
            screen.query_one("#cfg-model-select", Select).value = "gpt-5.5"
            screen.query_one("#cfg-key", Input).value = "sk-openai-test"
            screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.model == "openai/gpt-5.5"
            assert app.config.providers["openai"].api_key == "sk-openai-test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_screen.py::test_provider_pick_prefills_base_and_models -v`
Expected: FAIL — `#cfg-provider` not found.

- [ ] **Step 3: Write minimal implementation**

Rewrite `riftor/tui/config_screen.py`. Replace the imports block and the MODEL part of
`compose`, and extend `on_select_changed` / `on_button_pressed`. Full file:

```python
"""The /config settings modal — a grouped, aligned settings card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Rule, Select, Switch

from riftor.providers import (
    PROVIDER_DEFAULTS,
    PROVIDERS,
    apply_prefix,
    fetch_models,
    provider_key_for_model,
)
from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config
    from riftor.tui.app import RiftorApp


def _row(label: str, field: Widget) -> Horizontal:
    """A label-column + field row, so every field's left edge lines up."""
    return Horizontal(Label(label, classes="field-label"), field, classes="field-row")


def _model_options(provider_key: str) -> list[tuple[str, str]]:
    return [(m, m) for m in PROVIDER_DEFAULTS.get(provider_key, [])]


class ConfigScreen(ModalScreen[dict | None]):
    """Edit runtime settings. Dismisses with a dict of changes, or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self._original_theme = config.theme
        self._provider = provider_key_for_model(config.model)

    def compose(self) -> ComposeResult:
        theme = self.config.theme if self.config.theme in THEMES else "rift"
        pkey = self._provider
        meta = PROVIDERS[pkey]
        # the bare id currently in use (strip the prefix for display in the select)
        bare = self.config.model[len(meta.prefix):] if meta.prefix and \
            self.config.model.startswith(meta.prefix) else self.config.model
        base_val = (self.config.providers.get(pkey).api_base
                    if self.config.providers.get(pkey) else None) or meta.default_base or ""
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            with VerticalScroll(id="config-body"):
                yield Label("MODEL", classes="config-section")
                yield _row("Provider", Select(
                    [(m.label, k) for k, m in PROVIDERS.items()],
                    value=pkey, allow_blank=False, id="cfg-provider"))
                yield _row("Model", Select(
                    _model_options(pkey) or [(bare, bare)],
                    value=bare if (bare in PROVIDER_DEFAULTS.get(pkey, []) or not
                                   PROVIDER_DEFAULTS.get(pkey)) else Select.BLANK,
                    allow_blank=True, id="cfg-model-select"))
                yield _row("Custom id", Input(value="", placeholder="override (optional)",
                                              id="cfg-model"))
                yield _row("Base URL", Input(value=base_val, placeholder="provider default",
                                             id="cfg-base"))
                yield _row("API key", Input(password=True, placeholder="leave blank to keep",
                                            id="cfg-key"))
                with Horizontal(classes="field-row"):
                    yield Label("", classes="field-label")
                    yield Button("Fetch models", id="cfg-fetch", variant="primary")

                yield Rule()
                yield Label("GENERATION", classes="config-section")
                yield _row("Temperature", Input(value=str(self.config.temperature), id="cfg-temp"))
                yield _row("Max tokens", Input(value=str(self.config.max_tokens), id="cfg-maxtok"))

                yield Rule()
                yield Label("APPEARANCE", classes="config-section")
                yield _row("Theme", Select([(n, n) for n in THEMES], value=theme,
                                           allow_blank=False, id="cfg-theme"))
                yield _row("Lore", Switch(value=self.config.lore, id="cfg-lore"))
            with Horizontal(id="config-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    @property
    def _riftor_app(self) -> "RiftorApp":
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self.query_one("#cfg-provider", Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "cfg-theme":
            value = event.value
            if isinstance(value, str) and value in THEMES:
                self._riftor_app._apply_theme(value)
            return
        if event.select.id == "cfg-provider" and isinstance(event.value, str):
            self._provider = event.value
            meta = PROVIDERS[event.value]
            self.query_one("#cfg-base", Input).value = meta.default_base or ""
            self._set_model_options(_model_options(event.value))

    def _set_model_options(self, options: list[tuple[str, str]]) -> None:
        sel = self.query_one("#cfg-model-select", Select)
        sel.set_options(options or [("(type a custom id below)", "")])

    @work(thread=True, exclusive=True, group="fetch")
    def _fetch_models_worker(self, provider: str, base: str, key: str | None) -> None:
        result = fetch_models(provider, base or None, key or None)
        self.app.call_from_thread(self._apply_fetch_result, result)

    def _apply_fetch_result(self, result) -> None:
        self._set_model_options([(m, m) for m in result.models])
        if result.error:
            self._fail(f"fetch failed ({result.error[:60]}) — showing suggestions")
        else:
            self.query_one("#config-title", Label).update(
                f"riftor · config — {result.source}: {len(result.models)} models")

    def _revert_theme(self) -> None:
        self._riftor_app._apply_theme(self._original_theme)

    def action_cancel(self) -> None:
        self._revert_theme()
        self.dismiss(None)

    def _fail(self, message: str) -> None:
        self.query_one("#config-title", Label).update(f"riftor · config — {message}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self._revert_theme()
            self.dismiss(None)
            return
        if event.button.id == "cfg-fetch":
            key = self.query_one("#cfg-key", Input).value.strip() or None
            # fall back to the saved key for this provider when the field is blank
            if not key:
                saved = self.config.providers.get(self._provider)
                key = saved.api_key if saved else None
            base = self.query_one("#cfg-base", Input).value.strip()
            self.query_one("#config-title", Label).update("riftor · config — fetching…")
            self._fetch_models_worker(self._provider, base, key)
            return
        # Save
        try:
            temperature = float(self.query_one("#cfg-temp", Input).value)
        except ValueError:
            self._fail("temperature must be a number")
            return
        try:
            max_tokens = int(self.query_one("#cfg-maxtok", Input).value)
        except ValueError:
            self._fail("max tokens must be an integer")
            return

        provider = self._provider
        custom = self.query_one("#cfg-model", Input).value.strip()
        sel_val = self.query_one("#cfg-model-select", Select).value
        chosen = custom or (sel_val if isinstance(sel_val, str) and sel_val else "")
        model = apply_prefix(provider, chosen) if chosen else self.config.model

        result: dict = {
            "model": model,
            "provider": provider,
            "api_base": self.query_one("#cfg-base", Input).value.strip() or None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "theme": self.query_one("#cfg-theme", Select).value,
            "lore": self.query_one("#cfg-lore", Switch).value,
        }
        key = self.query_one("#cfg-key", Input).value.strip()
        if key:
            result["api_key"] = key
        self.dismiss(result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_screen.py -v`
Expected: PASS — including the original tests (they assert `#cfg-model`, `#cfg-key`,
`#cfg-temp`, `#cfg-maxtok`, `#cfg-theme`, `#cfg-lore` exist [still present], 3 section
headers [still MODEL/GENERATION/APPEARANCE], and **6** `.field-label`).

> ⚠️ The original `test_config_modal_renders_all_fields` asserts exactly **6** `.field-label`
> rows. The new MODEL section adds rows (Provider, Model, Custom id, Base URL, API key, +
> the Fetch button row uses an empty `.field-label`). Update that assertion in the next step.

- [ ] **Step 5: Update the stale count assertion**

In `tests/test_config_screen.py::test_config_modal_renders_all_fields`, change:

```python
            assert len(list(screen.query(".field-label"))) == 6
```
to:
```python
            # MODEL: Provider, Model, Custom id, Base URL, API key, (Fetch spacer) = 6
            # GENERATION: Temperature, Max tokens = 2 ; APPEARANCE: Theme, Lore = 2
            assert len(list(screen.query(".field-label"))) == 10
```

And update the field list loop to include the new ids:

```python
            for fid, kind in [
                ("#cfg-provider", Select), ("#cfg-model-select", Select),
                ("#cfg-model", Input), ("#cfg-base", Input), ("#cfg-key", Input),
                ("#cfg-temp", Input), ("#cfg-maxtok", Input),
                ("#cfg-theme", Select), ("#cfg-lore", Switch),
            ]:
```

- [ ] **Step 6: Run full config-screen suite**

Run: `uv run pytest tests/test_config_screen.py -v`
Expected: PASS (all, including the short-terminal regression at heights 24/30/40 — verify
the taller MODEL section still keeps Save/Cancel on screen; if height=24 fails, the body
scrolls so buttons stay visible by design — confirm the test passes, it asserts button
region within viewport which the `1fr` scrolling body guarantees).

- [ ] **Step 7: Commit**

```bash
git add riftor/tui/config_screen.py tests/test_config_screen.py
git commit -m "feat(config-screen): provider/model picker + fetch-models button"
```

---

## Task 7: Wire `_open_config` to persist per-provider creds

**Files:**
- Modify: `riftor/tui/app.py` (`_open_config`, ~lines 450-469)
- Test: covered by `tests/test_config_screen.py::test_save_assembles_prefixed_model_and_writes_key` (Task 6) — it asserts `app.config.providers["openai"].api_key`, which requires this wiring.

- [ ] **Step 1: Confirm the Task-6 save test currently fails on the wiring**

Run: `uv run pytest tests/test_config_screen.py::test_save_assembles_prefixed_model_and_writes_key -v`
Expected: FAIL — `KeyError: 'openai'` (the screen returns `provider`/`api_key` in its dict,
but `_open_config` doesn't write them into `config.providers` yet).

- [ ] **Step 2: Write the implementation**

In `riftor/tui/app.py`, replace the body of `_open_config` (lines 450-469) with:

```python
    @work(group="config")
    async def _open_config(self) -> None:
        from riftor.config import ProviderCreds  # local import: avoids load-time cost

        result = await self.push_screen_wait(ConfigScreen(self.config))
        if not isinstance(result, dict):
            self._note("config unchanged")
            return
        self.config.model = result["model"]
        self.config.temperature = result["temperature"]
        self.config.max_tokens = result["max_tokens"]
        self.config.lore = result["lore"]

        provider = result.get("provider")
        if provider:
            entry = self.config.providers.get(provider) or ProviderCreds()
            if result.get("api_base") is not None:
                entry.api_base = result["api_base"]
            if result.get("api_key"):
                entry.api_key = result["api_key"]
            if entry.api_key or entry.api_base:
                self.config.providers[provider] = entry

        self.provider = Provider(self.config)
        self.context.lore = self.config.lore
        self.status.set_lore(self.config.lore)
        self.status.set_model(self.config.model)
        self.config.theme = result["theme"]
        self._apply_theme(result["theme"])
        self.config.save()
        self._note("config saved")
```

> Removed: the old `if result.get("api_key"): self.config.api_key = result["api_key"]`.
> Keys now live in the per-provider table. The legacy `config.api_key` is still honored
> by `creds_for` for backward compatibility, but new keys entered via /config go to the table.

- [ ] **Step 3: Run the save test**

Run: `uv run pytest tests/test_config_screen.py::test_save_assembles_prefixed_model_and_writes_key -v`
Expected: PASS

- [ ] **Step 4: Run the full suite + lint**

Run: `uv run pytest -q && uv run ruff check riftor tests`
Expected: PASS, no lint errors

- [ ] **Step 5: Commit**

```bash
git add riftor/tui/app.py
git commit -m "feat(config): persist per-provider creds from the /config screen"
```

---

## Task 8: Docs

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Read the current doc to match its style**

Run: `sed -n '1,60p' docs/configuration.md` (use Read tool in practice)

- [ ] **Step 2: Add a "Providers & models" section**

Add this section after the existing model/config discussion (adapt heading depth to the file):

```markdown
## Providers & models

Open `/config` in the TUI to pick a provider and model. The **Provider** dropdown
lists Anthropic, OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Mistral, Ollama, and
Custom. Picking one prefills the **Base URL** with that provider's default and shows
curated default-model suggestions in the **Model** dropdown.

**Fetch models** pulls the live model list from the provider's endpoint:

- OpenAI-compatible providers (OpenAI, OpenRouter, Groq, DeepSeek, Mistral, Custom)
  query `{base}/models`.
- Ollama queries `{base}/api/tags`.
- Anthropic and Gemini have no public list endpoint, so only curated suggestions show.

Fetched models are merged with the curated favorites (favorites pinned first). If the
fetch fails (offline, bad key), riftor falls back to the curated list and shows a hint.

Use the **Custom id** field to type any litellm model id directly (it overrides the
dropdown). Use the **Custom** provider for self-hosted / OpenAI-compatible servers.

### Credentials

Credentials are stored per provider in `config.toml` (mode `0600`):

​```toml
[riftor]
model = "anthropic/claude-opus-4-8"

[providers.anthropic]
api_key = "sk-ant-..."

[providers.ollama]
api_base = "http://localhost:11434"
​```

Resolution order for a model's key/base: the matching `[providers.<key>]` entry →
the legacy top-level `api_key`/`api_base` → the provider's environment variable
(e.g. `ANTHROPIC_API_KEY`). Environment variables remain the recommended way to
supply keys in shared or CI environments.
```

(Replace the zero-width `​` characters around the code fence with normal triple backticks
when editing — they are only here to nest the fence inside this plan.)

- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: document the /config provider & model picker and [providers] table"
```

---

## Task 9: Final verification

- [ ] **Step 1: Full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check riftor tests`
Expected: all pass, no lint errors.

- [ ] **Step 2: Manual smoke (optional but recommended)**

Run: `uv run riftor` → type `/config` → switch Provider to OpenAI → confirm Base URL
prefills and Model dropdown shows `gpt-5.5` etc. → press Fetch models (with a key set) →
confirm the list expands or a graceful error shows → Save → confirm the title says
"config saved" and the status bar shows the new model.

- [ ] **Step 3: Confirm backward compatibility**

Run: temporarily point `XDG_CONFIG_HOME` at a dir containing an old flat `config.toml`
(no `[providers]`) and launch — it should load with `providers == {}` and behave as before.

```bash
mkdir -p /tmp/riftor-old/riftor
printf '[riftor]\nmodel = "anthropic/claude-sonnet-4-6"\n' > /tmp/riftor-old/riftor/config.toml
XDG_CONFIG_HOME=/tmp/riftor-old uv run python -c "from riftor.config import Config; c=Config.load(); print(c.model, c.providers)"
```
Expected: `anthropic/claude-sonnet-4-6 {}`

---

## Self-Review notes (for the implementer)

- **Spec coverage:** registry+defaults (T1), fetch+merge (T2), `[providers]` table (T3),
  `creds_for`/`has_credentials` (T4), Provider wiring (T5), UI picker+fetch (T6), app
  persistence (T7), docs (T8). All spec sections map to a task.
- **Type consistency:** `ProviderCreds`, `FetchResult`, `PROVIDERS`, `PROVIDER_DEFAULTS`,
  `provider_key_for_model`, `apply_prefix`, `fetch_models`, `creds_for` names are used
  identically across tasks.
- **Import direction:** `providers.py` imports nothing from `config`/`tui`; `config`
  imports `providers`; `config_screen` imports `providers`. No cycle.
- **Back-compat:** old flat config loads (T3 test + T9 step 3); legacy `api_key`/`api_base`
  honored by `creds_for` (T4 test); CLI `--api-key`/`--model` unchanged.
- **Textual API caveat:** if `Select.set_options` / `Select.BLANK` differ in the installed
  Textual version, adjust in T6 (the test will catch it). `_options` access in the T6 test
  is read-only introspection; if that private attr differs, assert via
  `screen.query_one("#cfg-model-select", Select).value` round-trip instead.
```
