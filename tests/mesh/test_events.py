"""Tests for mesh event handling and daemon line routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from riftor.mesh.events import MeshEventHandler, route_mesh_line


@pytest.mark.asyncio
async def test_handler_dispatch_invokes_registered_callback():
    handler = MeshEventHandler()
    seen = []

    async def cb(event, data):
        seen.append((event, data))

    handler.on("processed", cb)
    await handler.dispatch("processed", {"engagement_id": "eng1"})
    assert seen == [("processed", {"engagement_id": "eng1"})]


@pytest.mark.asyncio
async def test_handler_knows_new_subtopics():
    handler = MeshEventHandler()
    for subtopic in ("processed", "activity", "presence", "submit"):
        assert subtopic in handler._callbacks


@pytest.mark.asyncio
async def test_route_mesh_line_dispatches_subtopic():
    handler = MeshEventHandler()
    seen = []

    async def cb(event, data):
        seen.append((event, data))

    handler.on("processed", cb)
    line = {
        "type": "MeshEvent",
        "engagement_id": "eng1",
        "subtopic": "processed",
        "payload": {"event": "finding_published", "key": "finding/f1"},
    }
    routed = await route_mesh_line(line, handler)
    assert routed is True
    assert seen == [
        (
            "processed",
            {
                "engagement_id": "eng1",
                "payload": {"event": "finding_published", "key": "finding/f1"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_route_mesh_line_ignores_non_mesh_event():
    handler = MeshEventHandler()
    routed = await route_mesh_line({"id": 1, "result": {}}, handler)
    assert routed is False


@pytest.mark.asyncio
async def test_route_mesh_line_handles_missing_subtopic():
    handler = MeshEventHandler()
    routed = await route_mesh_line({"type": "MeshEvent", "payload": {}}, handler)
    assert routed is False


@pytest.mark.asyncio
async def test_event_sink_handler_can_issue_rpc_without_deadlock():
    """Regression: a sink handler that itself issues an RPC must not deadlock.

    The sink is dispatched off the read loop, so a handler reacting to a pushed
    MeshEvent (e.g. by calling get_state) still has its response resolved by the
    same read loop instead of blocking it.
    """
    import asyncio
    import json

    from riftor.mesh.protocol import MeshProtocol

    reader = asyncio.StreamReader()

    class _Writer:
        def __init__(self):
            self.buf = b""

        def write(self, data):
            self.buf += data

        async def drain(self):
            pass

    writer = _Writer()
    proto = MeshProtocol(reader, writer)

    rpc_result: list = []

    async def sink(data: dict) -> None:
        # React to the pushed event by issuing an RPC, as the real
        # 'processed' handler does via get_state.
        resp = await proto.request("get_state", {"engagement_id": "eng1"})
        rpc_result.append(resp.result)

    proto.set_event_sink(sink)
    await proto.start()

    # Feed a pushed MeshEvent line (triggers the sink -> RPC).
    reader.feed_data(
        (json.dumps({"type": "MeshEvent", "subtopic": "processed", "payload": {}}) + "\n").encode()
    )

    # Give the loop a tick to dispatch the sink and send the RPC, then feed the
    # RPC's response (request id 1, since it is the first request issued).
    await asyncio.sleep(0.05)
    reader.feed_data((json.dumps({"id": 1, "result": {"findings": []}}) + "\n").encode())

    # If the sink had been awaited inline, the read loop could never resolve the
    # get_state future and this would time out.
    await asyncio.wait_for(_until(lambda: rpc_result), timeout=2.0)
    assert rpc_result == [{"findings": []}]

    await proto.stop()


async def _until(pred):
    import asyncio

    while not pred():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@patch("riftor.mesh.manager.MeshDaemon")
async def test_presence_updates_member_list(mock_daemon_cls):
    """A presence heartbeat should populate the manager's members map."""
    mock_daemon = MagicMock()
    mock_daemon.start = AsyncMock()
    mock_daemon.stop = AsyncMock()
    mock_daemon_cls.return_value = mock_daemon

    from riftor.mesh.manager import MeshManager

    manager = MeshManager()
    await manager.start()

    assert manager.members == []

    manager.update_member_presence("node-abc", "2026-06-17T00:00:00Z")
    members = manager.members
    assert len(members) == 1
    assert members[0]["node_id"] == "node-abc"
    assert members[0]["last_seen"] == "2026-06-17T00:00:00Z"
    assert members[0]["online"] is True

    manager.update_member_presence("node-xyz", "2026-06-17T00:00:15Z")
    assert len(manager.members) == 2

    manager.update_member_presence("node-abc", "2026-06-17T00:00:30Z")
    assert len(manager.members) == 2
    assert manager.members[0]["last_seen"] == "2026-06-17T00:00:30Z"

    await manager.stop()
