"""ScreenshotGalleryScreen: renders entries, previews, deletes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.widgets import Button, ListItem, ListView, Static

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import RiftorApp
from riftor.tui.screenshot_gallery import (
    ScreenshotGalleryScreen,
    _PreviewPane,
    _ScreenshotItem,
)


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


def _make_screenshots(workdir: Path, count: int = 3) -> list[Path]:
    shots_dir = workdir / ".riftor" / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(1, count + 1):
        p = shots_dir / f"{i:03d}.png"
        p.write_bytes(b"\x89PNG\x0d\x0a\x1a\x0a" + b"\x00" * 42)
        paths.append(p)
    return paths


@pytest.mark.asyncio
async def test_gallery_empty_renders_note(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config()
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ScreenshotGalleryScreen)
            screen = app.screen
            # empty state: the note widget exists, and there is no list
            assert screen.query_one("#gallery-empty", Static) is not None
            assert not screen.query("#gallery-list")
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_gallery_renders_entries(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=3)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ScreenshotGalleryScreen)
            screen = app.screen
            lv = screen.query_one("#gallery-list", ListView)
            items = list(lv.query(_ScreenshotItem))
            assert len(items) == 3
            # newest-first ordering; each item carries its source path
            assert all(i.screenshot_path.suffix == ".png" for i in items)
            assert {i.screenshot_path.name for i in items} == {"001.png", "002.png", "003.png"}
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_gallery_select_shows_preview(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=2)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            preview = screen.query_one(_PreviewPane)
            assert preview._current is not None
            assert preview._current.name in {"001.png", "002.png"}
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_gallery_delete_requires_double_press(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=2)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            lv = screen.query_one("#gallery-list", ListView)
            assert len(list(lv.query(ListItem))) == 2
            await pilot.press("d")
            await pilot.pause()
            assert screen._deleting is True
            await pilot.press("escape")
            await pilot.pause()
            assert screen._deleting is False
            assert screen._delete_target is None
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_gallery_delete_removes_entry(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=2)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            lv = screen.query_one("#gallery-list", ListView)
            assert len(list(lv.query(ListItem))) == 2
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert screen._deleting is False
            items = list(lv.query(ListItem))
            assert len(items) == 1
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_gallery_delete_last_screenshot_dismisses(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=1)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert not isinstance(app.screen, ScreenshotGalleryScreen)


@pytest.mark.asyncio
async def test_gallery_delete_button(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=2)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            btn = screen.query_one("#gallery-delete-btn", Button)
            btn.press()
            await pilot.pause()
            assert screen._deleting is True
            btn.press()
            await pilot.pause()
            assert screen._deleting is False
            items = list(screen.query(ListItem))
            assert len(items) == 1
            await pilot.press("escape")


@pytest.mark.asyncio
async def test_gallery_close_button(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        _make_screenshots(workdir, count=1)
        cfg = Config()
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/screenshots"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#gallery-close-btn", Button).press()
            await pilot.pause()
            assert not isinstance(app.screen, ScreenshotGalleryScreen)
