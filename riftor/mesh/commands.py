"""TUI slash commands for mesh operations."""

from __future__ import annotations


def register_mesh_commands(app, manager) -> None:
    """Register all mesh-related slash commands with the Textual app."""
    from riftor.mesh.sidebar import MeshSidebar

    sidebar = app.query_one(MeshSidebar)

    app.mesh_manager = manager
    app.mesh_sidebar = sidebar
