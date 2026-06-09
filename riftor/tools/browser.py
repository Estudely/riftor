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
