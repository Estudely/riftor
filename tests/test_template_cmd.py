"""/template operator command (Textual harness)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.widgets import Input

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import RiftorApp


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


@pytest.mark.asyncio
async def test_template_apply_sets_stage_and_active(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/template webapp"
            await pilot.press("enter")
            await pilot.pause()
            assert app.engagement.active_template() == "webapp"
            assert app.engagement.stage == "R"


@pytest.mark.asyncio
async def test_template_off_clears(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        app.engagement.set_template("api")
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/template off"
            await pilot.press("enter")
            await pilot.pause()
            assert app.engagement.active_template() is None


@pytest.mark.asyncio
async def test_template_unknown_does_not_crash(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/template nonsense"
            await pilot.press("enter")
            await pilot.pause()
            assert app.engagement.active_template() is None
