"""Tests for mesh data models."""
from riftor.mesh.models import (
    MeshFinding,
    Severity,
    FindingStage,
    EngagementMeta,
    MeshEngagementState,
)


def test_mesh_finding_defaults():
    finding = MeshFinding(title="IDOR in /api/users")
    assert finding.id
    assert finding.severity == Severity.MEDIUM
    assert finding.stage == FindingStage.DRAFT


def test_mesh_finding_explicit():
    finding = MeshFinding(
        title="RCE in upload.php",
        severity=Severity.CRITICAL,
        vuln_class="rce",
        target="10.0.0.5",
        description="Remote code execution via file upload",
    )
    assert finding.severity == Severity.CRITICAL
    assert finding.vuln_class == "rce"


def test_engagement_meta():
    meta = EngagementMeta(name="Test Engagement")
    assert meta.id
    assert meta.name == "Test Engagement"
    assert meta.created_at


def test_mesh_engagement_state_defaults():
    state = MeshEngagementState(meta=EngagementMeta(name="test"))
    assert state.findings == []
    assert state.hosts == []
    assert state.services == []
