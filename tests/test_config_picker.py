"""Keyboard behavior for the prompt-owned inline config picker."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pytest
from textual.widget import Widget
from textual.widgets import Button, Input, Select, Static, Switch

import riftor.codex_auth as codex_auth_mod
import riftor.config as cfgmod
import riftor.providers as providers_mod
import riftor.tui.config_picker as picker_mod
from riftor.codex_auth import CodexAuthStatus
from riftor.config import REASONING_EFFORTS, Config, ProviderCreds
from riftor.providers import PROVIDER_DEFAULTS, PROVIDERS, FetchResult
from riftor.tui.app import RiftorApp
from riftor.tui.config_picker import ConfigPicker, PickerState
from riftor.tui.theme import THEMES
from riftor.tui.widgets import CommandDropdown, StatusBar


def _make_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: Config | None = None,
) -> RiftorApp:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", config_dir / "config.toml")
    monkeypatch.setattr(cfgmod, "PERMISSIONS_PATH", config_dir / "permissions.toml")
    monkeypatch.setattr(cfgmod, "KEYBINDINGS_PATH", config_dir / "keybindings.toml")
    if config is None:
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


async def _replace_prompt(pilot, text: str) -> None:
    await pilot.press("ctrl+shift+a", "backspace")
    if text:
        await _type_text(pilot, text)
    await pilot.pause()


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


def _rendered_text(widget: Widget) -> str:
    return str(widget.render())


def _visible_setting_rows(picker: Widget) -> list[Widget]:
    return [row for row in picker.query(".config-setting-row") if row.display]


def _visible_choice_rows(picker: Widget) -> list[Widget]:
    return [row for row in picker.query(".config-choice-row") if row.display]


def _highlighted_setting(picker: Widget) -> Widget:
    highlighted = [
        row for row in _visible_setting_rows(picker) if row.has_class("highlighted")
    ]
    assert len(highlighted) == 1
    return highlighted[0]


def _highlighted_choice(picker: Widget) -> Widget:
    highlighted = [
        row for row in _visible_choice_rows(picker) if row.has_class("highlighted")
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


async def _open_setting(
    app: RiftorApp,
    pilot,
    label: str,
    *,
    open_picker: bool = True,
) -> ConfigPicker:
    if open_picker:
        if not _picker_is_open(app):
            await _open_picker(app, pilot)
    picker = app.query_one("#config-picker", ConfigPicker)
    assert picker.state is PickerState.ROOT
    await _replace_prompt(pilot, label)
    rows = _visible_setting_rows(picker)
    exact = [
        row
        for row in rows
        if _rendered_text(row).strip().casefold().startswith(label.casefold())
    ]
    assert len(exact) == 1, [_rendered_text(row) for row in rows]
    await pilot.press("enter")
    await pilot.pause()
    return picker


async def _choose_choice(
    picker: ConfigPicker,
    pilot,
    label: str,
    *,
    query: str | None = None,
) -> None:
    await _replace_prompt(pilot, query or label)
    rows = _visible_choice_rows(picker)
    targets = [
        row
        for row in rows
        if _widget_text(row).strip().casefold() == label.casefold()
    ]
    assert len(targets) == 1, [_widget_text(row) for row in rows]
    current = rows.index(_highlighted_choice(picker))
    target = rows.index(targets[0])
    key = "down" if target > current else "up"
    if target != current:
        await pilot.press(*([key] * abs(target - current)))
        await pilot.pause()
    assert _highlighted_choice(picker) is targets[0]
    await pilot.press("enter")
    await pilot.pause()


async def _select_setting_choice(
    app: RiftorApp,
    pilot,
    setting_label: str,
    choice_label: str,
) -> ConfigPicker:
    picker = await _open_setting(app, pilot, setting_label)
    await _choose_choice(picker, pilot, choice_label)
    return picker


async def _submit_editor(pilot, value: str) -> None:
    await _replace_prompt(pilot, value)
    await pilot.press("enter")
    await pilot.pause()


async def _wait_until(pilot, condition: Callable[[], bool]) -> None:
    for _ in range(50):
        if condition():
            return
        await pilot.pause()
    assert condition()


def _persisted_config() -> Config:
    assert cfgmod.CONFIG_PATH.exists()
    return Config.load()


def _picker_status(picker: Widget) -> Widget:
    matches = list(picker.query("#config-picker-status"))
    assert len(matches) == 1
    return matches[0]


def _picker_status_text(picker: Widget) -> str:
    return _widget_text(_picker_status(picker))


def _assert_success_status(picker: Widget) -> None:
    assert _picker_status(picker).display
    status = _picker_status_text(picker).strip().casefold()
    assert status
    assert any(word in status for word in ("saved", "updated", "applied"))


def _stub_model_fetch(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[[str, str | None, str | None], FetchResult],
) -> None:
    """Patch both supported import styles so model tests never touch the network."""
    monkeypatch.setattr(providers_mod, "fetch_models", fake)
    monkeypatch.setattr(picker_mod, "fetch_models", fake, raising=False)


def _curated_fetch(
    provider_key: str,
    _api_base: str | None,
    _api_key: str | None,
) -> FetchResult:
    return FetchResult(
        models=list(PROVIDER_DEFAULTS[provider_key]),
        source="curated",
    )


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
        _assert_row_value(rows, "Main model", "openai/gpt-5.5")
        _assert_row_value(rows, "Temperature", "0.7")
        _assert_row_value(rows, "Worker model", "anthropic/claude-sonnet-4-6")
        _assert_row_value(rows, "Theme", "paper")
        _assert_row_value(rows, "Show thinking", "off")


@pytest.mark.asyncio
async def test_root_exposes_only_interactive_settings_not_advanced_toml_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)

        expected_labels = (
            "Main model",
            "API credentials",
            "Base URL",
            "Temperature",
            "Max tokens",
            "Tool call steps",
            "Reasoning effort",
            "Worker model",
            "Theme",
            "Show thinking",
            "Show tool output",
            "Browser headless",
            "Persistent profile",
        )
        row_text = [_rendered_text(row).lstrip() for row in _visible_setting_rows(picker)]

        assert len(row_text) == len(expected_labels)
        for label, rendered in zip(expected_labels, row_text, strict=True):
            assert rendered.startswith(label), row_text
        forbidden = (
            "Max parallel",
            "Timeout",
            "Max result",
            "Result preview",
            "Rate limit",
            "Plugins",
            "MCP",
            "Wordlist",
            "Skill",
            "HackerOne",
        )
        assert not any(
            rendered.startswith(forbidden)
            for rendered in row_text
        )


@pytest.mark.asyncio
async def test_markup_like_custom_model_id_renders_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_model = "custom/[bold]literal-model[/bold]"
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model=custom_model),
    )
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        rows = _visible_setting_rows(picker)
        model_rows = [
            row
            for row in rows
            if _rendered_text(row).lstrip().startswith("Main model")
        ]

        assert app.query_one("#prompt", Input).has_focus
        assert len(model_rows) == 1
        assert custom_model in _rendered_text(model_rows[0])


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
async def test_tab_keeps_prompt_focus_and_picker_filtering_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        prompt = app.query_one("#prompt", Input)

        await pilot.press("tab")
        await pilot.pause()
        assert prompt.has_focus

        await _type_text(pilot, "theme")
        await pilot.pause()
        visible_rows = _visible_setting_rows(picker)
        assert prompt.value == "theme"
        assert len(visible_rows) == 1
        assert "Theme" in _widget_text(visible_rows[0])


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
        choice_text = {
            _widget_text(choice).strip().casefold() for choice in choices
        }
        assert prompt.has_focus
        assert choice_text == set(THEMES)

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

        await _open_picker(app, pilot)
        picker = await _open_setting(
            app,
            pilot,
            "API credentials",
            open_picker=False,
        )
        await _choose_choice(picker, pilot, "Replace API key")

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


@pytest.mark.asyncio
async def test_keyboard_activation_message_reaches_the_app_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[ConfigPicker.Activated] = []

    def receive(
        _app: RiftorApp,
        event: ConfigPicker.Activated,
    ) -> None:
        received.append(event)

    monkeypatch.setattr(
        RiftorApp,
        "on_config_picker_activated",
        receive,
        raising=False,
    )
    app = _make_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await _select_setting_choice(app, pilot, "Show thinking", "on")

        assert len(received) == 1
        assert received[0].setting_key == "show_thinking"
        assert received[0].value == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "attribute", "choice", "expected"),
    [
        ("Show thinking", "show_thinking", "on", True),
        ("Show tool output", "show_tool_output", "off", False),
        ("Browser headless", "browser_headless", "on", True),
        ("Persistent profile", "browser_persistent_profile", "off", False),
    ],
)
async def test_display_choice_persists_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    attribute: str,
    choice: str,
    expected: bool,
) -> None:
    app = _make_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        picker = await _select_setting_choice(app, pilot, label, choice)

        assert getattr(app.config, attribute) is expected
        assert getattr(_persisted_config(), attribute) is expected
        _assert_success_status(picker)


@pytest.mark.asyncio
async def test_theme_applies_and_persists_immediately_without_escape_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="openai/gpt-5.5", theme="rift"),
    )

    async with app.run_test() as pilot:
        picker = await _select_setting_choice(app, pilot, "Theme", "paper")

        assert app.config.theme == "paper"
        assert app.theme == "paper"
        assert _persisted_config().theme == "paper"
        _assert_success_status(picker)

        await pilot.press("escape")
        await pilot.pause()

        assert not _picker_is_open(app)
        assert app.theme == "paper"
        assert app.config.theme == "paper"
        assert _persisted_config().theme == "paper"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "typed", "attribute", "expected"),
    [
        ("Temperature", "0.25", "temperature", 0.25),
        ("Max tokens", "8192", "max_tokens", 8192),
    ],
)
async def test_typed_generation_value_persists_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    typed: str,
    attribute: str,
    expected: float | int,
) -> None:
    app = _make_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, label)
        assert picker.state is PickerState.EDITOR

        await _submit_editor(pilot, typed)

        assert getattr(app.config, attribute) == expected
        assert getattr(_persisted_config(), attribute) == expected
        _assert_success_status(picker)


@pytest.mark.asyncio
async def test_reasoning_effort_persists_and_rebuilds_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        provider_before = app.provider
        picker = await _open_setting(app, pilot, "Reasoning effort")
        assert {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        } == set(REASONING_EFFORTS)

        await _choose_choice(picker, pilot, "high")

        assert app.config.reasoning_effort == "high"
        assert _persisted_config().reasoning_effort == "high"
        assert app.provider is not provider_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "attribute", "invalid", "error_terms"),
    [
        ("Temperature", "temperature", "very hot", ("temperature", "number")),
        ("Max tokens", "max_tokens", "4.5", ("max tokens", "integer")),
        (
            "Tool call steps",
            "max_steps",
            "0",
            ("tool call steps", "at least 1"),
        ),
    ],
)
async def test_invalid_numeric_value_stays_in_editor_without_mutating_memory_or_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    attribute: str,
    invalid: str,
    error_terms: tuple[str, str],
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    app.config.save()
    before_memory = getattr(app.config, attribute)
    before_disk = cfgmod.CONFIG_PATH.read_text()

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, label)
        prompt = app.query_one("#prompt", Input)

        await _submit_editor(pilot, invalid)

        assert picker.state is PickerState.EDITOR
        assert prompt.value == invalid
        status = _picker_status_text(picker).casefold()
        assert _picker_status(picker).display
        assert all(term in status for term in error_terms)
        assert getattr(app.config, attribute) == before_memory
        assert cfgmod.CONFIG_PATH.read_text() == before_disk

        await _replace_prompt(pilot, f"{invalid}x")
        assert _picker_status_text(picker).casefold() == status
        assert cfgmod.CONFIG_PATH.read_text() == before_disk

        await pilot.press("escape")
        await pilot.pause()
        assert picker.state is PickerState.ROOT
        assert (
            not _picker_status(picker).display
            or _picker_status_text(picker).casefold() != status
        )


@pytest.mark.asyncio
async def test_tool_call_steps_updates_persisted_and_live_loop_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Tool call steps")
        await _submit_editor(pilot, "31")

        assert app.config.max_steps == 31
        assert app.max_steps == 31
        assert _persisted_config().max_steps == 31
        _assert_success_status(picker)


@pytest.mark.asyncio
async def test_main_provider_then_curated_model_commits_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_model_fetch(monkeypatch, _curated_fetch)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="anthropic/claude-sonnet-4-6"),
    )
    app.config.save()
    disk_before = cfgmod.CONFIG_PATH.read_text()

    async with app.run_test() as pilot:
        provider_before = app.provider
        status_model_before = app.status.model
        picker = await _open_setting(app, pilot, "Main model")

        await _choose_choice(picker, pilot, "OpenAI")

        assert app.config.model == "anthropic/claude-sonnet-4-6"
        assert cfgmod.CONFIG_PATH.read_text() == disk_before
        assert app.provider is provider_before
        assert app.status.model == status_model_before
        assert "gpt-5.5-pro" in {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        }

        await _choose_choice(picker, pilot, "gpt-5.5-pro")

        assert app.config.model == "openai/gpt-5.5-pro"
        assert _persisted_config().model == "openai/gpt-5.5-pro"
        assert app.provider is not provider_before
        assert app.status.model == "openai/gpt-5.5-pro"
        _assert_success_status(picker)


@pytest.mark.asyncio
async def test_model_discovery_shows_curated_immediately_then_applies_live_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    live_model = "labs/[bold]preview[/bold]"

    def delayed_fetch(
        provider_key: str,
        _api_base: str | None,
        _api_key: str | None,
    ) -> FetchResult:
        assert provider_key == "openai"
        started.set()
        release.wait(timeout=2)
        return FetchResult(
            models=["gpt-5.5", "gpt-live-offline", live_model],
            source="merged",
        )

    _stub_model_fetch(monkeypatch, delayed_fetch)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="anthropic/claude-sonnet-4-6"),
    )

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Main model")
        try:
            await _choose_choice(picker, pilot, "OpenAI")
            assert started.wait(timeout=1)

            immediate = {
                _widget_text(row).strip() for row in _visible_choice_rows(picker)
            }
            assert "gpt-5.5" in immediate
            assert "gpt-live-offline" not in immediate
        finally:
            release.set()

        await _wait_until(
            pilot,
            lambda: "gpt-live-offline"
            in {
                _widget_text(row).strip()
                for row in _visible_choice_rows(picker)
            },
        )

        live_rows = [
            row
            for row in _visible_choice_rows(picker)
            if _widget_text(row).strip() == live_model
        ]
        assert len(live_rows) == 1
        assert live_model in _rendered_text(live_rows[0])


@pytest.mark.asyncio
async def test_model_discovery_failure_keeps_curated_models_selectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed_fetch(
        provider_key: str,
        _api_base: str | None,
        _api_key: str | None,
    ) -> FetchResult:
        return FetchResult(
            models=list(PROVIDER_DEFAULTS[provider_key]),
            source="curated",
            error="offline test failure",
        )

    _stub_model_fetch(monkeypatch, failed_fetch)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="anthropic/claude-sonnet-4-6"),
    )

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Main model")
        await _choose_choice(picker, pilot, "OpenAI")
        await _wait_until(
            pilot,
            lambda: "offline test failure"
            in _picker_status_text(picker).casefold(),
        )

        assert "gpt-5.5-pro" in {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        }
        assert app.config.model == "anthropic/claude-sonnet-4-6"

        await _choose_choice(picker, pilot, "gpt-5.5-pro")

        assert app.config.model == "openai/gpt-5.5-pro"
        assert _persisted_config().model == "openai/gpt-5.5-pro"


@pytest.mark.asyncio
async def test_current_non_curated_model_stays_selectable_in_its_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery_marker = "gpt-discovery-finished"

    def fetched(
        provider_key: str,
        _api_base: str | None,
        _api_key: str | None,
    ) -> FetchResult:
        return FetchResult(
            models=[*PROVIDER_DEFAULTS[provider_key], discovery_marker],
            source="merged",
        )

    _stub_model_fetch(monkeypatch, fetched)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="openai/gpt-private-preview"),
    )

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Main model")
        await _choose_choice(picker, pilot, "OpenAI")
        await _wait_until(
            pilot,
            lambda: discovery_marker
            in {
                _widget_text(row).strip()
                for row in _visible_choice_rows(picker)
            },
        )

        assert "gpt-private-preview" in {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        }

        await _choose_choice(picker, pilot, "gpt-private-preview")

        assert app.config.model == "openai/gpt-private-preview"
        assert _persisted_config().model == "openai/gpt-private-preview"


@pytest.mark.asyncio
async def test_openrouter_custom_model_applies_full_slug_prefix_only_on_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_model_fetch(monkeypatch, _curated_fetch)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="anthropic/claude-sonnet-4-6"),
    )

    async with app.run_test() as pilot:
        provider_before = app.provider
        picker = await _open_setting(app, pilot, "Main model")
        await _choose_choice(picker, pilot, "OpenRouter")

        assert app.config.model == "anthropic/claude-sonnet-4-6"
        assert "Enter custom model ID…" in {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        }

        await _choose_choice(
            picker,
            pilot,
            "Enter custom model ID…",
            query="custom model",
        )
        assert picker.state is PickerState.EDITOR
        assert app.query_one("#prompt", Input).password is False
        assert app.config.model == "anthropic/claude-sonnet-4-6"

        await _submit_editor(pilot, "openai/gpt-experimental")

        expected = "openrouter/openai/gpt-experimental"
        assert app.config.model == expected
        assert _persisted_config().model == expected
        assert app.status.model == expected
        assert app.provider is not provider_before


@pytest.mark.asyncio
async def test_model_choice_filter_navigation_moves_only_through_visible_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_model_fetch(monkeypatch, _curated_fetch)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="anthropic/claude-sonnet-4-6"),
    )

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Main model")
        await _choose_choice(picker, pilot, "OpenAI")
        await _replace_prompt(pilot, "gpt-5.4")

        rows = _visible_choice_rows(picker)
        assert [_widget_text(row).strip() for row in rows] == [
            "gpt-5.4",
            "gpt-5.4-mini",
        ]
        assert _highlighted_choice(picker) is rows[0]

        await pilot.press("down")
        await pilot.pause()
        assert _highlighted_choice(picker) is rows[1]

        await pilot.press("enter")
        await pilot.pause()
        assert app.config.model == "openai/gpt-5.4-mini"


@pytest.mark.asyncio
async def test_worker_use_main_model_persists_empty_worker_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(
            onboarded=True,
            model="openai/gpt-5.5",
            worker_model="anthropic/claude-sonnet-4-6",
        ),
    )

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Worker model")
        assert "Use main model" in {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        }

        await _choose_choice(picker, pilot, "Use main model")

        assert app.config.worker_model == ""
        assert _persisted_config().worker_model == ""
        _assert_row_value(
            _visible_setting_rows(picker),
            "Worker model",
            "use main model",
        )


@pytest.mark.asyncio
async def test_worker_provider_uses_own_default_base_without_clobbering_main_creds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    def curated(
        provider_key: str,
        api_base: str | None,
        api_key: str | None,
    ) -> FetchResult:
        calls.append((provider_key, api_base, api_key))
        return FetchResult(
            models=list(PROVIDER_DEFAULTS[provider_key]),
            source="curated",
        )

    _stub_model_fetch(monkeypatch, curated)
    main_base = "https://main-only.example/v1"
    config = Config(
        onboarded=True,
        model="openai/gpt-5.5",
        worker_model="",
        providers={
            "openai": ProviderCreds(
                api_key="sk-main-only",
                api_base=main_base,
            )
        },
    )
    app = _make_app(tmp_path, monkeypatch, config)

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "Worker model")
        await _choose_choice(picker, pilot, "DeepSeek")
        await _choose_choice(picker, pilot, "deepseek-v4-pro")

        assert app.config.model == "openai/gpt-5.5"
        assert app.config.worker_model == "deepseek/deepseek-v4-pro"
        assert app.config.providers["openai"] == ProviderCreds(
            api_key="sk-main-only",
            api_base=main_base,
        )
        assert app.config.providers["deepseek"] == ProviderCreds(
            api_key="sk-main-only",
            api_base=PROVIDERS["deepseek"].default_base,
        )
        assert app.config.providers["deepseek"].api_base != main_base

        loaded = _persisted_config()
        assert loaded.providers["openai"] == app.config.providers["openai"]
        assert loaded.providers["deepseek"] == app.config.providers["deepseek"]
        await _wait_until(pilot, lambda: bool(calls))
        assert (
            "deepseek",
            PROVIDERS["deepseek"].default_base,
            "sk-main-only",
        ) in calls
        _assert_success_status(picker)


@pytest.mark.asyncio
async def test_api_key_replace_is_blank_masked_and_never_renders_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    old_secret = "sk-old-private"
    new_secret = "sk-new-private"
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(
            onboarded=True,
            model="openai/gpt-5.5",
            providers={"openai": ProviderCreds(api_key=old_secret)},
        ),
    )

    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        rows = _visible_setting_rows(picker)
        _assert_row_value(rows, "API credentials", "set")
        credentials_row = next(
            row
            for row in rows
            if _rendered_text(row).strip().startswith("API credentials")
        )
        assert " ".join(_rendered_text(credentials_row).split()) == (
            "API credentials set"
        )
        assert old_secret not in " ".join(_widget_text(row) for row in rows)

        picker = await _open_setting(
            app,
            pilot,
            "API credentials",
            open_picker=False,
        )
        assert {
            _widget_text(row).strip() for row in _visible_choice_rows(picker)
        } == {"Replace API key", "Clear stored API key"}

        provider_before = app.provider
        await _choose_choice(picker, pilot, "Replace API key")
        prompt = app.query_one("#prompt", Input)
        assert picker.state is PickerState.EDITOR
        assert prompt.password is True
        assert prompt.value == ""

        await _submit_editor(pilot, new_secret)

        assert app.config.providers["openai"].api_key == new_secret
        assert _persisted_config().providers["openai"].api_key == new_secret
        assert app.provider is not provider_before
        assert new_secret not in _widget_text(picker)
        _assert_row_value(
            _visible_setting_rows(picker),
            "API credentials",
            "set",
        )


@pytest.mark.asyncio
async def test_blank_api_key_replacement_preserves_stored_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secret = "sk-keep-this"
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(
            onboarded=True,
            model="openai/gpt-5.5",
            providers={"openai": ProviderCreds(api_key=secret)},
        ),
    )
    app.config.save()
    disk_before = cfgmod.CONFIG_PATH.read_text()

    async with app.run_test() as pilot:
        picker = await _open_setting(app, pilot, "API credentials")
        await _choose_choice(picker, pilot, "Replace API key")
        prompt = app.query_one("#prompt", Input)
        assert prompt.password is True
        assert prompt.value == ""

        await pilot.press("enter")
        await pilot.pause()

        assert app.config.providers["openai"].api_key == secret
        assert _persisted_config().providers["openai"].api_key == secret
        assert cfgmod.CONFIG_PATH.read_text() == disk_before
        _assert_row_value(
            _visible_setting_rows(picker),
            "API credentials",
            "set",
        )


@pytest.mark.asyncio
async def test_clear_stored_api_key_is_a_separate_immediate_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base_override = "https://stored.example/v1"
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(
            onboarded=True,
            model="openai/gpt-5.5",
            providers={
                "openai": ProviderCreds(
                    api_key="sk-remove-me",
                    api_base=base_override,
                )
            },
        ),
    )

    async with app.run_test() as pilot:
        provider_before = app.provider
        picker = await _open_setting(app, pilot, "API credentials")
        await _choose_choice(picker, pilot, "Clear stored API key")

        assert app.config.providers["openai"].api_key is None
        assert app.config.providers["openai"].api_base == base_override
        loaded = _persisted_config()
        assert loaded.providers["openai"].api_key is None
        assert loaded.providers["openai"].api_base == base_override
        assert app.provider is not provider_before
        _assert_row_value(
            _visible_setting_rows(picker),
            "API credentials",
            "not set",
        )


@pytest.mark.asyncio
async def test_blank_base_url_clears_override_and_shows_provider_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_override = "https://override.example/v1"
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(
            onboarded=True,
            model="openai/gpt-5.5",
            providers={
                "openai": ProviderCreds(
                    api_key="sk-stays",
                    api_base=base_override,
                )
            },
        ),
    )

    async with app.run_test() as pilot:
        provider_before = app.provider
        picker = await _open_setting(app, pilot, "Base URL")
        prompt = app.query_one("#prompt", Input)
        assert picker.state is PickerState.EDITOR
        assert prompt.value == base_override

        await _submit_editor(pilot, "")

        assert app.config.providers["openai"].api_base is None
        assert app.config.providers["openai"].api_key == "sk-stays"
        loaded = _persisted_config()
        assert loaded.providers["openai"].api_base is None
        assert loaded.providers["openai"].api_key == "sk-stays"
        assert app.provider is not provider_before
        _assert_row_value(
            _visible_setting_rows(picker),
            "Base URL",
            PROVIDERS["openai"].default_base or "",
        )


@pytest.mark.asyncio
async def test_codex_root_shows_login_status_without_credentials_or_base_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = CodexAuthStatus(
        logged_in=True,
        expires_in_s=600,
        detail="logged in (10m left)",
    )
    monkeypatch.setattr(codex_auth_mod, "auth_status", lambda: status)
    monkeypatch.setattr(picker_mod, "auth_status", lambda: status, raising=False)
    app = _make_app(
        tmp_path,
        monkeypatch,
        Config(onboarded=True, model="codex/gpt-5.5"),
    )

    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        rows = _visible_setting_rows(picker)
        labels = [_rendered_text(row).strip() for row in rows]

        expected_labels = (
            "Main model",
            "Codex login",
            "Temperature",
            "Max tokens",
            "Tool call steps",
            "Reasoning effort",
            "Worker model",
            "Theme",
            "Show thinking",
            "Show tool output",
            "Browser headless",
            "Persistent profile",
        )
        assert len(labels) == len(expected_labels)
        for expected, rendered in zip(expected_labels, labels, strict=True):
            assert rendered.startswith(expected), labels
        assert "logged in (10m left)" in labels[1]
