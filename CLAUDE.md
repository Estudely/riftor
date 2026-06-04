# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

All commands use `uv` (Astral's Python package manager). Install dev dependencies once: `uv sync --extra dev`.

| Task | Command |
|------|---------|
| Launch TUI | `uv run riftor` |
| Lint | `uv run ruff check riftor dev tests` |
| Type check | `uv run pyright riftor` |
| Unit tests | `uv run pytest` |
| Single test | `uv run pytest tests/test_tools.py::test_function_name` |
| Smoke test | `uv run python dev/smoke.py` |
| All CI gates | `make check` (runs lint → typecheck → test → smoke) |
| Build | `uv build` |
| Pre-commit hooks | `make install-hooks` or `uv run pre-commit install` |

CI runs lint, type check, unit tests, and smoke on Python 3.11 + 3.12. All tests run **offline** — no API key or model needed.

- **Offline by design.** The provider checks `RIFTOR_DEMO_RESPONSE` first and, if set, yields that canned text instead of calling litellm (`agent/provider.py`) — this is how the suite and smoke test avoid the network. `tests/conftest.py` supplies the shared fixtures `tmp_workdir`, `engagement`, and `toolctx`. `pytest` runs in `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed).
- `dev/smoke.py` drives the *real* Textual app headlessly end-to-end and cancels any live stream, so it exercises the UI without a model. Extend it when adding TUI behavior.

## Architecture

riftor is a Python 3.11+ TUI pentest assistant: a Textual full-screen app backed by litellm, organized around the **RIFT** methodology (Recon → Intrusion → Foothold → Takeover).

### Entry and dispatch (`riftor/__main__.py`)

CLI arg parsing → loads config → dispatches:
- `--config`: prints config path and exits
- `--doctor`: checks installed recon tools on `PATH` and exits
- `--prompt`/`-p` (a.k.a. `--headless`): runs `headless.py` (one-shot, non-interactive; also reads stdin)
- default: launches `tui/app.py` (the full Textual app)

Other flags apply to either path: `--version`, `--model`, `--api-key`, `--workdir`, `--scope-file`, and `--i-know-what-i-am-doing-give-me-full-access` (stored as `yolo`; see below).

### Agent loop (shared by TUI and headless)

The core loop lives in both `tui/app.py` and `headless.py`. The skeleton is the same; the **gating differs by mode** (see "TUI vs headless" below). Each iteration:

1. User input → added to `Context` (conversation history)
2. `Context.repair()` runs first — it ensures every assistant `tool_call` has a following tool result, inserting a synthetic `[interrupted: …]` result for any orphaned id (this is what keeps a cancelled/crashed turn replayable)
3. `Provider.stream_turn(messages, tools)` streams `("text", delta)` chunks as the model talks, then yields a final `("done", Turn)`. **Tool calls are buffered during streaming and only appear on the final `Turn`** — they are reassembled by `index` from the chunk fragments, not streamed live.
4. For each tool call: scope-sensitive tools are checked against the engagement scope; dangerous tools (`requires_permission`) go through the `Permissions` engine
5. Tool results → context; loop continues up to `max_steps` (default **16**, `config.py`) or until a turn has no tool calls

### Sub-packages

- **`riftor/agent/`** — LLM abstraction. `provider.py` wraps litellm (streaming, tool calling, retry/backoff with deterministic jitter, `classify_error()` mapping exceptions to a typed `ProviderError`). **litellm is lazily imported** on first model call (~2.4s — an estimate in a comment, not a measured constant) to keep startup fast. `context.py` builds the system prompt from `agent/prompts/system.md` (loaded via `importlib.resources`) and appends `LORE_PREAMBLE` when `lore=True`; it also handles `compact()` (clip old tool results to ~400 chars, keep the last ~8 messages) and the `repair()` above. `session.py` persists sessions as JSON via tmp-file + `Path.replace()`; the `complete` flag marks mid-turn checkpoints so a crashed run can be detected and resumed.
- **`riftor/providers.py`** — provider registry feeding config and the model picker. Holds the `PROVIDERS` table and `PROVIDER_DEFAULTS` (curated model lists), resolves a provider from a model id (`provider_key_for_model`), and `fetch_models()` queries the live model list but **degrades to the curated defaults on any network/auth failure**. Edit this when touching model selection or credential resolution.
- **`riftor/tui/`** — Textual app. `app.py` is the agent loop + all slash commands (in `_COMMANDS`/handler dict) + scope enforcement + permission modals + per-step session checkpointing, context-window monitoring (prompts `/compact` near the limit), and an optional `rate_limit_per_min` gate. `theme.py` defines **7 themes** (`rift` default, `dusk`, `void`, `fracture`, `singularity`, plus the light `dawn`/`paper`) via a `_PALETTES` dict → Textual CSS variables. `widgets.py` holds the `StatusBar` (renders the `[R·I·F·T]` stage, scope/findings/token/cost/context gauges, model, yolo flag), `Banner`, and `CommandDropdown` (slash-command autocomplete: prefix match then `difflib` fuzzy fallback). `config_screen.py` is the runtime settings modal (model/provider/temperature/max_tokens/theme/lore) with live theme preview and provider model discovery.
- **`riftor/tools/`** — Agent-callable tool system. **All tools must be registered in the `ALL_TOOLS` list in `tools/__init__.py`** — its order is safe→mutating and is also the order shown to the model, and it **interleaves core and engagement tools** (read-only engagement tools like `ScopeListTool`/`ListHostsTool` come before mutating core tools like `WriteTool`/`BashTool`). Each tool extends `Tool` (ABC in `base.py`) and returns a `ToolResult`. A tool declares its policy via class flags: `requires_permission`, `danger`, `scope_sensitive` — these apply to engagement tools too (e.g. `AddScopeTool` requires permission), not just bash/write/edit. Tools are UI-agnostic — they return data; the app renders it. `core.py` has bash/read/write/edit/grep/glob/webfetch; `engagement.py` has the RIFT-specific tools (set-stage, scope, record service/finding, edit/delete finding, import-scan, generate-report).
- **`riftor/engagement/`** — RIFT methodology engine. `scope.py` does IP/CIDR/domain/wildcard matching (`Target.parse`/`Target.matches`). `state.py` is the SQLite-backed persistence layer (tables: `meta`, `scope`, `hosts`, `services`, `findings`, `activity`; findings gained `cvss`/`tags`/`notes` via an in-place column migration). `cvss.py` is pure-Python CVSS v3.1 base scoring (only `math`). `report.py` renders md/html/json/SARIF (`both` = md+html, `all` = all four). `parsers.py` turns raw recon output (nmap normal/greppable, httpx, nuclei — bracketed or JSONL) into a `ParsedScan` of services+findings; the `ImportScanTool` feeds it into state with skip/merge/allow-all dedup. `doctor.py` reports which external recon tools (nmap, httpx, nuclei, ffuf, gobuster, nikto, sqlmap, subfinder, dig, whatweb, curl, rg) are on `PATH`.
- **`riftor/safety/`** — Trust and safety. `permissions.py` resolves a call by precedence: **deny rules (incl. session-denied) → allow rules / session-grants → operator prompt**. Five default deny patterns guard `rm -rf`, `dd of=/dev/…`, `mkfs`, fork bombs, and writes to block devices (`> /dev/sda…`). Rules live in `~/.config/riftor/permissions.toml` (honoring `XDG_CONFIG_HOME`). `audit.py` provides JSONL audit logging with gzip rotation.

### Key design conventions

- **Bad config never crashes.** Malformed config or permission files degrade silently to defaults on load — the app always starts (`Config.load()`, `Permissions.load()`, scope-file preloading, and `providers.fetch_models()`).
- **Tools are independent of the UI.** `Tool.execute()` returns a `ToolResult`; both the TUI and headless mode call the same execute methods and just render results differently.
- **TUI vs headless gating differ — this is the main place the two loops diverge.** In the **TUI**, an out-of-scope or dangerous call raises an *interactive operator prompt* (once / session / always-allow / deny), so the operator can override per call; `scope dry-run` warns without blocking. In **headless** mode there is no operator: dangerous tools auto-deny unless an explicit allow rule exists in `permissions.toml`, and out-of-scope scope-sensitive calls are **always hard-blocked with no override**. Only the TUI does per-step session checkpointing, context-window monitoring, and rate-limiting.
- **YOLO mode bypasses every guardrail.** `--i-know-what-i-am-doing-give-me-full-access` sets `yolo=True`, which short-circuits the permission engine, scope enforcement, and the step limit (gated by `if not self.yolo` / `if not yolo` throughout `app.py` and `headless.py`). Preserve those guards when editing the loop — a regression here silently disarms all safety.
- **Engagement state is per-workdir.** Each working directory gets its own `.riftor/` with an `engagement.db` (SQLite), `sessions/`, and `reports/`. Sessions auto-save and resume.
- **Context windows vary by provider.** The status bar shows a context-window gauge using per-provider estimates (`app.py`): Anthropic 200k, OpenAI/OpenRouter/Groq 128k, Gemini 1M, Ollama 8k, default 128k.
- **`uv.lock` is committed** for reproducible installs. Dependencies are pinned.

### Ancillary

- `completions/` ships bash (`riftor.bash`) and zsh (`_riftor`) completions — **update them when you add or rename a CLI flag.**
- `docs/` holds `configuration.md` and the `riftor.1` man page; `todo.md` is the roadmap.
