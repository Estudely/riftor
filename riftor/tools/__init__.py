"""Tool registry."""

from __future__ import annotations

from riftor.tools.base import Tool, ToolContext, ToolResult
from riftor.tools.core import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WebFetchTool,
    WriteTool,
)

# Order is roughly safe -> mutating; it's also the order shown to the model.
ALL_TOOLS: list[Tool] = [
    ReadTool(),
    GlobTool(),
    GrepTool(),
    WebFetchTool(),
    WriteTool(),
    EditTool(),
    BashTool(),
]

_BY_NAME = {t.name: t for t in ALL_TOOLS}


def all_tools() -> list[Tool]:
    return ALL_TOOLS


def get(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def schemas() -> list[dict]:
    return [t.schema() for t in ALL_TOOLS]


__all__ = ["Tool", "ToolContext", "ToolResult", "all_tools", "get", "schemas"]
