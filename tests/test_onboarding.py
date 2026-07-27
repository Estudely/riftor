"""Onboarding wizard: step visibility, provider→model refresh, finish payload."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from textual.widgets import Button, Input, Select

import riftor.config as cfgmod
from riftor.config import Config
from riftor.providers import PROVIDER_DEFAULTS, apply_prefix
from riftor.tui.app import RiftorApp
from riftor.tui.onboarding import OnboardingScreen, _model_options


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


@pytest.mark.asyncio
async def test_onboarding_shows_one_step_at_a_time():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        screen = OnboardingScreen(Config())
        from textual.app import App

        class _Host(App):
            pass

        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            assert not screen.query_one("#onboard-step-0").has_class("hidden")
            assert screen.query_one("#onboard-step-1").has_class("hidden")
            assert screen.query_one("#onboard-step-2").has_class("hidden")
            screen._show_step(1)
            assert screen.query_one("#onboard-step-0").has_class("hidden")
            assert not screen.query_one("#onboard-step-1").has_class("hidden")


@pytest.mark.asyncio
async def test_provider_change_refreshes_model_list():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        screen = OnboardingScreen(Config(model="openrouter/auto"))
        from textual.app import App

        class _Host(App):
            pass

        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            event = Mock()
            event.select = screen.query_one("#ob-provider", Select)
            event.value = "deepseek"
            screen.on_select_changed(event)
            model_sel = screen.query_one("#ob-model", Select)
            values = [v for _, v in model_sel._options if v != Select.NULL]  # noqa: SLF001
            assert values == PROVIDER_DEFAULTS["deepseek"]


def test_model_options_for_deepseek():
    assert _model_options("deepseek") == [
        ("deepseek-v4-pro", "deepseek-v4-pro"),
        ("deepseek-v4-flash", "deepseek-v4-flash"),
    ]


def test_apply_prefix_deepseek():
    assert apply_prefix("deepseek", "deepseek-v4-pro") == "deepseek/deepseek-v4-pro"


@pytest.mark.asyncio
async def test_app_launches_onboarding_and_completes_deepseek_flow():
    """End-to-end: fresh app → onboarding modal → provider/model/scope → saved config."""
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        workdir = Path(d) / "proj"
        workdir.mkdir()
        cfg = Config(onboarded=False)
        app = RiftorApp(cfg, workdir=workdir)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.35)  # on_mount timer → OnboardingScreen
            assert isinstance(app.screen, OnboardingScreen)
            screen = app.screen

            prov = screen.query_one("#ob-provider", Select)
            prov.value = "deepseek"
            screen.on_select_changed(Mock(select=prov, value="deepseek"))
            screen.query_one("#ob-key", Input).value = "sk-test"
            screen.on_button_pressed(Mock(button=screen.query_one("#ob-next", Button)))
            await pilot.pause()

            assert screen.query_one("#onboard-step-0").has_class("hidden")
            assert not screen.query_one("#onboard-step-1").has_class("hidden")
            model_sel = screen.query_one("#ob-model", Select)
            values = [v for _, v in model_sel._options if v != Select.NULL]  # noqa: SLF001
            assert values == PROVIDER_DEFAULTS["deepseek"]
            model_sel.value = "deepseek-v4-pro"
            screen.on_button_pressed(Mock(button=screen.query_one("#ob-next", Button)))
            await pilot.pause()

            screen.query_one("#ob-scope", Input).value = "example.com"
            screen.on_button_pressed(Mock(button=screen.query_one("#ob-next", Button)))
            await pilot.pause(0.2)

            assert cfg.onboarded is True
            assert cfg.model == "deepseek/deepseek-v4-pro"
            assert cfg.providers["deepseek"].api_key == "sk-test"
            assert "example.com" in [t for t, _ in app.engagement.store.list_scope()]

