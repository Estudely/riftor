"""Tests for MeshClient using mocks."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from riftor.mesh.client import MeshClient, MeshError
from riftor.mesh.models import MeshFinding, Severity


@pytest.fixture
def mock_daemon():
    daemon = MagicMock()
    daemon.request = AsyncMock()
    return daemon


@pytest.mark.asyncio
async def test_ping_ok(mock_daemon):
    mock_daemon.request.return_value.ok = True
    client = MeshClient(mock_daemon)
    result = await client.ping()
    assert result is True


@pytest.mark.asyncio
async def test_create_engagement(mock_daemon):
    mock_daemon.request.return_value.ok = True
    mock_daemon.request.return_value.result = {
        "id": "test-id", "name": "My Eng",
        "created_at": "2026-01-01T00:00:00", "node_id": "node1",
    }
    client = MeshClient(mock_daemon)
    meta = await client.create_engagement("My Eng")
    assert meta.id == "test-id"
    assert meta.name == "My Eng"


@pytest.mark.asyncio
async def test_submit_finding(mock_daemon):
    mock_daemon.request.return_value.ok = True
    mock_daemon.request.return_value.result = {"submission_id": "sub-1"}
    client = MeshClient(mock_daemon)
    finding = MeshFinding(title="Test", severity=Severity.HIGH)
    sub_id = await client.submit_finding("eng-1", finding)
    assert sub_id == "sub-1"


@pytest.mark.asyncio
async def test_mesh_error(mock_daemon):
    mock_daemon.request.return_value.ok = False
    mock_daemon.request.return_value.error = {
        "code": "ENGAGEMENT_ERROR", "message": "Already exists",
    }
    client = MeshClient(mock_daemon)
    with pytest.raises(MeshError, match=r"\[ENGAGEMENT_ERROR\] Already exists"):
        await client.create_engagement("dup")
