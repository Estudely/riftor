"""Tests for daemon lifecycle."""
import pytest
from riftor.mesh.daemon import MeshDaemon


def test_find_binary_raises_when_not_found(monkeypatch):
    import shutil
    from pathlib import Path
    monkeypatch.setattr(shutil, "which", lambda _x: None)
    monkeypatch.setattr(Path, "exists", lambda _self: False)
    with pytest.raises(RuntimeError, match="Could not find riftor-meshd"):
        MeshDaemon()


def test_running_false_initially():
    daemon = MeshDaemon(binary_path="/nonexistent/path")
    assert not daemon.running


def test_protocol_raises_before_start():
    daemon = MeshDaemon(binary_path="/nonexistent/path")
    with pytest.raises(RuntimeError, match="not started"):
        _ = daemon.protocol
