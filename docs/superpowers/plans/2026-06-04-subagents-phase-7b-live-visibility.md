# Phase 7b — Live Worker Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `dispatch_chakla` worker batch observable in real time — a live per-worker table in the TUI, worker token/cost ticking in the status bar, and stderr progress in headless mode.

**Architecture:** A single optional `progress` callback on `ToolContext`. The dispatch tool and worker loop emit small event dicts through it at lifecycle points. The TUI renders them into an ephemeral `DataTable` (removed when the dispatch finishes; the existing aggregated text result block remains the permanent record). Headless prints terminal events to stderr. Tests leave the callback `None`, so every existing tool and test is unaffected. The callback runs on the app's event loop (`_agent` is `@work(exclusive=True)`, async — not a thread), so widget mutation is direct, no `call_from_thread`.

**Tech Stack:** Python 3.11+, Textual 8.2.7 (`DataTable`), litellm (offline via `RIFTOR_DEMO_RESPONSE`), pytest (asyncio auto mode), `uv`.

**Spec:** `docs/superpowers/specs/2026-06-04-subagents-phase-7b-live-visibility-design.md`

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `riftor/tools/base.py` | Add the `progress` field to `ToolContext` | Modify |
| `riftor/agent/subagent.py` | `run_chakla` emits per-tool-call `detail` events | Modify |
| `riftor/tools/subagent.py` | Dispatch emits `queued`/`running`/terminal events; binds per-worker closure | Modify |
| `riftor/tui/widgets.py` | New `FlockPane(DataTable)` widget (create-or-update by worker index) | Modify |
| `riftor/tui/app.py` | `_on_chakla_progress` handler; wire `progress=`; mount/clear pane; sum usage | Modify |
| `riftor/headless.py` | Set `progress=` to a stderr printer | Modify |
| `tests/test_subagent.py` | New offline tests for channel, detail, usage, headless | Modify |
| `dev/smoke.py` | Drive a dispatch; assert pane mounts/updates/removes + 🐦 usage | Modify |
| `docs/configuration.md`, `todo.md` | Document behavior; tick 7b boxes | Modify |

**Event shape** (the contract every task shares — a plain dict, no class):

```python
{
  "worker": 0,                      # stable 0-based worker index = DataTable row key (as str)
  "task": "nmap 10.0.0.5",          # the worker's task string
  "state": "queued"|"running"|"detail"|"done"|"timeout"|"error",
  "detail": "running bash…",        # live activity, or a final one-liner on terminal states
  "usage": Usage|None,              # worker's cumulative Usage; present on running/detail/terminal
  "n_recorded": 0,                  # findings/services committed; meaningful on terminal states
}
```

`Usage` is `riftor.agent.provider.Usage` (`prompt_tokens`, `completion_tokens`, `cost`, `.total_tokens` property, `.add(other)`).

---

## Task 1: Add the `progress` callback field to `ToolContext`

**Files:**
- Modify: `riftor/tools/base.py`
- Test: `tests/test_subagent.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_subagent.py`:

```python
def test_toolcontext_progress_defaults_to_none(tmp_workdir, engagement):
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement)
    assert ctx.progress is None


def test_toolcontext_progress_is_callable_when_set(tmp_workdir, engagement):
    seen = []
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement,
                      progress=lambda e: seen.append(e))
    assert ctx.progress is not None
    ctx.progress({"worker": 0, "state": "running"})
    assert seen == [{"worker": 0, "state": "running"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_toolcontext_progress_defaults_to_none tests/test_subagent.py::test_toolcontext_progress_is_callable_when_set -v`
Expected: FAIL — `TypeError: ToolContext.__init__() got an unexpected keyword argument 'progress'` (second test) / first may error on attribute access.

- [ ] **Step 3: Add the field**

In `riftor/tools/base.py`, add `Callable` to the typing import at the top:

```python
from typing import TYPE_CHECKING, Callable
```

Then add the field to `ToolContext` (after the `yolo: bool = False` line, keeping it last so it stays optional):

```python
    yolo: bool = False
    #: Optional UI/progress channel for tools that report incremental progress
    #: (DispatchChaklaTool / run_chakla). The callback takes one event dict and
    #: returns None. None in headless tests and ordinary tools — a no-op.
    #: INVARIANT: invoked on the caller's event loop. In the TUI the agent loop
    #: is @work(exclusive=True) (async, NOT thread=True), so the callback runs on
    #: the UI task and may mutate widgets directly. If the agent loop ever moves
    #: to a thread, the callback must marshal via call_from_thread.
    progress: "Callable[[dict], None] | None" = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_toolcontext_progress_defaults_to_none tests/test_subagent.py::test_toolcontext_progress_is_callable_when_set -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Typecheck + commit**

Run: `uv run pyright riftor/tools/base.py`
Expected: 0 errors

```bash
git add riftor/tools/base.py tests/test_subagent.py
git commit -m "feat(7b): add optional progress callback to ToolContext"
```

---

## Task 2: `run_chakla` emits per-tool-call `detail` events

**Files:**
- Modify: `riftor/agent/subagent.py:48-95`
- Test: `tests/test_subagent.py`

`run_chakla` gains one optional param, `progress`. The worker/task identity is bound by the caller (Task 3) into the closure, so `run_chakla` itself only stamps `state`/`detail`/`usage` — the caller's closure adds `worker`/`task`. To keep `run_chakla` self-contained and testable, the closure it receives takes a partial event dict and the caller fills the rest.

Define the contract: `progress` here is called as `progress({"state": "detail", "detail": "...", "usage": <Usage>})`. The dispatch tool (Task 3) wraps it to add `worker`/`task`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_subagent.py` (the `_run_one` helper already builds a worker run; we add a variant that passes a `progress` collector). Add this standalone test:

We use a tiny stub provider that yields exactly one `scope_list` tool call (a
read-only tool that runs without a grant), then a no-tool turn — forcing the
worker's tool loop to execute once so a `detail` event genuinely fires. This
verifies the feature directly rather than relying on Task 3.

```python
def test_run_chakla_emits_detail_events(tmp_workdir, engagement):
    from riftor.agent.provider import ToolCall, Turn, Usage

    class _StubProvider:
        """Yields one scope_list tool call, then a plain answer turn."""
        def __init__(self):
            self._calls = 0

        async def stream_turn(self, messages, schemas):
            self._calls += 1
            if self._calls == 1:
                tc = ToolCall(id="t1", name="scope_list", arguments={})
                yield ("done", Turn(
                    text="", tool_calls=[tc],
                    assistant_message={"role": "assistant", "content": None,
                                       "tool_calls": [{"id": "t1", "type": "function",
                                                       "function": {"name": "scope_list",
                                                                    "arguments": "{}"}}]},
                    usage=Usage(prompt_tokens=10, completion_tokens=5),
                ))
            else:
                yield ("text", "done.")
                yield ("done", Turn(
                    text="done.", tool_calls=[],
                    assistant_message={"role": "assistant", "content": "done."},
                    usage=Usage(prompt_tokens=4, completion_tokens=2),
                ))

    events = []
    cfg = Config()
    toolctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    result = asyncio.run(run_chakla(
        "recon 10.0.0.5",
        worker_provider=_StubProvider(),  # type: ignore[arg-type]
        toolctx=toolctx, permissions=toolctx.permissions, audit=toolctx.audit,
        max_steps=cfg.chakla_max_steps, yolo=False,
        db_lock=asyncio.Lock(), grant=set(),
        progress=lambda e: events.append(e),
    ))
    assert result.status == "done"
    # Exactly one detail event fired (one tool call), well-formed.
    detail_events = [e for e in events if e["state"] == "detail"]
    assert len(detail_events) == 1, events
    assert detail_events[0]["detail"]  # non-empty label like "scope_list…"
    assert "usage" in detail_events[0]  # carries the worker's running Usage
```

Note: the stub is a duck-typed stand-in for `Provider` (it only needs `stream_turn`); the `# type: ignore[arg-type]` keeps pyright quiet about the non-`Provider` argument. This proves a `detail` event fires per tool call with the documented shape, fully offline.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_run_chakla_emits_detail_events -v`
Expected: FAIL — `TypeError: run_chakla() got an unexpected keyword argument 'progress'`

- [ ] **Step 3: Add `progress` param + emit detail after each tool call**

In `riftor/agent/subagent.py`, update the imports to include `Callable`:

```python
from typing import TYPE_CHECKING, Callable, Literal
```

Update the `run_chakla` signature (add `progress` as the last keyword-only param):

```python
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
```

Replace the tool-call loop body (currently lines 82-86):

```python
            for call in turn.tool_calls:
                content = await _run_chakla_tool(
                    call, toolctx, permissions, audit, yolo=yolo, db_lock=db_lock, grant=grant
                )
                ctx.add_tool_result(call.id, content)
```

with a version that emits a `detail` event before running each tool:

```python
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
```

Add this helper near the other module-level helpers (e.g. after `_findings_count`):

```python
def _detail_label(call: "ToolCall") -> str:
    """A short live-activity label for a worker tool call, e.g. 'running nmap…'."""
    if call.name == "bash":
        cmd = str(call.arguments.get("command", "")).strip().split() if call.arguments else []
        head = cmd[0] if cmd else "bash"
        return f"running {head}…"
    if call.name in ("record_service", "record_finding", "import_scan"):
        return "recording…"
    return f"{call.name}…"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_run_chakla_emits_detail_events -v`
Expected: PASS

- [ ] **Step 5: Run the full 7a suite to confirm no regression**

Run: `uv run pytest tests/test_subagent.py -q`
Expected: all pass (existing 7a tests call `run_chakla` without `progress`, which defaults to `None`).

- [ ] **Step 6: Typecheck + commit**

Run: `uv run pyright riftor/agent/subagent.py`
Expected: 0 errors

```bash
git add riftor/agent/subagent.py tests/test_subagent.py
git commit -m "feat(7b): run_chakla emits per-tool-call detail events"
```

---

## Task 3: Dispatch emits queued/running/terminal events with worker index

**Files:**
- Modify: `riftor/tools/subagent.py:63-139`
- Test: `tests/test_subagent.py`

The dispatch owns worker indices. It builds, per worker, a small closure that stamps `worker`/`task` onto whatever `run_chakla` emits, and emits `queued` (before gather), `running` (at worker start), and exactly one terminal event (`done`/`timeout`/`error`) with the final `Usage` and `n_recorded`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_subagent.py`:

```python
def test_dispatch_emits_ordered_lifecycle_events(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done")
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(), yolo=False,
        progress=lambda e: events.append(dict(e)),
    )
    res = asyncio.run(DispatchChaklaTool().execute(
        {"tasks": ["recon A", "recon B", "recon C"], "tools": []}, ctx))
    assert not res.is_error
    # Every worker 0,1,2 emits queued, then running, then exactly one terminal.
    by_worker = {}
    for e in events:
        by_worker.setdefault(e["worker"], []).append(e["state"])
    assert set(by_worker) == {0, 1, 2}
    terminal = {"done", "timeout", "error"}
    for w, states in by_worker.items():
        assert states[0] == "queued", states
        assert "running" in states, states
        assert states[-1] in terminal, states
        assert sum(1 for s in states if s in terminal) == 1, states


def test_dispatch_terminal_events_carry_usage(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done")
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    asyncio.run(DispatchChaklaTool().execute({"tasks": ["a", "b"], "tools": []}, ctx))
    terminals = [e for e in events if e["state"] in ("done", "timeout", "error")]
    assert len(terminals) == 2
    for e in terminals:
        assert e["usage"] is not None  # final Usage attached for status-bar summing


def test_dispatch_terminal_usage_sums_to_worker_total(tmp_workdir, engagement, monkeypatch):
    # Spec test 3: the usage the app would accumulate from terminal events (the
    # way _on_chakla_progress does) equals the sum of each worker's real Usage.
    # Stub run_chakla to return known per-worker Usage so the sum is meaningful
    # offline (the demo provider reports zero usage). 47.2k tok total => 23_600
    # completion tokens per worker over 2 workers; 0.014 total cost.
    from riftor.agent.provider import Usage
    import riftor.tools.subagent as sub

    async def _fake(task, **k):
        return ChaklaResult(task=task, status="done",
                            usage=Usage(completion_tokens=23_600, cost=0.007), n_recorded=1)

    monkeypatch.setattr(sub, "run_chakla", _fake)
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    asyncio.run(DispatchChaklaTool().execute({"tasks": ["a", "b"], "tools": []}, ctx))
    # Accumulate the way the app does: only terminal events, via Usage.add.
    accumulated = Usage()
    for e in events:
        if e["state"] in ("done", "timeout", "error") and e["usage"] is not None:
            accumulated.add(e["usage"])
    assert accumulated.total_tokens == 47_200  # 23_600 * 2
    assert abs(accumulated.cost - 0.014) < 1e-9  # 0.007 * 2


def test_dispatch_progress_none_is_safe(tmp_workdir, engagement, monkeypatch):
    # A dispatch with no progress callback runs identically and returns the same
    # aggregated text (the 7a behavior is unchanged when progress is None).
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done: nothing notable")
    cfg = Config(api_key="test-key")
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )  # progress defaults to None
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["recon A"], "tools": []}, ctx))
    assert not res.is_error
    assert "recon A" in res.content


def test_dispatch_timeout_emits_timeout_event(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_timeout_s=1, api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    import riftor.tools.subagent as sub

    async def _hang(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(sub, "run_chakla", _hang)
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["slow"], "tools": []}, ctx))
    assert not res.is_error
    states = [e["state"] for e in events if e["worker"] == 0]
    assert "timeout" in states, states
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_subagent.py -k "dispatch_emits or terminal_events_carry or terminal_usage_sums or progress_none_is_safe or timeout_emits" -v`
Expected: FAIL — `test_dispatch_emits_ordered_lifecycle_events` and others fail because no events are emitted (`events` stays empty → KeyError/assert). `progress_none_is_safe` may already pass; that's fine.

- [ ] **Step 3: Emit events in `DispatchChaklaTool.execute`**

In `riftor/tools/subagent.py`, replace the worker-fanout block. The current code (lines 115-139) is:

```python
        worker_cfg = cfg.model_copy(update={"model": worker_model})
        worker_provider = Provider(worker_cfg)
        db_lock = asyncio.Lock()
        timeout = max(1, cfg.chakla_timeout_s)

        async def _one(task: str) -> ChaklaResult:
            try:
                return await asyncio.wait_for(
                    run_chakla(
                        task,
                        worker_provider=worker_provider,
                        toolctx=ctx,
                        permissions=perms,
                        audit=audit,
                        max_steps=cfg.chakla_max_steps,
                        yolo=ctx.yolo,
                        db_lock=db_lock,
                        grant=grant,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return ChaklaResult(task=task, status="timeout",
                                    error=f"timed out after {timeout}s")

        results = await asyncio.gather(*[_one(t) for t in tasks])
        return ToolResult(_format(results, labels, worker_cfg.model, clamped))
```

Replace it with (note: `_one` now takes `(idx, task)`, builds a per-worker emit closure, and emits lifecycle events):

```python
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
                # 6-key shape documented in the spec (worker/task/state/detail/
                # usage/n_recorded). `partial` overrides the defaults.
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
                        max_steps=cfg.chakla_max_steps,
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
```

Add this helper at module level (e.g. just above `_format`):

```python
def _terminal_detail(r: ChaklaResult) -> str:
    """A short one-liner for a finished worker's terminal event."""
    if r.n_recorded:
        return f"{r.n_recorded} recorded"
    first = r.text.strip().splitlines()[0] if r.text.strip() else ""
    return first[:80]
```

Note: `r.status` is `"done"|"timeout"|"error"` — exactly the terminal event states. For the timeout branch `r.usage` is a fresh empty `Usage` (the `ChaklaResult` default), so `"usage"` is non-`None` and sums to zero — consistent with `test_dispatch_terminal_events_carry_usage`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_subagent.py -k "dispatch_emits or terminal_events_carry or terminal_usage_sums or progress_none_is_safe or timeout_emits" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Full subagent suite + lint**

Run: `uv run pytest tests/test_subagent.py -q && uv run ruff check riftor/tools/subagent.py riftor/agent/subagent.py`
Expected: all tests pass, 0 lint errors.

- [ ] **Step 6: Typecheck + commit**

Run: `uv run pyright riftor/tools/subagent.py`
Expected: 0 errors

```bash
git add riftor/tools/subagent.py tests/test_subagent.py
git commit -m "feat(7b): dispatch emits queued/running/terminal progress events"
```

---

## Task 4: `FlockPane` widget

**Files:**
- Modify: `riftor/tui/widgets.py`
- Test: `tests/test_subagent.py`

A `DataTable` subclass with one create-or-update entry point keyed by worker index. The widget is UI-only; it reads event dicts and renders rows. Glyph colors use `palette(self.app)` like the other widgets.

- [ ] **Step 1: Write the failing test**

`DataTable` row-cell mutation needs a mounted app, so test the widget through a tiny Textual harness. Add to `tests/test_subagent.py`:

```python
def test_flockpane_creates_and_updates_rows():
    import asyncio as _asyncio
    from textual.app import App
    from riftor.tui.widgets import FlockPane

    class _Harness(App):
        def compose(self):
            yield FlockPane()

    async def _drive():
        app = _Harness()
        async with app.run_test() as pilot:
            pane = app.query_one(FlockPane)
            pane.update_worker({"worker": 0, "task": "nmap A", "state": "queued",
                                "detail": "", "usage": None, "n_recorded": 0})
            pane.update_worker({"worker": 1, "task": "httpx B", "state": "queued",
                                "detail": "", "usage": None, "n_recorded": 0})
            await pilot.pause()
            assert pane.row_count == 2
            # update worker 0 to running with detail
            pane.update_worker({"worker": 0, "task": "nmap A", "state": "running",
                                "detail": "running nmap…", "usage": None, "n_recorded": 0})
            await pilot.pause()
            assert pane.row_count == 2  # still 2 rows — updated, not appended
            assert pane.worker_indices == {0, 1}
            assert pane.worker_state(0) == "running"  # raw state tracked
            # the worker-0 row now reflects 'run' state
            row = pane.get_row("0")
            assert any("nmap A" in str(c) for c in row)
            assert any("run" in str(c) for c in row)

    _asyncio.run(_drive())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_flockpane_creates_and_updates_rows -v`
Expected: FAIL — `ImportError: cannot import name 'FlockPane' from 'riftor.tui.widgets'`

- [ ] **Step 3: Implement `FlockPane`**

**First, add `DataTable` to the textual import** — this MUST be done before the class is added, or the module fails to import with `NameError: name 'DataTable' is not defined`. In `riftor/tui/widgets.py`, the current import (line 8) is:

```python
from textual.widgets import ListItem, ListView, Static
```

Change it to:

```python
from textual.widgets import DataTable, ListItem, ListView, Static
```

Add the widget (place it after `StatusBar`, before `CommandDropdown`):

```python
#: state -> (glyph, short word) for the flock table
_FLOCK_STATE = {
    "queued": ("⋯", "queued"),
    "running": ("⟳", "run"),
    "detail": ("⟳", "run"),
    "done": ("✓", "done"),
    "timeout": ("✗", "t/o"),
    "error": ("✗", "err"),
}


def _fmt_tok(usage) -> str:
    """Format a Usage's total tokens like the dispatch result ('1.2k' / '850')."""
    if usage is None:
        return "—"
    n = usage.total_tokens
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class FlockPane(DataTable):
    """Live per-worker status table for an in-flight Chakla dispatch.

    One row per worker, keyed by the worker index (as a string). The sole entry
    point is :meth:`update_worker`, which creates the row on first sight of a
    worker index and updates it thereafter. UI-only: it renders event dicts.

    Per-worker state is tracked in ``self._state`` (index -> raw state string) so
    the app can count done/running without re-parsing rendered cells — exposed via
    the public ``worker_indices`` / ``worker_state`` accessors.
    """

    def __init__(self) -> None:
        super().__init__(zebra_stripes=False, cursor_type="none")
        self._state: dict[int, str] = {}
        self._cols: list = []

    def on_mount(self) -> None:
        self._cols = self.add_columns("#", "state", "task", "detail", "tok")

    @property
    def worker_indices(self) -> set[int]:
        """The set of worker indices that currently have a row."""
        return set(self._state)

    def worker_state(self, idx: int) -> str:
        """Raw state string for a worker (e.g. 'running', 'done'), or '' if unknown."""
        return self._state.get(idx, "")

    def update_worker(self, event: dict) -> None:
        idx = int(event["worker"])
        state = str(event.get("state", ""))
        glyph, word = _FLOCK_STATE.get(state, ("?", "?"))
        task = str(event.get("task", "")).replace("\n", " ").strip()[:48]
        detail = str(event.get("detail", "") or "")[:40] or "—"
        tok = _fmt_tok(event.get("usage"))
        state_cell = f"{glyph} {word}"
        key = str(idx)
        new_row = idx not in self._state
        self._state[idx] = state  # track raw state for counting
        if new_row:
            self.add_row(str(idx + 1), state_cell, task, detail, tok, key=key)
            return
        self.update_cell(key, self._cols[1], state_cell)
        self.update_cell(key, self._cols[2], task)
        self.update_cell(key, self._cols[3], detail)
        self.update_cell(key, self._cols[4], tok)
```

Note: `add_columns` is called in `on_mount` (the table must be mounted before columns are added) and returns the `ColumnKey`s we store in `self._cols` for `update_cell`. The test drives the widget inside `run_test()`, so `on_mount` has fired before `update_worker`. `self._state` maps worker index → raw state string (`"running"`, `"done"`, …) so the app counts states without re-parsing cells; `worker_indices` / `worker_state` are the public accessors (no private access from the app → no pyright complaint).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_flockpane_creates_and_updates_rows -v`
Expected: PASS

- [ ] **Step 5: Lint + typecheck + commit**

Run: `uv run ruff check riftor/tui/widgets.py && uv run pyright riftor/tui/widgets.py`
Expected: 0 errors

```bash
git add riftor/tui/widgets.py tests/test_subagent.py
git commit -m "feat(7b): add FlockPane DataTable widget"
```

---

## Task 5: App wiring — mount/update/clear the flock + sum worker usage

**Files:**
- Modify: `riftor/tui/app.py` (`__init__` ~172-181, `_run_tool` ~1233-1319, add new methods)
- Modify: `riftor/tui/themes/rift.tcss` (add `.flock-header` rule)
- Test: covered by `dev/smoke.py` in Task 7 (TUI behavior needs the real app)

The app owns a per-dispatch `self._flock` holder (header `Static` + `FlockPane`). `_on_chakla_progress` mounts it on the first event, updates the row, and sums terminal usage into `self.chakla_usage`. `_run_tool` clears it after the dispatch tool finishes.

- [ ] **Step 1: Add the flock holder + import**

In `riftor/tui/app.py`, ensure `FlockPane` is importable. The existing widgets import (line 39) is:

```python
from riftor.tui.widgets import STAGE_NAMES, Banner, CommandDropdown, StatusBar
```

Add `FlockPane` (alphabetical within the names):

```python
from riftor.tui.widgets import STAGE_NAMES, Banner, CommandDropdown, FlockPane, StatusBar
```

(`Static` and `Text` are already imported in `app.py` — `from textual.widgets import Input, Markdown, Static` at line 24 and `from rich.text import Text` at line 17 — so no extra imports are needed for the holder type or the header render.)

In `__init__`, after `self.chakla_usage = Usage()` (line 172), add the holder:

```python
        self.chakla_usage = Usage()
        self._flock: tuple[Static, FlockPane] | None = None  # (header, table) while a dispatch is live
```

- [ ] **Step 2: Add `_on_chakla_progress` and `_clear_flock` methods**

Add these methods near `_refresh_usage` (after line 280) in `app.py`:

```python
    def _on_chakla_progress(self, event: dict) -> None:
        """Render a live worker progress event. Runs on the UI task (the agent
        loop is @work(exclusive=True), async) so widget mutation is direct."""
        if self._flock is None:
            header = Static(classes="flock-header")
            table = FlockPane()
            self._flock = (header, table)
            # mount synchronously into the chat scroll; both are non-async mounts
            self.chat.mount(header)
            self.chat.mount(table)
            self._scroll_if_following()
        header, table = self._flock
        table.update_worker(event)
        if event.get("state") in ("done", "timeout", "error"):
            usage = event.get("usage")
            if usage is not None:
                self.chakla_usage.add(usage)
                self._refresh_usage()
        header.update(Text(self._flock_header_text(table), style=self._pal()["violet"]))

    def _flock_header_text(self, table: FlockPane) -> str:
        # Count from the widget's tracked raw state (public accessors), NOT by
        # re-parsing rendered cells — so a glyph/label change in _FLOCK_STATE
        # can't silently break the counts.
        indices = table.worker_indices
        done = sum(1 for i in indices if table.worker_state(i) in ("done", "timeout", "error"))
        run = sum(1 for i in indices if table.worker_state(i) in ("running", "detail"))
        return f"🦅 dispatch · {len(indices)} 🐦 · {done} done · {run} running"

    def _clear_flock(self) -> None:
        if self._flock is None:
            return
        header, table = self._flock
        self._flock = None
        try:
            header.remove()
            table.remove()
        except Exception:  # noqa: BLE001 — teardown must never crash the loop
            pass
```

- [ ] **Step 3: Wire `progress=` into the toolctx**

In `__init__`, update the `ToolContext(...)` construction (line 173) to pass the callback. Change:

```python
        self.toolctx = ToolContext(
            workdir=self.workdir,
            engagement=self.engagement,
            max_result_chars=config.max_result_chars,
            config=self.config,
            permissions=self.permissions,
            audit=self.audit,
            yolo=self.yolo,
        )
```

to add the `progress` arg as the last field:

```python
        self.toolctx = ToolContext(
            workdir=self.workdir,
            engagement=self.engagement,
            max_result_chars=config.max_result_chars,
            config=self.config,
            permissions=self.permissions,
            audit=self.audit,
            yolo=self.yolo,
            progress=self._on_chakla_progress,
        )
```

Because `self._flock` must exist before `_on_chakla_progress` can run, and `__init__` sets `self._flock` in Step 1 *before* this construction line — confirm ordering: `self.chakla_usage`/`self._flock` (Step 1) are set on lines 172-173a, and the `ToolContext` block follows. If your edit placed `self._flock` after the `ToolContext` block, move it above. The callback is only invoked later (during a dispatch), so strictly the attribute just needs to exist by then, but keep it above for clarity.

- [ ] **Step 4: Clear the flock after a dispatch in `_run_tool`**

In `_run_tool` (line ~1300-1319), the tool runs at:

```python
        try:
            result = await tool.execute(call.arguments, self.toolctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(f"error: {exc}", is_error=True)
```

Immediately after the `try/except` that produces `result` (before `result = result.truncated(...)`), add the teardown so the live table is removed regardless of success/error, then the aggregated text block renders as today:

```python
        if call.name == "dispatch_chakla":
            self._clear_flock()
```

(Place this right after the `except` block, on the line before `result = result.truncated(self.config.max_result_chars)`.)

- [ ] **Step 5: Add a CSS class for the header**

The app's CSS lives in `riftor/tui/themes/rift.tcss` (referenced via `CSS_PATH = "themes/rift.tcss"` at `app.py:137`), not inline in `app.py`. The chat classes `.tool` and `.tool-result` are defined there (lines 44-54). Add a `.flock-header` rule mirroring `.tool` so the header indents consistently. After the `.tool { … }` block (line 47), add:

```css
.flock-header {
    margin: 1 0 0 0;
    padding: 0 2;
    color: $violet;
}
```

(`$violet` is a defined theme variable, used throughout the palette. The `FlockPane` `DataTable` itself styles via Textual's built-in `DataTable` CSS — no extra rule needed for the table.)

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check riftor/tui/app.py && uv run pyright riftor/tui/app.py`
Expected: 0 errors. The app reads worker state only through the public `worker_indices` / `worker_state` accessors on `FlockPane` (added in Task 4), so there is no private-attribute access for pyright to flag.

- [ ] **Step 7: Commit**

```bash
git add riftor/tui/app.py riftor/tui/themes/rift.tcss
git commit -m "feat(7b): wire flock pane + worker usage into the TUI"
```

---

## Task 6: Headless stderr progress

**Files:**
- Modify: `riftor/headless.py:49-68`
- Test: `tests/test_subagent.py`

Set `toolctx.progress` to a stderr printer that prints one line per terminal worker event (ignoring queued/running/detail to avoid flooding).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_subagent.py`:

```python
def test_headless_progress_printer_writes_terminal_events(capsys):
    # Build the headless progress callback in isolation and confirm it prints
    # only terminal events, to stderr.
    from riftor.headless import _make_progress_printer

    printer = _make_progress_printer(total=3)
    printer({"worker": 0, "task": "nmap A", "state": "queued", "usage": None})
    printer({"worker": 0, "task": "nmap A", "state": "running", "usage": None})
    printer({"worker": 0, "task": "nmap A", "state": "detail",
             "detail": "running nmap…", "usage": None})
    from riftor.agent.provider import Usage
    printer({"worker": 0, "task": "nmap A", "state": "done",
             "detail": "4 services", "usage": Usage(completion_tokens=900), "n_recorded": 4})
    printer({"worker": 2, "task": "subfinder", "state": "timeout",
             "detail": "timed out", "usage": Usage()})
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout
    assert "[1/3]" in captured.err and "nmap A" in captured.err and "done" in captured.err
    assert "[3/3]" in captured.err and "timeout" in captured.err
    # queued/running/detail produced no lines
    assert "running nmap" not in captured.err
    assert captured.err.count("🐦") == 2  # only the two terminal events
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_headless_progress_printer_writes_terminal_events -v`
Expected: FAIL — `ImportError: cannot import name '_make_progress_printer' from 'riftor.headless'`

- [ ] **Step 3a: Add the printer factory**

`total` is the dispatch's worker count, used only for the `[i/N]` label. Headless wires the callback once at startup, before any dispatch parses its task list, so the count is unknown there — `total=0` is the "unknown" sentinel and the label drops the `/N` (showing just `[i]`). The unit test passes an explicit `total=3`, so it gets `[1/3]`. One conditional handles both. Add this factory to `riftor/headless.py` near the top, after the imports:

```python
def _make_progress_printer(total: int = 0):
    """Return a progress callback that prints one stderr line per terminal worker
    event. Non-terminal states (queued/running/detail) are ignored so stdout's
    sibling stream isn't flooded. ``total`` is the worker count for the ``[i/N]``
    label; 0 means unknown (label shows just ``[i]``)."""

    def _printer(event: dict) -> None:
        state = event.get("state")
        if state not in ("done", "timeout", "error"):
            return
        idx = int(event.get("worker", 0)) + 1
        task = str(event.get("task", "")).replace("\n", " ").strip()[:48]
        detail = str(event.get("detail", "") or "").strip()
        usage = event.get("usage")
        tok = ""
        if usage is not None and usage.total_tokens:
            n = usage.total_tokens
            tok = f", {n / 1000:.1f}k tok" if n >= 1000 else f", {n} tok"
        suffix = f" — {state}" + (f" ({detail}{tok})" if (detail or tok) else "")
        label = f"[{idx}/{total}]" if total else f"[{idx}]"
        print(f"  🐦 {label} {task}{suffix}", file=sys.stderr)

    return _printer
```

- [ ] **Step 3b: Wire the printer into the headless toolctx**

In `_run` (around line 60), the current `ToolContext(...)` construction has no `progress`. Add it as the last field (`total=0` — unknown across dispatches):

```python
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
```

The unit test from Step 1 calls `_make_progress_printer(total=3)` explicitly, so its assertions on `[1/3]` / `[3/3]` hold; the headless wiring uses the `total=0` default and prints `[1]` / `[3]`. Both paths use the same factory.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_headless_progress_printer_writes_terminal_events -v`
Expected: PASS

- [ ] **Step 5: Lint + typecheck + commit**

Run: `uv run ruff check riftor/headless.py && uv run pyright riftor/headless.py`
Expected: 0 errors

```bash
git add riftor/headless.py tests/test_subagent.py
git commit -m "feat(7b): headless stderr progress for worker dispatch"
```

---

## Task 7: Smoke test — flock pane mounts, updates, clears

**Files:**
- Modify: `dev/smoke.py`

Drive a real dispatch through the live Textual app headlessly and assert the pane lifecycle + status-bar usage, offline.

- [ ] **Step 1: Add a dispatch-driven flock assertion to `dev/smoke.py`**

In `dev/smoke.py`'s `main()`, after the existing scope-enforcement block (after line ~94, before the `/theme` block), add:

```python
        # Phase 7b: a dispatch mounts the live flock pane, updates it, then clears
        # it; the 🐦 status segment reflects summed worker usage. Offline via demo.
        import os
        from riftor.tui.widgets import FlockPane

        os.environ["RIFTOR_DEMO_RESPONSE"] = "worker reporting: recon complete"
        app.engagement.add_scope("10.0.0.0/24", "in")
        app.permissions.allow_for_session("dispatch_chakla")
        app.config.api_key = "smoke-key"  # blank worker model reuses main; needs creds
        app.config.chakla_model = ""
        # capture the pane during flight by hooking the progress callback
        seen_rows = {"max": 0}
        orig_progress = app._on_chakla_progress

        def _spy(event):
            orig_progress(event)
            if app._flock is not None:
                seen_rows["max"] = max(seen_rows["max"], app._flock[1].row_count)

        app.toolctx.progress = _spy
        from riftor.agent.provider import ToolCall

        await app._run_tool(
            ToolCall(id="d1", name="dispatch_chakla",
                     arguments={"tasks": ["recon 10.0.0.5", "recon 10.0.0.6"], "tools": []})
        )
        await pilot.pause()
        assert seen_rows["max"] >= 2, f"expected >=2 flock rows during flight, saw {seen_rows['max']}"
        assert app._flock is None, "flock pane should be cleared after the dispatch"
        assert app.status.chakla_tokens >= 0  # 🐦 usage segment fed (0 ok for demo)
        app.toolctx.progress = app._on_chakla_progress  # restore
```

Note: `app.permissions.allow_for_session("dispatch_chakla")` makes the dispatch run without an operator modal (smoke has no interactive operator). The two tasks are in scope (`10.0.0.0/24`), so no scope modal fires. The demo response yields plain text (no worker tool calls), so workers complete immediately with zero usage — enough to exercise mount → rows → clear.

- [ ] **Step 2: Run the smoke test**

Run: `uv run python dev/smoke.py`
Expected: prints `SMOKE OK` (and `TOOLS OK`, etc.). The new block runs without assertion errors.

- [ ] **Step 3: Commit**

```bash
git add dev/smoke.py
git commit -m "test(7b): smoke-test the live flock pane lifecycle"
```

---

## Task 8: Docs + roadmap

**Files:**
- Modify: `docs/configuration.md`, `todo.md`

- [ ] **Step 1: Document the behavior in `docs/configuration.md`**

Find the section that describes the subagent / Chakla worker behavior (search for `chakla` or `Chakla`). Add a short paragraph:

```markdown
### Live worker visibility

While a `dispatch_chakla` batch runs, the TUI shows a live "flock" table — one
row per worker (queued → running → done/timeout/error) with the worker's current
activity and token count. The table is removed when the dispatch finishes; the
aggregated text summary remains. Worker token/cost accrues in the status-bar 🐦
segment as each worker completes. In headless mode, one progress line per finished
worker is printed to stderr (the agent's answer stays on stdout).
```

- [ ] **Step 2: Tick the 7b checkboxes in `todo.md`**

Open `todo.md`, find the **Phase 7 → 7b** section, and check off the items this plan delivered: the progress channel, the flock pane, worker usage propagation, and headless progress. Leave any genuinely out-of-scope items (e.g. interactive worker cancel) unchecked.

- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md todo.md
git commit -m "docs(7b): document live worker visibility; tick roadmap"
```

---

## Task 9: Full CI gate

**Files:** none (verification only)

- [ ] **Step 1: Run all gates**

Run: `make check`
Expected: lint → typecheck → unit tests → smoke all pass. (Equivalent: `uv run ruff check riftor dev tests && uv run pyright riftor && uv run pytest && uv run python dev/smoke.py`.)

- [ ] **Step 2: If anything fails, fix and re-run**

Use superpowers:systematic-debugging for any failure. Do not mark complete with red gates.

- [ ] **Step 3: Final commit if fixes were needed**

```bash
git add -A
git commit -m "fix(7b): address CI gate findings"
```

---

## Notes for the implementing engineer

- **Offline always.** Every test sets `RIFTOR_DEMO_RESPONSE` so the provider yields canned text and never hits the network. The demo response contains *no tool calls*, so workers finish in one turn with no `detail`/tool execution — detail-event wiring is verified structurally (Task 2) and via the smoke path (Task 7), not by forcing a real worker tool call offline.
- **`pytest` is asyncio-auto.** Async tests need no decorator, but the existing subagent tests use `asyncio.run(...)` inside sync test functions — match that style for consistency.
- **The UI-loop invariant** (documented on `ToolContext.progress`) is why `_on_chakla_progress` mutates widgets directly. Do not add `call_from_thread` — it would be wrong on the current async worker.
- **Teardown safety:** `_clear_flock` swallows exceptions and `_run_tool` calls it for any `dispatch_chakla` result (success or error), so a failed dispatch never leaves a dangling pane.
- **Do not** fold worker `Usage` into `self.usage` (Baaj's own accumulator) — only into `self.chakla_usage`. This preserves the 7a "no double-counting" invariant.
