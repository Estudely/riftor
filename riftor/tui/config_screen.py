"""The /config settings modal — a grouped, aligned settings card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Rule, Select, Switch

from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config


def _row(label: str, field: Widget) -> Horizontal:
    """A label-column + field row, so every field's left edge lines up."""
    return Horizontal(Label(label, classes="field-label"), field, classes="field-row")


class ConfigScreen(ModalScreen[dict | None]):
    """Edit runtime settings. Dismisses with a dict of changes, or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        theme = self.config.theme if self.config.theme in THEMES else "rift"
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            with VerticalScroll(id="config-body"):
                yield Label("MODEL", classes="config-section")
                yield _row("Model", Input(value=self.config.model, id="cfg-model"))
                yield _row(
                    "API key",
                    Input(password=True, placeholder="leave blank to keep", id="cfg-key"),
                )

                yield Rule()
                yield Label("GENERATION", classes="config-section")
                yield _row("Temperature", Input(value=str(self.config.temperature), id="cfg-temp"))
                yield _row("Max tokens", Input(value=str(self.config.max_tokens), id="cfg-maxtok"))

                yield Rule()
                yield Label("APPEARANCE", classes="config-section")
                yield _row(
                    "Theme",
                    Select(
                        [(name, name) for name in THEMES],
                        value=theme,
                        allow_blank=False,
                        id="cfg-theme",
                    ),
                )
                yield _row("Lore", Switch(value=self.config.lore, id="cfg-lore"))
            with Horizontal(id="config-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cfg-model", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _fail(self, message: str) -> None:
        self.query_one("#config-title", Label).update(f"riftor · config — {message}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
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

        result: dict = {
            "model": self.query_one("#cfg-model", Input).value.strip() or self.config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "theme": self.query_one("#cfg-theme", Select).value,
            "lore": self.query_one("#cfg-lore", Switch).value,
        }
        key = self.query_one("#cfg-key", Input).value.strip()
        if key:
            result["api_key"] = key
        self.dismiss(result)
