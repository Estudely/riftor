"""MCP client registration helpers (#49) — offline, no live servers."""

from __future__ import annotations

from riftor.config import Config
from riftor.mcp import parse_mcp_servers, register_mcp, register_mcp_sync


def test_parse_mcp_servers_normalizes():
    specs = parse_mcp_servers(
        [
            {
                "name": "File System!",
                "command": "npx",
                "args": ["-y", "server"],
                "env": {"FOO": "1"},
            },
            {"name": "", "command": "x"},  # skipped
            {"name": "x", "command": ""},  # skipped
            "bad",
        ]
    )
    assert len(specs) == 1
    assert specs[0].name == "file_system"
    assert specs[0].command == "npx"
    assert specs[0].args == ["-y", "server"]
    assert specs[0].env == {"FOO": "1"}


def test_register_mcp_noop_when_disabled():
    cfg = Config(mcp_enabled=False, mcp_servers=[{"name": "x", "command": "true"}])
    errors = register_mcp_sync(cfg)
    assert errors == []


def test_register_mcp_noop_when_no_servers():
    cfg = Config(mcp_enabled=True, mcp_servers=[])
    errors = register_mcp_sync(cfg)
    assert errors == []


async def test_register_mcp_reports_missing_package(monkeypatch):
    """When servers are configured but the SDK is absent, surface a clear error."""
    import builtins
    import sys

    cfg = Config(
        mcp_enabled=True,
        mcp_servers=[{"name": "demo", "command": "true"}],
    )
    real_import = builtins.__import__

    def _block_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    # Ensure a previously-imported mcp doesn't short-circuit the check.
    sys.modules.pop("mcp", None)
    monkeypatch.setattr(builtins, "__import__", _block_mcp)
    errors = await register_mcp(cfg)
    assert errors
    assert "riftor[mcp]" in errors[0].error or "not installed" in errors[0].error
