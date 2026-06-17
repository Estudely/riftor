"""JSON-line protocol client for riftor-meshd communication."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class MeshResponse:
    id: int | None
    result: dict | None
    error: dict | None

    @property
    def ok(self) -> bool:
        return self.error is None


class MeshProtocol:
    """Async client that speaks JSON-line protocol over stdin/stdout streams."""

    def __init__(self, reader: asyncio.StreamReader, writer):
        self._reader = reader
        self._writer = writer
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[MeshResponse]] = {}
        self._read_task: asyncio.Task | None = None
        self._event_sink: Callable[[dict], Awaitable[None]] | None = None

    def set_event_sink(self, sink: Callable[[dict], Awaitable[None]] | None) -> None:
        """Register an async callback for pushed (non-Response) daemon lines."""
        self._event_sink = sink

    async def start(self) -> None:
        self._read_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

    def _next_req_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def request(self, method: str, params: dict | None = None) -> MeshResponse:
        req_id = self._next_req_id()
        payload = json.dumps({
            "id": req_id,
            "method": method,
            "params": params or {},
        }) + "\n"

        future: asyncio.Future[MeshResponse] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        self._writer.write(payload.encode())
        await self._writer.drain()

        try:
            return await asyncio.wait_for(future, timeout=30.0)
        finally:
            self._pending.pop(req_id, None)

    async def _read_loop(self) -> None:
        while True:
            try:
                line = await self._reader.readline()
            except Exception:
                break

            if not line:
                break

            try:
                data = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict):
                continue

            if "id" in data and data.get("type") != "MeshEvent":
                req_id = data.get("id")
                if req_id is not None and req_id in self._pending:
                    future = self._pending[req_id]
                    if not future.done():
                        future.set_result(MeshResponse(
                            id=req_id,
                            result=data.get("result"),
                            error=data.get("error"),
                        ))
            elif self._event_sink is not None:
                # Pushed lines (gossip-derived MeshEvent lines, etc.) have no
                # pending request to resolve — hand them to the event sink.
                try:
                    await self._event_sink(data)
                except Exception:
                    pass

