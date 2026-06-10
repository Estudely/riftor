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
                ("#cfg-provider", Select), ("#cfg-model-filter", Input),
                ("#cfg-model-select", Select),
                ("#cfg-model", Input), ("#cfg-base", Input), ("#cfg-key", Input),
                ("#cfg-temp", Input), ("#cfg-maxtok", Input), ("#cfg-maxsteps", Input),
                ("#cfg-chakla-provider", Select), ("#cfg-chakla-model-filter", Input),
                ("#cfg-chakla-model-select", Select),
                ("#cfg-chakla-custom", Input),
                ("#cfg-label-main", Input), ("#cfg-label-worker", Input),
                ("#cfg-theme", Select), ("#cfg-lore", Switch),
                ("#cfg-show-thinking", Switch), ("#cfg-show-tool-output", Switch),
                ("#cfg-browser-headless", Switch), ("#cfg-browser-persistent", Switch),
                ("#cfg-reasoning-effort", Select),
            ]:
                assert screen.query_one(fid, kind) is not None, fid
            # five grouped section headers (MODEL / GENERATION / WORKERS / APPEARANCE / DISPLAY)
            assert len(list(screen.query(".config-section"))) == 5
            # aligned label column: one .field-label per field row. WORKERS now has
            # 3 picker rows + 2 label rows (was 1 plain input + 2 labels) => +2.
            # +3 field rows for the DISPLAY section => 15 + 3 = 18, plus the
            # GENERATION "Tool call steps" row => 19, plus the MODEL "Codex login"
            # status row => 20, plus 2 DISPLAY browser switches => 22,
            # plus 2 model search/filter rows (MODEL + WORKERS) => 24.
            assert len(list(screen.query(".field-label"))) == 24
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
            app.screen.query_one("#cfg-maxsteps", Input).value = "24"
            app.screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.temperature == 0.7
            assert app.config.max_tokens == 4096
            # max_steps flows to both the persisted config and the live loop budget.
            assert app.config.max_steps == 24
            assert app.max_steps == 24


@pytest.mark.asyncio
async def test_display_settings_save():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="ollama_chat/x", api_base="http://localhost:11434")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#cfg-show-thinking", Switch).value = False
            screen.query_one("#cfg-show-tool-output", Switch).value = False
            screen.query_one("#cfg-reasoning-effort", Select).value = "high"
            screen.query_one("#save").press()
            await pilot.pause()
            assert app.config.show_thinking is False
            assert app.config.show_tool_output is False
            assert app.config.reasoning_effort == "high"


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
            # public-API check: gpt-5.5 is a selectable option after picking openai
            sel = screen.query_one("#cfg-model-select", Select)
            sel.value = "gpt-5.5"
            await pilot.pause()
            assert sel.value == "gpt-5.5"
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


@pytest.mark.asyncio
async def test_config_opens_with_non_curated_model():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        # a model id NOT in PROVIDER_DEFAULTS — must not crash on open
        cfg = Config(model="anthropic/claude-some-old-model-2024")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfigScreen)
            from textual.widgets import Select
            assert app.screen.query_one("#cfg-model-select", Select) is not None
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_fetch_button_repopulates_models(monkeypatch):
    from textual.widgets import Select
    import riftor.tui.config_screen as cs
    fake = cs.FetchResult(models=["m-one", "m-two"], source="merged", error=None)
    monkeypatch.setattr(cs, "fetch_models", lambda *a, **k: fake)
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="openai/gpt-5.5")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#cfg-fetch", Button).press()
            await pilot.pause()
            await pilot.pause()
            sel = screen.query_one("#cfg-model-select", Select)
            sel.value = "m-two"            # only legal if the fetched option was applied
            await pilot.pause()
            assert sel.value == "m-two"


@pytest.mark.asyncio
async def test_worker_provider_switch_does_not_clobber_main_base():
    # Regression: switching the WORKER provider Select used to rewrite the shared
    # #cfg-base field, so saving corrupted the MAIN provider's stored base. Drive
    # the REAL ConfigScreen + _open_config and assert the clobber invariant.
    from riftor.providers import PROVIDERS
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="openai/gpt-5.5")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ConfigScreen)
            # main = openai + key; base auto-fills to openai's default
            screen.query_one("#cfg-provider", Select).value = "openai"
            await pilot.pause()
            screen.query_one("#cfg-key", Input).value = "sk-openai"
            # switch the WORKER provider to a DIFFERENT provider (deepseek) and
            # pick a worker model, so a distinct worker provider is actually saved
            screen.query_one("#cfg-chakla-provider", Select).value = "deepseek"
            await pilot.pause()
            screen.query_one("#cfg-chakla-custom", Input).value = "deepseek-chat"
            # save WITHOUT re-touching the base field
            screen.query_one("#save").press()
            await pilot.pause()
        # INVARIANT: the main openai base is untouched (NOT deepseek's base)
        assert app.config.providers["openai"].api_base == PROVIDERS["openai"].default_base
        assert app.config.providers["openai"].api_key == "sk-openai"
        # the worker provider got ITS OWN default base, and reused the shared key
        assert app.config.providers["deepseek"].api_base == PROVIDERS["deepseek"].default_base
        assert app.config.providers["deepseek"].api_key == "sk-openai"


@pytest.mark.asyncio
async def test_codex_provider_hides_key_base_fetch_shows_login(monkeypatch):
    # Picking the Codex provider hides API-key/Base-URL/Fetch (Codex reads
    # ~/.codex/auth.json — no key/base/model-list) and reveals the login status.
    # Switching back to another provider restores the standard rows.
    import riftor.tui.config_screen as cs
    from riftor.codex_auth import CodexAuthStatus

    monkeypatch.setattr(
        cs, "auth_status",
        lambda: CodexAuthStatus(logged_in=True, expires_in_s=600, detail="logged in (10m left)"))
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ConfigScreen)

            def row_hidden(wid: str) -> bool:
                return screen.query_one(wid).parent.has_class("hidden")

            def row_visible(wid: str) -> bool:
                # Textual sets widget.display=False when `display: none` applies
                # via CSS. This is the REAL visibility, so a missing
                # `.field-row.hidden` rule would make a "hidden" row still True.
                return screen.query_one(wid).parent.display

            # Non-codex provider: standard rows visible, status row hidden.
            assert not row_hidden("#cfg-key")
            assert not row_hidden("#cfg-base")
            assert not row_hidden("#cfg-fetch")
            assert row_hidden("#cfg-codex-status")
            assert row_visible("#cfg-key")
            assert row_visible("#cfg-base")
            assert row_visible("#cfg-fetch")
            assert not row_visible("#cfg-codex-status")

            # Switch to Codex: key/base/fetch hidden, login status shown.
            screen.query_one("#cfg-provider", Select).value = "codex"
            await pilot.pause()
            assert row_hidden("#cfg-key")
            assert row_hidden("#cfg-base")
            assert row_hidden("#cfg-fetch")
            assert not row_hidden("#cfg-codex-status")
            # Real computed visibility: the CSS rule must actually take effect.
            assert not row_visible("#cfg-key")
            assert not row_visible("#cfg-base")
            assert not row_visible("#cfg-fetch")
            assert row_visible("#cfg-codex-status")
            status_text = screen._codex_status_text()
            assert "✓" in status_text and "logged in" in status_text

            # Switch back: the standard rows return and the status hides.
            screen.query_one("#cfg-provider", Select).value = "anthropic"
            await pilot.pause()
            assert not row_hidden("#cfg-key")
            assert not row_hidden("#cfg-base")
            assert not row_hidden("#cfg-fetch")
            assert row_hidden("#cfg-codex-status")
            assert row_visible("#cfg-key")
            assert row_visible("#cfg-base")
            assert row_visible("#cfg-fetch")
            assert not row_visible("#cfg-codex-status")
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_codex_status_text_marks_logged_out(monkeypatch):
    import riftor.tui.config_screen as cs
    from riftor.codex_auth import CodexAuthStatus
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="anthropic/claude-opus-4-8")
        screen = ConfigScreen(cfg)
        monkeypatch.setattr(
            cs, "auth_status",
            lambda: CodexAuthStatus(logged_in=False, expires_in_s=None,
                                    detail="not logged in — run `codex login`"))
        text = screen._codex_status_text()
        assert text.startswith("⚠") and "not logged in" in text
        monkeypatch.setattr(
            cs, "auth_status",
            lambda: CodexAuthStatus(logged_in=True, expires_in_s=60, detail="token present"))
        assert screen._codex_status_text().startswith("✓")


@pytest.mark.asyncio
async def test_openrouter_model_no_duplicate_option_on_open():
    with tempfile.TemporaryDirectory() as d:
        _patch_paths(Path(d))
        cfg = Config(model="openrouter/auto")
        app = RiftorApp(cfg, workdir=Path(d))
        async with app.run_test() as pilot:
            app.query_one("#prompt", Input).value = "/config"
            await pilot.press("enter")
            await pilot.pause()
            sel = app.screen.query_one("#cfg-model-select", Select)
            # _options is a list of (prompt, value) tuples; extract string values only
            values = [v for _, v in sel._options if isinstance(v, str)]
            assert "auto" not in values, "stripped 'auto' must not appear as a duplicate"
            assert "openrouter/auto" in values, "full slug must be present"
            assert sel.value == "openrouter/auto", "full slug must be selected"
            await pilot.press("escape")
            await pilot.pause()
