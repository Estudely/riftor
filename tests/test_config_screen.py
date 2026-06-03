"""ConfigScreen: the redesigned modal renders its fields and round-trips values."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select, Switch

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import RiftorApp
from riftor.tui.config_screen import ConfigScreen


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


@pytest.mark.asyncio
async def test_config_modal_renders_all_fields():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfigScreen)
            screen = app.screen
            # every field is present and addressable by its stable id
            for fid, kind in [
                ("#cfg-provider", Select), ("#cfg-model-select", Select),
                ("#cfg-model", Input), ("#cfg-base", Input), ("#cfg-key", Input),
                ("#cfg-temp", Input), ("#cfg-maxtok", Input),
                ("#cfg-theme", Select), ("#cfg-lore", Switch),
            ]:
                assert screen.query_one(fid, kind) is not None, fid
            # three grouped section headers (MODEL / GENERATION / APPEARANCE)
            assert len(list(screen.query(".config-section"))) == 3
            # aligned label column: one .field-label per field row
            assert len(list(screen.query(".field-label"))) == 10
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
@pytest.mark.parametrize("height", [24, 30, 40])
async def test_save_cancel_buttons_visible_on_short_terminals(height):
    # regression: the footer used to render below the viewport on short terminals
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test(size=(90, height)) as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            for bid in ("#save", "#cancel"):
                btn = app.screen.query_one(bid, Button)
                r = btn.region
                assert r.y >= 0 and (r.y + r.height) <= height, (
                    f"{bid} off-screen at height={height}: y={r.y} h={r.height}"
                )


@pytest.mark.asyncio
async def test_theme_previews_live_and_reverts_on_cancel():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434", theme="rift")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            assert app.theme == "rift"
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            # changing the dropdown previews instantly — before Save
            app.screen.query_one("#cfg-theme", Select).value = "paper"
            await pilot.pause()
            assert app.theme == "paper", "theme should preview live"
            # cancelling reverts to the original
            await pilot.press("escape")
            await pilot.pause()
            assert app.theme == "rift", "cancel should revert the preview"


@pytest.mark.asyncio
async def test_config_modal_saves_changes():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#cfg-temp", Input).value = "0.7"
            app.screen.query_one("#cfg-maxtok", Input).value = "4096"
            app.screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.temperature == 0.7
            assert app.config.max_tokens == 4096


@pytest.mark.asyncio
async def test_provider_pick_prefills_base_and_models():
    from textual.widgets import Select
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            for fid in ("#cfg-provider", "#cfg-model-select", "#cfg-base", "#cfg-fetch"):
                assert screen.query_one(fid) is not None, fid
            screen.query_one("#cfg-provider", Select).value = "openai"
            await pilot.pause()
            assert screen.query_one("#cfg-base", Input).value == "https://api.openai.com/v1"
            opts = [v for _, v in screen.query_one("#cfg-model-select", Select)._options]
            assert "gpt-5.5" in opts
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_save_assembles_prefixed_model_and_writes_key():
    from textual.widgets import Select
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#cfg-provider", Select).value = "openai"
            await pilot.pause()
            screen.query_one("#cfg-model-select", Select).value = "gpt-5.5"
            screen.query_one("#cfg-key", Input).value = "sk-openai-test"
            screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.model == "openai/gpt-5.5"
            assert app.config.providers["openai"].api_key == "sk-openai-test"
