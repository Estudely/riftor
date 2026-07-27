"""DispatchWorkerTool: dispatch a batch of parallel worker subagents.

One worker per task string, run in parallel (asyncio.gather) with a per-worker
timeout. Workers share the engagement DB; their findings persist directly.
"""
from __future__ import annotations

import asyncio

from riftor.agent.provider import Provider, Usage
from riftor.agent.subagent import WorkerResult, run_worker
from riftor.terminology import terminology
from riftor.tools.base import Tool, ToolContext, ToolResult
from riftor.workers.registry import get_worker


class DispatchWorkerTool(Tool):
    name = "dispatch_worker"
    description = (
        "Dispatch a batch of lightweight worker subagents to run discrete tasks "
        "in parallel — ideal for recon (one worker per host/tool). Provide an "
        "explicit list of task strings and a worker role (recon, scout, tester, "
        "analyst, exploiter). One worker runs per task on a cheaper model. "
        "Workers share the engagement scope and database. Use this to fan out "
        "independent work; do not use it for a single task or sequential work."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Discrete task descriptions; one worker runs per task.",
            },
            "worker": {
                "type": "string",
                "description": (
                    "Worker role: recon, scout, tester, analyst, or exploiter. "
                    "Defaults to recon."
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Privileged tools to grant beyond the worker role defaults. "
                    "Scope is still enforced."
                ),
            },
        },
        "required": ["tasks"],
    }
    requires_permission = True
    danger = False
    scope_sensitive = False

    def preview(self, args: dict) -> str:
        tasks = args.get("tasks") or []
        worker = args.get("worker") or "recon"
        grant = args.get("tools") or []
        n = len(tasks) if isinstance(tasks, list) else 0
        grant_s = f" grant {list(grant)}" if grant else ""
        return f"dispatch {n} {worker} workers{grant_s} · " + "; ".join(
            str(t) for t in (tasks[:3] if isinstance(tasks, list) else [])
        )[:240]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.config is None or ctx.permissions is None or ctx.audit is None:
            return ToolResult("subagents unavailable (no config in this context)", is_error=True)

        perms = ctx.permissions.without_session_grants()
        audit = ctx.audit

        tasks = args.get("tasks") or []
        if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
            return ToolResult("error: 'tasks' must be a list of strings", is_error=True)
        tasks = [t for t in tasks if t.strip()]
        if not tasks:
            return ToolResult("error: 'tasks' is empty", is_error=True)

        worker_name = str(args.get("worker") or "recon").strip().lower()
        spec = get_worker(worker_name)
        if spec is None:
            return ToolResult(f"error: unknown worker '{worker_name}'", is_error=True)

        cfg = ctx.config
        labels = terminology()
        max_workers = max(1, cfg.worker_max_parallel)
        clamped = False
        if len(tasks) > max_workers:
            tasks = tasks[:max_workers]
            clamped = True

        grant_list = args.get("tools")
        if isinstance(grant_list, list) and grant_list and all(isinstance(t, str) for t in grant_list):
            grant = {t for t in grant_list}
        else:
            grant = set(spec.tools)

        worker_model = spec.model or cfg.worker_model or cfg.model

        is_local = worker_model.startswith(("ollama/", "ollama_chat/", "codex/"))
        api_key, _api_base = cfg.creds_for(worker_model)
        if not is_local and api_key is None:
            return ToolResult(
                f"no credentials for worker model {worker_model!r}; set them in "
                f"/config WORKERS (provider + key), or leave the worker model blank "
                f"to reuse the main model ({cfg.model}).",
                is_error=True,
            )

        worker_cfg = cfg.model_copy(update={"model": worker_model})
        worker_provider = Provider(worker_cfg)
        db_lock = asyncio.Lock()
        timeout = max(1, cfg.worker_timeout_s)
        emit = ctx.progress or (lambda _e: None)

        for idx, task in enumerate(tasks):
            emit({"worker": idx, "task": task, "state": "queued",
                  "detail": "", "usage": None, "n_recorded": 0})

        async def _one(idx: int, task: str) -> WorkerResult:
            def worker_emit(partial: dict) -> None:
                emit({"worker": idx, "task": task, "state": "detail",
                      "detail": "", "usage": None, "n_recorded": 0, **partial})

            emit({"worker": idx, "task": task, "state": "running",
                  "detail": "", "usage": None, "n_recorded": 0})
            try:
                r = await asyncio.wait_for(
                    run_worker(
                        task,
                        worker_spec=spec,
                        worker_provider=worker_provider,
                        toolctx=ctx,
                        permissions=perms,
                        audit=audit,
                        max_steps=cfg.max_steps,
                        yolo=ctx.yolo,
                        db_lock=db_lock,
                        grant=grant,
                        progress=worker_emit,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                r = WorkerResult(task=task, status="timeout",
                                 error=f"timed out after {timeout}s")
            emit({"worker": idx, "task": task, "state": r.status,
                  "detail": (r.error or _terminal_detail(r)),
                  "usage": r.usage, "n_recorded": r.n_recorded})
            return r

        results = await asyncio.gather(*[_one(i, t) for i, t in enumerate(tasks)])
        return ToolResult(_format(results, labels, worker_cfg.model, worker_name, clamped))


def _terminal_detail(r: WorkerResult) -> str:
    if r.n_recorded:
        return f"{r.n_recorded} recorded"
    first = r.text.strip().splitlines()[0] if r.text.strip() else ""
    return first[:80]


def _format(
    results: list[WorkerResult],
    labels: dict,
    model: str,
    worker_name: str,
    clamped: bool,
) -> str:
    total = Usage()
    done = sum(1 for r in results if r.status == "done")
    timed = sum(1 for r in results if r.status == "timeout")
    errored = sum(1 for r in results if r.status == "error")
    for r in results:
        total.add(r.usage)

    tok = f"{total.total_tokens / 1000:.1f}k" if total.total_tokens >= 1000 else str(
        total.total_tokens
    )
    header = (
        f"{labels['worker_emoji']} {len(results)} {worker_name} workers ({model}) · "
        f"{done} done"
        + (f", {timed} timed out" if timed else "")
        + (f", {errored} errored" if errored else "")
        + f" · {tok} tok · ${total.cost:.3f}"
    )
    if clamped:
        header += "  [tasks clamped to worker_max_parallel]"

    lines = [header]
    for i, r in enumerate(results, 1):
        task1 = r.task.replace("\n", " ").strip()[:120]
        mark = {"done": "✓", "timeout": "✗", "error": "✗"}.get(r.status, "?")
        recorded = f" → {r.n_recorded} recorded" if r.n_recorded else ""
        detail = r.error if r.error else (r.text.strip().splitlines()[0] if r.text.strip() else "")
        lines.append(f"[{i}] {mark} {task1}{recorded}" + (f" — {detail}"[:200] if detail else ""))
    return "\n".join(lines)


# Backward-compat alias
DispatchChaklaTool = DispatchWorkerTool
