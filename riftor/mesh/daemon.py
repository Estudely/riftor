"""Mesh daemon process manager.

Spawns and manages the riftor-meshd Rust binary as a subprocess.
"""

from __future__ import annotations

import asyncio
import signal
import logging
from pathlib import Path

from riftor.mesh.protocol import MeshProtocol, MeshResponse

logger = logging.getLogger(__name__)


class MeshDaemon:
    """Manages the riftor-meshd subprocess lifecycle."""

    def __init__(self, binary_path: str | None = None):
        self._binary_path = binary_path or self._find_binary()
        self._process: asyncio.subprocess.Process | None = None
        self._protocol: MeshProtocol | None = None

    @property
    def protocol(self) -> MeshProtocol:
        if self._protocol is None:
            raise RuntimeError("Daemon not started. Call start() first.")
        return self._protocol

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return

        logger.info("Starting riftor-meshd: %s", self._binary_path)

        self._process = await asyncio.create_subprocess_exec(
            self._binary_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Use subprocess streams directly — no need for connect_read_pipe/connect_write_pipe
        self._protocol = MeshProtocol(
            self._process.stdout,   # StreamReader
            self._process.stdin,    # StreamWriter-like (has write + drain)
        )
        await self._protocol.start()
        logger.info("riftor-meshd started")

    async def stop(self) -> None:
        if not self.running or self._process is None:
            return

        logger.info("Stopping riftor-meshd")

        if self._protocol:
            await self._protocol.stop()

        self._process.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

        self._process = None
        self._protocol = None
        logger.info("riftor-meshd stopped")

    async def request(self, method: str, params: dict | None = None) -> MeshResponse:
        if not self.running:
            await self.start()
        return await self.protocol.request(method, params)

    def _find_binary(self) -> str:
        """Find the riftor-meshd binary."""
        import shutil
        pkg_dir = Path(__file__).parent.parent.parent
        candidate = pkg_dir / "meshd" / "target" / "release" / "riftor-meshd"
        if candidate.exists():
            return str(candidate)
        candidate = pkg_dir / "meshd" / "target" / "debug" / "riftor-meshd"
        if candidate.exists():
            return str(candidate)
        found = shutil.which("riftor-meshd")
        if found:
            return found
        raise RuntimeError(
            "Could not find riftor-meshd binary. "
            "Build with: cargo build --manifest-path meshd/Cargo.toml --release"
        )
