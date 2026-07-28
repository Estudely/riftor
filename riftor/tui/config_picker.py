"""Prompt-owned, keyboard-first inline configuration picker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from riftor.config import REASONING_EFFORTS
from riftor.providers import PROVIDERS, provider_key_for_model
from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config


_GROUPS = ("MODEL", "GENERATION", "WORKERS", "APPEARANCE", "DISPLAY")
_ROOT_PLACEHOLDER = "filter config settings…"


class PickerState(str, Enum):
    """The three interaction states exposed to the app and Task 2."""

    ROOT = "root"
    CHOICE = "choice"
    EDITOR = "editor"


@dataclass(frozen=True, slots=True)
class ConfigChoice:
    """One keyboard-selectable value in a choice child view."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class ConfigSetting:
    """A setting row and the child interaction used to activate it."""

    group: str
    key: str
    label: str
    value: str
    choices: tuple[ConfigChoice, ...] = ()
    editor_value: str = ""
    password: bool = False


@dataclass(frozen=True, slots=True)
class _PromptState:
    placeholder: str
    value: str
    password: bool


class _SettingGroup(Static):
    def __init__(self, name: str) -> None:
        self.group_name = name
        super().__init__(name, markup=False, classes="config-setting-group")


class _SettingRow(Static):
    def __init__(self, setting: ConfigSetting) -> None:
        self.setting = setting
        super().__init__(
            self._content(setting),
            markup=False,
            classes="config-setting-row",
        )

    @staticmethod
    def _content(setting: ConfigSetting) -> str:
        return f"{setting.label:<22} {setting.value}"

    def set_setting(self, setting: ConfigSetting) -> None:
        self.setting = setting
        self.update(self._content(setting))


class _ChoiceRow(Static):
    def __init__(self, setting_key: str, choice: ConfigChoice) -> None:
        self.setting_key = setting_key
        self.choice = choice
        super().__init__(
            choice.label,
            markup=False,
            classes="config-choice-row picker-hidden",
        )


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def _boolean_choices() -> tuple[ConfigChoice, ...]:
    return (
        ConfigChoice("on", "true"),
        ConfigChoice("off", "false"),
    )


def _settings_for(config: "Config") -> tuple[ConfigSetting, ...]:
    """Build the root rows from the current runtime config without mutating it."""
    provider_key = provider_key_for_model(config.model)
    provider = PROVIDERS.get(provider_key, PROVIDERS["custom"])
    saved = config.providers.get(provider_key)
    api_key, _ = config.creds_for(config.model)
    api_base = (
        (saved.api_base if saved else None)
        or config.api_base
        or provider.default_base
        or "provider default"
    )
    provider_choices = tuple(
        ConfigChoice(meta.label, key) for key, meta in PROVIDERS.items()
    )
    reasoning_choices = tuple(
        ConfigChoice(effort, effort) for effort in REASONING_EFFORTS
    )
    theme_choices = tuple(ConfigChoice(name, name) for name in THEMES)
    boolean_choices = _boolean_choices()

    return (
        ConfigSetting(
            "MODEL",
            "provider",
            "Provider",
            provider.label,
            choices=provider_choices,
        ),
        ConfigSetting(
            "MODEL",
            "model",
            "Model",
            config.model,
            editor_value=config.model,
        ),
        ConfigSetting(
            "MODEL",
            "api_key",
            "API key",
            "set" if api_key else "not set",
            password=True,
        ),
        ConfigSetting(
            "MODEL",
            "api_base",
            "Base URL",
            api_base,
            editor_value="" if api_base == "provider default" else api_base,
        ),
        ConfigSetting(
            "GENERATION",
            "temperature",
            "Temperature",
            str(config.temperature),
            editor_value=str(config.temperature),
        ),
        ConfigSetting(
            "GENERATION",
            "max_tokens",
            "Max tokens",
            str(config.max_tokens),
            editor_value=str(config.max_tokens),
        ),
        ConfigSetting(
            "GENERATION",
            "max_steps",
            "Tool call steps",
            str(config.max_steps),
            editor_value=str(config.max_steps),
        ),
        ConfigSetting(
            "GENERATION",
            "reasoning_effort",
            "Reasoning effort",
            config.reasoning_effort,
            choices=reasoning_choices,
        ),
        ConfigSetting(
            "WORKERS",
            "worker_model",
            "Worker model",
            config.worker_model or "reuse main model",
            editor_value=config.worker_model,
        ),
        ConfigSetting(
            "APPEARANCE",
            "theme",
            "Theme",
            config.theme,
            choices=theme_choices,
        ),
        ConfigSetting(
            "DISPLAY",
            "show_thinking",
            "Show thinking",
            _on_off(config.show_thinking),
            choices=boolean_choices,
        ),
        ConfigSetting(
            "DISPLAY",
            "show_tool_output",
            "Show tool output",
            _on_off(config.show_tool_output),
            choices=boolean_choices,
        ),
        ConfigSetting(
            "DISPLAY",
            "browser_headless",
            "Browser headless",
            _on_off(config.browser_headless),
            choices=boolean_choices,
        ),
        ConfigSetting(
            "DISPLAY",
            "browser_persistent_profile",
            "Persistent profile",
            _on_off(config.browser_persistent_profile),
            choices=boolean_choices,
        ),
    )


class ConfigPicker(Vertical):
    """Inline picker whose keyboard controller remains the main prompt."""

    class Activated(Message):
        """A value chosen or submitted for Task 2 to validate and persist."""

        def __init__(self, setting: ConfigSetting, value: str) -> None:
            super().__init__()
            self.setting = setting
            self.setting_key = setting.key
            self.value = value

    def __init__(self, config: "Config", id: str = "config-picker") -> None:
        super().__init__(id=id)
        self.config = config
        self._state = PickerState.ROOT
        self._open = False
        self._prompt_state: _PromptState | None = None
        self._root_query = ""
        self._selected_key: str | None = None
        self._choice_index = 0
        self._active_setting: ConfigSetting | None = None

        self._settings = _settings_for(config)
        self._groups = {name: _SettingGroup(name) for name in _GROUPS}
        self._rows = {
            setting.key: _SettingRow(setting) for setting in self._settings
        }
        self._choice_rows = {
            setting.key: [
                _ChoiceRow(setting.key, choice) for choice in setting.choices
            ]
            for setting in self._settings
            if setting.choices
        }
        self._child_title = Static(
            "",
            markup=False,
            id="config-picker-child-title",
            classes="picker-hidden",
        )
        self._editor_note = Static(
            "Type the new value in the prompt.",
            markup=False,
            id="config-picker-editor-note",
            classes="picker-hidden",
        )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="config-picker-list"):
            yield self._child_title
            yield self._editor_note
            for group_name in _GROUPS:
                yield self._groups[group_name]
                for setting in self._settings:
                    if setting.group == group_name:
                        yield self._rows[setting.key]
            for setting in self._settings:
                yield from self._choice_rows.get(setting.key, ())
        yield Static(
            "↑/↓ move · Enter activate · Esc back/close",
            markup=False,
            id="config-picker-hint",
        )

    @property
    def state(self) -> PickerState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def selected_setting(self) -> ConfigSetting | None:
        if self._state is not PickerState.ROOT or self._selected_key is None:
            return self._active_setting
        return next(
            (setting for setting in self._settings if setting.key == self._selected_key),
            None,
        )

    @property
    def _list(self) -> VerticalScroll:
        return self.query_one("#config-picker-list", VerticalScroll)

    @staticmethod
    def _show(widget: Widget, visible: bool) -> None:
        widget.set_class(not visible, "picker-hidden")

    def refresh_values(self) -> None:
        """Refresh displayed values after an activation handler updates Config."""
        current = _settings_for(self.config)
        self._settings = current
        for setting in current:
            self._rows[setting.key].set_setting(setting)

    def open(self, prompt: Input) -> None:
        """Open at the root and take temporary ownership of the prompt."""
        if self._open:
            return
        self.refresh_values()
        self._prompt_state = _PromptState(
            placeholder=prompt.placeholder,
            value=prompt.value,
            password=prompt.password,
        )
        self._open = True
        self.add_class("active")
        self._root_query = ""
        self._selected_key = None
        self._show_root(prompt, "")
        self._list.scroll_home(animate=False)
        prompt.focus()

    def close(self, prompt: Input) -> None:
        """Close and restore the prompt exactly as it was before opening."""
        if not self._open:
            return
        saved = self._prompt_state
        self._open = False
        self._state = PickerState.ROOT
        self._active_setting = None
        self.remove_class("active")
        self._hide_child_rows()
        self._prompt_state = None
        if saved is not None:
            prompt.placeholder = saved.placeholder
            prompt.password = saved.password
            prompt.value = saved.value
            prompt.cursor_position = len(prompt.value)
        prompt.focus()

    def filter(self, query: str) -> None:
        """Filter root rows by label, current value, key, or group."""
        if not self._open or self._state is not PickerState.ROOT:
            return
        self._root_query = query
        folded = query.strip().casefold()
        visible = [
            setting
            for setting in self._settings
            if not folded
            or folded
            in " ".join(
                (setting.group, setting.key, setting.label, setting.value)
            ).casefold()
        ]
        visible_keys = {setting.key for setting in visible}
        for setting in self._settings:
            self._show(self._rows[setting.key], setting.key in visible_keys)
        for group_name in _GROUPS:
            self._show(
                self._groups[group_name],
                any(setting.group == group_name for setting in visible),
            )
        if self._selected_key not in visible_keys:
            self._selected_key = visible[0].key if visible else None
        self._apply_highlight()
        self._scroll_highlight_visible()

    def move(self, delta: int) -> None:
        """Move the highlight in the active root or choice list."""
        if not self._open:
            return
        if self._state is PickerState.CHOICE:
            setting = self._active_setting
            if setting is None:
                return
            rows = self._choice_rows.get(setting.key, [])
            if not rows:
                return
            self._choice_index = max(
                0,
                min(len(rows) - 1, self._choice_index + delta),
            )
        elif self._state is PickerState.ROOT:
            visible = [
                setting
                for setting in self._settings
                if not self._rows[setting.key].has_class("picker-hidden")
            ]
            if not visible:
                return
            keys = [setting.key for setting in visible]
            try:
                index = keys.index(self._selected_key)
            except ValueError:
                index = 0
            index = max(0, min(len(keys) - 1, index + delta))
            self._selected_key = keys[index]
        else:
            return
        self._apply_highlight()
        self._scroll_highlight_visible()

    def activate(self, prompt: Input) -> None:
        """Activate the highlighted row or submit the active child value."""
        if not self._open:
            return
        if self._state is PickerState.ROOT:
            setting = self.selected_setting
            if setting is None:
                return
            self._active_setting = setting
            if setting.choices:
                self._show_choice(setting, prompt)
            else:
                self._show_editor(setting, prompt)
            return
        if self._active_setting is None:
            return
        if self._state is PickerState.CHOICE:
            rows = self._choice_rows[self._active_setting.key]
            choice = rows[self._choice_index].choice
            self.post_message(self.Activated(self._active_setting, choice.value))
        else:
            self.post_message(self.Activated(self._active_setting, prompt.value))
        self._show_root(prompt, self._root_query)

    def back(self, prompt: Input) -> None:
        """Return from a child to root, or close when already at root."""
        if not self._open:
            return
        if self._state is PickerState.ROOT:
            self.close(prompt)
        else:
            self._show_root(prompt, self._root_query)

    def _hide_root_rows(self) -> None:
        for group in self._groups.values():
            self._show(group, False)
        for row in self._rows.values():
            self._show(row, False)

    def _hide_child_rows(self) -> None:
        self._show(self._child_title, False)
        self._show(self._editor_note, False)
        for rows in self._choice_rows.values():
            for row in rows:
                self._show(row, False)

    def _show_root(self, prompt: Input, query: str) -> None:
        self._state = PickerState.ROOT
        self._active_setting = None
        self._hide_child_rows()
        prompt.password = False
        prompt.placeholder = _ROOT_PLACEHOLDER
        prompt.value = query
        prompt.cursor_position = len(query)
        self.filter(query)

    def _show_choice(self, setting: ConfigSetting, prompt: Input) -> None:
        self._state = PickerState.CHOICE
        self._hide_root_rows()
        self._hide_child_rows()
        self._child_title.update(f"{setting.label} · choose a value")
        self._show(self._child_title, True)
        rows = self._choice_rows[setting.key]
        for row in rows:
            self._show(row, True)
        self._choice_index = next(
            (
                index
                for index, row in enumerate(rows)
                if row.choice.label.casefold() == setting.value.casefold()
            ),
            0,
        )
        prompt.password = False
        prompt.placeholder = f"choose {setting.label.casefold()} with ↑/↓"
        prompt.value = ""
        self._apply_highlight()
        self._list.scroll_home(animate=False)

    def _show_editor(self, setting: ConfigSetting, prompt: Input) -> None:
        self._state = PickerState.EDITOR
        self._hide_root_rows()
        self._hide_child_rows()
        self._child_title.update(f"Edit {setting.label}")
        self._show(self._child_title, True)
        self._show(self._editor_note, True)
        prompt.placeholder = f"edit {setting.label.casefold()}…"
        prompt.password = setting.password
        prompt.value = setting.editor_value
        prompt.cursor_position = len(prompt.value)
        self._apply_highlight()
        self._list.scroll_home(animate=False)

    def _apply_highlight(self) -> None:
        for row in self._rows.values():
            row.remove_class("highlighted")
        for rows in self._choice_rows.values():
            for row in rows:
                row.remove_class("highlighted")
        if self._state is PickerState.ROOT and self._selected_key is not None:
            self._rows[self._selected_key].add_class("highlighted")
        elif self._state is PickerState.CHOICE and self._active_setting is not None:
            rows = self._choice_rows[self._active_setting.key]
            if rows:
                rows[self._choice_index].add_class("highlighted")

    def _scroll_highlight_visible(self) -> None:
        highlighted = list(self.query(".highlighted"))
        if highlighted:
            highlighted[0].scroll_visible(animate=False)
