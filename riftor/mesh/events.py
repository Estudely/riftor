"""Event dispatch system for mesh events."""
from __future__ import annotations

import logging
from collections.abc import Callable, Awaitable

logger = logging.getLogger(__name__)

MeshCallback = Callable[[str, dict], Awaitable[None]]


class MeshEventHandler:
    def __init__(self):
        self._callbacks: dict[str, list[MeshCallback]] = {
            "state_changed": [],
            "member_joined": [],
            "member_left": [],
            "submission_received": [],
            "engagement_created": [],
            "engagement_joined": [],
            "daemon_started": [],
            # gossip-derived subtopics emitted by the daemon as MeshEvent lines
            "processed": [],
            "activity": [],
            "presence": [],
            "submit": [],
        }

    def on(self, event: str, callback: MeshCallback) -> None:
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def off(self, event: str, callback: MeshCallback) -> None:
        if event in self._callbacks:
            self._callbacks[event].remove(callback)

    async def dispatch(self, event: str, data: dict) -> None:
        callbacks = self._callbacks.get(event, [])
        for cb in callbacks:
            try:
                await cb(event, data)
            except Exception:
                logger.exception("Error in mesh event handler for %s", event)


async def route_mesh_line(data: dict, handler: MeshEventHandler) -> bool:
    """Route a parsed daemon stdout line to the event handler.

    Recognizes gossip-derived ``MeshEvent`` lines of the shape::

        {"type": "MeshEvent", "engagement_id": ..., "subtopic": ..., "payload": ...}

    and dispatches them to ``handler`` keyed by ``subtopic``. Non-MeshEvent
    lines (e.g. ``Response`` lines) are ignored. Returns ``True`` if the line
    was a MeshEvent that got dispatched, ``False`` otherwise.
    """
    if not isinstance(data, dict) or data.get("type") != "MeshEvent":
        return False
    subtopic = data.get("subtopic")
    if not subtopic:
        return False
    body = {
        "engagement_id": data.get("engagement_id"),
        "payload": data.get("payload"),
    }
    await handler.dispatch(subtopic, body)
    return True
