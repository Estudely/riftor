# Codex (ChatGPT subscription) login — design

**Date:** 2026-06-08
**Status:** Approved for planning

## Problem

riftor authenticates to every model provider with an API key (per-provider creds in
`config.toml`, or an env var). Users who have a ChatGPT Plus/Pro/Team subscription can
run inference through that subscription instead of paying for API credits — this is how
OpenAI's official Codex CLI works. We want riftor users to use their Codex/ChatGPT
subscription from inside riftor.

## How Codex login actually works (background)

OpenAI's Codex CLI does **not** use an API key for subscription users. `codex login`
performs an OAuth 2.0 + PKCE browser flow against `auth.openai.com` (loopback callback on
`localhost:1455`) and writes tokens to `~/.codex/auth.json`. Inference then goes to an
**undocumented** endpoint, `https://chatgpt.com/backend-api/codex/responses`, using the
**Responses API** shape (not Chat Completions), with special headers
(`Authorization: Bearer <jwt>`, `chatgpt-account-id`, `originator: codex_cli_rs`,
`OpenAI-Beta: responses=experimental`), `store=false`, stripped token-limit fields, and a
server-side check that the request's `instructions` field matches the canonical Codex
system prompt. Tokens refresh against `auth.openai.com/oauth/token`.

This is meaningfully different from riftor's Chat-Completions-based providers, and the
endpoint is unstable. Rather than reimplement any of it, we **reuse the official CLI's
token file** and delegate the endpoint machinery to a maintained litellm custom provider.

`auth.json` structure (relevant fields):
```jsonc
{
  "tokens": {
    "access_token": "<JWT>",      // Bearer used for inference; exp lives in the JWT
    "refresh_token": "<...>",
    "id_token": "<JWT>",          // chatgpt_account_id etc. in the "auth" claim
    "account_id": "<...>"
  },
  "last_refresh": "<RFC3339>",
  "OPENAI_API_KEY": null
}
```

## Decisions (locked during brainstorming)

1. **Token source:** reuse `~/.codex/auth.json` written by the official `codex login`.
   riftor never runs the OAuth flow and never writes this file.
2. **Inference path:** delegate to the `litellm-codex-oauth-provider` package (the
   `codex/` custom provider), which reads `~/.codex/auth.json` and bridges
   Chat Completions → the Codex Responses backend.
3. **UX:** first-class **Codex (ChatGPT)** provider in the `/config` picker, with a live
   login-status line. riftor does **not** shell out to `codex login` — it points the user
   to run it themselves.
4. **Dependency:** `litellm-codex-oauth-provider` is a **hard runtime dependency**.

## Architecture

riftor exposes Codex as just another litellm model id (`codex/gpt-5.5-codex`). The agent
loop, streaming, and tool-call handling are unchanged. Two integration points:

- **Registration:** in `_get_litellm()` (`agent/provider.py`), after configuring litellm,
  set `litellm.custom_provider_map` to register the `codex` handler exactly once. This
  sits behind the existing lazy-litellm import, so there is no startup penalty, and every
  caller (TUI, headless, workers) gets Codex registered automatically.
- **Credentials:** Codex supplies no api_key/api_base from riftor — the custom handler
  reads `~/.codex/auth.json` itself. `creds_for("codex/…")` returns `(None, None)`.

## Components

### `riftor/providers.py`

- Add to `PROVIDERS`:
  ```python
  "codex": ProviderMeta("codex", "Codex (ChatGPT)", "codex/", None, "codex", None),
  ```
  `env=None`, `list_kind="codex"` (new kind), `default_base=None`.
- Add `PROVIDER_DEFAULTS["codex"]`: curated bare ids
  (`["gpt-5.5-codex", "gpt-5.5", "gpt-5.4-codex"]` — exact ids verified against the package
  at build time).
- `fetch_models`: `list_kind == "codex"` returns the curated list only (`source="curated"`),
  never touching the network — the Codex backend exposes no public `/models` list.
- `apply_prefix("codex", id)`: existing logic already prepends `codex/` to bare ids and
  passes through already-prefixed ids; works unchanged.

### `riftor/config.py`

- `creds_for(model)`: add an explicit early return for the `codex` provider key →
  `(None, None)`, so a stale global `api_key` is never forwarded to the Codex handler.
- `has_credentials()`: treat `codex/` like Ollama — return `True` based on provider type
  (don't block the UI on it). Real login validity is surfaced as status, not a hard gate.
- `provider_env()`: returns `None` for Codex (already handled by the `meta.env` check).

### `riftor/codex_auth.py` (new)

Read-only helper; never writes `auth.json`, never raises.

```python
def codex_home() -> Path            # $CODEX_HOME or ~/.codex
def auth_status() -> CodexAuthStatus
```

`CodexAuthStatus` dataclass: `logged_in: bool`, `expires_in_s: int | None`, `detail: str`.

Logic:
- File missing → `logged_in=False`, `detail="run `codex login`"`.
- Present → parse `tokens.access_token` as a JWT, base64-decode the **payload** segment
  (no signature verification — display only), read `exp` → `expires_in_s`.
- Decode/parse failure → `logged_in=True`, `expires_in_s=None`, `detail="token present"`.

No new dependency: payload decode is `base64` + `json` on the middle JWT segment.

### `riftor/tui/config_screen.py`

- When the selected provider is `codex` (`on_select_changed`, `cfg-provider` branch):
  hide the `#cfg-key` (API key) and `#cfg-base` (Base URL) rows and the `#cfg-fetch`
  button (toggle a `hidden` class, the pattern `show_section` already uses), and show a
  dedicated `#cfg-codex-status` Label populated from `codex_auth.auth_status()`
  (e.g. `✓ ChatGPT login active (expires in 42m)` or `⚠ not logged in — run \`codex login\``).
  Restore those rows when switching to a non-Codex provider.
- Model dropdown is populated from `PROVIDER_DEFAULTS["codex"]` like any other provider;
  the custom-id Input still works.
- Save path (`on_button_pressed`): unchanged in shape — `apply_prefix("codex", chosen)`
  builds the id; no `api_key`/`api_base` are written for Codex.

### `riftor/tui/app.py`

- Add Codex to the per-provider context-window estimate → 128k (matches the OpenAI/GPT-5
  default). Stage, scope, findings, yolo display unchanged.

### `riftor/engagement/doctor.py` + `--doctor`

- Add a Codex line to the doctor report: whether `~/.codex/auth.json` is present and its
  login status (reusing `codex_auth.auth_status()`).

### `pyproject.toml` / `uv.lock`

- Add `litellm-codex-oauth-provider` to runtime dependencies; relock `uv.lock`.

### `docs/configuration.md`

- Add a "Codex / ChatGPT subscription login" section: the `codex login` prerequisite,
  the `codex/<model>` id format, and that billing goes to the ChatGPT subscription.

## Data flow

```
user selects "Codex (ChatGPT)" in /config, picks codex/gpt-5.5-codex
  → config.model = "codex/gpt-5.5-codex"; no api_key stored
  → first model call → _get_litellm() registers litellm.custom_provider_map (once)
  → Provider._kwargs(): creds_for() → (None, None); no api_key/api_base set
  → litellm.acompletion(model="codex/...") routes to the codex custom handler
  → handler reads ~/.codex/auth.json, refreshes if near expiry, calls the
    Codex Responses backend, bridges the response back to Chat-Completions shape
  → riftor's stream_turn() consumes deltas + tool calls exactly as today
```

## Error handling

- **Not logged in / no `auth.json`:** the model call fails inside the custom handler; the
  resulting exception flows through `classify_error()` → an `auth`-kind `ProviderError`
  ("authentication failed…"). `/config` and `--doctor` proactively show the not-logged-in
  state so the user fixes it before a call. `auth_status()` itself never raises.
- **Expired token / refresh failure:** handled by the package (proactive refresh ~5 min
  before expiry); a hard failure surfaces as a classified `ProviderError`.
- **Undocumented endpoint breaks (instructions-prompt/header drift):** surfaces as a
  `validation` or `unknown` `ProviderError` from `classify_error()`. This risk is owned by
  the package, not riftor; documented as a known caveat.
- **Bad config ethos preserved:** missing/malformed `auth.json`, a missing `$CODEX_HOME`,
  or a failed `custom_provider_map` registration must never crash startup — they degrade to
  a not-logged-in status, consistent with riftor's load-time degradation everywhere else.

## Testing

Offline-first, matching the existing suite (`RIFTOR_DEMO_RESPONSE`, no network, no real
`~/.codex`).

**Unit:**
- `tests/test_providers.py` (extend): `codex` in `PROVIDERS`;
  `provider_key_for_model("codex/gpt-5.5-codex") == "codex"`;
  `apply_prefix("codex", "gpt-5.5-codex") == "codex/gpt-5.5-codex"` + pass-through for
  already-prefixed ids; `fetch_models("codex", …)` returns curated only, no network.
- `tests/test_config.py` (extend): `creds_for("codex/…") == (None, None)`;
  `has_credentials()` is `True` for a `codex/` model; `provider_env()` is `None` for Codex.
- `tests/test_codex_auth.py` (new): with `CODEX_HOME` pointed at `tmp_path` —
  (a) missing file → `logged_in=False`;
  (b) `auth.json` with a JWT whose `exp` is in the future → `logged_in=True`,
      positive `expires_in_s`;
  (c) malformed JSON / garbage JWT → degrades, never raises.

**Smoke (`dev/smoke.py`):** add a step that sets the model to `codex/gpt-5.5-codex`, drives
a turn with `RIFTOR_DEMO_RESPONSE` set, and asserts the loop runs without crashing —
proving `custom_provider_map` registration is harmless when mocked and does not require the
token file to exist.

**Manual (documented, not in CI):** with a real `codex login`, run
`uv run riftor --model codex/gpt-5.5-codex -p "say hi"` and confirm a live response and a
token refresh. This is the only step exercising the undocumented endpoint; CI stays offline.

**CI gates unchanged:** `make check` (ruff → pyright → pytest → smoke) stays green.

## Out of scope / YAGNI

- No OAuth/PKCE flow inside riftor (we reuse the official CLI's token file).
- No `codex login` shell-out or in-riftor login command.
- No multi-account / token load-balancing.
- No native Responses-API support in `provider.py` (the package bridges to Chat Completions).
- No new CLI flag (so bash/zsh completions need no changes).
