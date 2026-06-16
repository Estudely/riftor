"""Tests for MeshProtocol."""
import asyncio
import pytest
from riftor.mesh.protocol import MeshProtocol, MeshResponse


class _FakeWriter:
    def write(self, data): pass
    async def drain(self): pass


@pytest.mark.asyncio
async def test_mesh_response_ok():
    resp = MeshResponse(id=1, result={"pong": True}, error=None)
    assert resp.ok is True
    assert resp.result == {"pong": True}


@pytest.mark.asyncio
async def test_mesh_response_error():
    resp = MeshResponse(id=1, result=None, error={"code": "TEST", "message": "bang"})
    assert resp.ok is False
    assert resp.error == {"code": "TEST", "message": "bang"}


@pytest.mark.asyncio
async def test_request_id_is_monotonic():
    proto = MeshProtocol(asyncio.StreamReader(), _FakeWriter())
    id1 = proto._next_req_id()
    id2 = proto._next_req_id()
    assert id2 == id1 + 1


@pytest.mark.asyncio
async def test_start_stop_does_not_crash():
    proto = MeshProtocol(asyncio.StreamReader(), _FakeWriter())
    await proto.start()
    assert proto._read_task is not None
    await proto.stop()
