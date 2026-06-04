"""Chakla worker loop: a stripped headless agent loop for subagent tasks.

A Chakla worker is dispatched by DispatchChaklaTool. It runs an isolated
conversation Context (lore=False — a crisp executor, never the rift persona),
streams from a cheap worker Provider, executes tools with headless-style gating,
and reports a concise ChaklaResult. Workers share the engagement DB (writes
serialized by an asyncio.Lock) but never share conversation state or the
operator's interactive trust.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

from riftor.agent.context import Context
from riftor.agent.provider import Provider, ProviderError, ToolCall, Turn, Usage

if TYPE_CHECKING:
    from riftor.safety.audit import AuditLog
    from riftor.safety.permissions import Permissions
    from riftor.tools.base import ToolContext

#: The dispatch tool is excluded from the worker tool set so a Chakla can never
#: spawn its own Chaklas (no recursion).
DISPATCH_TOOL_NAME = "dispatch_chakla"


@dataclass
class ChaklaResult:
    """The outcome of one Chakla worker."""

    task: str
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    n_recorded: int = 0
    status: Literal["done", "timeout", "error"] = "done"
    error: str | None = None


def worker_schemas() -> list[dict]:
    """Tool schemas for a worker — everything except the dispatch tool."""
    from riftor import tools

    return [t.schema() for t in tools.all_tools() if t.name != DISPATCH_TOOL_NAME]


async def run_chakla(
    task: str,
    *,
    worker_provider: Provider,
    toolctx: ToolContext,
    permissions: "Permissions",
    audit: "AuditLog",
    max_steps: int,
    yolo: bool,
    db_lock: asyncio.Lock,
    grant: set[str],
    progress: "Callable[[dict], None] | None" = None,
) -> ChaklaResult:
    """Run one worker on ``task``. Never raises — failures become ChaklaResult.error."""
    result = ChaklaResult(task=task)
    ctx = Context(lore=False)
    ctx.add_user(task)
    schemas = worker_schemas()
    findings_before = _findings_count(toolctx)

    try:
        for _ in range(max_steps):
            ctx.repair()
            turn: Turn | None = None
            async for kind, payload in worker_provider.stream_turn(ctx.messages, schemas):
                if kind == "text":
                    result.text += str(payload)
                elif kind == "done":
                    turn = payload  # type: ignore[assignment]
            if turn is None:
                break
            result.usage.add(turn.usage)
            ctx.add_message(turn.assistant_message)
            if not turn.tool_calls:
                break
            for call in turn.tool_calls:
                if progress is not None:
                    progress({
                        "state": "detail",
                        "detail": _detail_label(call),
                        "usage": result.usage,
                    })
                content = await _run_chakla_tool(
                    call, toolctx, permissions, audit, yolo=yolo, db_lock=db_lock, grant=grant
                )
                ctx.add_tool_result(call.id, content)
    except ProviderError as exc:
        result.status = "error"
        result.error = f"[{exc.kind}] {exc}"
    except Exception as exc:  # noqa: BLE001 — a worker must never crash the dispatch
        result.status = "error"
        result.error = str(exc)

    result.n_recorded = max(0, _findings_count(toolctx) - findings_before)
    return result


def _findings_count(toolctx: ToolContext) -> int:
    eng = toolctx.engagement
    if eng is None:
        return 0
    try:
        return eng.findings_count()
    except Exception:  # noqa: BLE001
        return 0


def _detail_label(call: ToolCall) -> str:
    """A short live-activity label for a worker tool call, e.g. 'running nmap…'."""
    if call.name == "bash":
        cmd = str(call.arguments.get("command", "")).strip().split() if call.arguments else []
        head = cmd[0] if cmd else "bash"
        return f"running {head}…"
    if call.name in ("record_service", "record_finding", "import_scan"):
        return "recording…"
    return f"{call.name}…"


async def _run_chakla_tool(
    call: ToolCall,
    toolctx: ToolContext,
    permissions: "Permissions",
    audit: "AuditLog",
    *,
    yolo: bool,
    db_lock: asyncio.Lock,
    grant: set[str],
) -> str:
    """Headless-style gating for a worker tool call. Returns the result content."""
    from riftor import tools

    tool = tools.get(call.name)
    if tool is None:
        return f"error: unknown tool '{call.name}'"
    preview = tool.preview(call.arguments)
    eng = toolctx.engagement

    # 1. Scope: hard block, no override (workers have no operator).
    if not yolo and getattr(tool, "scope_sensitive", False) and eng is not None:
        violations = eng.violations(" ".join(str(v) for v in call.arguments.values()))
        if violations:
            audit.record(tool.name, preview, allowed=False)
            return f"[blocked: out of scope] {', '.join(violations)} not in scope."

    # 2. Deny rules bind workers (deny wins over any grant).
    if not yolo and permissions.is_denied(tool.name, preview):
        audit.record(tool.name, preview, allowed=False)
        return "[blocked by policy] denied by a deny rule."

    # 3. Privileged tools: allowed only via a standing allow rule OR the ephemeral
    #    dispatch grant. Read-only tools are always free (they never reach here
    #    because they have requires_permission=False).
    if not yolo and tool.requires_permission:
        granted = tool.name in grant or permissions.is_allowed(tool.name, preview)
        if not granted:
            audit.record(tool.name, preview, allowed=False)
            return (
                f"[denied] {tool.name} was not granted to this worker. "
                "The dispatch did not authorize it."
            )

    # Workers share one engagement DB connection on a single event loop. The lock
    # serializes each tool's execution so concurrent workers can't interleave
    # multi-step tool work (e.g. a long-running bash await) against the shared
    # engagement state mid-operation.
    async with db_lock:
        try:
            res = await tool.execute(call.arguments, toolctx)
        except Exception as exc:  # noqa: BLE001
            res = tools.ToolResult(f"error: {exc}", is_error=True)
    res = res.truncated(toolctx.max_result_chars)
    audit.record(tool.name, preview, allowed=True, is_error=res.is_error)
    return res.content
