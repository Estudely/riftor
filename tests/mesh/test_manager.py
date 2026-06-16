"""Tests for MeshManager."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
@patch("riftor.mesh.manager.MeshDaemon")
async def test_start_stop(mock_daemon_cls):
    mock_daemon = MagicMock()
    mock_daemon.start = AsyncMock()
    mock_daemon.stop = AsyncMock()
    mock_daemon_cls.return_value = mock_daemon

    from riftor.mesh.manager import MeshManager
    manager = MeshManager()
    assert not manager.running

    await manager.start()
    assert manager.running
    mock_daemon.start.assert_called_once()

    await manager.stop()
    assert not manager.running
    mock_daemon.stop.assert_called_once()


@pytest.mark.asyncio
@patch("riftor.mesh.manager.MeshDaemon")
async def test_create_engagement_updates_state(mock_daemon_cls):
    mock_daemon = MagicMock()
    mock_daemon.start = AsyncMock()
    mock_daemon.stop = AsyncMock()
    from riftor.mesh.protocol import MeshResponse
    mock_daemon.request = AsyncMock(return_value=MeshResponse(
        id=1,
        result={"id": "eng-1", "name": "Test", "created_at": "2026-01-01T00:00:00", "node_id": "node1"},
        error=None,
    ))
    mock_daemon_cls.return_value = mock_daemon

    from riftor.mesh.manager import MeshManager
    manager = MeshManager()
    await manager.start()

    meta = await manager.create_engagement("Test")
    assert meta.name == "Test"
    assert manager.current_state is not None
    assert manager.current_state.meta.id == "eng-1"

    await manager.stop()


@pytest.mark.asyncio
@patch("riftor.mesh.manager.MeshDaemon")
async def test_raises_when_not_started(mock_daemon_cls):
    mock_daemon = MagicMock()
    mock_daemon_cls.return_value = mock_daemon

    from riftor.mesh.manager import MeshManager
    manager = MeshManager()
    with pytest.raises(RuntimeError, match="not started"):
        await manager.create_engagement("test")


@pytest.mark.asyncio
@patch("riftor.mesh.manager.MeshDaemon")
async def test_raises_when_no_engagement(mock_daemon_cls):
    mock_daemon = MagicMock()
    mock_daemon.start = AsyncMock()
    mock_daemon_cls.return_value = mock_daemon

    from riftor.mesh.manager import MeshManager
    from riftor.mesh.models import MeshFinding
    manager = MeshManager()
    await manager.start()

    with pytest.raises(RuntimeError, match="No active engagement"):
        await manager.submit_finding(MeshFinding(title="test"))
