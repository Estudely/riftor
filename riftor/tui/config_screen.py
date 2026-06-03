"""The /config settings modal — a grouped, aligned settings card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Rule, Select, Switch

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
    from riftor.tui.app import RiftorApp


def _row(label: str, field: Widget) -> Horizontal:
    """A label-column + field row, so every field's left edge lines up."""
    return Horizontal(Label(label, classes="field-label"), field, classes="field-row")


def _model_options(provider_key: str) -> list[tuple[str, str]]:
    return [(m, m) for m in PROVIDER_DEFAULTS.get(provider_key, [])]


class ConfigScreen(ModalScreen[dict | None]):
    """Edit runtime settings. Dismisses with a dict of changes, or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self._original_theme = config.theme
        self._provider = provider_key_for_model(config.model)
        self._provider_initialized = False

    def compose(self) -> ComposeResult:
        theme = self.config.theme if self.config.theme in THEMES else "rift"
        pkey = self._provider
        meta = PROVIDERS[pkey]
        bare = (self.config.model[len(meta.prefix):]
                if meta.prefix and pkey != "openrouter"
                and self.config.model.startswith(meta.prefix)
                else self.config.model)
        saved = self.config.providers.get(pkey)
        base_val = (saved.api_base if saved else None) or meta.default_base or ""
        model_opts = _model_options(pkey)
        # Guarantee the configured model is a selectable option even when it is
        # not in PROVIDER_DEFAULTS (e.g. an older or custom id). This keeps
        # ``model_val`` always legal — Select.BLANK does not exist in Textual
        # 8.x and would crash on mount with InvalidSelectValueError.
        if bare and bare not in [v for _, v in model_opts]:
            model_opts = [(bare, bare), *model_opts]
        model_val = bare if bare else Select.NULL
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            with VerticalScroll(id="config-body"):
                yield Label("MODEL", classes="config-section")
                yield _row("Provider", Select(
                    [(m.label, k) for k, m in PROVIDERS.items()],
                    value=pkey, allow_blank=False, id="cfg-provider"))
                yield _row("Model", Select(
                    model_opts, value=model_val, allow_blank=True, id="cfg-model-select"))
                yield _row("Custom id", Input(
                    value="", placeholder="override (optional)", id="cfg-model"))
                yield _row("Base URL", Input(
                    value=base_val, placeholder="provider default", id="cfg-base"))
                yield _row("API key", Input(
                    password=True, placeholder="leave blank to keep", id="cfg-key"))
                with Horizontal(classes="field-row"):
                    yield Label("", classes="field-label")
                    yield Button("Fetch models", id="cfg-fetch", variant="primary")

                yield Rule()
                yield Label("GENERATION", classes="config-section")
                yield _row("Temperature", Input(value=str(self.config.temperature), id="cfg-temp"))
                yield _row("Max tokens", Input(value=str(self.config.max_tokens), id="cfg-maxtok"))

                yield Rule()
                yield Label("APPEARANCE", classes="config-section")
                yield _row("Theme", Select([(n, n) for n in THEMES], value=theme,
                                           allow_blank=False, id="cfg-theme"))
                yield _row("Lore", Switch(value=self.config.lore, id="cfg-lore"))
            with Horizontal(id="config-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    @property
    def _riftor_app(self) -> "RiftorApp":
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self.query_one("#cfg-provider", Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "cfg-theme":
            value = event.value
            if isinstance(value, str) and value in THEMES:
                self._riftor_app._apply_theme(value)
            return
        if event.select.id == "cfg-provider" and isinstance(event.value, str):
            self._provider = event.value
            meta = PROVIDERS[event.value]
            self.query_one("#cfg-base", Input).value = meta.default_base or ""
            if self._provider_initialized:
                self._set_model_options(_model_options(event.value))
            else:
                self._provider_initialized = True

    def _set_model_options(self, options: list[tuple[str, str]]) -> None:
        sel = self.query_one("#cfg-model-select", Select)
        sel.set_options(options or [("(type a custom id below)", "")])

    @work(thread=True, exclusive=True, group="fetch")
    def _fetch_models_worker(self, provider: str, base: str, key: str | None) -> None:
        result = fetch_models(provider, base or None, key or None)
        self.app.call_from_thread(self._apply_fetch_result, result)

    def _apply_fetch_result(self, result: FetchResult) -> None:
        if not self.is_running:  # screen dismissed while the fetch was in flight
            return
        self._set_model_options([(m, m) for m in result.models])
        if result.error:
            self._fail(f"fetch failed ({result.error[:60]}) — showing suggestions")
        else:
            self.query_one("#config-title", Label).update(
                f"riftor · config — {result.source}: {len(result.models)} models")

    def _revert_theme(self) -> None:
        self._riftor_app._apply_theme(self._original_theme)

    def action_cancel(self) -> None:
        self._revert_theme()
        self.dismiss(None)

    def _fail(self, message: str) -> None:
        self.query_one("#config-title", Label).update(f"riftor · config — {message}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self._revert_theme()
            self.dismiss(None)
            return
        if event.button.id == "cfg-fetch":
            key = self.query_one("#cfg-key", Input).value.strip() or None
            if not key:
                saved = self.config.providers.get(self._provider)
                key = saved.api_key if saved else None
            base = self.query_one("#cfg-base", Input).value.strip()
            self.query_one("#config-title", Label).update("riftor · config — fetching…")
            self._fetch_models_worker(self._provider, base, key)
            return
        try:
            temperature = float(self.query_one("#cfg-temp", Input).value)
        except ValueError:
            self._fail("temperature must be a number")
            return
        try:
            max_tokens = int(self.query_one("#cfg-maxtok", Input).value)
        except ValueError:
            self._fail("max tokens must be an integer")
            return

        provider = self._provider
        custom = self.query_one("#cfg-model", Input).value.strip()
        sel_val = self.query_one("#cfg-model-select", Select).value
        chosen = custom or (sel_val if isinstance(sel_val, str) and sel_val else "")
        model = apply_prefix(provider, chosen) if chosen else self.config.model

        result: dict = {
            "model": model,
            "provider": provider,
            "api_base": self.query_one("#cfg-base", Input).value.strip() or None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "theme": self.query_one("#cfg-theme", Select).value,
            "lore": self.query_one("#cfg-lore", Switch).value,
        }
        key = self.query_one("#cfg-key", Input).value.strip()
        if key:
            result["api_key"] = key
        self.dismiss(result)
