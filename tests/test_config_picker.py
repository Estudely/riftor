"""Keyboard behavior for the prompt-owned inline config picker."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widget import Widget
from textual.widgets import Button, Input, Select, Static, Switch

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import RiftorApp
from riftor.tui.widgets import CommandDropdown, StatusBar


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RiftorApp:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", config_dir / "config.toml")
    monkeypatch.setattr(cfgmod, "PERMISSIONS_PATH", config_dir / "permissions.toml")
    monkeypatch.setattr(cfgmod, "KEYBINDINGS_PATH", config_dir / "keybindings.toml")
    config = Config(
        onboarded=True,
        model="openai/gpt-5.5",
        temperature=0.7,
        max_tokens=4096,
        max_steps=24,
        worker_model="anthropic/claude-sonnet-4-6",
        worker_max_parallel=3,
        worker_timeout_s=180,
        theme="paper",
        show_thinking=False,
        show_tool_output=True,
        browser_headless=False,
        browser_persistent_profile=True,
    )
    return RiftorApp(config, workdir=tmp_path)


async def _type_text(pilot, text: str) -> None:
    await pilot.press(*("space" if character == " " else character for character in text))


async def _open_picker(app: RiftorApp, pilot) -> Widget:
    await _type_text(pilot, "/config")
    await pilot.press("enter")
    await pilot.pause()
    picker = app.query_one("#config-picker", Widget)
    assert picker.display
    return picker


def _widget_text(widget: Widget) -> str:
    nodes = ([widget] if isinstance(widget, Static) else []) + list(widget.query(Static))
    return " ".join(str(node.content) for node in nodes)


def _visible_setting_rows(picker: Widget) -> list[Widget]:
    return [row for row in picker.query(".config-setting-row") if row.display]


def _highlighted_setting(picker: Widget) -> Widget:
    highlighted = [
        row for row in _visible_setting_rows(picker) if row.has_class("highlighted")
    ]
    assert len(highlighted) == 1
    return highlighted[0]


def _picker_is_open(app: RiftorApp) -> bool:
    matches = list(app.query("#config-picker"))
    return bool(matches and matches[0].display)


def _assert_row_value(rows: list[Widget], label: str, value: str) -> None:
    label = label.casefold()
    value = value.casefold()
    rendered = [_widget_text(row).casefold() for row in rows]
    assert any(label in text and value in text for text in rendered), rendered


@pytest.mark.asyncio
async def test_config_opens_inline_above_prompt_without_replacing_main_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        original_screen = app.screen

        picker = await _open_picker(app, pilot)
        prompt = app.query_one("#prompt", Input)

        assert app.screen is original_screen
        assert prompt.has_focus
        top_level = list(app.screen.children)
        assert top_level.index(picker) + 1 == top_level.index(prompt)
        assert app.query_one("#chat").display
        assert app.query_one("#sidebar").display
        assert app.query_one(StatusBar).display
        assert not app.query_one("#cmd-dropdown", CommandDropdown).visible
        assert not list(picker.query(Button))
        assert not list(picker.query(Select))
        assert not list(picker.query(Switch))


@pytest.mark.asyncio
async def test_root_renders_grouped_settings_with_current_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)

        groups = [
            _widget_text(group).strip()
            for group in picker.query(".config-setting-group")
        ]
        assert groups == ["MODEL", "GENERATION", "WORKERS", "APPEARANCE", "DISPLAY"]

        rows = _visible_setting_rows(picker)
        _assert_row_value(rows, "Model", "openai/gpt-5.5")
        _assert_row_value(rows, "Temperature", "0.7")
        _assert_row_value(rows, "Worker model", "anthropic/claude-sonnet-4-6")
        _assert_row_value(rows, "Theme", "paper")
        _assert_row_value(rows, "Show thinking", "off")


@pytest.mark.asyncio
async def test_typing_filters_setting_rows_and_never_opens_command_autocomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        prompt = app.query_one("#prompt", Input)

        await _type_text(pilot, "theme")
        await pilot.pause()

        assert prompt.value == "theme"
        visible_rows = _visible_setting_rows(picker)
        assert len(visible_rows) == 1
        assert "Theme" in _widget_text(visible_rows[0])
        assert not app.query_one("#cmd-dropdown", CommandDropdown).visible

        await pilot.press("ctrl+shift+a")
        await _type_text(pilot, "/")
        await pilot.pause()
        assert prompt.value == "/"
        assert not app.query_one("#cmd-dropdown", CommandDropdown).visible


@pytest.mark.asyncio
async def test_up_and_down_move_the_visible_row_highlight_without_selecting_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        rows = _visible_setting_rows(picker)
        assert len(rows) > 2

        first = _highlighted_setting(picker)
        assert first is rows[0]
        assert not any(
            group.has_class("highlighted")
            for group in picker.query(".config-setting-group")
        )

        await pilot.press("down")
        await pilot.pause()
        assert _highlighted_setting(picker) is rows[1]

        await pilot.press("up")
        await pilot.pause()
        assert _highlighted_setting(picker) is first


@pytest.mark.asyncio
async def test_enter_opens_choice_view_and_escape_returns_to_root_then_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        prompt = app.query_one("#prompt", Input)
        await _type_text(pilot, "theme")
        await pilot.press("enter")
        await pilot.pause()

        choices = [
            choice
            for choice in picker.query(".config-choice-row")
            if choice.display
        ]
        choice_text = " ".join(_widget_text(choice).casefold() for choice in choices)
        assert prompt.has_focus
        assert {"rift", "dusk", "paper"} <= set(choice_text.split())

        await pilot.press("escape")
        await pilot.pause()
        assert _picker_is_open(app)
        assert any("Theme" in _widget_text(row) for row in _visible_setting_rows(picker))
        assert not [
            choice
            for choice in picker.query(".config-choice-row")
            if choice.display
        ]

        await pilot.press("escape")
        await pilot.pause()
        assert not _picker_is_open(app)


@pytest.mark.asyncio
async def test_editor_and_close_restore_prompt_password_history_and_slash_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "offline response")
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        normal_placeholder = prompt.placeholder
        normal_password = prompt.password

        await _type_text(pilot, "remember me")
        await pilot.press("enter")
        await pilot.pause()

        picker = await _open_picker(app, pilot)
        await _type_text(pilot, "api key")
        await pilot.press("enter")
        await pilot.pause()

        assert prompt.has_focus
        assert prompt.password is True
        assert "API key" in _widget_text(picker)
        await _type_text(pilot, "discarded secret")

        await pilot.press("escape")
        await pilot.pause()
        assert _picker_is_open(app)
        assert prompt.password is False

        await pilot.press("escape")
        await pilot.pause()
        assert not _picker_is_open(app)
        assert prompt.has_focus
        assert prompt.placeholder == normal_placeholder
        assert prompt.value == ""
        assert prompt.password is normal_password

        await pilot.press("up")
        await pilot.pause()
        assert prompt.value == "remember me"

        await pilot.press("ctrl+shift+a", "backspace")
        await _type_text(pilot, "/he")
        await pilot.pause()
        assert app.query_one("#cmd-dropdown", CommandDropdown).visible


@pytest.mark.asyncio
async def test_short_terminal_scrolls_highlight_and_keeps_hint_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    height = 24
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test(size=(90, height)) as pilot:
        picker = await _open_picker(app, pilot)
        rows = _visible_setting_rows(picker)
        assert len(rows) > 8

        await pilot.press(*(["down"] * (len(rows) - 1)))
        await pilot.pause()

        highlighted = _highlighted_setting(picker)
        hint = picker.query_one("#config-picker-hint", Widget)
        prompt = app.query_one("#prompt", Input)
        assert highlighted is rows[-1]
        assert prompt.has_focus
        assert hint.display
        hint_text = _widget_text(hint).casefold()
        assert "enter" in hint_text
        assert "esc" in hint_text
        assert ("↑" in hint_text and "↓" in hint_text) or (
            "up" in hint_text and "down" in hint_text
        )

        for widget in (picker, highlighted, hint, prompt):
            region = widget.region
            assert region.y >= 0
            assert region.y + region.height <= height
        assert highlighted.region.y >= picker.region.y
        assert (
            highlighted.region.y + highlighted.region.height
            <= picker.region.y + picker.region.height
        )
