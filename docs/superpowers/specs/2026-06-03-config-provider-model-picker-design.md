# /config provider & model picker — design

**Date:** 2026-06-03
**Status:** approved

## Goal

Make `/config` a place to set up **providers and models**: pick a provider from a
dropdown, get curated default-model suggestions, fetch the live model list from the
provider's endpoint, and store credentials per provider. Decouple credentials from
the single active model so a future "multiple models at once" feature is purely
additive.

## Non-goals (YAGNI)

- Multiple *active* models in this release. We prepare the schema for it (per-provider
  creds, a model-aware credential resolver) but keep exactly one active `model`.
- A new TOML dependency. Serialization stays hand-written to preserve the 0600 write path.
- Per-provider temperature/max_tokens. Generation params remain global for now.

## Architecture

Three units, each independently testable:

1. **`riftor/providers.py` (new)** — the provider registry, curated defaults, and the
   dynamic `fetch_models()` function. No Textual, no pydantic-config coupling → unit-testable
   with monkeypatched `urllib`.
2. **`riftor/config.py` (extended)** — a nested `[providers]` table + a model-aware
   `creds_for()` resolver. Backward-compatible load/save.
3. **`riftor/tui/config_screen.py` (extended)** — provider Select, model Select (+ free-text
   override Input), Base URL, API key, and a "Fetch models" button.

Wiring lives in `riftor/tui/app.py` (`_open_config`) and `riftor/agent/provider.py`
(`_kwargs` calls `creds_for`).

## 1. `riftor/providers.py`

### `ProviderMeta`
One dataclass entry per provider:

```python
@dataclass(frozen=True)
class ProviderMeta:
    key: str            # "anthropic"
    label: str          # "Anthropic"
    prefix: str         # "anthropic/" (litellm prefix; "" for custom)
    env: str | None     # "ANTHROPIC_API_KEY"
    list_kind: str      # "openai" | "ollama" | "none"
    default_base: str | None
```

Registry (dict keyed by `key`), order = dropdown order:

| key | label | prefix | env | list_kind | default_base |
|-----|-------|--------|-----|-----------|--------------|
| anthropic | Anthropic | `anthropic/` | ANTHROPIC_API_KEY | none | — |
| openai | OpenAI | `openai/` | OPENAI_API_KEY | openai | https://api.openai.com/v1 |
| openrouter | OpenRouter | `openrouter/` | OPENROUTER_API_KEY | openai | https://openrouter.ai/api/v1 |
| gemini | Gemini | `gemini/` | GEMINI_API_KEY | none | — |
| groq | Groq | `groq/` | GROQ_API_KEY | openai | https://api.groq.com/openai/v1 |
| deepseek | DeepSeek | `deepseek/` | DEEPSEEK_API_KEY | openai | https://api.deepseek.com/v1 |
| mistral | Mistral | `mistral/` | MISTRAL_API_KEY | openai | https://api.mistral.ai/v1 |
| ollama | Ollama | `ollama_chat/` | — | ollama | http://localhost:11434 |
| custom | Custom… | `` | — | openai | — |

### `PROVIDER_DEFAULTS`
Curated, best-first, bare ids (no prefix). Researched & verified 2026-06-03.

```python
PROVIDER_DEFAULTS = {
    "anthropic": ["claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"],
    "openai":    ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"],
    "deepseek":  ["deepseek-v4-pro", "deepseek-v4-flash"],
    "openrouter":["openrouter/auto", "anthropic/claude-opus-4.8",
                  "anthropic/claude-sonnet-4.6", "openai/gpt-5.5"],
    "gemini":    ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
    "groq":      ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
                  "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
    "mistral":   ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    "ollama":    [],   # purely dynamic via /api/tags
    "custom":    [],
}
```

Notes baked in from research:
- Anthropic 4.6+ ids are **dateless** (`claude-opus-4-8`, not `…-YYYYMMDD`). Anthropic & Gemini have **no public unauth list endpoint** → `list_kind="none"`, curated only.
- OpenRouter uses **dotted** minor versions (`claude-opus-4.8`) and ids keep their `provider/` slash — passed through, not re-prefixed.
- DeepSeek legacy `deepseek-chat`/`deepseek-reasoner` are being removed after 2026-07-24 → intentionally omitted from defaults.

### `fetch_models(provider_key, api_base, api_key) -> FetchResult`

```python
@dataclass
class FetchResult:
    models: list[str]          # merged, curated-first, deduped
    source: str                # "live" | "curated" | "merged"
    error: str | None = None   # human hint if the live fetch failed
```

- `list_kind == "openai"`: GET `{base or default_base}/models`, header
  `Authorization: Bearer {key}` (omit if no key), parse `data[].id`.
- `list_kind == "ollama"`: GET `{base}/api/tags`, parse `models[].name`.
- `list_kind == "none"`: no network; `FetchResult(curated, "curated")`.
- Any exception (network/auth/timeout/parse) → `FetchResult(curated, "curated", error=<short msg>)`. Never raises.
- Timeout ~4s via `urllib.request`.
- **Merge:** curated favorites first, then live ids with curated removed (stable de-dup). Empty curated (ollama/custom) → live list as-is.

## 2. `riftor/config.py`

### New nested creds

```python
class ProviderCreds(BaseModel):
    api_key: str | None = None
    api_base: str | None = None

class Config(BaseModel):
    ...
    providers: dict[str, ProviderCreds] = {}   # keyed by provider key
```

### Load
TOML nests `[providers.anthropic]` as a sibling of `[riftor]`. `load()` reads the
`riftor` table for scalar fields **and** the top-level `providers` table:

```python
data = tomllib.load(fh)
section = data.get("riftor", data)
providers = data.get("providers", {})        # top-level sibling table
return cls(**section, providers=providers)
```

Old flat configs (no `[providers]`) → `providers={}`, everything else identical → **backward compatible**.

### Save / `_to_toml`
Append, after the existing scalar lines:

```toml
[providers.<key>]
api_key = "..."     # only if set
api_base = "..."    # only if set
```

Empty `providers` → no section emitted (old-style file). 0600 write path unchanged.

### `creds_for(model) -> tuple[str|None, str|None]`
Single source of truth for which key/base to use for a given model. Precedence:

1. Per-provider table entry (matched by the model's prefix → provider key).
2. Legacy global `self.api_key` / `self.api_base` (back-compat).
3. Env var (`provider_env`) for the key; `None` base.
4. `(None, None)`.

`has_credentials()` updated to also consult `providers[...]`. `provider_env`,
`_KNOWN_PROVIDERS`, `model_warning`, `detect_defaults` unchanged in behavior (they may
import the registry from `providers.py` to avoid duplicating the prefix list).

**Forward-compat:** `creds_for(model)` takes the model as an argument, so a future
`models=[...]` list resolves each model's creds with zero changes to this layer.

## 3. `riftor/tui/config_screen.py`

`MODEL` section rebuilt; `GENERATION`/`APPEARANCE` untouched.

```
MODEL
  Provider   [ Anthropic        ▼ ]   #cfg-provider  (Select)
  Model      [ claude-opus-4-8  ▼ ]   #cfg-model-select (Select)
  Custom id  [ ...... ]               #cfg-model     (Input — free-text override; wins if non-empty)
  Base URL   [ ...... ]               #cfg-base      (Input)
  API key    [ ••••• leave blank ]    #cfg-key       (Input password)
             [ Fetch models ]         #cfg-fetch     (Button)
```

- Existing ids `#cfg-model`, `#cfg-key`, `#cfg-temp`, `#cfg-maxtok`, `#cfg-theme`,
  `#cfg-lore` are **preserved** (`#cfg-model` becomes the free-text override Input) so
  current tests keep passing. New ids: `#cfg-provider`, `#cfg-model-select`, `#cfg-base`, `#cfg-fetch`.
- On open: provider is inferred from `config.model`'s prefix; base/key fields show that
  provider's saved values (key shown as "leave blank to keep", never echoed).
- Provider change → prefill `#cfg-base` with `default_base`, repopulate `#cfg-model-select`
  from `PROVIDER_DEFAULTS` (instant, no network), update key placeholder.
- **Fetch models** → threaded `@work(thread=True)` worker calls `fetch_models`; on return,
  repopulate `#cfg-model-select` with `result.models`; if `result.error`, show it via `_fail()`.
- Theme live-preview/revert behavior unchanged.
- **Save** assembles the final model string: `meta.prefix + chosen_bare_id` (chosen =
  free-text `#cfg-model` if non-empty, else `#cfg-model-select`); OpenRouter/custom ids
  pass through unchanged. Result dict gains `provider`, `api_base`; keeps `model`,
  `api_key?`, `temperature`, `max_tokens`, `theme`, `lore`.

## 4. Wiring

- **`app.py::_open_config`**: on save, write `api_key`/`api_base` into
  `config.providers[provider]`, set `config.model`, rebuild `Provider`, update status bar.
- **`agent/provider.py::_kwargs`**: replace direct `config.api_key`/`config.api_base`
  reads with `key, base = config.creds_for(config.model)`.
- **`__main__.py`**: `--api-key` still sets the legacy global key (honored as resolver
  fallback) → no CLI break. No other changes required.

## Testing (TDD — tests written first)

- **`tests/test_providers.py` (new):**
  - openai `/models` parse via monkeypatched `urlopen`; ollama `/api/tags` parse.
  - merge pins curated first and de-dupes; `list_kind="none"` returns curated, no network.
  - network/auth error → curated + `error` set, never raises.
  - prefix assembly (anthropic prepend; openrouter/custom pass-through).
- **`tests/test_config.py`:** `[providers]` round-trips save→load; `creds_for` precedence
  (provider table → legacy global → env → none); old flat config loads; 0600 preserved.
- **`tests/test_config_screen.py`:** existing assertions still pass; provider pick prefills
  base + repopulates models; Save assembles prefixed model string; per-provider key written.

## Docs

Update `docs/configuration.md`: document the `[providers]` table, the provider/model
picker, the Fetch-models button, and which providers support live fetch vs curated-only.

## Backward-compatibility summary

- Old flat `config.toml` loads unchanged (`providers={}`).
- Legacy global `api_key`/`api_base` still honored via `creds_for` fallback.
- `--api-key` / `--model` CLI flags unchanged.
- All existing config-screen widget ids preserved → existing tests unaffected.
