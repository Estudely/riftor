"""/memory operator command (Textual harness)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.widgets import Input

import riftor.config as cfgmod
from riftor.config import Config
from riftor.engagement.memory import MemoryStore
from riftor.tui.app import RiftorApp


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


@pytest.mark.asyncio
async def test_memory_add_then_persists(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/memory add target hates noise"
            await pilot.press("enter")
            await pilot.pause()
            rows = MemoryStore(workdir).list()
            assert any(r["text"] == "target hates noise" for r in rows)
            assert rows[0]["source"] == "operator"


@pytest.mark.asyncio
async def test_memory_add_with_tag(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/memory add [creds] admin:admin on 10.0.0.1"
            await pilot.press("enter")
            await pilot.pause()
            rows = MemoryStore(workdir).list()
            assert rows[0]["tag"] == "creds"
            assert rows[0]["text"] == "admin:admin on 10.0.0.1"


@pytest.mark.asyncio
async def test_memory_rm(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        entry = MemoryStore(workdir).add("delete me", source="operator")
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = f"/memory rm {entry.id}"
            await pilot.press("enter")
            await pilot.pause()
            assert MemoryStore(workdir).list() == []


@pytest.mark.asyncio
async def test_memory_clear(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        MemoryStore(workdir).add("a", source="operator")
        MemoryStore(workdir).add("b", source="operator")
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/memory clear"
            await pilot.press("enter")
            await pilot.pause()
            assert MemoryStore(workdir).list() == []


@pytest.mark.asyncio
async def test_memory_add_empty_after_tag_not_persisted(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/memory add [a]   "
            await pilot.press("enter")
            await pilot.pause()
            assert MemoryStore(workdir).list() == []


@pytest.mark.asyncio
async def test_memory_bare_lists_without_crash(monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", Path(tempfile.mkdtemp()))
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        MemoryStore(workdir).add("a note", source="operator")
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/memory"
            await pilot.press("enter")
            await pilot.pause()
            # listing branch executed without error; row still present
            assert len(MemoryStore(workdir).list()) == 1
