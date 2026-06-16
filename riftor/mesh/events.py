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
