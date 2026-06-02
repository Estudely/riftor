"""Permission state + the confirmation modal for dangerous tool calls."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class Permissions:
    """Tracks which tools the operator has allowed for the rest of the session."""

    def __init__(self) -> None:
        self.session_allowed: set[str] = set()

    def needs_prompt(self, tool_name: str, requires_permission: bool) -> bool:
        return requires_permission and tool_name not in self.session_allowed

    def allow_for_session(self, tool_name: str) -> None:
        self.session_allowed.add(tool_name)


class ConfirmScreen(ModalScreen[str]):
    """Asks the operator to approve a tool call. Dismisses with once/session/deny."""

    BINDINGS = [
        ("escape", "decide('deny')", "Deny"),
        ("a", "decide('once')", "Allow once"),
        ("s", "decide('session')", "Allow session"),
    ]

    def __init__(self, tool_name: str, preview: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.preview = preview

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                Text(f"permission required  ·  {self.tool_name}", style="bold #f0abfc"),
                id="confirm-title",
            )
            yield Static(Text(self.preview or "(no detail)", style="#e9e9f2"), id="confirm-detail")
            with Horizontal(id="confirm-buttons"):
                yield Button("Allow once (a)", id="once", variant="success")
                yield Button("Allow session (s)", id="session", variant="primary")
                yield Button("Deny (esc)", id="deny", variant="error")

    def on_mount(self) -> None:
        self.query_one("#once", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def action_decide(self, choice: str) -> None:
        self.dismiss(choice)
