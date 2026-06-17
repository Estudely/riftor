"""Tests for mesh event handling and daemon line routing."""
import pytest

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
