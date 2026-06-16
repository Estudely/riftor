"""Pydantic models for mesh data types."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStage(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    VERIFIED = "verified"
    CLOSED = "closed"
    DUPLICATE = "duplicate"


class EngagementState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class MemberInfo(BaseModel):
    node_id: str
    display_name: str = ""
    role: str = "worker"
    online: bool = False


class EngagementMeta(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    node_id: str = ""


class MeshFinding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    severity: Severity = Severity.MEDIUM
    stage: FindingStage = FindingStage.DRAFT
    target: str = ""
    vuln_class: str = ""
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    cvss: str | None = None
    author: str = ""
    co_contributors: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_finding_ids: list[str] = Field(default_factory=list)


class MeshHost(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ips: list[str] = Field(default_factory=list)
    hostnames: list[str] = Field(default_factory=list)
    os_guess: str = ""


class MeshService(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    host_id: str = ""
    port: int = 0
    protocol: str = "tcp"
    service_name: str = ""
    version: str = ""
    banner: str = ""


class MeshTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    column: str = "backlog"
    assigned_to: str | None = None
    skill_tags: list[str] = Field(default_factory=list)
    submitted_by: str | None = None


class MeshEngagementState(BaseModel):
    meta: EngagementMeta
    findings: list[MeshFinding] = Field(default_factory=list)
    hosts: list[MeshHost] = Field(default_factory=list)
    services: list[MeshService] = Field(default_factory=list)
    tasks: list[MeshTask] = Field(default_factory=list)
    members: list[MemberInfo] = Field(default_factory=list)
