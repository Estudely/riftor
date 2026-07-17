# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product snapshot

- **Version:** 3.3.0 (PyPI + GitHub Release)
- **Website:** https://riftor.dev (source: https://github.com/Estudely/riftor-website — separate repo)
- **Roadmap:** `todo.md` (Phase 8 launch done; housekeeping + post-launch feature MVPs shipped in v3.3.0)
- **Config reference:** `docs/configuration.md` (canonical; site "Docs" links here)
- **Website stays separate** — do not fold `riftor-website` into this repo as a branch

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

Optional extras: `uv sync --extra browser` (Playwright for `browser_*` tools), `--extra browser-ui` (inline screenshots on Python 3.12+), `--extra mcp` (stdio MCP client), `--extra dev`.

CI runs lint, type check, unit tests, and smoke on Python 3.11 + 3.12. All tests run **offline** — no API key or model needed.

- **Offline by design.** The provider checks `RIFTOR_DEMO_RESPONSE` first and, if set, **short-circuits before any litellm call** (`Provider._demo_response` in `agent/provider.py`) — do not use litellm's `mock_response` (litellm 1.92+ imports fastapi when tools are present). This is how the suite and smoke test avoid the network. `tests/conftest.py` supplies the shared fixtures `tmp_workdir`, `engagement`, and `toolctx`. `pytest` runs in `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed).
- `dev/smoke.py` drives the *real* Textual app headlessly end-to-end and cancels any live stream, so it exercises the UI without a model. Extend it when adding TUI behavior.

## Architecture

riftor is a Python 3.11+ TUI pentest assistant: a Textual full-screen app backed by litellm, organized around the **RIFT** methodology (Recon → Intrusion → Foothold → Takeover). Current release ships **36** agent tools, **350+** bundled methodology skills, Baaj/Chakla subagents, optional browser automation + MCP client, plugins, memory/lessons/hypotheses, bounty-scope import, attack graphs, session branching, and hard scope + permission gates.

### Entry and dispatch (`riftor/__main__.py`)

CLI arg parsing → loads config → dispatches:
- `--config`: prints config path and exits
- `--doctor`: checks installed recon tools on `PATH` and exits
- `--prompt`/`-p` (a.k.a. `--headless`): runs `headless.py` (one-shot, non-interactive; also reads stdin)
- default: launches `tui/app.py` (the full Textual app)

Other flags: `--version`, `--model`, `--api-key`, `--workdir`, `--scope-file`, `--chakla-model`, `--browser-headed`, and `--i-know-what-i-am-doing-give-me-full-access` (stored as `yolo`; see below).

### Agent loop (shared by TUI and headless)

The core loop lives in both `tui/app.py` and `headless.py`. The skeleton is the same; the **gating differs by mode** (see "TUI vs headless" below). Each iteration:

1. User input → added to `Context` (conversation history)
2. `Context.repair()` runs first — it ensures every assistant `tool_call` has a following tool result, inserting a synthetic `[interrupted: …]` result for any orphaned id (this is what keeps a cancelled/crashed turn replayable)
3. `Provider.stream_turn(messages, tools)` streams `("text", delta)` chunks as the model talks, then yields a final `("done", Turn)`. **Tool calls are buffered during streaming and only appear on the final `Turn`** — they are reassembled by `index` from the chunk fragments, not streamed live.
4. For each tool call: **anti-loop** (`agent/antiloop.py`) can warn/stop on repeated identical calls; scope-sensitive tools are checked against the engagement scope; dangerous tools (`requires_permission`) go through the `Permissions` engine
5. Tool results → context; loop continues up to `max_steps` (default **16**, `config.py`) or until a turn has no tool calls
6. After each tool-using round, the **barren-round circuit breaker** (`agent/circuit.py`) stops the TUI run if there is no new engagement progress for several rounds. Progress = **hosts + services + findings** (not findings alone — issue #104). `/continue [N]` raises the live session `max_steps` and the barren ceiling so operators are not forced to re-extend every few rounds

### Sub-packages

- **`riftor/agent/`** — LLM abstraction. `provider.py` wraps litellm (streaming, tool calling, retry/backoff with deterministic jitter, `classify_error()` mapping exceptions to a typed `ProviderError`, optional cost estimate when the provider omits `.cost`). **litellm is lazily imported** on first model call (~2.4s — an estimate in a comment, not a measured constant) under a reentrant lock (issue #123). Demo mode short-circuits before litellm when `RIFTOR_DEMO_RESPONSE` is set. `context.py` builds the system prompt from `agent/prompts/system.md` (loaded via `importlib.resources`) and appends `LORE_PREAMBLE` when `lore=True` (and genz persona when `genz=True`); it also handles `compact()` (clip old tool results to ~400 chars, keep the last ~8 messages) and the `repair()` above. `session.py` persists sessions as JSON via tmp-file + `Path.replace()`; the `complete` flag marks mid-turn checkpoints so a crashed run can be detected and resumed; also backs `/branch` (message fork) and `/rollback` (truncate history). `subagent.py` is the Chakla worker loop (isolated Context, `lore=False`, headless-style gating, shared engagement DB under a lock). `antiloop.py` classifies repeated tool-call signatures. `circuit.py` is the barren-round / progress breaker. `custom_route.py` + `codex_provider.py` handle litellm registry-collision route markers and the optional `codex/` custom provider.
- **`riftor/providers.py`** — provider registry feeding config and the model picker. Holds the `PROVIDERS` table and `PROVIDER_DEFAULTS` (curated model lists), resolves a provider from a model id (`provider_key_for_model`), and `fetch_models()` queries the live model list but **degrades to the curated defaults on any network/auth failure**. Edit this when touching model selection or credential resolution.
- **`riftor/tui/`** — Textual app. `app.py` is the agent loop + all slash commands (in `_COMMANDS`/handler dict) + scope enforcement + permission modals + per-step session checkpointing, context-window monitoring (prompts `/compact` near the limit), optional `rate_limit_per_min` gate, Chakla "flock" progress UI, `!` shell shortcut + CWD header. Notable commands: `/scope bounty`, `/graph`, `/merge`, `/branch`, `/rollback`, `/continue`. `theme.py` defines **7 themes** (`rift` default, `dusk`, `void`, `fracture`, `singularity`, plus the light `dawn`/`paper`) via a `_PALETTES` dict → Textual CSS variables. `widgets.py` holds the `StatusBar` (renders the `[R·I·F·T]` stage, scope/findings/token/cost/context gauges, model, yolo / worker-cost segments), `Banner`, and `CommandDropdown` (slash-command autocomplete: prefix match then `difflib` fuzzy fallback). `config_screen.py` is the runtime settings modal (model/provider/temperature/max_tokens/theme/lore + WORKERS/Chakla section) with live theme preview and provider model discovery. `screenshot_gallery.py` backs `/screenshots`.
- **`riftor/tools/`** — Agent-callable tool system (**36** built-ins). **All tools must be registered in the `ALL_TOOLS` list in `tools/__init__.py`** — its order is safe→mutating and is also the order shown to the model, and it **interleaves core and engagement tools** (read-only engagement tools like `ScopeListTool`/`ListHostsTool`/`WordlistTool` come before mutating core tools like `WriteTool`/`BashTool`). Each tool extends `Tool` (ABC in `base.py`) and returns a `ToolResult`. A tool declares its policy via class flags: `requires_permission`, `danger`, `scope_sensitive` — these apply to engagement tools too (e.g. `AddScopeTool` requires permission), not just bash/write/edit. Tools are UI-agnostic — they return data; the app renders it.
  - `core.py` — bash / read / write / edit / grep / glob / webfetch (+ `run_shell` for the `!` shortcut)
  - `engagement.py` — scope, stage, record/edit/delete finding & service, import_scan, generate_report, wordlist, load_skill, hypotheses, lessons, remember, `import_bounty_scope`, `merge_engagement`
  - `browser.py` — `browser_*` tools (optional `riftor[browser]` / Playwright; degrade with an install hint when absent)
  - `subagent.py` — `DispatchChaklaTool` (`dispatch_chakla`)
  - Plugins append more tools at startup via `register_plugins()` (see `plugins.py`); MCP servers append tools via `riftor/mcp.py` when `riftor[mcp]` + `[[mcp_servers]]` are configured
- **`riftor/engagement/`** — RIFT methodology engine. `scope.py` does IP/CIDR/domain/wildcard matching (`Target.parse`/`Target.matches`). `state.py` is the SQLite-backed persistence layer (tables: `meta`, `scope`, `hosts`, `services`, `findings`, `activity`, `hypotheses`; findings have `cvss`/`tags`/`notes`/`confidence`/`verification_method` via in-place column migration). `cvss.py` is pure-Python CVSS v3.1 base scoring (only `math`). `report.py` renders md/html/json/SARIF (`both` = md+html, `all` = all four). `parsers.py` turns raw recon output (nmap normal/greppable, httpx, nuclei — bracketed or JSONL) into a `ParsedScan` of services+findings; the `ImportScanTool` feeds it into state with skip/merge/allow-all dedup. `doctor.py` reports which external recon tools (nmap, httpx, nuclei, ffuf, gobuster, nikto, sqlmap, subfinder, dig, whatweb, curl, rg) are on `PATH`. Also: `lessons.py` (cross-session lessons), `memory.py` (per-engagement notes), `templates.py` (engagement playbooks), `wordlists.py` (local wordlist discovery), `bounty_scope.py` (HackerOne / generic bounty import), `merge.py` (collaborative `engagement.db` merge), `graph.py` (kill-chain Mermaid `/graph`).
- **`riftor/skills/`** — Bundled methodology skill markdown (350+). Loaded by the `load_skill` tool / `/skills` command; not operator-editable in-tree (operator skills can also live under XDG config — see `docs/configuration.md`).
- **`riftor/mcp.py`** — Optional stdio MCP client (`riftor[mcp]`); discovers remote tools at startup and registers them alongside builtins. Configured via `[[mcp_servers]]` in config.toml. Bad servers warn + skip (never fatal).
- **`riftor/plugins.py`** — Operator plugins: drop a `.py` (or package) exporting `TOOLS: list[Tool]` into `~/.config/riftor/plugins/`. Discovered at TUI/headless startup; bad plugins warn + skip (never fatal). Config: `plugins_enabled` / `plugins_allow` / `plugins_deny`.
- **`riftor/terminology.py`** — Renameable Baaj/Chakla labels (`label_main` / `label_worker`).
- **`riftor/safety/`** — Trust and safety. `permissions.py` resolves a call by precedence: **deny rules (incl. session-denied) → allow rules / session-grants → operator prompt**. Five default deny patterns guard `rm -rf`, `dd of=/dev/…`, `mkfs`, fork bombs, and writes to block devices (`> /dev/sda…`). Rules live in `~/.config/riftor/permissions.toml` (honoring `XDG_CONFIG_HOME`). `audit.py` provides JSONL audit logging with gzip rotation.

### Key design conventions

- **Bad config never crashes.** Malformed config or permission files degrade silently to defaults on load — the app always starts (`Config.load()`, `Permissions.load()`, scope-file preloading, plugin load, and `providers.fetch_models()`).
- **Tools are independent of the UI.** `Tool.execute()` returns a `ToolResult`; both the TUI and headless mode call the same execute methods and just render results differently.
- **TUI vs headless gating differ — this is the main place the two loops diverge.** In the **TUI**, an out-of-scope or dangerous call raises an *interactive operator prompt* (once / session / always-allow / deny), so the operator can override per call; `scope dry-run` warns without blocking. In **headless** mode there is no operator: dangerous tools auto-deny unless an explicit allow rule exists in `permissions.toml`, and out-of-scope scope-sensitive calls are **always hard-blocked with no override**. Chakla workers use the same headless-style gating. Both TUI and headless do per-step session checkpointing (issue #120). Only the TUI does context-window monitoring, rate-limiting, the barren-round circuit breaker, and the live flock panel.
- **YOLO mode bypasses every guardrail.** `--i-know-what-i-am-doing-give-me-full-access` sets `yolo=True`, which short-circuits the permission engine, scope enforcement, and the step limit (gated by `if not self.yolo` / `if not yolo` throughout `app.py` and `headless.py`). Preserve those guards when editing the loop — a regression here silently disarms all safety.
- **Engagement state is per-workdir.** Each working directory gets its own `.riftor/` with an `engagement.db` (SQLite), `sessions/`, `reports/`, and optionally `browser-profile/`. Sessions auto-save and resume.
- **Context windows vary by provider.** The status bar shows a context-window gauge using per-provider estimates (`app.py`): Anthropic 200k, OpenAI/OpenRouter/Groq 128k, Gemini 1M, Ollama 8k, default 128k.
- **Telemetry is intentionally not shipped.** Do not add analytics/phone-home. The only `telemetry` reference is `litellm.telemetry = False`. Historical design note (dropped): `docs/superpowers/specs/2026-06-10-telemetry-design.md` (gitignored under `docs/superpowers/`).
- **`uv.lock` is committed** for reproducible installs. Dependencies are pinned.

### Releases

`pyproject.toml` is the version source of truth. Flow (see `CONTRIBUTING.md`):
1. `uv version X.Y.Z` (bumps `pyproject.toml` + `uv.lock`)
2. PR → merge to `main`
3. `git tag vX.Y.Z && git push origin vX.Y.Z` on the merged commit
4. `.github/workflows/release.yml` runs CI, publishes to PyPI (trusted publishing), creates the GitHub Release

Do **not** tag before the bump is on `main`.

### Ancillary

- `completions/` ships bash (`riftor.bash`) and zsh (`_riftor`) completions — **update them when you add or rename a CLI flag.**
- `docs/` holds `configuration.md`, the `riftor.1` man page, and release notes (e.g. `RELEASE_NOTES_v3.3.0.md`); `todo.md` is the roadmap.
- Website is **not** built from this repo — edit https://github.com/Estudely/riftor-website for https://riftor.dev.
