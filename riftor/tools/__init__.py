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
from riftor.tools.engagement import (
    AddScopeTool,
    DeleteFindingTool,
    EditFindingTool,
    GenerateReportTool,
    ImportScanTool,
    ListHostsTool,
    ListHypothesesTool,
    ListLessonsTool,
    LoadSkillTool,
    RecordFindingTool,
    RecordHypothesisTool,
    RecordLessonTool,
    RecordServiceTool,
    RememberTool,
    ResolveHypothesisTool,
    ScopeListTool,
    SetStageTool,
    WordlistTool,
)
from riftor.tools.subagent import DispatchChaklaTool
from riftor.tools.browser import (
    BrowserClickTool,
    BrowserConsoleMessagesTool,
    BrowserEvalTool,
    BrowserNavigateTool,
    BrowserNetworkRequestsTool,
    BrowserScreenshotTool,
    BrowserSnapshotTool,
    BrowserTypeTool,
)

# Order is roughly safe -> mutating; it's also the order shown to the model.
ALL_TOOLS: list[Tool] = [
    ScopeListTool(),
    ListHostsTool(),
    WordlistTool(),
    ReadTool(),
    GlobTool(),
    GrepTool(),
    WebFetchTool(),
    BrowserNavigateTool(),
    BrowserSnapshotTool(),
    BrowserClickTool(),
    BrowserTypeTool(),
    BrowserScreenshotTool(),
    BrowserConsoleMessagesTool(),
    BrowserNetworkRequestsTool(),
    SetStageTool(),
    ImportScanTool(),
    RecordServiceTool(),
    RecordFindingTool(),
    EditFindingTool(),
    DeleteFindingTool(),
    GenerateReportTool(),
    LoadSkillTool(),
    DispatchChaklaTool(),
    RecordHypothesisTool(),
    ResolveHypothesisTool(),
    ListHypothesesTool(),
    RecordLessonTool(),
    ListLessonsTool(),
    RememberTool(),
    AddScopeTool(),
    WriteTool(),
    EditTool(),
    BashTool(),
    BrowserEvalTool(),
]

_BY_NAME = {t.name: t for t in ALL_TOOLS}


def all_tools() -> list[Tool]:
    return ALL_TOOLS


def register_plugins(config) -> list:
    """Discover operator plugins and register them into ALL_TOOLS + _BY_NAME.

    The single registry mutator. Keeps all_tools()/schemas()/get() consistent so a
    plugin tool offered to the model can also be dispatched. Idempotent: a tool name
    already present (built-in or already-registered plugin) is skipped. Returns the
    list of PluginError for the caller to surface. Explicit at startup — NOT called
    at import time, so the registry stays deterministic for tests.
    """
    from riftor.plugins import load_plugins

    plugin_tools, errors = load_plugins(config, builtin_names=set(_BY_NAME))
    for t in plugin_tools:
        if t.name in _BY_NAME:
            continue
        ALL_TOOLS.append(t)
        _BY_NAME[t.name] = t
    return errors


def get(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def schemas() -> list[dict]:
    return [t.schema() for t in ALL_TOOLS]


__all__ = ["Tool", "ToolContext", "ToolResult", "all_tools", "get", "register_plugins", "schemas"]
