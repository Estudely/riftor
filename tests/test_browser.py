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
