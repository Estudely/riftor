"""TUI slash commands for mesh operations."""

from __future__ import annotations


def register_mesh_commands(app, manager) -> None:
    """Register all mesh-related slash commands with the Textual app."""
    from riftor.mesh.sidebar import MeshSidebar

    sidebar = app.query_one(MeshSidebar)

    app.mesh_manager = manager
    app.mesh_sidebar = sidebar


async def mesh_mode_cmd(app, manager, mode: str) -> None:
    if mode not in ("autonomous", "review", "critical"):
        app._note("Usage: /mesh mode autonomous|review|critical")
        return
    try:
        new_mode = await manager.set_processor_mode(mode)
        app._note(f"Processor mode: {new_mode}")
    except Exception as e:
        app._error(f"Failed to set mode: {e}")


async def mesh_queue_cmd(app, manager) -> None:
    try:
        stats = await manager.get_queue_stats()
        lines = [
            f"Pending: {stats.get('pending', 0)}",
            f"Processing: {stats.get('processing', 0)}",
            f"Completed: {stats.get('completed', 0)}",
            f"Failed: {stats.get('failed', 0)}",
        ]
        app._note("\n".join(lines))
    except Exception as e:
        app._error(f"Failed: {e}")


async def mesh_processor_cmd(app, manager) -> None:
    try:
        stats = await manager.get_queue_stats()
        mode = stats.get("mode", "unknown")
        workers = stats.get("worker_count", "?")
        cb = "OPEN" if stats.get("circuit_open") else "closed"
        app._note(f"Processor: {mode} | Workers: {workers} | Circuit: {cb}")
    except Exception as e:
        app._error(f"Failed: {e}")


async def mesh_review_cmd(app, manager) -> None:
    try:
        decisions = await manager.get_review_queue()
        if not decisions:
            app._note("No pending decisions")
            return
        lines = [f"{len(decisions)} pending decisions:"]
        for i, d in enumerate(decisions[:5]):
            title = d.get("finding", {}).get("title", "N/A")
            decision = d.get("decision", "N/A")
            sev = d.get("severity", "N/A")
            lines.append(f"  #{i+1}: {title} [{sev}] ({decision})")
        app._note("\n".join(lines))
    except Exception as e:
        app._error(f"Failed: {e}")


async def mesh_approve_cmd(app, manager, submission_id: str) -> None:
    try:
        result = await manager.approve_review(submission_id)
        app._note(f"Approved: {result.get('status', 'ok')}")
    except Exception as e:
        app._error(f"Failed: {e}")


async def mesh_reject_cmd(app, manager, submission_id: str, reason: str) -> None:
    try:
        result = await manager.reject_review(submission_id, reason)
        app._note(f"Rejected: {result.get('status', 'ok')}")
    except Exception as e:
        app._error(f"Failed: {e}")
