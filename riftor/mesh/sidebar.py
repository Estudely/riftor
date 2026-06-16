"""Textual widget for the mesh sidebar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label, RichLog


class MeshSidebar(Vertical):
    """Sidebar showing mesh connection status, members, and activity."""

    def __init__(self, mesh_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._manager = mesh_manager

    def compose(self) -> ComposeResult:
        with Vertical(id="mesh-header"):
            yield Static("MESH", classes="sidebar-title")
        with Vertical(id="mesh-status"):
            yield Label("Not connected", id="mesh-connection-status")
        with Vertical(id="mesh-engagement"):
            yield Label("", id="mesh-engagement-name")
        with Vertical(id="mesh-processor"):
            yield Static("Processor", classes="section-header")
            yield Label("Not connected", id="mesh-processor-status")
        with Vertical(id="mesh-members"):
            yield Static("Members", classes="section-header")
            yield Static("", id="mesh-members-list")
        with Vertical(id="mesh-activity"):
            yield Static("Activity", classes="section-header")
            yield RichLog(id="mesh-activity-log", max_lines=50, auto_scroll=True)

    def set_manager(self, manager) -> None:
        self._manager = manager

    def update_connection_status(self, connected: bool, engagement_name: str = "") -> None:
        status = self.query_one("#mesh-connection-status", Label)
        name = self.query_one("#mesh-engagement-name", Label)
        if connected:
            status.update("\u25cf Connected")
            name.update(engagement_name)
        else:
            status.update("Not connected")
            name.update("")

    def update_members(self, members: list) -> None:
        lines = []
        for m in members:
            indicator = "\U0001f7e2" if m.get("online") else "\u26ab"
            name = m.get("display_name") or m.get("node_id", "unknown")
            role = m.get("role", "")
            lines.append(f" {indicator} {name} ({role})")
        member_widget = self.query_one("#mesh-members-list", Static)
        member_widget.update("\n".join(lines) if lines else "  No members")

    def update_processor_status(self, mode: str = "", pending: int = 0) -> None:
        status = self.query_one("#mesh-processor-status", Label)
        if mode:
            status.update(f"\u25cf [{mode}] Queue: {pending}")
        else:
            status.update("Not connected")

    def add_activity(self, entry: str) -> None:
        log = self.query_one("#mesh-activity-log", RichLog)
        log.write(entry)
