# Shell command shortcut + CWD header

**Date**: 2026-06-11
**Status**: approved

## Overview

Two quality-of-life additions to the Riftor TUI:

1.  **`!` prefix** — type `!ls -la` to run a shell command directly, bypassing the LLM agent. Output renders in a collapsible pane below the chat. Pure convenience; does not feed into the agent context.
2.  **CWD header** — a single-line `Static` widget above the chat showing the initial working directory. Set once at startup, never updated.

## Design

### `!` command

**Dispatch** (`riftor/tui/app.py`, `on_input_submitted()`):

- Check `text.startswith("!")` **before** the existing `/` check.
- Strip `!`, validate non-empty, delegate to `_shell_cmd(text)`.
- `!` commands are stored in `self._shell_history` (separate from `self._history`, which is for agent messages).

**Execution** (`riftor/tools/core.py`):

- New public function: `async def run_shell(command: str, workdir: str, timeout: int = 30) -> ShellResult`
- Returns a `ShellResult` dataclass: `stdout: str`, `stderr: str`, `exit_code: int`, `truncated: bool`.
- Uses `asyncio.create_subprocess_shell` with the same timeout and output-truncation (~10 KB) patterns already established in `BashTool`.
- `BashTool.execute()` is **not** refactored in this change (clean follow-up opportunity).

**Output rendering** (`riftor/tui/app.py`):

- A `Collapsible` container (starts collapsed, auto-expands when content appears) holds a `RichLog` widget (`shell_log`).
- Mounted below the chat `VerticalScroll`.
- Each entry: `$ <command>` header line (accent/violet) followed by monospace output. stderr lines styled red.
- `/clearlog` slash command clears the `RichLog`.
- Collapsible header shows `▼ Shell output — N commands`.

### CWD header

**Widget** (`riftor/tui/app.py`):

- A `Static` widget mounted between the `Banner` (if present) and the chat `VerticalScroll`, inside `compose()`.
- Renders: `cwd: /home/user/targets/acme-corp` — label in accent color, path in muted.

**Data source**:

- Uses the existing `self.workdir` (a `Path`), set from `Config.workdir` at app initialisation.
- Set once in `on_mount()`; never updated when the agent changes directory via the bash tool.

### Files touched

| File | Changes |
|---|---|
| `riftor/tui/app.py` | New `_shell_cmd()` method, dispatch in `on_input_submitted()`, mount `cwd_header` + `shell_log` in `compose()`, `/clearlog` in `_COMMANDS` + handler dict |
| `riftor/tools/core.py` | New `ShellResult` dataclass + `run_shell()` async function |

### Non-goals

- No persistent shell session across `!` invocations.
- No shell output injected into agent context.
- No shell history recall/autocomplete (can be added later).
- CWD header does not update dynamically on agent `cd` — stays at startup cwd.

### Testing

- Unit test: `run_shell("echo hello", workdir)` returns correct `ShellResult`.
- Smoke test: extend `dev/smoke.py` to type `!echo smoke` and verify the shell pane renders output.
- Manual: launch TUI, type `!ls`, verify output appears in pane below chat and that CWD header is visible above chat.
