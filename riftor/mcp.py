"""MCP client: discover tools from stdio MCP servers and proxy them.

Optional extra ``riftor[mcp]`` (``mcp>=1.0,<2``). When the SDK is absent or
``mcp_enabled`` is false, registration is a no-op — mirrors plugins/browser.
Bad servers warn + skip; never fatal.

MVP transport: stdio only. Discovery connects once at startup; each
``execute()`` opens a fresh short-lived session (simple lifecycle, no orphan
subprocesses across /new).

Config (``config.toml``):

```toml
[riftor]
mcp_enabled = true

[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Any

from riftor.tools.base import Tool, ToolContext, ToolResult

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


@dataclass
class McpServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class McpError:
    server: str
    error: str


def _sanitize(name: str) -> str:
    cleaned = _NAME_SAFE.sub("_", (name or "").strip()).strip("_").lower()
    return cleaned or "tool"


def parse_mcp_servers(raw: Any) -> list[McpServerSpec]:
    """Normalize config ``mcp_servers`` (list of dicts) into specs. Never raises."""
    if not isinstance(raw, list):
        return []
    out: list[McpServerSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not name or not command:
            continue
        args = item.get("args") or []
        if not isinstance(args, list):
            args = []
        env = item.get("env") or {}
        if not isinstance(env, dict):
            env = {}
        out.append(
            McpServerSpec(
                name=_sanitize(name),
                command=command,
                args=[str(a) for a in args],
                env={str(k): str(v) for k, v in env.items()},
            )
        )
    return out


def _merged_env(spec: McpServerSpec) -> dict[str, str] | None:
    if not spec.env:
        return None
    merged = dict(os.environ)
    merged.update(spec.env)
    return merged


async def _with_session(spec: McpServerSpec, fn):
    """Open a stdio MCP session, run ``fn(session)``, tear down."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=spec.command,
        args=list(spec.args),
        env=_merged_env(spec),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def _schema_dict(remote: Any) -> dict:
    schema = getattr(remote, "inputSchema", None) or getattr(remote, "input_schema", None)
    if schema is None:
        return {"type": "object", "properties": {}}
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    return schema


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(str(block))
    if getattr(result, "is_error", False):
        raise RuntimeError("\n".join(parts) or "MCP tool returned is_error")
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, default=str)
    return ""


class McpToolProxy(Tool):
    """A Tool that forwards execute() to a remote MCP server tool."""

    requires_permission = True
    danger = True
    scope_sensitive = False

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict,
        spec: McpServerSpec,
        remote_name: str,
    ) -> None:
        self.name = name
        self.description = description or f"MCP tool {remote_name} from {spec.name}"
        self.parameters = parameters or {"type": "object", "properties": {}}
        self._spec = spec
        self._remote_name = remote_name

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        try:
            async def _call(session):
                return await session.call_tool(
                    self._remote_name, arguments=params or {}
                )

            result = await _with_session(self._spec, _call)
            return ToolResult(content=_result_text(result))
        except ImportError:
            return ToolResult(
                content="mcp package not installed — pip install 'riftor[mcp]'",
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                content=f"mcp error ({self._spec.name}/{self._remote_name}): {exc}",
                is_error=True,
            )


async def _discover(spec: McpServerSpec) -> list[Tool]:
    async def _list(session):
        return await session.list_tools()

    listed = await _with_session(spec, _list)
    tools: list[Tool] = []
    for remote in listed.tools:
        remote_name = getattr(remote, "name", "") or ""
        if not remote_name:
            continue
        proxy_name = f"mcp_{spec.name}_{_sanitize(remote_name)}"
        tools.append(
            McpToolProxy(
                name=proxy_name,
                description=getattr(remote, "description", None) or remote_name,
                parameters=_schema_dict(remote),
                spec=spec,
                remote_name=remote_name,
            )
        )
    return tools


# Last successful discovery summary for /doctor.
_LAST_CONNECTED: list[str] = []
_LAST_ERRORS: list[McpError] = []


def mcp_status() -> tuple[list[str], list[McpError]]:
    return list(_LAST_CONNECTED), list(_LAST_ERRORS)


async def register_mcp(config, *, builtin_names: set[str] | None = None) -> list[McpError]:
    """Discover MCP tools and append them to the tool registry. Never raises."""
    global _LAST_CONNECTED, _LAST_ERRORS
    from riftor import tools as tools_mod

    _LAST_CONNECTED = []
    _LAST_ERRORS = []

    enabled = getattr(config, "mcp_enabled", True)
    if not enabled:
        return []
    specs = parse_mcp_servers(getattr(config, "mcp_servers", None))
    if not specs:
        return []

    try:
        import mcp  # noqa: F401
    except ImportError:
        err = McpError(
            "*",
            "mcp package not installed — pip install 'riftor[mcp]'",
        )
        _LAST_ERRORS = [err]
        return [err]

    taken = set(builtin_names or set())
    for t in tools_mod.all_tools():
        taken.add(t.name)

    errors: list[McpError] = []
    for spec in specs:
        try:
            proxies = await _discover(spec)
        except Exception:  # noqa: BLE001
            errors.append(McpError(spec.name, traceback.format_exc(limit=3).strip()))
            continue
        _LAST_CONNECTED.append(spec.name)
        for tool in proxies:
            if tool.name in taken:
                errors.append(
                    McpError(spec.name, f"tool name collision, skipped: {tool.name}")
                )
                continue
            tools_mod.ALL_TOOLS.append(tool)
            tools_mod._BY_NAME[tool.name] = tool
            taken.add(tool.name)

    _LAST_ERRORS = errors
    return errors


def register_mcp_sync(config, *, builtin_names: set[str] | None = None) -> list[McpError]:
    """Sync wrapper for headless / non-async startup."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(register_mcp(config, builtin_names=builtin_names))
    # Already inside a running loop — caller must use ``register_mcp`` (async).
    return []
