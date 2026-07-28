"""Prompt-owned, keyboard-first inline configuration picker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from riftor.codex_auth import auth_status
from riftor.config import REASONING_EFFORTS
from riftor.providers import (
    PROVIDER_DEFAULTS,
    PROVIDERS,
    FetchResult,
    apply_prefix,
    fetch_models,
    provider_key_for_model,
)
from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config


_GROUPS = ("MODEL", "GENERATION", "WORKERS", "APPEARANCE", "DISPLAY")
_ROOT_PLACEHOLDER = "filter config settings…"
_CUSTOM_MODEL = "__custom_model__"
_USE_MAIN_MODEL = "__use_main_model__"


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
    root_visible: bool = True
    readonly: bool = False


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
        classes = "config-setting-row"
        if not setting.root_visible:
            classes += " picker-hidden"
        super().__init__(
            self._content(setting),
            markup=False,
            classes=classes,
        )

    @staticmethod
    def _content(setting: ConfigSetting) -> str:
        return f"{setting.label:<22} {setting.value}"

    def set_setting(self, setting: ConfigSetting) -> None:
        self.setting = setting
        self.update(self._content(setting))
        self.set_class(not setting.root_visible, "picker-hidden")


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


def _provider_choices() -> tuple[ConfigChoice, ...]:
    return tuple(ConfigChoice(meta.label, key) for key, meta in PROVIDERS.items())


def _bare_model(provider_key: str, model: str) -> str:
    """Return the model spelling shown inside one provider's child list."""
    meta = PROVIDERS.get(provider_key)
    if (
        meta is not None
        and provider_key != "openrouter"
        and meta.prefix
        and model.startswith(meta.prefix)
    ):
        return model[len(meta.prefix) :]
    return model


def _settings_for(config: "Config") -> tuple[ConfigSetting, ...]:
    """Build the root rows from the current runtime config without mutating it."""
    provider_key = provider_key_for_model(config.model)
    provider = PROVIDERS.get(provider_key, PROVIDERS["custom"])
    saved = config.providers.get(provider_key)
    api_key, _ = config.creds_for(config.model)
    override_base = (saved.api_base if saved else None) or config.api_base
    display_base = override_base or provider.default_base or "provider default"
    provider_choices = _provider_choices()
    credential_choices = (
        ConfigChoice("Replace API key", "replace"),
        ConfigChoice("Clear stored API key", "clear"),
    )
    reasoning_choices = tuple(
        ConfigChoice(effort, effort) for effort in REASONING_EFFORTS
    )
    theme_choices = tuple(ConfigChoice(name, name) for name in THEMES)
    boolean_choices = _boolean_choices()
    codex = provider_key == "codex"
    codex_detail = auth_status().detail if codex else ""

    return (
        ConfigSetting(
            "MODEL",
            "model",
            "Main model",
            config.model,
            choices=provider_choices,
        ),
        ConfigSetting(
            "MODEL",
            "api_key",
            "API credentials",
            "set" if api_key else "not set",
            choices=credential_choices,
            password=True,
            root_visible=not codex,
        ),
        ConfigSetting(
            "MODEL",
            "api_base",
            "Base URL",
            display_base,
            editor_value=override_base or "",
            root_visible=not codex,
        ),
        ConfigSetting(
            "MODEL",
            "codex_login",
            "Codex login",
            codex_detail,
            root_visible=codex,
            readonly=True,
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
            config.worker_model or "Use main model",
            choices=provider_choices
            + (ConfigChoice("Use main model", _USE_MAIN_MODEL),),
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

        def __init__(
            self,
            setting: ConfigSetting,
            value: str,
            *,
            provider_key: str | None = None,
            action: str | None = None,
        ) -> None:
            super().__init__()
            self.setting = setting
            self.setting_key = setting.key
            self.value = value
            self.provider_key = provider_key
            self.action = action

    def __init__(self, config: "Config", id: str = "config-picker") -> None:
        super().__init__(id=id)
        self.config = config
        self._state = PickerState.ROOT
        self._open = False
        self._prompt_state: _PromptState | None = None
        self._root_query = ""
        self._selected_key: str | None = None
        self._choice_index = 0
        self._choice_query = ""
        self._active_setting: ConfigSetting | None = None
        self._choice_flow = ""
        self._editor_action: str | None = None
        self._pending_provider_key: str | None = None
        self._pending_worker = False
        self._fetch_generation = 0

        self._settings = _settings_for(config)
        self._groups = {name: _SettingGroup(name) for name in _GROUPS}
        self._rows = {
            setting.key: _SettingRow(setting) for setting in self._settings
        }
        self._choice_rows: list[_ChoiceRow] = []
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
        self._status = Static(
            "",
            markup=False,
            id="config-picker-status",
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
        yield self._status
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

    def clear_status(self) -> None:
        """Hide the inline status without changing picker navigation state."""
        self._status.update("")
        self._status.remove_class("config-status-success")
        self._status.remove_class("config-status-error")
        self._show(self._status, False)

    def show_status(self, message: str) -> None:
        """Show a non-fatal informational status in the picker."""
        self._status.update(message)
        self._status.remove_class("config-status-success")
        self._status.remove_class("config-status-error")
        self._show(self._status, True)

    def show_error(self, message: str) -> None:
        """Keep the active child open and show a validation error."""
        self._status.update(message)
        self._status.remove_class("config-status-success")
        self._status.add_class("config-status-error")
        self._show(self._status, True)

    def complete_activation(self, prompt: Input, message: str) -> None:
        """Refresh values and return to root after the app persisted a value."""
        self.refresh_values()
        self._show_root(prompt, self._root_query)
        self._status.update(message)
        self._status.remove_class("config-status-error")
        self._status.add_class("config-status-success")
        self._show(self._status, True)

    def refresh_values(self) -> None:
        """Refresh displayed values after an activation handler updates Config."""
        current = _settings_for(self.config)
        self._settings = current
        for setting in current:
            self._rows[setting.key].set_setting(setting)
        if self._open and self._state is PickerState.ROOT:
            self._filter_root(self._root_query)

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
        self.clear_status()
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
        self._choice_flow = ""
        self._editor_action = None
        self._pending_provider_key = None
        self._fetch_generation += 1
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
        """Filter the active root or choice list without taking prompt focus."""
        if not self._open:
            return
        if self._state is PickerState.CHOICE:
            self._filter_choices(query)
            return
        if self._state is not PickerState.ROOT:
            return
        self._root_query = query
        self._filter_root(query)

    def _filter_root(self, query: str) -> None:
        folded = query.strip().casefold()
        visible = [
            setting
            for setting in self._settings
            if setting.root_visible
            and (
                not folded
                or folded
                in " ".join(
                    (setting.group, setting.key, setting.label, setting.value)
                ).casefold()
            )
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

    def _filter_choices(self, query: str) -> None:
        self._choice_query = query
        folded = query.strip().casefold()
        visible_indices: list[int] = []
        for index, row in enumerate(self._choice_rows):
            visible = not folded or folded in (
                f"{row.choice.label} {row.choice.value}".casefold()
            )
            self._show(row, visible)
            if visible:
                visible_indices.append(index)
        if self._choice_index not in visible_indices:
            self._choice_index = visible_indices[0] if visible_indices else 0
        self._apply_highlight()
        self._scroll_highlight_visible()

    def move(self, delta: int) -> None:
        """Move the highlight in the active root or choice list."""
        if not self._open:
            return
        if self._state is PickerState.CHOICE:
            visible = [
                index
                for index, row in enumerate(self._choice_rows)
                if not row.has_class("picker-hidden")
            ]
            if not visible:
                return
            try:
                position = visible.index(self._choice_index)
            except ValueError:
                position = 0
            position = max(0, min(len(visible) - 1, position + delta))
            self._choice_index = visible[position]
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
            self.clear_status()
            self._active_setting = setting
            if setting.readonly:
                self.show_status(setting.value)
            elif setting.key in {"model", "worker_model"}:
                self._show_provider_choices(
                    setting,
                    prompt,
                    worker=setting.key == "worker_model",
                )
            elif setting.choices:
                self._show_choice(
                    setting,
                    prompt,
                    setting.choices,
                    flow="setting",
                    selected_value=setting.value,
                )
            else:
                self._show_editor(setting, prompt)
            return
        if self._active_setting is None:
            return
        if self._state is PickerState.CHOICE:
            if not self._choice_rows:
                return
            row = self._choice_rows[self._choice_index]
            if row.has_class("picker-hidden"):
                return
            self._activate_choice(row.choice, prompt)
        else:
            self._activate_editor(prompt)

    def _activate_choice(self, choice: ConfigChoice, prompt: Input) -> None:
        setting = self._active_setting
        if setting is None:
            return
        if self._choice_flow == "provider":
            if choice.value == _USE_MAIN_MODEL:
                self._fetch_generation += 1
                self.post_message(self.Activated(setting, ""))
                return
            self._show_model_choices(choice.value, prompt)
            return
        if self._choice_flow == "model":
            if choice.value == _CUSTOM_MODEL:
                self._show_editor(
                    setting,
                    prompt,
                    editor_value="",
                    password=False,
                    title="Enter custom model ID",
                    action="custom_model",
                )
                return
            provider_key = self._pending_provider_key
            if provider_key is None:
                return
            self._fetch_generation += 1
            self.post_message(
                self.Activated(
                    setting,
                    apply_prefix(provider_key, choice.value),
                    provider_key=provider_key,
                )
            )
            return
        if setting.key == "api_key":
            provider_key = provider_key_for_model(self.config.model)
            if choice.value == "replace":
                self._show_editor(
                    setting,
                    prompt,
                    editor_value="",
                    password=True,
                    title="Replace API key",
                    action="replace_api_key",
                )
            elif choice.value == "clear":
                self.post_message(
                    self.Activated(
                        setting,
                        "",
                        provider_key=provider_key,
                        action="clear",
                    )
                )
            return
        self.post_message(self.Activated(setting, choice.value))

    def _activate_editor(self, prompt: Input) -> None:
        setting = self._active_setting
        if setting is None:
            return
        if self._editor_action == "custom_model":
            typed = prompt.value.strip()
            if not typed:
                self.show_error("model ID cannot be blank")
                return
            provider_key = self._pending_provider_key
            if provider_key is None:
                return
            self._fetch_generation += 1
            self.post_message(
                self.Activated(
                    setting,
                    apply_prefix(provider_key, typed),
                    provider_key=provider_key,
                    action="custom",
                )
            )
            return
        provider_key = (
            provider_key_for_model(self.config.model)
            if setting.key in {"api_key", "api_base"}
            else None
        )
        self.post_message(
            self.Activated(
                setting,
                prompt.value,
                provider_key=provider_key,
                action="replace" if self._editor_action == "replace_api_key" else None,
            )
        )

    def back(self, prompt: Input) -> None:
        """Return from a child to root, or close when already at root."""
        if not self._open:
            return
        if self._state is PickerState.ROOT:
            self.close(prompt)
        else:
            self._fetch_generation += 1
            self.clear_status()
            self._show_root(prompt, self._root_query)

    def _hide_root_rows(self) -> None:
        for group in self._groups.values():
            self._show(group, False)
        for row in self._rows.values():
            self._show(row, False)

    def _hide_child_rows(self) -> None:
        self._show(self._child_title, False)
        self._show(self._editor_note, False)
        for row in self._choice_rows:
            self._show(row, False)

    def _show_root(self, prompt: Input, query: str) -> None:
        self._state = PickerState.ROOT
        self._active_setting = None
        self._choice_flow = ""
        self._editor_action = None
        self._pending_provider_key = None
        self._pending_worker = False
        self._hide_child_rows()
        prompt.password = False
        prompt.placeholder = _ROOT_PLACEHOLDER
        prompt.value = query
        prompt.cursor_position = len(query)
        self._root_query = query
        self._filter_root(query)

    def _replace_choice_rows(
        self,
        setting: ConfigSetting,
        choices: tuple[ConfigChoice, ...],
    ) -> None:
        old_rows = self._choice_rows
        for row in old_rows:
            self._show(row, False)
            row.remove()
        self._choice_rows = [_ChoiceRow(setting.key, choice) for choice in choices]
        if self._choice_rows:
            self._list.mount(*self._choice_rows)

    def _show_choice(
        self,
        setting: ConfigSetting,
        prompt: Input,
        choices: tuple[ConfigChoice, ...],
        *,
        flow: str,
        selected_value: str | None = None,
        title: str | None = None,
    ) -> None:
        self._state = PickerState.CHOICE
        self._hide_root_rows()
        self._hide_child_rows()
        self._replace_choice_rows(setting, choices)
        self._choice_flow = flow
        self._editor_action = None
        self._child_title.update(title or f"{setting.label} · choose a value")
        self._show(self._child_title, True)
        self._choice_index = next(
            (
                index
                for index, row in enumerate(self._choice_rows)
                if selected_value is not None
                and (
                    row.choice.value.casefold() == selected_value.casefold()
                    or row.choice.label.casefold() == selected_value.casefold()
                )
            ),
            0,
        )
        self._choice_query = ""
        prompt.password = False
        prompt.placeholder = f"choose {setting.label.casefold()} with ↑/↓"
        prompt.value = ""
        self._filter_choices("")
        self._list.scroll_home(animate=False)

    def _show_provider_choices(
        self,
        setting: ConfigSetting,
        prompt: Input,
        *,
        worker: bool,
    ) -> None:
        self._pending_worker = worker
        self._pending_provider_key = None
        configured = self.config.worker_model if worker else self.config.model
        selected = (
            _USE_MAIN_MODEL
            if worker and not configured
            else provider_key_for_model(configured or self.config.model)
        )
        self._show_choice(
            setting,
            prompt,
            setting.choices,
            flow="provider",
            selected_value=selected,
            title=f"{setting.label} · choose provider",
        )

    def _configured_model_choice(self, provider_key: str) -> str | None:
        configured = (
            self.config.worker_model if self._pending_worker else self.config.model
        )
        if not configured or provider_key_for_model(configured) != provider_key:
            return None
        return _bare_model(provider_key, configured)

    def _model_choices(
        self,
        provider_key: str,
        models: list[str],
    ) -> tuple[ConfigChoice, ...]:
        configured = self._configured_model_choice(provider_key)
        values = ([configured] if configured else []) + list(models)
        seen: set[str] = set()
        choices: list[ConfigChoice] = []
        for model in values:
            if model and model not in seen:
                seen.add(model)
                choices.append(ConfigChoice(model, model))
        choices.append(ConfigChoice("Enter custom model ID…", _CUSTOM_MODEL))
        return tuple(choices)

    def _show_model_choices(self, provider_key: str, prompt: Input) -> None:
        setting = self._active_setting
        if setting is None:
            return
        self._pending_provider_key = provider_key
        choices = self._model_choices(
            provider_key,
            list(PROVIDER_DEFAULTS.get(provider_key, [])),
        )
        self._show_choice(
            setting,
            prompt,
            choices,
            flow="model",
            selected_value=self._configured_model_choice(provider_key),
            title=f"{setting.label} · {PROVIDERS[provider_key].label} models",
        )
        self._fetch_generation += 1
        generation = self._fetch_generation
        api_base, api_key = self._discovery_credentials(
            provider_key,
            worker=self._pending_worker,
        )
        self._fetch_models_worker(
            generation,
            provider_key,
            self._pending_worker,
            api_base,
            api_key,
        )

    def _discovery_credentials(
        self,
        provider_key: str,
        *,
        worker: bool,
    ) -> tuple[str | None, str | None]:
        meta = PROVIDERS[provider_key]
        saved = self.config.providers.get(provider_key)
        if worker:
            if provider_key == "codex":
                return None, None
            main_key, main_base = self.config.creds_for(self.config.model)
            api_key = (saved.api_key if saved else None) or main_key
            same_as_main = provider_key == provider_key_for_model(self.config.model)
            api_base = (saved.api_base if saved else None) or (
                main_base if same_as_main else None
            ) or meta.default_base
            return api_base, api_key

        probe = f"{meta.prefix}__model_discovery__" if meta.prefix else self.config.model
        api_key, resolved_base = self.config.creds_for(probe)
        current_provider = provider_key_for_model(self.config.model)
        api_base = resolved_base if provider_key == current_provider else None
        return api_base or meta.default_base, api_key

    @work(thread=True, exclusive=False, group="config-model-fetch")
    def _fetch_models_worker(
        self,
        generation: int,
        provider_key: str,
        worker: bool,
        api_base: str | None,
        api_key: str | None,
    ) -> None:
        try:
            result = fetch_models(provider_key, api_base, api_key)
        except Exception as exc:  # noqa: BLE001 — custom fetch seams must stay non-fatal
            result = FetchResult(
                models=list(PROVIDER_DEFAULTS.get(provider_key, [])),
                source="curated",
                error=str(exc)[:160],
            )
        try:
            self.app.call_from_thread(
                self._apply_fetch_result,
                generation,
                provider_key,
                worker,
                result,
            )
        except RuntimeError:
            return

    def _apply_fetch_result(
        self,
        generation: int,
        provider_key: str,
        worker: bool,
        result: FetchResult,
    ) -> None:
        if (
            not self._open
            or self._state is not PickerState.CHOICE
            or self._choice_flow != "model"
            or generation != self._fetch_generation
            or provider_key != self._pending_provider_key
            or worker != self._pending_worker
            or self._active_setting is None
        ):
            return
        selected = (
            self._choice_rows[self._choice_index].choice.value
            if self._choice_rows
            else self._configured_model_choice(provider_key)
        )
        choices = self._model_choices(provider_key, result.models)
        current_choices = tuple(row.choice for row in self._choice_rows)
        if choices != current_choices:
            self._replace_choice_rows(self._active_setting, choices)
            self._choice_index = next(
                (
                    index
                    for index, row in enumerate(self._choice_rows)
                    if row.choice.value == selected
                ),
                0,
            )
            self._filter_choices(self._choice_query)
        if result.error:
            self.show_status(
                f"model discovery failed: {result.error} — showing suggestions"
            )
        else:
            self.show_status(f"{result.source} · {len(result.models)} models")

    def _show_editor(
        self,
        setting: ConfigSetting,
        prompt: Input,
        *,
        editor_value: str | None = None,
        password: bool | None = None,
        title: str | None = None,
        action: str | None = None,
    ) -> None:
        self._state = PickerState.EDITOR
        self._hide_root_rows()
        self._hide_child_rows()
        self._choice_flow = ""
        self._editor_action = action
        self._child_title.update(title or f"Edit {setting.label}")
        self._show(self._child_title, True)
        self._show(self._editor_note, True)
        prompt.placeholder = f"edit {setting.label.casefold()}…"
        prompt.password = setting.password if password is None else password
        prompt.value = setting.editor_value if editor_value is None else editor_value
        prompt.cursor_position = len(prompt.value)
        self._apply_highlight()
        self._list.scroll_home(animate=False)

    def _apply_highlight(self) -> None:
        for row in self._rows.values():
            row.remove_class("highlighted")
        for row in self._choice_rows:
            row.remove_class("highlighted")
        if self._state is PickerState.ROOT and self._selected_key is not None:
            self._rows[self._selected_key].add_class("highlighted")
        elif (
            self._state is PickerState.CHOICE
            and self._choice_rows
            and self._choice_index < len(self._choice_rows)
            and not self._choice_rows[self._choice_index].has_class("picker-hidden")
        ):
            self._choice_rows[self._choice_index].add_class("highlighted")

    def _scroll_highlight_visible(self) -> None:
        highlighted = list(self.query(".highlighted"))
        if highlighted:
            highlighted[0].scroll_visible(animate=False)
