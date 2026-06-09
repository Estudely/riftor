"""Browser tool behavior: context wiring, lifecycle, snapshot, scope, screenshots."""

from __future__ import annotations

import pytest  # noqa: F401  # used by browser tests added in later tasks

from riftor.tools import ToolContext


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
