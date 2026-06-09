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

from riftor.tools.base import Tool, ToolContext, ToolResult

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
        self._ref_targets: dict[str, dict] = {}
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
                await self.close()  # reap the dangling driver before raising
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
            try:
                await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                return False
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    async def page(self) -> "Page":
        if self._page is None:
            self._context, self._page = await self._launch()
            self._attach_listeners(self._page)
        return self._page

    def _attach_listeners(self, page: "Page") -> None:
        if not hasattr(page, "on"):
            return
        page.on("console", lambda m: self.console_log.append(f"[{m.type}] {m.text}"))
        page.on(
            "requestfinished",
            lambda r: self.network_log.append(f"{r.method} {r.url}"),
        )

    # roles the model can act on → these get a [ref=eN] tag
    _INTERACTIVE = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "switch", "searchbox", "slider",
    }

    # Low-value text leaves the legacy accessibility.snapshot() folded into the
    # parent's name; CDP's getFullAXTree surfaces them as separate nodes. Drop
    # them so the model-facing snapshot stays compact (token economy).
    _NOISE_ROLES = {"StaticText", "InlineTextBox", "ListMarker", "LineBreak"}

    async def _ax_tree(self, page: "Page") -> dict | None:
        """Return a nested ``{role, name, level?, children}`` accessibility tree.

        Playwright removed ``page.accessibility.snapshot()`` in 1.5x, so we source
        the tree from the Chromium DevTools Protocol (``Accessibility.getFullAXTree``)
        and rebuild the same nested-dict shape the old API produced — keeping
        :meth:`snapshot_text` and :meth:`resolve_ref` unchanged. If the legacy API
        is still present (or a test injects a fake page exposing it) we use it."""
        legacy = getattr(page, "accessibility", None)
        if legacy is not None and hasattr(legacy, "snapshot"):
            return await legacy.snapshot(interesting_only=False)

        cdp = await page.context.new_cdp_session(page)
        try:
            await cdp.send("Accessibility.enable")
            raw = await cdp.send("Accessibility.getFullAXTree")
        finally:
            try:
                await cdp.detach()
            except Exception:  # noqa: BLE001
                pass
        nodes = {n["nodeId"]: n for n in raw.get("nodes", [])}
        if not nodes:
            return None

        def convert_children(node: dict) -> list[dict]:
            kids: list[dict] = []
            for cid in node.get("childIds") or []:
                child = nodes.get(cid)
                if child is None:
                    continue
                if child.get("ignored"):
                    # ignored container: drop the node but splice its (recursively
                    # un-ignored) descendants up into the parent — matching how the
                    # legacy snapshot API elided ignored/presentational wrappers.
                    kids.extend(convert_children(child))
                    continue
                kids.append(convert(child))
            return kids

        def convert(node: dict) -> dict:
            role = (node.get("role") or {}).get("value", "") or ""
            name = (node.get("name") or {}).get("value", "") or ""
            out: dict = {"role": role, "name": name}
            for prop in node.get("properties") or []:
                if prop.get("name") == "level":
                    lvl = (prop.get("value") or {}).get("value")
                    if lvl:
                        out["level"] = lvl
            out["children"] = convert_children(node)
            return out

        # The first node is the document root (RootWebArea).
        root = raw["nodes"][0]
        return convert(root)

    async def snapshot_text(self) -> str:
        """Compact, ref-tagged accessibility tree of the current page. Interactive
        nodes get a stable [ref=eN] id used by click/type. Refs reset each call."""
        page = await self.page()
        tree = await self._ax_tree(page)
        self._ref_targets = {}
        lines: list[str] = []
        counter = 0

        def walk(node: dict, depth: int) -> None:
            nonlocal counter
            role = node.get("role", "")
            if role in ("WebArea", "RootWebArea", "", "generic", "none"):
                # elided container: keep children at the parent's depth (no extra indent)
                for child in node.get("children", []) or []:
                    walk(child, depth)
                return
            if role in self._NOISE_ROLES:
                # drop redundant text leaves; recurse children defensively
                for child in node.get("children", []) or []:
                    walk(child, depth)
                return
            name = node.get("name", "") or ""
            indent = "  " * depth
            label = f'{role} "{name}"' if name else role
            if node.get("level"):
                label += f" [level={node['level']}]"
            if role in self._INTERACTIVE:
                counter += 1
                ref = f"e{counter}"
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
        node = self._ref_targets.get(ref)
        if node is None:
            raise KeyError(ref)
        role = node.get("role", "")
        name = node.get("name", "") or ""
        page = self._page
        assert page is not None
        # NOTE: the AX tree (CDP getFullAXTree, see _ax_tree) yields role names
        # that coincide with ARIA roles for the _INTERACTIVE set (button/link/
        # textbox/etc). get_by_role does not validate the role, so an out-of-
        # vocabulary role would match nothing → a later action times out rather
        # than erroring here. The Task 11 integration test asserts a resolved
        # locator actually actuates (Submit sets document.title), catching a
        # mismatch against a real browser.
        if name:
            return page.get_by_role(role, name=name).first
        return page.get_by_role(role).first

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
        self._ref_targets.clear()


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
