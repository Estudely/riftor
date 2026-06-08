"""DispatchChaklaTool: Baaj dispatches a batch of cheap Chakla workers.

One worker per task string, run in parallel (asyncio.gather) with a per-worker
timeout. Workers share the engagement DB; their findings persist directly. The
tool returns a compact per-worker digest — the full data lives in the DB.
"""
from __future__ import annotations

import asyncio

from riftor.agent.provider import Provider, Usage
from riftor.agent.subagent import ChaklaResult, run_chakla
from riftor.terminology import terminology
from riftor.tools.base import Tool, ToolContext, ToolResult

#: Privileged tools granted to workers by default when a dispatch is approved.
_DEFAULT_GRANT = ["bash"]


class DispatchChaklaTool(Tool):
    name = "dispatch_chakla"
    description = (
        "Dispatch a batch of lightweight worker subagents (Chakla) to run discrete, "
        "low-effort tasks in parallel — ideal for recon (one worker per host/tool). "
        "Provide an explicit list of task strings; one worker runs per task on a cheap "
        "model. Workers share the engagement scope and database, so any services or "
        "findings they record appear immediately. Workers are sandboxed: they enforce "
        "scope, obey deny rules, and may only run the tools this dispatch grants. Use "
        "this to fan out independent work; do not use it for a single task you can do "
        "yourself."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Discrete task descriptions; one worker runs per task.",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Privileged tools to grant the workers beyond the always-free "
                    "read-only set. Defaults to [\"bash\"]. Scope is still enforced."
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
        grant = args.get("tools") or _DEFAULT_GRANT
        n = len(tasks) if isinstance(tasks, list) else 0
        return f"dispatch {n} workers · grant {list(grant)} · " + "; ".join(
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

        cfg = ctx.config
        labels = terminology(cfg)
        max_workers = max(1, cfg.chakla_max_workers)
        clamped = False
        if len(tasks) > max_workers:
            tasks = tasks[:max_workers]
            clamped = True

        grant_list = args.get("tools")
        if (not isinstance(grant_list, list) or not grant_list
                or not all(isinstance(t, str) for t in grant_list)):
            grant_list = list(_DEFAULT_GRANT)
        grant = {t for t in grant_list}

        # Resolve the worker model: empty chakla_model => reuse the main model,
        # which is always credentialed (the user configured cfg.model). Primary
        # defense against the "worker has no creds" auth-failure bug.
        worker_model = cfg.chakla_model or cfg.model

        # Defense-in-depth: if an explicit worker model has no resolvable creds —
        # and it isn't a local/Ollama model that needs none — refuse to dispatch
        # with a clear, actionable error instead of fanning out N workers that all
        # fail "authentication failed" (and possibly leak the wrong provider's key).
        # We gate on api_key only: the reported failure mode is a missing/mismatched
        # key. A keyless custom endpoint (api_base but no key) is the rare exception
        # and is refused here; set any placeholder key or use the blank-reuse path.
        is_local = worker_model.startswith(("ollama/", "ollama_chat/"))
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
        timeout = max(1, cfg.chakla_timeout_s)
        emit = ctx.progress or (lambda _e: None)

        # All rows appear immediately: emit queued for every worker up front.
        for idx, task in enumerate(tasks):
            emit({"worker": idx, "task": task, "state": "queued",
                  "detail": "", "usage": None, "n_recorded": 0})

        async def _one(idx: int, task: str) -> ChaklaResult:
            def worker_emit(partial: dict) -> None:
                # run_chakla supplies state/detail/usage; we add worker/task and
                # fill any missing keys so every emitted event has the full
                # 6-key shape (worker/task/state/detail/usage/n_recorded).
                # `partial` overrides the defaults.
                emit({"worker": idx, "task": task, "state": "detail",
                      "detail": "", "usage": None, "n_recorded": 0, **partial})

            emit({"worker": idx, "task": task, "state": "running",
                  "detail": "", "usage": None, "n_recorded": 0})
            try:
                r = await asyncio.wait_for(
                    run_chakla(
                        task,
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
                r = ChaklaResult(task=task, status="timeout",
                                 error=f"timed out after {timeout}s")
            emit({"worker": idx, "task": task, "state": r.status,
                  "detail": (r.error or _terminal_detail(r)),
                  "usage": r.usage, "n_recorded": r.n_recorded})
            return r

        results = await asyncio.gather(*[_one(i, t) for i, t in enumerate(tasks)])
        return ToolResult(_format(results, labels, worker_cfg.model, clamped))


def _terminal_detail(r: ChaklaResult) -> str:
    """A short one-liner for a finished worker's terminal event."""
    if r.n_recorded:
        return f"{r.n_recorded} recorded"
    first = r.text.strip().splitlines()[0] if r.text.strip() else ""
    return first[:80]


def _format(results: list[ChaklaResult], labels: dict, model: str, clamped: bool) -> str:
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
        f"{labels['worker_emoji']} {len(results)} {labels['worker']} workers ({model}) · "
        f"{done} done"
        + (f", {timed} timed out" if timed else "")
        + (f", {errored} errored" if errored else "")
        + f" · {tok} tok · ${total.cost:.3f}"
    )
    if clamped:
        header += "  [tasks clamped to chakla_max_workers]"

    lines = [header]
    for i, r in enumerate(results, 1):
        task1 = r.task.replace("\n", " ").strip()[:120]
        mark = {"done": "✓", "timeout": "✗", "error": "✗"}.get(r.status, "?")
        recorded = f" → {r.n_recorded} recorded" if r.n_recorded else ""
        detail = r.error if r.error else (r.text.strip().splitlines()[0] if r.text.strip() else "")
        lines.append(f"[{i}] {mark} {task1}{recorded}" + (f" — {detail}"[:200] if detail else ""))
    return "\n".join(lines)
