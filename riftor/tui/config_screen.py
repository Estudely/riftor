"""The /config settings modal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Switch

from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config


class ConfigScreen(ModalScreen[dict | None]):
    """Edit runtime settings. Dismisses with a dict of changes, or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            yield Label("model")
            yield Input(value=self.config.model, id="cfg-model")
            yield Label("temperature")
            yield Input(value=str(self.config.temperature), id="cfg-temp")
            yield Label("max tokens")
            yield Input(value=str(self.config.max_tokens), id="cfg-maxtok")
            yield Label("theme")
            theme = self.config.theme if self.config.theme in THEMES else "rift"
            yield Select(
                [(name, name) for name in THEMES],
                value=theme,
                allow_blank=False,
                id="cfg-theme",
            )
            with Horizontal(id="cfg-lore-row"):
                yield Label("lore  ")
                yield Switch(value=self.config.lore, id="cfg-lore")
            yield Label("api key (blank = keep current)")
            yield Input(password=True, placeholder="leave blank to keep", id="cfg-key")
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
