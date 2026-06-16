"""High-level async client for mesh operations."""

from __future__ import annotations

from riftor.mesh.daemon import MeshDaemon
from riftor.mesh.models import (
    MeshFinding, MeshHost, MeshService, EngagementMeta, MeshEngagementState,
)


class MeshError(Exception):
    def __init__(self, error: dict):
        self.code = error.get("code", "UNKNOWN")
        self.message = error.get("message", "Unknown error")
        super().__init__(f"[{self.code}] {self.message}")


class MeshClient:
    def __init__(self, daemon: MeshDaemon):
        self._daemon = daemon

    async def ping(self) -> bool:
        resp = await self._daemon.request("ping")
        return resp.ok

    async def create_identity(self) -> dict:
        resp = await self._daemon.request("create_identity")
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result or {}

    async def create_engagement(self, name: str) -> EngagementMeta:
        resp = await self._daemon.request("create_engagement", {"name": name})
        if not resp.ok:
            raise MeshError(resp.error or {})
        result = resp.result or {}
        return EngagementMeta(**result)

    async def generate_invite(self, engagement_id: str) -> str:
        resp = await self._daemon.request("generate_invite", {"engagement_id": engagement_id})
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result.get("invite", "") if resp.result else ""

    async def join_engagement(self, invite: str) -> EngagementMeta:
        resp = await self._daemon.request("join_engagement", {"invite": invite})
        if not resp.ok:
            raise MeshError(resp.error or {})
        result = resp.result or {}
        return EngagementMeta(**result)

    async def leave_engagement(self, engagement_id: str) -> None:
        resp = await self._daemon.request("leave_engagement", {"engagement_id": engagement_id})
        if not resp.ok:
            raise MeshError(resp.error or {})

    async def submit_finding(self, engagement_id: str, finding: MeshFinding) -> str:
        resp = await self._daemon.request("submit", {
            "engagement_id": engagement_id,
            "submission": {"type": "finding", "data": finding.model_dump()},
        })
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result.get("submission_id", "") if resp.result else ""

    async def submit_host(self, engagement_id: str, host: MeshHost) -> str:
        resp = await self._daemon.request("submit", {
            "engagement_id": engagement_id,
            "submission": {"type": "host", "data": host.model_dump()},
        })
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result.get("submission_id", "") if resp.result else ""

    async def submit_service(self, engagement_id: str, service: MeshService) -> str:
        resp = await self._daemon.request("submit", {
            "engagement_id": engagement_id,
            "submission": {"type": "service", "data": service.model_dump()},
        })
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result.get("submission_id", "") if resp.result else ""

    async def get_state(self, engagement_id: str) -> MeshEngagementState:
        resp = await self._daemon.request("get_state", {"engagement_id": engagement_id})
        if not resp.ok:
            raise MeshError(resp.error or {})
        result = resp.result or {}
        findings = [MeshFinding(**f) for f in result.get("findings", [])]
        hosts = [MeshHost(**h) for h in result.get("hosts", [])]
        services = [MeshService(**s) for s in result.get("services", [])]
        return MeshEngagementState(
            meta=EngagementMeta(name="loaded", id=engagement_id),
            findings=findings, hosts=hosts, services=services,
        )

    async def add_blob(self, engagement_id: str, data: bytes) -> str:
        import base64
        resp = await self._daemon.request("add_blob", {
            "engagement_id": engagement_id,
            "data": base64.b64encode(data).decode(),
        })
        if not resp.ok:
            raise MeshError(resp.error or {})
        return resp.result.get("hash", "") if resp.result else ""

    async def get_blob(self, engagement_id: str, hash_: str) -> bytes:
        import base64
        resp = await self._daemon.request("get_blob", {"engagement_id": engagement_id, "hash": hash_})
        if not resp.ok:
            raise MeshError(resp.error or {})
        b64 = resp.result.get("data", "") if resp.result else ""
        return base64.b64decode(b64)
