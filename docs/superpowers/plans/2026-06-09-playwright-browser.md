# Playwright Browser Capability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give riftor's agent a headed/headless Playwright browser — navigate, ref-tagged accessibility snapshot, click, type, screenshot, eval, console/network capture — wired into riftor's existing scope/permission/audit guardrails.

**Architecture:** Embed the Python `playwright` package as native `Tool` subclasses (no MCP). A lazily-launched, session-scoped `BrowserManager` lives on `ToolContext` (mirroring `ctx.engagement`). The model perceives pages as a compact ref-tagged accessibility tree; screenshots save to `.riftor/screenshots/` and render inline via the optional `textual-image` extra with a path fallback. Profile is incognito by default, persistent opt-in via `/config`.

**Tech Stack:** Python 3.11+, asyncio, `playwright` (async API), Textual, optional `textual-image[textual]`, pytest (`asyncio_mode=auto`, offline).

**Reference spec:** `docs/superpowers/specs/2026-06-09-playwright-browser-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `riftor/tools/browser.py` | `BrowserManager` + ref-snapshot helper + 8 browser `Tool` subclasses | Create |
| `riftor/tools/base.py` | Add `browser: BrowserManager \| None` field to `ToolContext` | Modify |
| `riftor/tools/__init__.py` | Register the 8 browser tools in `ALL_TOOLS` | Modify |
| `riftor/config.py` | `browser_headless`, `browser_persistent_profile` fields + TOML | Modify |
| `riftor/engagement/doctor.py` | Browser readiness row (package + binary) | Modify |
| `riftor/__main__.py` | `--browser-headed` flag (per-run override) | Modify |
| `riftor/tui/app.py` | Build `BrowserManager`, `/browser` command, teardown, first-run hint | Modify |
| `riftor/headless.py` | Browser teardown in `finally` | Modify |
| `pyproject.toml` | `playwright` core dep + `browser-ui` extra | Modify |
| `completions/riftor.bash`, `completions/_riftor` | `--browser-headed` flag | Modify |
| `tests/test_browser.py` | Mocked-unit + skip-guarded integration tests | Create |
| `tests/fixtures/login.html` | Local static HTML fixture for integration tests | Create |
| `dev/smoke.py` | Add skip-guarded browser smoke pass | Modify |

**Naming contract (used across tasks — keep exact):**
- `BrowserManager.page() -> Page` (async), `.close()` (async, idempotent), `.launched: bool`, `.snapshot_text() -> str`, `.resolve_ref(ref: str)` returns a Playwright `Locator` or raises `KeyError`.
- Tool names: `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_eval`, `browser_console_messages`, `browser_network_requests`.
- Config fields: `browser_headless: bool = True`, `browser_persistent_profile: bool = False`.

---

## Task 1: Add `playwright` dependency and the `browser-ui` extra

**Files:**
- Modify: `pyproject.toml:18-22` (dependencies), `pyproject.toml:24-32` (optional-dependencies)

- [ ] **Step 1: Add `playwright` to core dependencies**

In `pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "textual>=0.79",
    "litellm>=1.55",
    "pydantic>=2.6",
    "playwright>=1.44",
]
```

- [ ] **Step 2: Add the optional `browser-ui` extra**

In `[project.optional-dependencies]`, add (alongside the existing `dev` block):

```toml
browser-ui = [
    "textual-image[textual]>=0.13; python_version >= '3.12'",
]
```

(The `python_version` marker keeps it from attempting install on 3.11, where `textual-image` is unsupported.)

- [ ] **Step 3: Sync and regenerate the lockfile**

Run: `uv sync --extra dev && uv run playwright install chromium`
Expected: dependencies resolve; `uv.lock` updates; Chromium downloads (~150 MB).

- [ ] **Step 4: Verify playwright imports**

Run: `uv run python -c "from playwright.async_api import async_playwright; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add playwright core dep + optional browser-ui extra"
```

---

## Task 2: Add `browser` field to `ToolContext`

**Files:**
- Modify: `riftor/tools/base.py:34-55` (`ToolContext` dataclass), `riftor/tools/base.py:10-13` (TYPE_CHECKING imports)
- Test: `tests/test_browser.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_browser.py`:

```python
"""Browser tool behavior: context wiring, lifecycle, snapshot, scope, screenshots."""

from __future__ import annotations

import pytest

from riftor.tools import ToolContext


def test_toolcontext_has_browser_field_default_none():
    ctx = ToolContext()
    assert ctx.browser is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_browser.py::test_toolcontext_has_browser_field_default_none -v`
Expected: FAIL — `AttributeError: 'ToolContext' object has no attribute 'browser'`

- [ ] **Step 3: Add the field**

In `riftor/tools/base.py`, under the TYPE_CHECKING block (lines 10-13) add:

```python
if TYPE_CHECKING:
    from riftor.config import Config
    from riftor.engagement import Engagement
    from riftor.safety.audit import AuditLog
    from riftor.safety.permissions import Permissions
    from riftor.tools.browser import BrowserManager
```

In the `ToolContext` dataclass (after the `progress` field, ~line 55) add:

```python
    #: Lazily-launched, session-scoped browser. The first long-lived resource in
    #: riftor (every other tool is stateless per call). None until a browser_*
    #: tool launches it. Mirrors how ``engagement`` persists across calls.
    browser: "BrowserManager | None" = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_browser.py::test_toolcontext_has_browser_field_default_none -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add riftor/tools/base.py tests/test_browser.py
git commit -m "feat: add browser field to ToolContext"
```

---

## Task 3: `BrowserManager` — lazy launch + idempotent teardown

**Files:**
- Create: `riftor/tools/browser.py`
- Test: `tests/test_browser.py`

- [ ] **Step 1: Write the failing tests (mocked, no real browser)**

Append to `tests/test_browser.py`:

```python
class _FakePage:
    def __init__(self):
        self.closed = False
        self.url = "about:blank"

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.pages_created = 0
        self.closed = False

    async def new_page(self):
        self.pages_created += 1
        return _FakePage()

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_manager_lazy_no_launch_until_page(monkeypatch, tmp_workdir):
    from riftor.tools import browser as bmod

    launches = {"n": 0}

    async def fake_launch(self):
        launches["n"] += 1
        return _FakeContext(), _FakePage()

    monkeypatch.setattr(bmod.BrowserManager, "_launch", fake_launch)
    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    assert not mgr.launched
    assert launches["n"] == 0
    page = await mgr.page()
    assert mgr.launched
    assert launches["n"] == 1
    # second call reuses, no relaunch
    await mgr.page()
    assert launches["n"] == 1
    await mgr.close()


@pytest.mark.asyncio
async def test_manager_close_idempotent(monkeypatch, tmp_workdir):
    from riftor.tools import browser as bmod

    ctx_obj = _FakeContext()

    async def fake_launch(self):
        return ctx_obj, _FakePage()

    monkeypatch.setattr(bmod.BrowserManager, "_launch", fake_launch)
    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    await mgr.page()
    await mgr.close()
    assert ctx_obj.closed
    await mgr.close()  # second close must not raise
    assert not mgr.launched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_browser.py -k manager -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.tools.browser'`

- [ ] **Step 3: Create `riftor/tools/browser.py` with the manager skeleton**

```python
"""Playwright browser: a lazily-launched, session-scoped BrowserManager plus the
browser_* tools. The manager is riftor's first long-lived resource — every other
tool is stateless per call. It lives on ToolContext (like ctx.engagement) and is
torn down on app exit / session switch / explicit /browser close.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


class BrowserError(Exception):
    """Browser couldn't launch (binaries missing, install failed, etc.)."""


class BrowserManager:
    """Owns one Chromium context+page for the session. Lazy: nothing launches
    until ``page()`` is first awaited."""

    def __init__(self, workdir: Path, *, headless: bool, persistent: bool) -> None:
        self._workdir = workdir
        self._headless = headless
        self._persistent = persistent
        self._pw = None  # async_playwright context manager instance
        self._context = None  # BrowserContext
        self._page: "Page | None" = None
        self._refs: dict[str, "Locator"] = {}
        self.console_log: list[str] = []
        self.network_log: list[str] = []

    @property
    def launched(self) -> bool:
        return self._page is not None

    async def _launch(self) -> tuple[object, "Page"]:
        """Start Playwright + Chromium. Returns (context, page). Auto-installs
        Chromium binaries on first use; raises BrowserError on failure."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - playwright is a core dep
            raise BrowserError(f"playwright not installed: {exc}") from exc

        self._pw = await async_playwright().start()
        profile = self._workdir / ".riftor" / "browser-profile"
        try:
            context, page = await self._do_launch(profile)
        except Exception as exc:  # noqa: BLE001 — likely missing browser binaries
            if not await self._try_install():
                raise BrowserError(
                    "Chromium not available and auto-install failed. "
                    "Run: playwright install chromium"
                ) from exc
            context, page = await self._do_launch(profile)
        return context, page

    async def _do_launch(self, profile: Path) -> tuple[object, "Page"]:
        if self._persistent:
            profile.mkdir(parents=True, exist_ok=True)
            context = await self._pw.chromium.launch_persistent_context(
                str(profile), headless=self._headless
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await self._pw.chromium.launch(headless=self._headless)
            context = await browser.new_context()
            page = await context.new_page()
        return context, page

    async def _try_install(self) -> bool:
        """Run `playwright install chromium`. Returns True on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    async def page(self) -> "Page":
        if self._page is None:
            self._context, self._page = await self._launch()
            self._attach_listeners(self._page)
        return self._page

    def _attach_listeners(self, page: "Page") -> None:
        page.on("console", lambda m: self.console_log.append(f"[{m.type}] {m.text}"))
        page.on(
            "requestfinished",
            lambda r: self.network_log.append(f"{r.method} {r.url}"),
        )

    async def close(self) -> None:
        """Idempotent teardown."""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._page = None
        self._pw = None
        self._refs.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_browser.py -k manager -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add riftor/tools/browser.py tests/test_browser.py
git commit -m "feat: BrowserManager lazy launch + idempotent teardown"
```

---

## Task 4: Ref-tagged accessibility snapshot

**Files:**
- Modify: `riftor/tools/browser.py` (add `snapshot_text`, `resolve_ref`)
- Test: `tests/test_browser.py`

- [ ] **Step 1: Write the failing test (mocked page tree)**

Append to `tests/test_browser.py`:

```python
@pytest.mark.asyncio
async def test_snapshot_text_tags_interactive_nodes(monkeypatch, tmp_workdir):
    from riftor.tools import browser as bmod

    tree = {
        "role": "WebArea",
        "name": "",
        "children": [
            {"role": "heading", "name": "Sign in", "level": 1},
            {"role": "textbox", "name": "Username"},
            {"role": "button", "name": "Submit"},
        ],
    }

    class _AxPage:
        class accessibility:
            @staticmethod
            async def snapshot(interesting_only=False):
                return tree

    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    mgr._page = _AxPage()  # inject a fake page
    text = await mgr.snapshot_text()
    assert "heading \"Sign in\"" in text
    assert "textbox \"Username\" [ref=e" in text
    assert "button \"Submit\" [ref=e" in text
    # non-interactive heading gets no ref
    assert "heading \"Sign in\" [ref=" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_browser.py::test_snapshot_text_tags_interactive_nodes -v`
Expected: FAIL — `AttributeError: 'BrowserManager' object has no attribute 'snapshot_text'`

- [ ] **Step 3: Implement snapshot + ref resolution**

Add to `BrowserManager` in `riftor/tools/browser.py`:

```python
    # roles the model can act on → these get a [ref=eN] tag
    _INTERACTIVE = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "switch", "searchbox", "slider",
    }

    async def snapshot_text(self) -> str:
        """Compact, ref-tagged accessibility tree of the current page. Interactive
        nodes get a stable [ref=eN] id used by click/type. Refs reset each call."""
        page = await self.page()
        tree = await page.accessibility.snapshot(interesting_only=False)
        self._refs.clear()
        self._ref_targets: dict[str, dict] = {}
        lines: list[str] = []
        counter = {"n": 0}

        def walk(node: dict, depth: int) -> None:
            role = node.get("role", "")
            if role in ("WebArea", "RootWebArea", "", "generic", "none"):
                for child in node.get("children", []) or []:
                    walk(child, depth)
                return
            name = node.get("name", "") or ""
            indent = "  " * depth
            label = f'{role} "{name}"' if name else role
            if node.get("level"):
                label += f" [level={node['level']}]"
            if role in self._INTERACTIVE:
                counter["n"] += 1
                ref = f"e{counter['n']}"
                self._ref_targets[ref] = node
                label += f" [ref={ref}]"
            lines.append(f"{indent}- {label}")
            for child in node.get("children", []) or []:
                walk(child, depth + 1)

        if tree:
            walk(tree, 0)
        return "\n".join(lines) if lines else "(empty page)"

    def resolve_ref(self, ref: str) -> "Locator":
        """Map a ref id from the last snapshot to a Playwright locator. The
        accessibility node carries role+name; we locate by ARIA role+name."""
        node = getattr(self, "_ref_targets", {}).get(ref)
        if node is None:
            raise KeyError(ref)
        role = node.get("role", "")
        name = node.get("name", "") or ""
        page = self._page
        assert page is not None
        if name:
            return page.get_by_role(role, name=name).first
        return page.get_by_role(role).first
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_browser.py::test_snapshot_text_tags_interactive_nodes -v`
Expected: PASS

- [ ] **Step 5: Add stale-ref test and verify**

Append:

```python
@pytest.mark.asyncio
async def test_resolve_unknown_ref_raises(tmp_workdir):
    from riftor.tools import browser as bmod

    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    with pytest.raises(KeyError):
        mgr.resolve_ref("e99")
```

Run: `uv run pytest tests/test_browser.py -k "snapshot or ref" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add riftor/tools/browser.py tests/test_browser.py
git commit -m "feat: ref-tagged accessibility snapshot + ref resolution"
```

---

## Task 5: The 8 browser tools

**Files:**
- Modify: `riftor/tools/browser.py` (add tool classes), `riftor/tools/__init__.py:1-78` (register)
- Test: `tests/test_browser.py`

- [ ] **Step 1: Write failing registration + flag tests**

Append to `tests/test_browser.py`:

```python
def test_browser_tools_registered_with_correct_flags():
    from riftor import tools

    names = {t.name for t in tools.all_tools()}
    expected = {
        "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
        "browser_screenshot", "browser_eval", "browser_console_messages",
        "browser_network_requests",
    }
    assert expected <= names

    nav = tools.get("browser_navigate")
    assert nav.scope_sensitive is True
    assert nav.requires_permission is False

    ev = tools.get("browser_eval")
    assert ev.scope_sensitive is True
    assert ev.requires_permission is True
    assert ev.danger is True

    # action-on-loaded-page tools are NOT independently scope-sensitive
    for n in ("browser_click", "browser_type", "browser_snapshot", "browser_screenshot"):
        assert tools.get(n).scope_sensitive is False


@pytest.mark.asyncio
async def test_navigate_without_browser_errors_cleanly(toolctx):
    from riftor import tools

    # toolctx.browser is None and config is None → tool must error, not crash
    r = await tools.get("browser_navigate").execute({"url": "https://example.com"}, toolctx)
    assert r.is_error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_browser.py -k "registered or without_browser" -v`
Expected: FAIL — `browser_navigate` not found / returns None.

- [ ] **Step 3: Implement the tools**

Add to `riftor/tools/browser.py` (after `BrowserManager`):

```python
from riftor.tools.base import Tool, ToolContext, ToolResult, resolve_path


def _ensure_manager(ctx: ToolContext) -> "BrowserManager | None":
    """Get-or-create the session BrowserManager from ctx. Returns None if there's
    no config to derive settings from (bare test contexts)."""
    if ctx.browser is not None:
        return ctx.browser
    if ctx.config is None:
        return None
    mgr = BrowserManager(
        ctx.workdir,
        headless=getattr(ctx.config, "browser_headless", True),
        persistent=getattr(ctx.config, "browser_persistent_profile", False),
    )
    ctx.browser = mgr
    return mgr


class _BrowserTool(Tool):
    """Shared error handling for browser tools."""

    async def _manager(self, ctx: ToolContext) -> "BrowserManager":
        mgr = _ensure_manager(ctx)
        if mgr is None:
            raise BrowserError("no active browser context (config unavailable)")
        return mgr


class BrowserNavigateTool(_BrowserTool):
    name = "browser_navigate"
    description = "Navigate the browser to a URL. Returns load status and a ref-tagged accessibility snapshot of the resulting page."
    scope_sensitive = True
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute URL to load."}},
        "required": ["url"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("url", ""))[:300]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult("error: empty url", is_error=True)
        try:
            mgr = await self._manager(ctx)
            page = await mgr.page()
            resp = await page.goto(url, wait_until="domcontentloaded")
            status = resp.status if resp else "?"
            snap = await mgr.snapshot_text()
            return ToolResult(f"navigated → {page.url} [{status}]\n\n{snap}").truncated(
                ctx.max_result_chars
            )
        except BrowserError as exc:
            return ToolResult(f"error: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: navigation failed: {exc}", is_error=True)


class BrowserSnapshotTool(_BrowserTool):
    name = "browser_snapshot"
    description = "Return a ref-tagged accessibility snapshot of the current page."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            mgr = await self._manager(ctx)
            if not mgr.launched:
                return ToolResult("error: no page loaded; use browser_navigate first", is_error=True)
            return ToolResult(await mgr.snapshot_text()).truncated(ctx.max_result_chars)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: {exc}", is_error=True)


class BrowserClickTool(_BrowserTool):
    name = "browser_click"
    description = "Click an element by its [ref=eN] id from the latest snapshot. Returns the new page snapshot."
    parameters = {
        "type": "object",
        "properties": {"ref": {"type": "string", "description": "Element ref, e.g. e9."}},
        "required": ["ref"],
    }

    def preview(self, args: dict) -> str:
        return f"ref={args.get('ref', '')}"

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        ref = str(args.get("ref", "")).strip()
        try:
            mgr = await self._manager(ctx)
            try:
                locator = mgr.resolve_ref(ref)
            except KeyError:
                return ToolResult(f"error: unknown ref '{ref}' — snapshot may be stale", is_error=True)
            await locator.click()
            snap = await mgr.snapshot_text()
            return ToolResult(f"clicked {ref}\n\n{snap}").truncated(ctx.max_result_chars)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: click failed: {exc}", is_error=True)


class BrowserTypeTool(_BrowserTool):
    name = "browser_type"
    description = "Type text into an element by its [ref=eN] id. Set submit=true to press Enter after. Returns the new page snapshot."
    parameters = {
        "type": "object",
        "properties": {
            "ref": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "description": "Press Enter after typing."},
        },
        "required": ["ref", "text"],
    }

    def preview(self, args: dict) -> str:
        return f"ref={args.get('ref', '')} text={str(args.get('text', ''))[:40]!r}"

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        ref = str(args.get("ref", "")).strip()
        text = str(args.get("text", ""))
        try:
            mgr = await self._manager(ctx)
            try:
                locator = mgr.resolve_ref(ref)
            except KeyError:
                return ToolResult(f"error: unknown ref '{ref}' — snapshot may be stale", is_error=True)
            await locator.fill(text)
            if args.get("submit"):
                await locator.press("Enter")
            snap = await mgr.snapshot_text()
            return ToolResult(f"typed into {ref}\n\n{snap}").truncated(ctx.max_result_chars)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: type failed: {exc}", is_error=True)


class BrowserScreenshotTool(_BrowserTool):
    name = "browser_screenshot"
    description = "Save a PNG screenshot of the current page to .riftor/screenshots/ and return its path."
    parameters = {
        "type": "object",
        "properties": {"full_page": {"type": "boolean", "description": "Capture full scrollable page."}},
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            mgr = await self._manager(ctx)
            if not mgr.launched:
                return ToolResult("error: no page loaded; use browser_navigate first", is_error=True)
            page = await mgr.page()
            shots = ctx.workdir / ".riftor" / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            n = len(list(shots.glob("*.png"))) + 1
            path = shots / f"{n:03d}.png"
            await page.screenshot(path=str(path), full_page=bool(args.get("full_page")))
            size = path.stat().st_size
            return ToolResult(f"screenshot saved → {path} ({size} bytes)\npage: {page.url}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: screenshot failed: {exc}", is_error=True)


class BrowserEvalTool(_BrowserTool):
    name = "browser_eval"
    description = "Execute arbitrary JavaScript in the current page and return its result. Powerful and dangerous — like running shell code in the browser."
    scope_sensitive = True
    requires_permission = True
    danger = True
    parameters = {
        "type": "object",
        "properties": {"js": {"type": "string", "description": "JavaScript expression to evaluate."}},
        "required": ["js"],
    }

    def preview(self, args: dict) -> str:
        return str(args.get("js", ""))[:300]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        js = str(args.get("js", ""))
        try:
            mgr = await self._manager(ctx)
            page = await mgr.page()
            value = await page.evaluate(js)
            return ToolResult(f"{value!r}").truncated(ctx.max_result_chars)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"error: eval failed: {exc}", is_error=True)


class BrowserConsoleMessagesTool(_BrowserTool):
    name = "browser_console_messages"
    description = "Return console messages captured from the current page (errors, warnings, logged values)."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        mgr = _ensure_manager(ctx)
        if mgr is None or not mgr.launched:
            return ToolResult("(no browser activity)")
        log = mgr.console_log[-200:]
        return ToolResult("\n".join(log) if log else "(no console messages)").truncated(
            ctx.max_result_chars
        )


class BrowserNetworkRequestsTool(_BrowserTool):
    name = "browser_network_requests"
    description = "Return the network requests (method + URL) captured from the current page."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        mgr = _ensure_manager(ctx)
        if mgr is None or not mgr.launched:
            return ToolResult("(no browser activity)")
        log = mgr.network_log[-200:]
        return ToolResult("\n".join(log) if log else "(no requests captured)").truncated(
            ctx.max_result_chars
        )
```

- [ ] **Step 4: Register the tools in `ALL_TOOLS`**

In `riftor/tools/__init__.py`, add imports after the `from riftor.tools.subagent import DispatchChaklaTool` line (line 33):

```python
from riftor.tools.browser import (
    BrowserClickTool,
    BrowserConsoleMessagesTool,
    BrowserEvalTool,
    BrowserNavigateTool,
    BrowserNetworkRequestsTool,
    BrowserScreenshotTool,
    BrowserSnapshotTool,
    BrowserTypeTool,
)
```

In the `ALL_TOOLS` list, insert the read-ish browser tools after `WebFetchTool()` (line 41) and put `BrowserEvalTool()` near the mutating tools. Final list ordering — insert these:

After `WebFetchTool(),`:
```python
    BrowserNavigateTool(),
    BrowserSnapshotTool(),
    BrowserClickTool(),
    BrowserTypeTool(),
    BrowserScreenshotTool(),
    BrowserConsoleMessagesTool(),
    BrowserNetworkRequestsTool(),
```
And after `BashTool(),` (the last mutating tool, line 60):
```python
    BrowserEvalTool(),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_browser.py -k "registered or without_browser" -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full suite + lint + typecheck**

Run: `uv run pytest tests/test_browser.py -v && uv run ruff check riftor && uv run pyright riftor/tools/browser.py`
Expected: all PASS / no errors.

- [ ] **Step 7: Commit**

```bash
git add riftor/tools/browser.py riftor/tools/__init__.py tests/test_browser.py
git commit -m "feat: add 8 browser tools wired into the registry"
```

---

## Task 6: Config fields for headed/headless + profile

**Files:**
- Modify: `riftor/config.py:66-72` (fields), `riftor/config.py:223-241` (`_to_toml`)
- Test: `tests/test_browser.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_browser.py`:

```python
def test_config_browser_defaults_and_toml():
    from riftor.config import Config

    cfg = Config()
    assert cfg.browser_headless is True
    assert cfg.browser_persistent_profile is False
    toml = cfg._to_toml()
    assert "browser_headless = true" in toml
    assert "browser_persistent_profile = false" in toml
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_browser.py::test_config_browser_defaults_and_toml -v`
Expected: FAIL — `AttributeError` / fields missing from TOML.

- [ ] **Step 3: Add the fields**

In `riftor/config.py`, after the `rate_limit_per_min` field (line 72) add:

```python
    # Browser (Playwright). Incognito by default — a pentest tool must not silently
    # persist a client's session cookies. Persistent profile is opt-in via /config.
    browser_headless: bool = True
    browser_persistent_profile: bool = False
```

In `_to_toml()`, in the `lines += [...]` block after `f"rate_limit_per_min = {self.rate_limit_per_min}",` (line 234) add:

```python
            f"browser_headless = {str(self.browser_headless).lower()}",
            f"browser_persistent_profile = {str(self.browser_persistent_profile).lower()}",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_browser.py::test_config_browser_defaults_and_toml -v`
Expected: PASS

- [ ] **Step 5: Run config tests for regressions**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (round-trip load/save still works).

- [ ] **Step 6: Commit**

```bash
git add riftor/config.py tests/test_browser.py
git commit -m "feat: browser_headless + browser_persistent_profile config"
```

---

## Task 7: Doctor browser readiness row

**Files:**
- Modify: `riftor/engagement/doctor.py` (add a browser check + render in both functions)
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_doctor.py`:

```python
def test_browser_status_in_plain_report():
    from riftor.engagement import doctor

    text = doctor.render_plain(doctor.check_toolchain())
    assert "browser" in text.lower()
    assert "playwright" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py::test_browser_status_in_plain_report -v`
Expected: FAIL — no "browser" string in the report.

- [ ] **Step 3: Add a browser-readiness helper + render it**

In `riftor/engagement/doctor.py`, after `summarize(...)` (line 72) add:

```python
def browser_status() -> tuple[bool, bool, str]:
    """Return (package_ok, binary_ok, detail) for the Playwright browser."""
    try:
        import importlib.util

        pkg_ok = importlib.util.find_spec("playwright") is not None
    except Exception:  # noqa: BLE001
        pkg_ok = False
    binary_ok = False
    if pkg_ok:
        from pathlib import Path

        # Playwright caches browsers under ~/.cache/ms-playwright (Linux/macOS).
        cache = Path.home() / ".cache" / "ms-playwright"
        binary_ok = cache.exists() and any(cache.glob("chromium-*"))
    if not pkg_ok:
        detail = "playwright package not installed"
    elif not binary_ok:
        detail = "Chromium not installed — run: playwright install chromium"
    else:
        detail = "ready (chromium installed)"
    return pkg_ok, binary_ok, detail
```

In `render_plain(...)` before the final `return "\n".join(lines)` (after the Codex block, ~line 121) add:

```python
    pkg_ok, binary_ok, detail = browser_status()
    mark = "ok " if (pkg_ok and binary_ok) else "MISSING"
    lines.append("Browser (Playwright):")
    lines.append(f"  [{mark}] {'browser':<10} {detail}")
```

In `render_markdown(...)` before its `summary = summarize(...)` line (line 94) add:

```python
    pkg_ok, binary_ok, detail = browser_status()
    bmark = "✓" if (pkg_ok and binary_ok) else "✗"
    lines.append("_Browser_")
    lines.append(f"- {bmark} `browser` (Playwright) — {detail}")
    lines.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add riftor/engagement/doctor.py tests/test_doctor.py
git commit -m "feat: doctor reports Playwright browser readiness"
```

---

## Task 8: `--browser-headed` CLI flag (per-run override)

**Files:**
- Modify: `riftor/__main__.py:30-44` (argparse), `riftor/__main__.py:60-67` (apply override)
- Modify: `completions/riftor.bash`, `completions/_riftor`

- [ ] **Step 1: Add the argparse flag**

In `riftor/__main__.py`, after the `--scope-file` argument (line 32) add:

```python
    parser.add_argument(
        "--browser-headed", dest="browser_headed", action="store_true",
        help="run the Playwright browser headed (visible) for this run",
    )
```

- [ ] **Step 2: Apply the override (per-run, not persisted)**

After `if args.api_key: cfg.api_key = args.api_key` (line ~67) add:

```python
    if args.browser_headed:
        cfg.browser_headless = False  # this run only; not written to config.toml
```

- [ ] **Step 3: Update shell completions**

In `completions/riftor.bash`, add `--browser-headed` to the flag list (find the line listing `--scope-file` and add it alongside).
In `completions/_riftor`, add a line in the `_arguments` block:

```
'--browser-headed[run the Playwright browser headed for this run]'
```

- [ ] **Step 4: Verify the flag parses**

Run: `uv run riftor --browser-headed --doctor`
Expected: doctor output prints (flag accepted, no error).

- [ ] **Step 5: Commit**

```bash
git add riftor/__main__.py completions/riftor.bash completions/_riftor
git commit -m "feat: --browser-headed per-run flag + completions"
```

---

## Task 9: TUI lifecycle — `/browser` command, teardown, first-run hint

**Files:**
- Modify: `riftor/tui/app.py:53-59` (`_COMMANDS`), `:61-95` (HELP), `:466-506` (handler dict), add `_browser_cmd`, add `on_unmount`, add first-run hint in `_run_tool`
- Test: covered by smoke (Task 11); logic kept thin.

- [ ] **Step 1: Add `/browser` to the command list and help**

In `_COMMANDS` (line 58), add `"/browser"` to the list (before `"/exit"`).
In `HELP`, under `_Settings & sessions_` add a line:

```
- `/browser [headed|headless|close]` — browser status / mode / teardown
```

- [ ] **Step 2: Register the handler**

In the `handlers` dict in `_command` (after `"/doctor": self._doctor_cmd,`, line 501) add:

```python
            "/browser": lambda: self._browser_cmd(arg),
```

- [ ] **Step 3: Implement `_browser_cmd` and `on_unmount`**

Add a method near `_doctor_cmd` (line 960):

```python
    def _browser_cmd(self, arg: str) -> None:
        sub = (arg or "").strip().lower()
        mgr = self.toolctx.browser
        if sub in ("headed", "headless"):
            self.config.browser_headless = sub == "headless"
            self.config.save()
            self._note(f"browser mode → {sub} (applies on next launch)")
            return
        if sub == "close":
            if mgr is not None and mgr.launched:
                self.run_worker(mgr.close(), exclusive=False)
                self._note("browser closed")
            else:
                self._note("no browser running")
            return
        mode = "headless" if self.config.browser_headless else "headed"
        profile = "persistent" if self.config.browser_persistent_profile else "incognito"
        state = "running" if (mgr and mgr.launched) else "not launched"
        self._note(f"browser: {state} · {mode} · {profile} (toggle in /config)")
```

Add an `on_unmount` method (near `on_mount`, line 222) — if one exists, add the teardown into it instead:

```python
    async def on_unmount(self) -> None:
        mgr = self.toolctx.browser
        if mgr is not None and mgr.launched:
            try:
                await mgr.close()
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 4: Add the one-time first-run incognito hint**

In `_run_tool` (line 1314), right after `tool = tools.get(call.name)` resolves and before execution — add a guarded note. Add an instance flag in `__init__` (after `self._autoscroll = True`, line 186):

```python
        self._browser_hint_shown = False
```

Then in `_run_tool`, just before `result = await tool.execute(...)` (line 1383):

```python
        if call.name.startswith("browser_") and not self._browser_hint_shown:
            self._browser_hint_shown = True
            if not self.config.browser_persistent_profile:
                self._note(
                    "browser running in incognito (nothing saved) · enable persistent "
                    "profile in /config to keep cookies/logins across runs"
                )
```

- [ ] **Step 5: Verify the app boots and the command exists**

Run: `uv run python -c "from riftor.tui.app import RiftorApp, _COMMANDS; assert '/browser' in _COMMANDS; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check riftor && uv run pyright riftor`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add riftor/tui/app.py
git commit -m "feat: /browser command, teardown on unmount, first-run incognito hint"
```

---

## Task 10: Headless teardown

**Files:**
- Modify: `riftor/headless.py:104-132` (wrap loop in try/finally to close the browser)

- [ ] **Step 1: Wrap the step loop so the browser always closes**

In `riftor/headless.py`, in `_run`, change the `for _ in range(max_steps):` loop (lines 104-130) to be inside a `try/finally`:

```python
    context.add_user(prompt)
    max_steps = 10**9 if yolo else cfg.max_steps
    try:
        for _ in range(max_steps):
            context.repair()
            text_parts: list[str] = []
            turn = None
            try:
                async for event, payload in provider.stream_turn(context.messages, schemas):
                    if event == "thinking":
                        if cfg.show_thinking:
                            sys.stderr.write(str(payload))
                            sys.stderr.flush()
                    elif event == "text":
                        sys.stdout.write(str(payload))
                        sys.stdout.flush()
                        text_parts.append(str(payload))
                    elif event == "done":
                        turn = payload  # type: ignore[assignment]
            except ProviderError as exc:
                print(f"\nriftor: provider error [{exc.kind}] — {exc}", file=sys.stderr)
                return 1
            if turn is None:
                break
            context.add_message(turn.assistant_message)
            if not turn.tool_calls:
                break
            for call in turn.tool_calls:
                await _run_tool_headless(call, engagement, permissions, audit, toolctx, context, yolo=yolo)
    finally:
        if toolctx.browser is not None and toolctx.browser.launched:
            try:
                await toolctx.browser.close()
            except Exception:  # noqa: BLE001
                pass
    print()  # trailing newline
    return 0
```

(Note: the `return 1` on ProviderError still runs the `finally` — the browser closes either way.)

- [ ] **Step 2: Verify headless still runs offline**

Run: `RIFTOR_DEMO_RESPONSE="done" uv run riftor -p "say hi" --workdir /tmp/riftor-hl-test`
Expected: prints the demo response, exits 0, no leaked process.

- [ ] **Step 3: Lint**

Run: `uv run ruff check riftor/headless.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add riftor/headless.py
git commit -m "feat: tear down browser at end of headless run"
```

---

## Task 11: Real-browser integration tests + smoke (skip-guarded)

**Files:**
- Create: `tests/fixtures/login.html`
- Modify: `tests/test_browser.py` (integration tests), `dev/smoke.py`

- [ ] **Step 1: Create the local static fixture**

Create `tests/fixtures/login.html`:

```html
<!DOCTYPE html>
<html>
<head><title>Login Fixture</title></head>
<body>
  <header><a href="#home">Home</a></header>
  <main>
    <h1>Sign in</h1>
    <form>
      <label>Username <input type="text" aria-label="Username"></label>
      <label>Password <input type="password" aria-label="Password"></label>
      <button type="button" onclick="document.title='clicked'">Submit</button>
    </form>
  </main>
</body>
</html>
```

- [ ] **Step 2: Write skip-guarded integration tests**

Append to `tests/test_browser.py`:

```python
import importlib.util
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "login.html"


def _browser_available() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    cache = Path.home() / ".cache" / "ms-playwright"
    return cache.exists() and any(cache.glob("chromium-*"))


requires_browser = pytest.mark.skipif(
    not _browser_available(), reason="playwright/Chromium not installed"
)


@requires_browser
@pytest.mark.asyncio
async def test_real_navigate_snapshot_click(tmp_workdir):
    from riftor.config import Config
    from riftor.tools import ToolContext
    from riftor import tools

    ctx = ToolContext(workdir=tmp_workdir, config=Config(browser_headless=True))
    url = _FIXTURE.as_uri()
    r = await tools.get("browser_navigate").execute({"url": url}, ctx)
    assert not r.is_error
    assert "Sign in" in r.content
    assert "[ref=e" in r.content  # interactive elements got refs
    # find a ref for the Submit button from the snapshot text
    import re
    refs = re.findall(r"button \"Submit\" \[ref=(e\d+)\]", r.content)
    assert refs, r.content
    rc = await tools.get("browser_click").execute({"ref": refs[0]}, ctx)
    assert not rc.is_error
    # HARDENING (from Task 4 review): the click must actually ACTUATE — not just
    # return without error — to catch any AX-role vs ARIA-role mismatch in
    # resolve_ref. The fixture's Submit button sets document.title='clicked'.
    rt = await tools.get("browser_eval").execute({"js": "document.title"}, ctx)
    assert "clicked" in rt.content
    # screenshot writes a file
    rs = await tools.get("browser_screenshot").execute({}, ctx)
    assert not rs.is_error
    assert (tmp_workdir / ".riftor" / "screenshots" / "001.png").exists()
    await ctx.browser.close()
```

- [ ] **Step 3: Run integration test (passes if browser present, skips otherwise)**

Run: `uv run pytest tests/test_browser.py::test_real_navigate_snapshot_click -v`
Expected: PASS (browser installed in Task 1) — or SKIP on a machine without Chromium.

- [ ] **Step 4: Add a skip-guarded smoke pass**

`dev/smoke.py` defines several top-level functions and runs them from a
`if __name__ == "__main__":` block (e.g. `asyncio.run(main())`,
`asyncio.run(test_tools())`, ...). Add a new standalone async function and call
it from that block. Add this function near the other test functions:

```python
async def _browser_smoke() -> None:
    """Lazy-launch → navigate (local fixture) → teardown. Skips if no Chromium."""
    import importlib.util
    from pathlib import Path

    if importlib.util.find_spec("playwright") is None:
        print("smoke: browser skipped (no playwright)")
        return
    cache = Path.home() / ".cache" / "ms-playwright"
    if not (cache.exists() and any(cache.glob("chromium-*"))):
        print("smoke: browser skipped (no Chromium binary)")
        return
    import tempfile
    from riftor.config import Config
    from riftor.tools import ToolContext
    from riftor import tools

    fixture = Path(__file__).parent.parent / "tests" / "fixtures" / "login.html"
    with tempfile.TemporaryDirectory() as d:
        ctx = ToolContext(workdir=Path(d), config=Config(browser_headless=True))
        r = await tools.get("browser_navigate").execute({"url": fixture.as_uri()}, ctx)
        assert not r.is_error and "Sign in" in r.content, r.content
        await ctx.browser.close()
    print("smoke: browser ok")
```

Then add `asyncio.run(_browser_smoke())` as the last line inside the
`if __name__ == "__main__":` block (after `test_session()`).

- [ ] **Step 5: Run the smoke test**

Run: `uv run python dev/smoke.py`
Expected: existing smoke output + `smoke: browser ok` (or a skip line).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/login.html tests/test_browser.py dev/smoke.py
git commit -m "test: real-browser integration tests + smoke (skip-guarded)"
```

---

## Task 12: Inline screenshot rendering in the TUI (optional `textual-image`)

**Files:**
- Modify: `riftor/tui/app.py` — top-of-file soft import; render screenshot results inline in `_run_tool` after `browser_screenshot`.

- [ ] **Step 1: Add the soft import at module top**

Near the top of `riftor/tui/app.py` (with the other imports), add:

```python
# Optional inline image rendering. textual-image needs Python >= 3.12 and a
# graphics-capable terminal (Kitty/Sixel); import at module top per its detection
# requirement. A failed import simply means we show the screenshot path instead.
try:
    from textual_image.widget import Image as _InlineImage  # type: ignore
except Exception:  # noqa: BLE001 — never let a missing optional dep break startup
    _InlineImage = None
```

- [ ] **Step 2: Render the screenshot inline after a successful `browser_screenshot`**

In `_run_tool`, after `await self._show_tool_result(result.content, is_error=result.is_error)` (line 1399), add:

```python
        if (
            call.name == "browser_screenshot"
            and not result.is_error
            and _InlineImage is not None
        ):
            # parse "screenshot saved → <path> (...)" from the result content
            import re

            m = re.search(r"saved → (\S+\.png)", result.content)
            if m:
                from pathlib import Path as _P

                shot = _P(m.group(1))
                if shot.exists():
                    try:
                        await self._mount(_InlineImage(str(shot)))
                    except Exception:  # noqa: BLE001 — fall back to the path line already shown
                        pass
```

(The path line is always shown by `_show_tool_result`; inline rendering is purely additive, so the path fallback is automatic when `_InlineImage` is None or rendering fails — including inside tmux.)

- [ ] **Step 3: Verify startup is unaffected on 3.11 (import is None) and 3.12**

Run: `uv run python -c "from riftor.tui import app; print('inline:', app._InlineImage is not None)"`
Expected: `ok` — prints `inline: True` or `inline: False` depending on environment; either way no crash.

- [ ] **Step 4: Lint + typecheck + full suite**

Run: `uv run ruff check riftor && uv run pyright riftor && uv run pytest`
Expected: all PASS / no errors.

- [ ] **Step 5: Commit**

```bash
git add riftor/tui/app.py
git commit -m "feat: render browser screenshots inline when terminal supports it"
```

---

## Task 13: Docs + system prompt awareness

**Files:**
- Modify: `docs/configuration.md` (browser config options), `docs/riftor.1` (man page: `--browser-headed`, `/browser`), `riftor/agent/prompts/system.md` (tell the model the browser tools exist)

- [ ] **Step 1: Document the config options**

In `docs/configuration.md`, add a "Browser" subsection documenting `browser_headless` (default true) and `browser_persistent_profile` (default false, incognito), plus the `/browser` command and `--browser-headed` flag. Note Chromium auto-installs on first use and that inline rendering needs `pip install 'riftor[browser-ui]'` on Python ≥ 3.12.

- [ ] **Step 2: Update the man page**

In `docs/riftor.1`, add `--browser-headed` to the OPTIONS section and `/browser` to any command listing, mirroring the style of existing entries.

- [ ] **Step 3: Make the model aware of the browser tools**

In `riftor/agent/prompts/system.md`, add a short paragraph (in the tools/methodology area) noting the agent can drive a browser via `browser_navigate`/`browser_snapshot`/`browser_click`/`browser_type` for SPA recon and authenticated flows, acts on elements by their `[ref=eN]` ids from the latest snapshot, and that `browser_eval` runs arbitrary JS and is gated like `bash`.

- [ ] **Step 4: Verify the prompt still loads**

Run: `uv run python -c "from riftor.agent.context import Context; c=Context(lore=False); print('system prompt ok', len(c.messages))"`
Expected: prints `system prompt ok ...` with no error.

- [ ] **Step 5: Run all CI gates**

Run: `make check`
Expected: lint → typecheck → test → smoke all PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/configuration.md docs/riftor.1 riftor/agent/prompts/system.md
git commit -m "docs: document browser tools, config, flag; make the model aware"
```

---

## Self-Review notes (resolved)

- **Spec coverage:** every spec decision maps to a task — embed (T3/T5), snapshot-primary (T4), inline+path screenshot (T11/T12), lean 6+2 tools with correct flags (T5), lazy session-scoped manager on ToolContext (T2/T3), incognito-default + first-run hint (T6/T9), headed/headless via config+flag+slash (T6/T8/T9), core dep + auto-install + doctor (T1/T3/T7), action+snapshot result (T5), mocked+skip-guarded tests (T3-T6, T11).
- **Type consistency:** `BrowserManager.page()/.close()/.launched/.snapshot_text()/.resolve_ref()`, tool names, and config field names are identical across all tasks.
- **No placeholders:** every code step shows the actual code; commands include expected output.
- **Known runtime detail deferred to implementation:** `page.accessibility.snapshot()` is deprecated-but-functional (Task 4 uses it; if a worker finds it removed in the installed Playwright, switch to an ARIA-locator walk producing the same `- role "name" [ref=eN]` format — the contract, asserted by tests, does not change).
