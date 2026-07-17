"""Headless / one-shot mode: run a single task without the TUI.

Useful for scripting and CI. The agent streams to stdout. For safety, dangerous
tools (bash/write/edit) only run if an explicit allow rule exists in
``permissions.toml`` — otherwise they are auto-denied (no interactive operator to
approve). Out-of-scope, scope-sensitive calls are always blocked.

Exit codes:
  0   finished normally (model stopped without more tool calls)
  1   provider error
  2   no prompt given
  3   missing API credentials
  4   stopped after ``max_steps`` (truncated; raise the budget or use YOLO)
  130 interrupted (KeyboardInterrupt)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Callable

from riftor import tools
from riftor.agent import session as sessions
from riftor.agent.context import Context
from riftor.agent.provider import Provider, ProviderError, ToolCall, Turn
from riftor.config import Config
from riftor import config as configmod
from riftor.engagement import Engagement
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import Permissions
from riftor.tools import ToolContext, ToolResult


def _make_progress_printer(total: int = 0) -> Callable[[dict], None]:
    """Return a progress callback that prints one stderr line per terminal worker
    event. Non-terminal states (queued/running/detail) are ignored so stdout's
    sibling stream isn't flooded. ``total`` is the worker count for the ``[i/N]``
    label; 0 means unknown (label shows just ``[i]``).

    Token formatting parallels ``widgets._fmt_tok`` but folds in a comma prefix and
    `` tok`` suffix and omits the ``—`` placeholder (this is a log fragment, not a
    table cell), so the two are intentionally not shared."""

    def _printer(event: dict) -> None:
        state = event.get("state")
        if state not in ("done", "timeout", "error"):
            return
        idx = int(event.get("worker", 0)) + 1
        task = str(event.get("task", "")).replace("\n", " ").strip()[:48]
        detail = str(event.get("detail", "") or "").strip()[:80]  # cap free-form detail length
        usage = event.get("usage")
        tok = ""
        if usage is not None and usage.total_tokens:
            n = usage.total_tokens
            tok = f", {n / 1000:.1f}k tok" if n >= 1000 else f", {n} tok"
        suffix = f" — {state}" + (f" ({detail}{tok})" if (detail or tok) else "")
        label = f"[{idx}/{total}]" if total else f"[{idx}]"
        print(f"  🐦 {label} {task}{suffix}", file=sys.stderr)

    return _printer


def run_headless(
    cfg: Config,
    workdir: Path,
    *,
    prompt: str | None,
    scope_file: str | None = None,
    yolo: bool = False,
) -> int:
    if not prompt:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        if not prompt:
            print("riftor --headless: no prompt (use --prompt or pipe stdin)", file=sys.stderr)
            return 2
    if not cfg.has_credentials() and not os.environ.get("RIFTOR_DEMO_RESPONSE"):
        env = cfg.provider_env() or "ANTHROPIC_API_KEY"
        print(f"riftor: no API key for {cfg.model}; set {env}", file=sys.stderr)
        return 3
    try:
        return asyncio.run(_run(cfg, workdir, prompt, scope_file, yolo=yolo))
    except KeyboardInterrupt:
        return 130


async def _run(cfg: Config, workdir: Path, prompt: str, scope_file: str | None, yolo: bool = False) -> int:
    context = Context(lore=cfg.lore, workdir=workdir)
    provider = Provider(cfg)
    engagement = Engagement(workdir)
    if scope_file:
        try:
            engagement.import_scope(Path(scope_file).expanduser().read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"riftor: scope file error: {exc}", file=sys.stderr)
    permissions = Permissions.load(configmod.PERMISSIONS_PATH)
    audit = AuditLog()
    toolctx = ToolContext(
        workdir=workdir,
        engagement=engagement,
        max_result_chars=cfg.max_result_chars,
        config=cfg,
        permissions=permissions,
        audit=audit,
        yolo=yolo,
        progress=_make_progress_printer(),
    )
    for err in tools.register_plugins(cfg):
        print(f"riftor: plugin '{err.module}' skipped: {err.error.splitlines()[-1]}", file=sys.stderr)
    if getattr(cfg, "mcp_servers", None):
        from riftor.mcp import register_mcp

        for err in await register_mcp(cfg):
            print(
                f"riftor: mcp '{err.server}' skipped: {err.error.splitlines()[-1]}",
                file=sys.stderr,
            )
    schemas = tools.schemas()

    context.add_user(prompt)
    max_steps = 10**9 if yolo else cfg.max_steps
    # Persist the session so a crashed headless run (OOM, SIGTERM, network drop)
    # leaves a resumable checkpoint, matching the TUI's behavior (issue #120).
    sid = sessions.new_id()

    def _checkpoint(complete: bool) -> None:
        try:
            sessions.save(workdir, sid, context.dump(), cfg.model, complete=complete)
        except Exception:  # noqa: BLE001 — never let checkpointing break the run
            pass

    truncated = False
    try:
        for _ in range(max_steps):
            context.repair()
            _checkpoint(complete=False)  # per-step checkpoint before the model call
            text_parts: list[str] = []
            turn: Turn | None = None
            try:
                async for event, payload in provider.stream_turn(context.messages, schemas):
                    if event == "thinking":
                        if cfg.show_thinking:
                            # reasoning goes to stderr so stdout stays the clean answer
                            sys.stderr.write(str(payload))
                            sys.stderr.flush()
                    elif event == "text":
                        sys.stdout.write(str(payload))
                        sys.stdout.flush()
                        text_parts.append(str(payload))
                    elif event == "done":
                        turn = payload if isinstance(payload, Turn) else None
            except ProviderError as exc:
                print(f"\nriftor: provider error [{exc.kind}] — {exc}", file=sys.stderr)
                _checkpoint(complete=False)  # leave a resumable checkpoint on error
                return 1
            if turn is None:
                break
            context.add_message(turn.assistant_message)
            if not turn.tool_calls:
                break
            for call in turn.tool_calls:
                await _run_tool_headless(call, engagement, permissions, audit, toolctx, context, yolo=yolo)
        else:
            # for-else: loop exhausted without a clean break → step budget hit
            truncated = True
        _checkpoint(complete=not truncated)
    finally:
        if toolctx.browser is not None and toolctx.browser.launched:
            try:
                await toolctx.browser.close()
            except Exception:  # noqa: BLE001
                pass
        engagement.close()
    print()  # trailing newline
    if truncated:
        print(
            f"riftor: stopped after {max_steps} steps (max_steps); "
            "raise max_steps in config or pass --i-know-what-i-am-doing-give-me-full-access",
            file=sys.stderr,
        )
        return 4
    return 0


async def _run_tool_headless(
    call: ToolCall,
    engagement: Engagement,
    permissions: Permissions,
    audit: AuditLog,
    toolctx: ToolContext,
    context: Context,
    yolo: bool = False,
) -> None:
    tool = tools.get(call.name)
    if tool is None:
        context.add_tool_result(call.id, f"error: unknown tool '{call.name}'")
        return
    preview = tool.preview(call.arguments)
    print(f"\n  ⛏ {tool.name}  {preview}", file=sys.stderr)

    if not yolo and getattr(tool, "scope_sensitive", False):
        violations = engagement.violations(" ".join(str(v) for v in call.arguments.values()))
        if violations:
            context.add_tool_result(
                call.id,
                f"[blocked: out of scope] {', '.join(violations)} not in scope.",
            )
            audit.record(tool.name, preview, allowed=False)
            return

    if not yolo and permissions.is_denied(tool.name, preview):
        context.add_tool_result(call.id, "[blocked by policy] denied by a deny rule.")
        audit.record(tool.name, preview, allowed=False)
        return

    # No operator present: dangerous tools require a standing allow rule.
    if not yolo and tool.requires_permission and not permissions.is_allowed(tool.name, preview):
        context.add_tool_result(
            call.id,
            "[denied: headless] This tool needs approval and no allow rule exists. "
            "Add one to ~/.config/riftor/permissions.toml "
            "(see docs/configuration.md — Headless / CI allowlist).",
        )
        audit.record(tool.name, preview, allowed=False)
        return

    try:
        result = await tool.execute(call.arguments, toolctx)
    except Exception as exc:  # noqa: BLE001
        result = ToolResult(f"error: {exc}", is_error=True)
    result = result.truncated(toolctx.max_result_chars)
    audit.record(tool.name, preview, allowed=True, is_error=result.is_error)
    context.add_tool_result(call.id, result.content)
