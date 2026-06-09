"""Browser tool behavior: context wiring, lifecycle, snapshot, scope, screenshots."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from riftor.tools import ToolContext

_FIXTURE = Path(__file__).parent / "fixtures" / "login.html"


def _browser_available() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    cache = Path.home() / ".cache" / "ms-playwright"
    return cache.exists() and any(cache.glob("chromium-*"))


requires_browser = pytest.mark.skipif(
    not _browser_available(), reason="playwright/Chromium not installed"
)


def test_toolcontext_has_browser_field_default_none():
    ctx = ToolContext()
    assert ctx.browser is None


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
    assert isinstance(page, _FakePage)
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


@pytest.mark.asyncio
async def test_launch_failure_reaps_driver_and_raises(monkeypatch, tmp_workdir):
    from riftor.tools import browser as bmod

    started = {"stopped": False}

    class _FakePW:
        async def stop(self):
            started["stopped"] = True

    async def fake_start():
        return _FakePW()

    async def boom(self, profile):
        raise RuntimeError("no chromium")

    async def install_fails(self):
        return False

    # async_playwright().start() → our fake driver
    class _FakeAP:
        def start(self):
            return fake_start()

    # _launch does `from playwright.async_api import async_playwright` at call
    # time, so patch the attribute on the playwright.async_api module itself —
    # the `from ... import` re-reads it each call.
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakeAP())
    monkeypatch.setattr(bmod.BrowserManager, "_do_launch", boom)
    monkeypatch.setattr(bmod.BrowserManager, "_try_install", install_fails)

    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    with pytest.raises(bmod.BrowserError):
        await mgr.page()
    # driver was reaped (close() called _pw.stop() and reset handles)
    assert mgr._pw is None
    assert started["stopped"] is True
    assert not mgr.launched


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


@pytest.mark.asyncio
async def test_resolve_unknown_ref_raises(tmp_workdir):
    from riftor.tools import browser as bmod

    mgr = bmod.BrowserManager(tmp_workdir, headless=True, persistent=False)
    with pytest.raises(KeyError):
        mgr.resolve_ref("e99")


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


def test_config_browser_defaults_and_toml():
    from riftor.config import Config

    cfg = Config()
    assert cfg.browser_headless is True
    assert cfg.browser_persistent_profile is False
    toml = cfg._to_toml()
    assert "browser_headless = true" in toml
    assert "browser_persistent_profile = false" in toml


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
