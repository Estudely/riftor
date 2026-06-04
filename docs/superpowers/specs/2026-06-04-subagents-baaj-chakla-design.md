# Subagents (Baaj / Chakla) — Design

**Date:** 2026-06-04
**Status:** Approved — ready for implementation planning
**Scope:** v1 = Approach A (core dispatch). Live worker-visibility (Approach B) is a recorded follow-up (`todo.md` Phase 7b), out of scope here.

## Summary

Add orchestrator/worker delegation to riftor. A powerful main agent **Baaj** (🦅 eagle) dispatches multiple lightweight, cheap workers **Chakla** (🐦 sparrows) to run batches of low-effort parallel tasks — the flagship use case being recon (nmap / httpx / subfinder sweeps across in-scope hosts). The naming terminology is config-renameable so "Baaj"/"Chakla" can become anything later.

The entire feature is **one new tool plus one new worker loop**. Neither agent loop (`tui/app.py:_agent`, `headless.py:_run`) changes — dispatch rides the existing tool-dispatch path, so it works identically in TUI and headless mode. This is the central design decision and the reason the footprint is small.

## Goals

- Baaj can delegate an explicit list of discrete tasks, one Chakla worker per task, running in parallel.
- Workers use a separate, cheap model (e.g. Haiku) configurable independently of the main model.
- Workers can actually *do recon* — i.e. run `bash` — gated safely (see Permission bridge).
- Findings/services workers discover land directly in the shared engagement DB; Baaj sees them with no merge step.
- Fan-out is capped and time-bounded so one dispatch can't silently explode cost or hang.
- Terminology (Baaj/Chakla) and the worker model are configurable.

## Non-goals (v1)

- **No live per-worker progress UI.** v1 shows a spinner during dispatch, then an aggregated result. The live "flock" panel and a tool→UI progress channel are Phase 7b.
- **No worker session persistence / resume.** Workers are ephemeral; a sub-task has no resume story.
- **No nested dispatch.** A Chakla cannot spawn Chaklas (recursion is structurally excluded).
- **No per-worker audit transcript merged into the parent log.** (Possible future work.)

## Architecture & data flow

```
Baaj (main agent, expensive model)
  │  emits a tool call:  dispatch_chakla(tasks=[...])
  ▼
DispatchChaklaTool.execute(args, ctx)          # tools/subagent.py
  │  1. guard: ctx.config is None → "subagents unavailable" error
  │  2. clamp len(tasks) to chakla_max_workers (note any clamp in the result)
  │  3. worker_cfg = ctx.config.model_copy(update={"model": chakla_model, ...})
  │     worker_provider = Provider(worker_cfg)        # the cheap model
  │  4. build a scoped ephemeral permission grant (the bridge)
  │  5. shared asyncio.Lock for DB writes
  ▼
  asyncio.gather(
    wait_for(run_chakla(task_1, ...), timeout),   # 🐦 Chakla worker 1
    wait_for(run_chakla(task_2, ...), timeout),   # 🐦 Chakla worker 2
    ...
  )
  ▼
  aggregate → one ToolResult (per-worker summaries + totals), truncated normally
  ▼
Baaj sees the aggregated digest + can query the shared DB for everything workers recorded
```

### State sharing matrix

| State | Shared with workers? | Mechanism |
|---|---|---|
| Engagement DB (`Store`, SQLite) | **SHARED** | Same `workdir` → same `.riftor/engagement.db`. Findings/services persist directly; no merge. Writes serialized by a shared `asyncio.Lock`. |
| Scope target list | **SHARED (read)** | Workers inherit scope targets and enforce them per-call. |
| Permission deny/allow *rules* | **SHARED (read)** | Parent `Permissions` passed down; deny rules bind every worker. |
| Permission session grants | **MUST NOT SHARE** | Workers never inherit "allow for session" approvals; they use the ephemeral dispatch grant only. |
| Conversation `Context` | **ISOLATED** | Fresh `Context(lore=False)` per worker; workers never touch Baaj's history. |
| `Usage` (tokens/cost) | **SEPARATE accumulator** | Worker spend tracked distinctly so it's visible and not double-counted. |
| Anti-loop tracking | **ISOLATED** | Each worker keeps its own. |
| yolo flag | **INHERITED (read)** | Workers inherit `toolctx.yolo`; a worker is never more privileged than its parent. |

### Files

**New:**
- `riftor/tools/subagent.py` — `DispatchChaklaTool`.
- `riftor/agent/subagent.py` — `run_chakla()` worker loop + `ChaklaResult`.

**Modified:**
- `riftor/tools/__init__.py` — register `DispatchChaklaTool` in `ALL_TOOLS` (after read-only/list tools, before mutating `WriteTool`/`EditTool`/`BashTool`).
- `riftor/tools/base.py` — extend `ToolContext` with optional fields.
- `riftor/config.py` — new fields + `_to_toml()` lines.
- `riftor/tui/config_screen.py` + `riftor/tui/app.py` — config wiring, status-bar usage.
- `riftor/tui/widgets.py` — separate worker-cost status segment; terminology labels.
- `riftor/__main__.py` — `--chakla-model` flag.
- `completions/riftor.bash`, `completions/_riftor` — new flag.
- `docs/configuration.md`, `docs/riftor.1` — document fields + flag.

## The Chakla worker loop

`run_chakla(task, *, worker_provider, toolctx, permissions, audit, max_steps, yolo, db_lock, grant) -> ChaklaResult` in `agent/subagent.py`. It is the headless loop body (`headless.py:67-88`) factored into a reusable function, differing as follows:

```python
child_ctx = Context(lore=False)            # crisp executor, never roleplays
child_ctx.add_user(task)                   # the worker's whole job = the task string
usage_acc = Usage()
for step in range(max_steps):              # default chakla_max_steps (8)
    child_ctx.repair()
    async for kind, payload in worker_provider.stream_turn(child_ctx.messages, child_schemas):
        if kind == "done":
            turn = payload
    usage_acc.add(turn.usage)
    if not turn.tool_calls:
        break                              # worker is done talking
    for call in turn.tool_calls:
        result = await _run_chakla_tool(call, ...)   # gated — see below
        child_ctx.add_tool_result(call.id, result.content)
return ChaklaResult(task, final_text, usage_acc, n_recorded, error=None)
```

- **`child_schemas` excludes `dispatch_chakla`** — a sparrow cannot spawn sparrows. No recursion, ever. (`tools.schemas()` returns all tools today; workers get a filtered variant.)
- Workers write findings/services to the shared DB through `db_lock`.
- `lore=False` always — workers are executors, not the rift persona, regardless of `config.lore`.

### `ChaklaResult`

A small dataclass: `task: str`, `text: str` (final prose answer), `usage: Usage`, `n_recorded: int` (findings+services committed), `status: Literal["done","timeout","error"]`, `error: str | None`.

## Safety / permission model

Workers have **no operator**, so gating mirrors headless mode (`headless.py:109-132`), never the interactive TUI path. For each worker tool call (`_run_chakla_tool`):

1. **Scope (hard, no override).** `scope_sensitive` tools call `engagement.violations(probe)`. Out-of-scope is hard-blocked with no override — exactly like headless. This binds even under the permission grant.
2. **Deny rules.** Parent `permissions.is_denied(name, preview)` applies. A `rm -rf` deny on Baaj holds for every Chakla.
3. **Permission bridge (the make-or-break decision).** Read-only tools (read/grep/glob/list/query) are always free. For the privileged tools workers need (default `["bash"]`), instead of headless auto-deny, the worker checks an **ephemeral `grant`** created when the operator approved the `dispatch_chakla` call:
   - `DispatchChaklaTool` is `requires_permission=True`, so the dispatch itself goes through the normal approval gate (`ConfirmScreen` in TUI; standing-allow-rule check in headless).
   - Approving the dispatch authorizes the requested tools **for this dispatch only**. The grant is never written to `permissions.toml` and never becomes a session grant. It lives and dies with the `execute()` call.
   - Scope (step 1) is still enforced per-command, so "approve the workers' bash" never means "off-scope bash."
   - The grant covers **only** the tools named in the `tools` arg. Precedence matches the existing engine: **deny wins** — a tool that is both deny-ruled (step 2) and granted (step 3) is still denied.
4. **yolo.** Workers inherit `toolctx.yolo`. If Baaj is yolo, workers are too (consistent — the operator already disarmed guards). A worker can never be *more* privileged than its parent; no `yolo=True` child under a `yolo=False` parent.

### `ToolContext` extension (`tools/base.py`)

Four new **optional** fields so the dispatch tool can reach the LLM and gating, keeping all existing tools and the `toolctx` test fixture working unchanged:

```python
config: "Config | None" = None
permissions: "Permissions | None" = None
audit: "AuditLog | None" = None
yolo: bool = False
```

Populated at the two construction sites (`tui/app.py`, `headless.py`). The dispatch tool guards: `if ctx.config is None: return ToolResult("subagents unavailable", is_error=True)`. The live `Provider` is **not** placed in `ToolContext` (it holds keys and is per-model); the worker provider is built inside the tool from `ctx.config`.

## Concurrency & limits

- **Fan-out cap.** `len(tasks)` clamped to `chakla_max_workers` (default 5). Clamping is reported in the result, never silent.
- **Per-worker step budget.** `chakla_max_steps` (default 8, vs Baaj's 16).
- **Per-worker timeout.** Each worker wrapped in `asyncio.wait_for(run_chakla(...), timeout=chakla_timeout_s)` (default 300s). Timeout/exception → `ChaklaResult(status="timeout"/"error")`, not a crash. Findings already committed survive.
- **DB safety.** Shared `asyncio.Lock` around engagement-store mutations prevents `SQLITE_BUSY` under parallel writers.
- **Join.** `asyncio.gather(*workers, return_exceptions=True)` so one failed worker doesn't poison the batch.

## Config

New `Config` fields (Pydantic — old `config.toml` files load fine; fields take defaults until next save):

```python
chakla_model: str = "anthropic/claude-haiku-4-5-20251001"  # cheap worker default
chakla_max_workers: int = 5        # hard fan-out cap
chakla_max_steps: int = 8          # per-worker step budget
chakla_timeout_s: int = 300        # per-worker wall-clock timeout
label_main: str = "Baaj"           # renameable terminology
label_worker: str = "Chakla"
```

- `_to_toml()` gets an explicit line per field (it constructs TOML manually).
- `detect_defaults()` leaves `chakla_model` at its default.
- Worker provider built via `ctx.config.model_copy(update={"model": chakla_model})`. `creds_for(model)` is already model-keyed and routes via `provider_key_for_model`, so a worker on `anthropic/claude-haiku-4-5` resolves Anthropic creds from the shared `providers` dict with zero extra wiring. Use `model_copy` (not a shared reference) — `Config` is mutable; mutating `.model` in place would corrupt the parent.

### Terminology helper

A small module-level helper (e.g. `terminology(config) -> {main, worker, main_emoji, worker_emoji}`) centralizes labels + emoji (🦅 / 🐦) so the UI reads labels from config and renaming is one place. Labels are **UI-facing only** — they do not become CLI-parse tokens (the tool name stays `dispatch_chakla` internally; renaming labels never breaks command parsing or docs).

## Tool schema

`dispatch_chakla` — `requires_permission=True`, `danger=False`, `scope_sensitive=False`:

```json
{
  "tasks": ["nmap top-1000 on 10.0.0.5", "httpx probe web ports on 10.0.0.5", "subfinder example.com"],
  "tools": ["bash"]
}
```

- `tasks` (required): list of discrete task strings; one worker per task.
- `tools` (optional, default `["bash"]`): the privileged tools (those with `requires_permission=True` — `add_scope`/`write`/`edit`/`bash`) to grant workers. All other tools are non-privileged and run without a grant; the grant gate is reached only for permissioned tools.
- Permission preview shows: *"{label_main} dispatches N {label_worker} workers (model: <chakla_model>) — granting [bash] within scope. Tasks: …"* so the operator sees exactly what is authorized.

## Result aggregation

A single `ToolResult` whose content is a compact per-worker digest (findings live in the DB; this is a summary, not the data):

```
🐦 3 Chakla workers (claude-haiku-4-5) · 2 done, 1 timed out · 47.2k tok · $0.014
[1] ✓ nmap top-1000 on 10.0.0.5 → 4 services recorded
[2] ✓ httpx probe → 2 services, 1 finding recorded
[3] ✗ subfinder example.com → timed out after 300s
```

Truncated by `config.max_result_chars` like any tool result. Baaj can `/findings` or query the store directly for the full data.

## UI surface

- **Status bar** (`widgets.py`): add `set_chakla_usage(tokens, cost)` and render a distinct segment (e.g. `🐦 47k $0.01`) only when non-zero, kept separate from Baaj's own token/cost so worker spend is visible and never double-counted.
- **Config screen** (`config_screen.py`): a "WORKERS" section with a Chakla provider+model picker (reusing existing `PROVIDER_DEFAULTS`/`fetch_models` dropdown machinery) + inputs for `label_main`/`label_worker`. Collected in `on_button_pressed`, unpacked and persisted in `RiftorApp._open_config` (`app.py`).

## CLI / docs / completions

- `--chakla-model` flag in `__main__.py` (runtime override, not persisted), parallel to `--model`.
- Update `completions/riftor.bash` and `completions/_riftor` (project convention: update completions when adding/renaming a flag).
- Document new fields + flag in `docs/configuration.md` and `docs/riftor.1`.

## Testing (all offline)

`RIFTOR_DEMO_RESPONSE` makes the worker provider stream canned text, so the full fan-out is testable with no network. The shared `toolctx` fixture (`tests/conftest.py`) gains the optional fields (defaulting None) — existing tests unaffected.

Test coverage:
- Happy path: dispatch N tasks → N workers run → each records a finding to the DB → aggregated result lists them.
- Clamp: `tasks` longer than `chakla_max_workers` is clamped and the clamp is reported.
- Scope hard-block inside a worker: an off-scope `bash` probe is blocked with no override.
- Deny-rule inheritance: a parent `bash rm -rf` deny blocks the same in a worker.
- Permission bridge: read-only tools run without a grant; `bash` runs only under the dispatch grant; the grant does not leak to `permissions.toml` or session grants.
- Timeout path: a worker exceeding `chakla_timeout_s` yields `status="timeout"`, the batch still returns, committed findings persist.
- Guard: `ctx.config is None` → `"subagents unavailable"` error, no crash.
- No recursion: `dispatch_chakla` absent from `child_schemas`.

Extend `dev/smoke.py` only if a TUI spawn affordance is added.

## Top risks (mitigations baked into the design)

1. **Headless auto-deny would kill recon** → solved by the permission bridge (approve dispatch = scoped ephemeral grant).
2. **SQLite write contention under parallel workers** → shared `asyncio.Lock` around store mutations.
3. **Cost/step amplification (1 dispatch = N × max_steps)** → `chakla_max_workers` cap + lower `chakla_max_steps` + per-worker timeout; worker spend shown separately in the status bar.
4. **`ToolContext` change touching every tool + tests** → all new fields optional and guarded for `None`.
5. **Usage double-counting** → workers use a distinct provider and a separate accumulator; never folded into Baaj's `turn.usage`.
6. **Persona bleed** → workers always `lore=False`.
7. **Self-recursion / id collision** → `dispatch_chakla` excluded from worker schemas; worker `Context` never merged into Baaj's.
8. **Permission escalation** → workers never inherit session grants; never more privileged than parent; scope always hard-enforced.
