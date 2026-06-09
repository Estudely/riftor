# Playwright Browser Capability for riftor — Design

**Date:** 2026-06-09
**Status:** Approved design, pending implementation plan
**Author:** brainstorm session (amanverasia + Claude)

## Goal

Give riftor's agent the ability to drive a real browser — navigate, read page
structure, click, type, screenshot, and inspect console/network traffic — for
SPA-aware recon, authenticated scanning, and form-driven testing that plain
`WebFetchTool`/`httpx` cannot do. Support both **headed** (watch the browser) and
**headless** (default, server/SSH-friendly) operation.

## Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Architecture | **Embed the Python `playwright` package** as native riftor `Tool` subclasses. NOT the Node `@playwright/mcp` subprocess. |
| 2 | Model perception | **Snapshot-primary, screenshot fallback.** Model reasons over a compact ref-tagged accessibility tree (`click ref=e9`); screenshots are an explicit fallback tool. |
| 3 | TUI screenshot display | **Inline-first, path fallback.** Render inline via `textual-image` (Kitty/Sixel) when the terminal supports it; otherwise show a clickable path. The *tool always writes the PNG to disk* regardless. |
| 4 | Tool set | **Lean 6 + 2:** navigate, snapshot, click, type, screenshot, eval, console_messages, network_requests. |
| 5 | Lifecycle | **Lazy launch, session-scoped, `BrowserManager` on `ToolContext`** (mirrors `ctx.engagement`). Teardown on app exit / `/browser close` / session end. |
| 6 | Profile | **Incognito by default; persistent is opt-in** via `/config` (`browser_persistent_profile`). One-time hint on first launch. |
| 7 | Headed/headless | **Headless by default**, override via `--browser-headed` flag, `/browser headed` slash command, and config screen. |
| 8 | Install | **`playwright` is a core dependency; auto-install Chromium** on first browser use, degrading gracefully to an actionable error. `--doctor`/`/doctor` gain a browser row. |
| 9 | Action result | Action tools return **status + fresh ref-tagged snapshot** (Playwright-MCP pattern), filtered to interactive nodes, capped by `ToolResult.truncated()`. |
| 10 | Testing | **Mocked units + skip-guarded real-browser integration tests** against local static HTML fixtures (offline). |

## Why embed, not MCP

riftor's entire safety story — `scope_sensitive` URL probing, the
`requires_permission` operator prompt, the audit log, the YOLO bypass — is
enforced **per-call, in-process** (`tui/app.py`, `headless.py`). The MCP route
runs the browser in a separate Node process, forcing us to re-implement that
gating at a process-boundary bridge. Embedding makes a browser action a plain
`Tool` subclass that inherits the exact same treatment as `BashTool` for free,
with no Node runtime and no subprocess to supervise. The cost — we maintain the
accessibility-snapshot/ref scheme ourselves — is bounded and well-understood.

## Architecture

### New module: `riftor/tools/browser.py`

Holds the `BrowserManager` and all browser `Tool` subclasses.

#### `BrowserManager` — the first long-lived resource

Every existing tool is stateless per call (fresh subprocess, fresh HTTP). The
browser is different: one Chromium process must persist across many tool calls.
It lives on `ToolContext`, exactly as `ctx.engagement` (the SQLite connection)
already does.

```python
class BrowserManager:
    def __init__(self, workdir: Path, *, headless: bool, persistent: bool): ...
    async def page(self) -> "Page":
        """Lazily launch Chromium on first call; return the shared Page."""
    async def close(self) -> None:
        """Idempotent teardown."""
    @property
    def launched(self) -> bool: ...
```

- **Lazy:** no Chromium launched until the first `browser_*` tool call. Mirrors
  riftor's lazy `litellm` import — heavy weight (~150–300 MB binaries, a process)
  is not paid for sessions that never touch the browser.
- **Lazy binary install:** on first launch, if Chromium binaries are missing,
  run `playwright install chromium` (with a TUI notice: "downloading Chromium
  (~150 MB), one time…"). On failure (no network, sandbox, perms) return an
  actionable error — never crash. (Honors the "bad config never crashes" rule.)
- **Persistent vs incognito:** `persistent=True` →
  `chromium.launch_persistent_context(.riftor/browser-profile/)`;
  `persistent=False` (default) → `chromium.launch()` + ephemeral `new_context()`.
- **Headed/headless:** `headless` passed to `launch(...)`.
- **Single shared page** reused across calls so the model can
  navigate → snapshot → click → snapshot on the same page.
- No module-level globals (clean for tests + multiple app instances).

`ToolContext` gains one optional field:

```python
# tools/base.py — ToolContext
browser: "BrowserManager | None" = None  # lazily set by the app on first browser use
```

The app constructs the manager from config and assigns `ctx.browser` (or the
manager is created lazily inside the tool via a small accessor that reads
`ctx.config`). Decision deferred to the plan; either keeps tests building a bare
`ToolContext`.

### The ref-tagged accessibility snapshot

A helper builds a compact, deterministic text tree of the current page where each
interactive element carries a stable `ref` id the model uses to act:

```
- banner
  - link "Home" [ref=e3]
  - link "Login" [ref=e4]
- main
  - heading "Sign in" [level=1]
  - textbox "Username" [ref=e7]
  - textbox "Password" [ref=e8]
  - button "Submit" [ref=e9]
```

- Built from Playwright's ARIA/accessibility data. `page.accessibility.snapshot()`
  is deprecated-but-functional; the plan will choose between using it and walking
  ARIA roles via locators. Either way the output format above is the contract.
- **Filtered to interactive/interesting nodes** to keep token cost low and stay
  within riftor's context-window budget.
- **ref → element resolution:** the manager keeps a per-snapshot map from `ref`
  id to a Playwright locator/handle so `click`/`type` can resolve `ref=e9`.
  Refs are regenerated on each snapshot; a stale ref returns a clear error.

### Tool set (registered in `ALL_TOOLS`, `tools/__init__.py`)

Order follows the safe→mutating convention; browser tools slot among the core
tools (read-only-ish first, `eval` last near `BashTool`).

| Tool | Flags | Behavior |
|------|-------|----------|
| `browser_navigate(url)` | `scope_sensitive` | Go to URL. Returns status (final URL, HTTP status) **+ fresh snapshot**. |
| `browser_snapshot()` | — | Return the current page's ref-tagged snapshot. |
| `browser_click(ref)` | — | Click element by ref. Returns status **+ fresh snapshot**. |
| `browser_type(ref, text, submit?)` | — | Type into element; optional Enter. Returns status **+ fresh snapshot**. |
| `browser_screenshot(full_page?)` | — | Save PNG to `.riftor/screenshots/`, return path + dims. |
| `browser_eval(js)` | `scope_sensitive`, `requires_permission`, `danger` | Run arbitrary JS in the page (the browser's `BashTool`). |
| `browser_console_messages()` | — | Return captured console log (errors, CSP, leaked tokens). |
| `browser_network_requests()` | — | Return captured XHR/fetch log (hidden endpoints, auth headers). |

- `navigate` and `eval` are **scope-sensitive** — their URL/JS args are probed
  against engagement scope by the existing enforcement path. The other action
  tools operate on the already-loaded page (the `navigate` was the gate), so they
  are not independently scope-sensitive.
- `eval` is **dangerous + requires permission** — arbitrary JS execution. The
  operator approves it exactly like `bash`. In headless mode it auto-denies
  unless an explicit allow rule exists.
- `console_messages`/`network_requests` read buffers the manager accumulates via
  Playwright `page.on("console")` / `page.on("request"/"response")` listeners
  attached at page creation.

### Result content

Action tools (`navigate`/`click`/`type`) return a short status line **plus** the
new ref-tagged snapshot, so the model always holds current page state without a
separate round-trip — the proven Playwright-MCP shape. Size is controlled by
filtering the snapshot to interactive nodes and applying the existing
`ToolResult.truncated(ctx.max_result_chars)` cap. `browser_snapshot` remains for
an explicit re-read.

### Screenshot display in the TUI

The `browser_screenshot` **tool** always writes the PNG to
`.riftor/screenshots/NNN-<slug>.png` and returns a text result (path, dimensions,
page URL). This makes screenshots first-class engagement artifacts (a later
enhancement can let the report generator embed them, alongside `.riftor/reports/`).

The **TUI rendering** is a separate, optional layer:

- **Inline-first:** if `textual-image` is importable and the terminal supports a
  true graphics protocol (Kitty TGP / Sixel — auto-detected by `textual-image`),
  mount an `Image` widget so the operator sees the screenshot in-app.
- **Path fallback:** otherwise (Python 3.11 where `textual-image` can't install;
  no graphics protocol; inside tmux without passthrough) render a polished
  fallback — an OSC-8 clickable hyperlink to the PNG plus the absolute path. No
  noisy half-block approximation (useless for reading text in a screenshot).
- **Optional dependency:** `textual-image` requires Python ≥ 3.12 but riftor
  targets 3.11+. It is therefore an **optional extra** (`riftor[browser-ui]` or
  similar) imported behind `try/except ImportError` — a failed import silently
  selects the path fallback (honors "bad config never crashes"). Import at module
  top, before `App.run()`, per `textual-image`'s detection requirement.

### Profile: incognito default, persistent opt-in

- Config gains `browser_persistent_profile: bool = False`.
- **Incognito (default):** ephemeral context, nothing written to disk. A pentest
  tool must not silently persist a client's authenticated session cookies.
- **Persistent (opt-in via `/config`):** `launch_persistent_context` against
  `.riftor/browser-profile/`; cookies/logins survive across runs.
- **First-run hint:** the first time a browser tool launches in a session, the TUI
  prints a one-time note: *"Browser running in incognito (nothing saved). Enable
  persistent profile in /config to keep cookies/logins across runs."*

### Config additions (`config.py`)

```python
browser_headless: bool = True
browser_persistent_profile: bool = False
```

Both added to `_to_toml()`, surfaced in the config screen
(`tui/config_screen.py`), and validated leniently (clamp, never raise) per
existing convention. CLI: `--browser-headed` flag in `__main__.py` sets
`browser_headless=False` for **this run only** (an in-memory override on the
loaded `Config`, not written back to `config.toml` — matches how `--model`/
`--api-key` override without persisting). Update bash/zsh completions in
`completions/`.

### Slash command: `/browser`

Add `/browser` to `_COMMANDS` and the help text:

- `/browser` — show status (launched?, headed/headless, incognito/persistent,
  current URL).
- `/browser headed` / `/browser headless` — toggle mode (takes effect on next
  launch, or relaunch if already running).
- `/browser close` — explicit teardown.

### Doctor integration (`engagement/doctor.py`)

Add a browser row to the toolchain report (both `render_markdown` and
`render_plain`): whether the `playwright` package imports and whether Chromium
binaries are installed, with the exact `playwright install chromium` command when
missing. Surfaced by `riftor --doctor` and `/doctor`, and folded into the
one-line `_toolchain_heads_up()`.

### Lifecycle wiring (`tui/app.py`, `headless.py`)

- **TUI:** create/attach `BrowserManager` from config; tear it down in an
  `on_unmount` (add if absent) and on `/new`/session switch. Per-step session
  checkpointing is unaffected (the browser is runtime-only, not serialized).
- **Headless:** same lazy manager; **always `close()` at end of run** in a
  `finally`. `browser_eval` (dangerous) auto-denies without an allow rule;
  out-of-scope `browser_navigate` is hard-blocked (no operator) — both inherited
  from the existing headless gating, no new code.

## Dependencies

- **Core:** add `playwright>=1.4x` to `pyproject.toml` dependencies.
- **Optional extra:** `textual-image[textual]` under an extra (e.g.
  `[project.optional-dependencies] browser-ui = ["textual-image[textual]"]`),
  imported softly.
- `uv.lock` regenerated (committed, per convention).
- Browser binaries are NOT a pip dependency — installed at runtime via
  `playwright install chromium` (auto, on first use).

## Testing

CI runs **fully offline** on Python 3.11 + 3.12 with no extra binaries — the
browser work must not break that.

- **Mocked unit tests** (no real browser, always run): scope-sensitivity flags
  and probing, the first-run incognito notice, lifecycle (lazy launch / idempotent
  close), result formatting (status + snapshot, truncation), the auto-install
  error path, ref→element resolution and stale-ref errors. Mock the
  `BrowserManager`/`Page`.
- **Real-browser integration tests** (skip-guarded): drive Playwright against tiny
  **local static HTML fixtures** (`file://` or a localhost `http.server`) — fully
  offline and deterministic. `pytest.skip(...)` when the `playwright` import fails
  or Chromium binaries are absent, exactly as recon-tool tests skip on missing
  PATH binaries. Covers navigate → snapshot → click → type → screenshot.
- **Smoke test** (`dev/smoke.py`): one headless lazy-launch → navigate (local
  fixture) → teardown pass, skip-guarded the same way.

This is belt-and-suspenders by design: mocks catch our logic bugs fast and keep
CI green binary-free; the real-browser tests catch Playwright-API drift where the
actual value of a browser lives.

## Out of scope (deferable)

- Extra tools: back/forward, hover, select_option, wait_for, tabs, file_upload,
  pdf, storage-state save/restore. Add when a concrete workflow needs them.
- Report generator embedding screenshots into HTML/SARIF.
- Multi-tab/multi-page orchestration.
- Vision-primary perception (we are snapshot-primary).
- Concurrent browsers for Chakla subagents (workers default to `bash` only).

## Risks & mitigations

- **Hallucinated findings.** Browser agents notoriously fake exploitation. The
  existing `/review` self-critique and the requirement that findings carry
  evidence apply; treat any browser-derived "vuln" as needing PoC validation.
- **Binary install weight/failure.** Mitigated by lazy install + graceful error +
  doctor visibility.
- **tmux graphics fragility.** Mitigated by the polished path fallback firing
  whenever inline rendering isn't confidently available.
- **First long-lived resource.** Teardown must be reliable (app exit, session
  switch, headless `finally`) to avoid leaked Chromium processes — explicitly
  covered in lifecycle tests.
