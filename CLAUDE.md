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

## Architecture

riftor is a Python 3.11+ TUI pentest assistant: a Textual full-screen app backed by litellm, organized around the **RIFT** methodology (Recon → Intrusion → Foothold → Takeover).

### Entry and dispatch (`riftor/__main__.py`)

CLI arg parsing → loads config → dispatches to one of three paths:
- `--config`: prints config path and exits
- `--doctor`: checks installed recon tools and exits
- `--prompt` / `--headless`: runs `headless.py` (one-shot, non-interactive)
- default: launches `tui/app.py` (the full Textual app)

### Agent loop (shared by TUI and headless)

The core loop lives in both `tui/app.py` and `headless.py` — they share the same pattern:

1. User input → added to `Context` (conversation history)
2. `Provider.stream_turn(messages, tool_schemas)` — streams text deltas via litellm, yields a `Turn` with optional tool calls
3. If the turn has tool calls, execute each one via the tool registry (`tools/__init__.py`)
4. Dangerous tools (bash/write/edit) go through the `Permissions` engine for approval; scope-sensitive tools are checked against the engagement scope
5. Tool results are added to context; loop continues up to `max_steps` iterations or until no tool calls remain

### Sub-packages

- **`riftor/agent/`** — LLM abstraction. `provider.py` wraps litellm (streaming, tool calling, retry/backoff, error classification). litellm is **lazily imported** (~2.4s) on first model call to keep startup fast. `context.py` manages conversation history with compaction and repair. `session.py` persists sessions as JSON with crash-safe atomic writes.
- **`riftor/tui/`** — Textual app. `app.py` contains the agent loop, all slash commands, scope enforcement, and permission modals. `theme.py` defines 6 themes (rift/dusk/void/fracture/singularity/dawn/paper) using Textual CSS variables.
- **`riftor/tools/`** — Agent-callable tool system. **All tools must be registered in `tools/__init__.py`** in the `ALL_TOOLS` list (order is safe→mutating, which is also the order shown to the model). Each tool extends `Tool` (ABC in `base.py`) and returns `ToolResult`. Tools are UI-agnostic — they return data; the app renders it. `core.py` has bash/read/write/edit/grep/glob/webfetch. `engagement.py` has RIFT-specific tools.
- **`riftor/engagement/`** — RIFT methodology engine. `scope.py` does IP/CIDR/domain/wildcard matching for scope enforcement. `state.py` is the SQLite-backed persistence layer (scope, hosts, services, findings, activity log). `cvss.py` is pure-Python CVSS v3.1 scoring (no deps). `report.py` renders reports in md/html/json/SARIF.
- **`riftor/safety/`** — Trust and safety. `permissions.py` implements layered permissions (deny rules → allow rules → session grants → operator prompt). Default deny rules guard against destructive bash commands (`rm -rf`, `dd of=/dev/…`, `mkfs`, fork bombs). `audit.py` provides JSONL audit logging with gzip rotation.

### Key design conventions

- **Bad config never crashes.** Malformed config or permission files degrade silently to defaults on load — the app always starts. This pattern appears in `Config.load()`, `Permissions.load()`, and scope-file preloading.
- **Tools are independent of the UI.** `Tool.execute()` returns a `ToolResult`; both the TUI and headless mode call the same execute methods and just render results differently.
- **Headless mode is restrictive.** Dangerous tools (bash/write/edit) auto-deny in headless mode unless an explicit allow rule exists in `permissions.toml` (no interactive operator to approve). Out-of-scope calls are always blocked.
- **Engagement state is per-workdir.** Each working directory gets its own `.riftor/` directory with an `engagement.db` (SQLite), `sessions/`, and `reports/`. Sessions auto-save and resume.
- **Context windows vary by provider.** The status bar shows a context-window gauge using per-provider estimates: Anthropic 200k, OpenAI/OpenRouter 128k, Gemini 1M, Ollama 8k, default 128k.
- **`uv.lock` is committed** for reproducible installs. Dependencies are pinned.
