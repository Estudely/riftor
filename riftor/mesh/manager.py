"""Mesh manager — orchestrates daemon, client, and event handling."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from riftor.mesh.daemon import MeshDaemon
from riftor.mesh.client import MeshClient
from riftor.mesh.events import MeshEventHandler
from riftor.mesh.models import (
    MeshEngagementState, EngagementMeta, MeshFinding,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MeshManager:
    def __init__(self, binary_path: str | None = None):
        self._daemon = MeshDaemon(binary_path)
        self._client: MeshClient | None = None
        self._events = MeshEventHandler()
        self._current_engagement: MeshEngagementState | None = None
        self._running = False

    @property
    def events(self) -> MeshEventHandler:
        return self._events

    @property
    def current_state(self) -> MeshEngagementState | None:
        return self._current_engagement

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        await self._daemon.start()
        self._client = MeshClient(self._daemon)
        self._running = True
        await self._events.dispatch("daemon_started", {})

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._daemon.stop()
        self._client = None
        self._current_engagement = None

    async def create_identity(self) -> dict:
        return await self._ensure_client().create_identity()

    async def create_engagement(self, name: str) -> EngagementMeta:
        client = self._ensure_client()
        meta = await client.create_engagement(name)
        self._current_engagement = MeshEngagementState(meta=meta)
        await self._events.dispatch("engagement_created", meta.model_dump())
        return meta

    async def generate_invite(self, engagement_id: str) -> str:
        return await self._ensure_client().generate_invite(engagement_id)

    async def join_engagement(self, invite: str) -> EngagementMeta:
        client = self._ensure_client()
        meta = await client.join_engagement(invite)
        state = await client.get_state(meta.id)
        state.meta = meta
        self._current_engagement = state
        await self._events.dispatch("engagement_joined", meta.model_dump())
        return meta

    async def leave_engagement(self) -> None:
        client = self._ensure_client()
        eng = self._current_engagement
        if eng:
            await client.leave_engagement(eng.meta.id)
            self._current_engagement = None

    async def submit_finding(self, finding: MeshFinding) -> str:
        client = self._ensure_client()
        state = self._ensure_engagement()
        sub_id = await client.submit_finding(state.meta.id, finding)
        state.findings.append(finding)
        await self._events.dispatch("submission_received", {
            "submission_id": sub_id, "finding": finding.model_dump(),
        })
        return sub_id

    async def refresh_state(self) -> MeshEngagementState:
        client = self._ensure_client()
        state = self._ensure_engagement()
        self._current_engagement = await client.get_state(state.meta.id)
        return self._current_engagement

    def _ensure_client(self) -> MeshClient:
        if not self._running or self._client is None:
            raise RuntimeError("Mesh not started. Call start() first.")
        return self._client

    def _ensure_engagement(self) -> MeshEngagementState:
        if self._current_engagement is None:
            raise RuntimeError("No active engagement. Call create_engagement() or join_engagement() first.")
        return self._current_engagement
