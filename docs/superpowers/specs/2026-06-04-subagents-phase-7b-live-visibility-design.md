# Phase 7b — Live Worker Visibility (Baaj / Chakla) — Design

**Date:** 2026-06-04
**Status:** Approved — ready for implementation planning
**Scope:** Full 7b — the tool→UI progress channel, the live "flock" pane (TUI), live worker token/cost in the status bar, and headless stderr progress.
**Depends on:** Phase 7a (shipped in `v0.5.0`, fixed in `v0.5.1`). `DispatchChaklaTool`, the `run_chakla` worker loop, the `🐦` status-bar segment, and config/terminology wiring already exist.
**Supersedes the open decisions in:** `docs/superpowers/specs/2026-06-04-subagents-phase-7b-live-visibility.md` (the handoff doc). That doc framed the problem and listed decisions A/B/C; this doc settles them.
**Prerequisite reading:** `docs/superpowers/specs/2026-06-04-subagents-baaj-chakla-design.md` (7a design).

---

## Summary

Today a `dispatch_chakla` call shows one spinner, then a single aggregated result block when **all** workers finish — up to a minute of silence for a 5-worker recon sweep. 7b makes the flock observable in real time: a live per-worker table (queued → running → done/timeout/error, with per-tool-call detail and live tokens), worker token/cost ticking in the status bar, and stderr progress lines in headless mode.

The whole feature hangs off **one new optional field** — a `progress` callback on `ToolContext`. The dispatch tool and worker loop emit small event dicts through it; the TUI renders them into an ephemeral `DataTable`; headless prints them to stderr; tests leave the callback `None` so every existing tool and test is unaffected. This mirrors exactly how 7a added optional `ToolContext` fields.

## Settled decisions

| Decision | Choice |
|---|---|
| **A. Progress channel mechanism** | **A1** — optional `progress` callback on `ToolContext`. |
| **B. Flock pane lifecycle** | **B1** — ephemeral: live table during dispatch, removed on completion; the existing aggregated **text** `ToolResult` block remains as the permanent transcript record. |
| **C. Worker usage delivery** | **C1** — sum per-worker usage carried on the terminal progress events into `app.chakla_usage`; no new `ToolResult` field. |
| **Flock layout** | Aligned table (`DataTable`): columns `# · state · task · detail · tok`, with a header `Static` line above it showing live counts. Glyphs `⋯` queued · `⟳` running · `✓` done · `✗` timeout/error. |
| **Granularity** | Per-tool-call detail — the progress callback threads into `run_chakla`'s tool loop so detail shows "running nmap…", "recording service…" live. |

Rejected: **A2** (Textual `Message` from the tool — couples the tool to Textual, breaks the UI-agnostic rule in `CLAUDE.md`, unusable from headless) and **A3** (shared `asyncio.Queue` — adds a consumer task for no benefit, since A1's callback is already loop-safe; see the invariant below).

## Goals

1. Live per-worker status table ("the flock") in the TUI during a dispatch.
2. Live worker token/cost in the status-bar `🐦` segment (the render already exists; only the accumulation is missing).
3. Headless equivalent — one stderr line per terminal worker event so non-TUI runs aren't silent.

## Non-goals

- No change to the worker gating/safety model (7a's scope/deny/grant contract is settled).
- No persistence of worker transcripts (workers stay ephemeral).
- No interactive control of running workers (pause/cancel individual sparrows).
- No persistent flock panel for post-hoc review (B1, not B2).

---

## The core invariant (why A1 is safe)

`_agent` is decorated `@work(exclusive=True)` in `riftor/tui/app.py:1113` — an **async** Textual worker that runs on the app's own event loop, **not** `@work(thread=True)`. Therefore `tool.execute(...)` (and any `ctx.progress(...)` callback it invokes) runs on the UI task. The callback can mutate widgets (`DataTable`, `StatusBar`) **directly**, with no `call_from_thread` / `post_message` marshalling.

**This is a load-bearing invariant.** The design states it explicitly so a future change that moves the agent loop onto a thread (`@work(thread=True)`) is recognized as a breaking change for the progress channel — at which point the callback would need to marshal via `self.call_from_thread` (the pattern already used by `_fetch_models_worker` in `config_screen.py`). Until then, direct mutation is correct.

---

## Design

### Sub-feature 1 — the progress channel + flock pane

#### The channel: one optional field on `ToolContext` (`riftor/tools/base.py`)

```python
from typing import Callable  # added to imports
# in ToolContext, alongside the 7a optional fields:
progress: "Callable[[dict], None] | None" = None
```

Optional, defaults `None`. Every existing tool ignores it; the bare-`ToolContext` test fixture and all 7a tests are unaffected. Only `DispatchChaklaTool` and `run_chakla` ever call it.

#### The event shape

A plain dict (no new dataclass needed):

```python
{
  "worker": 0,                      # stable 0-based worker index (row key)
  "task": "nmap top-1000 on 10.0.0.5",
  "state": "queued"|"running"|"detail"|"done"|"timeout"|"error",
  "detail": "running nmap…",        # live activity, or a final one-liner on terminal states
  "usage": Usage(...) | None,       # the worker's cumulative Usage; present on terminal states
  "n_recorded": 4,                  # findings/services committed; present on terminal states
}
```

- `usage` carries the worker's `Usage` object (`riftor/agent/provider.py:48`) so the app can `chakla_usage.add(event["usage"])` directly — reusing `Usage.add`, no field-by-field math, no new `ToolResult` field. For the table's `tok` cell, the renderer reads `event["usage"].total_tokens`.
- Non-terminal events may carry a partial `usage` (the worker's running total) for the live `tok` cell, or `None`; the app only *accumulates into `chakla_usage`* on terminal states to avoid double counting.

#### Emission points

**`DispatchChaklaTool.execute` (`riftor/tools/subagent.py`)** owns the worker indices:

- Build a tiny local emit helper that no-ops when `ctx.progress is None`:
  ```python
  emit = ctx.progress or (lambda _e: None)
  ```
- Before `asyncio.gather` (currently line 138): emit one `queued` event per `(idx, task)` so all rows appear instantly.
- Wrap `_one(task, idx)` (currently `_one(task)`, line 118): emit `running` when the worker starts; emit the terminal `done`/`timeout`/`error` (carrying the worker's final `Usage` and `n_recorded`) when it resolves. The existing `asyncio.TimeoutError` branch emits `timeout`.
- Pass a per-worker progress closure (with `worker`/`task` bound) down into `run_chakla` so the worker only supplies `state`/`detail`/`usage`.

**`run_chakla` (`riftor/agent/subagent.py`)** gets one new optional param (the worker index/task are bound into the closure by the dispatch tool, so `run_chakla` needs neither):

```python
async def run_chakla(task, *, worker_provider, toolctx, permissions, audit,
                     max_steps, yolo, db_lock, grant,
                     progress: "Callable[[dict], None] | None" = None) -> ChaklaResult:
```

- After each tool call in the loop (currently lines 82–86), emit a `detail` event: a short label derived from the call (tool name + a brief arg hint, e.g. `"running nmap…"`), and a `"… recorded"`-style detail when a record/import tool returns. Carry the worker's running `result.usage` for the live `tok` cell.
- All emission guarded `if progress is not None`. With `progress=None` the loop is byte-for-byte today's behavior.
- The terminal event itself is emitted by the **dispatch tool's** `_one` wrapper (which knows the final status incl. timeout), not by `run_chakla` — `run_chakla` only emits `running`/`detail`. This keeps the timeout path (which lives in `_one`, not `run_chakla`) the single source of the terminal event.

#### The flock widget (`riftor/tui/widgets.py`)

A new `FlockPane(DataTable)`:

- Columns: `#`, `state`, `task`, `detail`, `tok`. Row key = worker index.
- `update_worker(event: dict) -> None` — the sole entry point. If `event["worker"]` has no row yet, add one (keyed by the index, with the `task` from the event); otherwise update the existing row. It sets the state glyph (`⋯`/`⟳`/`✓`/`✗` + word: `queued`/`run`/`done`/`t/o`), the `detail` cell, and the `tok` cell (`event["usage"].total_tokens` formatted `1.2k`-style, reusing the existing `_format` token-formatting idiom; `—` when no usage yet). Making `update_worker` create-or-update means the `queued` events naturally seed the rows — no separate `start`/roster method.
- Glyph colors via `palette(self.app)` (the pattern `StatusBar.refresh_bar` and `Banner.render` already use) so it themes across all 7 themes.

The header line is a **separate `Static`** mounted just above the table (e.g. `🦅 dispatch · 5 🐦 (model) · 2 done · 1 running`), so the `DataTable` stays purely tabular and the header can show live counts. The pane is therefore a small 2-widget group: header `Static` + `FlockPane`.

#### App wiring (`riftor/tui/app.py`)

- New per-dispatch handle `self._flock` (a small holder exposing `.header` (the `Static`) and `.table` (the `FlockPane`), or `None`), initialized `None` in `__init__`.
- New method `_on_chakla_progress(self, event: dict) -> None`:
  1. If `self._flock is None`, mount the header `Static` + `FlockPane` group into the chat scroll (via the existing `_mount`) and create the empty pane. Rows are then created lazily by `update_worker`: because the dispatch emits all `queued` events first (before any `running`), each worker's row is added by its `queued` event — no separate roster is passed. `update_worker` creates the row if the worker index is not yet present, else updates it.
  2. `self._flock.table.update_worker(event)`.
  3. On terminal state (`done`/`timeout`/`error`): `self.chakla_usage.add(event["usage"])` then `self._refresh_usage()` — the `🐦` segment (`widgets.py:119`) updates live via the existing `set_chakla_usage` path (`app.py:278`).
  4. Refresh the header `Static` with updated counts.
- Wire it at the `toolctx` construction site (`app.py:173`): add `progress=self._on_chakla_progress`.
- **Teardown (B1):** after `dispatch_chakla`'s `execute` returns in `_run_tool` (around line 1302–1316), if `self._flock is not None`, remove the header+table group and set `self._flock = None`. Then the existing `_show_tool_result(result.content)` renders the aggregated **text** block exactly as today — the permanent record. Teardown happens in a `finally`-style guard so an errored dispatch still clears the pane.

### Sub-feature 2 — live worker token/cost (status bar)

No widget change — the `🐦` segment already renders when `chakla_tokens` is non-zero (`widgets.py:119–125`) and `_refresh_usage` already pushes `chakla_usage` (`app.py:278`). The only gap is that nothing accumulates into `self.chakla_usage`. Closed by C1: step 3 of `_on_chakla_progress` sums each worker's terminal `Usage`. Updates as each worker finishes (not just once at the end).

### Sub-feature 3 — headless progress (`riftor/headless.py`)

At the `toolctx` construction (`headless.py:60`), set `progress=` to a small stderr printer. It prints **one line per terminal worker event** (ignores `queued`/`running`/`detail` to avoid flooding stdout's sibling stream), e.g.:

```
  🐦 [2/5] httpx host A — done (2 services, 0.9k tok)
  🐦 [5/5] subfinder ex.com — timed out
```

Written to **stderr** (stdout stays the agent's answer stream), matching the existing `  ⛏ {tool}` convention at `headless.py:113`. The `[i/N]` index/count is derived from the event's `worker` plus the known task count. A few lines, gated behind the same channel.

---

## Files this will touch

- `riftor/tools/base.py` — add optional `progress: Callable[[dict], None] | None = None` to `ToolContext`; import `Callable`.
- `riftor/tools/subagent.py` — `emit` helper; `queued` events before `gather`; `_one` carries the worker index and emits `running` + terminal events; pass a per-worker progress closure into `run_chakla`.
- `riftor/agent/subagent.py` — `run_chakla` gains an optional `progress` param; emit `detail` events after each tool call (guarded on `None`).
- `riftor/tui/widgets.py` — new `FlockPane(DataTable)` with `start` / `update_worker`.
- `riftor/tui/app.py` — `self._flock` handle; `_on_chakla_progress`; wire `progress=` at `app.py:173`; mount/clear the pane in `_run_tool`; usage accumulation already plumbed via `_refresh_usage`.
- `riftor/headless.py` — set `progress=` to a stderr printer at the toolctx construction.
- `dev/smoke.py` — extend to drive a dispatch and assert the pane mounts/updates/removes and the `🐦` segment reflects usage, offline.
- `tests/test_subagent.py` — new offline cases (below).
- `docs/configuration.md` — note the live flock pane + headless progress if user-visible. `todo.md` — tick the 7b checkboxes.

## Test plan (all offline via `RIFTOR_DEMO_RESPONSE`)

1. **Events fire in order** — dispatch 3 tasks with a fake `progress` callback collecting events; assert each worker index `0..2` emits `running` then exactly one terminal state, and counts match. Pure logic, no UI.
2. **Per-tool-call detail** — a worker that invokes a tool emits at least one `detail` event between `running` and its terminal event.
3. **Usage sums from events** — after a dispatch, the accumulated worker `Usage` (summed from terminal events the way `_on_chakla_progress` does) equals the sum of per-worker `Usage` (mirror `_format`'s total at `subagent.py:142–148`).
4. **`progress is None` is safe** — a dispatch built from a bare `ToolContext` (no `progress`) runs identically and returns the same aggregated text; the existing 7a `tests/test_subagent.py` passes unchanged.
5. **Headless separation** — capture stdout + stderr around a dispatch; assert worker progress lines (`🐦 [i/N] …`) land on stderr and the agent answer on stdout.
6. **Smoke: FlockPane mounts / updates / removes** — extend `dev/smoke.py` (it drives the real Textual app headlessly) to run a dispatch with a canned response; assert the pane appears with N rows during flight, the `🐦` status segment is non-zero, and the pane is removed after completion; tear down cleanly.
7. **Regression** — full `make check` (lint → typecheck → test → smoke) green on Python 3.11 + 3.12.

## Effort & sequencing

Three increments, each independently green-able under `make check`:

1. **Channel + producer emission** (`base.py`, `subagent.py` ×2) — the foundation. Testable purely with a fake callback (tests 1–4) before any UI exists.
2. **Flock pane + app wiring + usage** (`widgets.py`, `app.py`) — the TUI surface; smoke test (6) and usage test (3).
3. **Headless stderr** (`headless.py`) — a few lines; test (5).

Use the writing-plans → subagent-driven-development flow, with TDD per task (the 7a plan is a good granularity template).

## Top risks (mitigations baked in)

1. **Loop-safety regression** — the UI-task invariant is documented; a future `thread=True` would need `call_from_thread`. Stated explicitly above.
2. **Usage double-counting** — accumulate into `chakla_usage` only on terminal events, never on `running`/`detail`. Worker usage stays in its own accumulator, never folded into Baaj's `turn.usage` (7a invariant preserved).
3. **`ToolContext` change touching every tool/test** — the new field is optional and `None`-guarded everywhere it's read; mirrors 7a.
4. **Pane left mounted after an errored dispatch** — teardown runs in a `finally`-style guard in `_run_tool`.
5. **stderr flooding in headless** — only terminal events print; `detail`/`running`/`queued` are dropped on the headless callback.
6. **Detail-event volume in the TUI** — `update_worker` mutates an existing row in place (keyed by index); it does not append, so per-tool-call detail cannot grow the transcript unboundedly.
```
