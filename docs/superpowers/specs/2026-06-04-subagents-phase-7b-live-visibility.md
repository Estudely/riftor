# Phase 7b — Live Worker Visibility (Baaj / Chakla)

**Date:** 2026-06-04
**Status:** Not started — handoff doc for later pickup
**Depends on:** Phase 7a (shipped in `v0.5.0`, fixed in `v0.5.1`). The `dispatch_chakla` tool, `run_chakla` worker loop, and config/status-bar wiring already exist.
**Prerequisite reading:** `docs/superpowers/specs/2026-06-04-subagents-baaj-chakla-design.md` (the 7a design) and the implementation plan `docs/superpowers/plans/2026-06-04-subagents-baaj-chakla.md`.

---

## Why this exists

In 7a (Approach A) the user explicitly asked for live visibility into what subagents are doing, but we deferred it so the core dispatch could ship first. From the original brainstorm:

> "i also would want to see what the subagents are doing right, so there needs to be a status for that. but that is something we can work on after the basics are done."

Today, dispatching workers shows a single spinner, then one aggregated result block appears when **all** workers finish. For a 5-worker recon sweep that can be a minute of silence. 7b makes the flock observable in real time: which sparrow is on which task, running / done / errored, findings recorded so far, and live token/cost.

This doc is self-contained: it explains the one hard problem (there is no tool→UI channel today), gives a concrete design for each sub-feature with current file:line anchors, lists the open decisions, and a test plan. Pick it up cold from here.

## Goals

1. **Live per-worker status pane** ("the flock") in the TUI: one row per Chakla showing task, state (queued → running → done/timeout/error), and a short live status (e.g. "running nmap…", "2 services recorded").
2. **Live worker token/cost** in the status bar — the `🐦` segment plumbing already exists (`widgets.py:119-125`) but stays at zero because nothing increments `app.chakla_usage`. Wire real per-dispatch usage into it.
3. **Headless equivalent** — periodic progress lines to stderr so non-TUI runs aren't silent during a long dispatch.

## Non-goals

- No change to the worker *gating/safety* model (7a's scope/deny/grant contract is settled).
- No persistence of worker transcripts (workers stay ephemeral, per 7a).
- No interactive control of running workers (pause/cancel individual sparrows) — out of scope; a future idea, not 7b.

---

## The core problem: there is no tool→UI progress channel today

This is the whole reason 7b is a real project and not a 20-line tweak.

In the current architecture a tool runs to completion and returns **one** `ToolResult`; the app renders it once. The flow (TUI, `riftor/tui/app.py`):

- `_run_tool` (line 1233) calls `await tool.execute(args, self.toolctx)` (line ~1310), then `_show_tool_result(result.content)` (line 1316) — a single render at the end.
- `Tool.execute(args, ctx) -> ToolResult` (`riftor/tools/base.py`) has **no callback, no event emitter, no streaming channel**. A tool cannot emit "I'm 2/5 done" mid-execution.
- `DispatchChaklaTool.execute` (`riftor/tools/subagent.py:118-139`) fans out with `asyncio.gather(*[_one(t) for t in tasks])` and only returns after the gather resolves. Individual `run_chakla` completions are invisible to the app until then.
- `ToolContext` (`riftor/tools/base.py`) carries `workdir/engagement/max_result_chars/config/permissions/audit/yolo` — **no UI handle**.

So to show live worker status we must add a **progress channel** from the dispatch tool back to the UI. This is the central design decision (Decision A below). Everything else hangs off it.

---

## Design

### Sub-feature 1 — the progress channel + flock pane (the hard part)

**Decision A (must pick one) — how the tool signals progress to the UI:**

- **Option A1 — an optional progress callback on `ToolContext`.** Add `progress: Callable[[dict], None] | None = None` to `ToolContext` (keep it optional, like the 7a additions, so all other tools and tests are unaffected). The TUI sets it to a method that updates the flock widget; headless sets it to a function that prints stderr lines; tests leave it `None`. `DispatchChaklaTool` and `run_chakla` call `ctx.progress(event)` at lifecycle points. **Recommended** — smallest surface, mirrors how 7a threaded optional fields, works for both TUI and headless with different callbacks. The callback must be invoked on the event loop the UI runs on; in Textual, marshal via `self.call_from_thread` is **not** needed (dispatch runs in the app's own async worker), but updating widgets must happen on the UI task — verify whether the dispatch runs under `@work` and use `self.app.call_later`/`post_message` if a thread boundary is crossed. (`_fetch_models_worker` in `config_screen.py:137` is the existing pattern for `@work(thread=True)` + `call_from_thread`.)

- **Option A2 — a Textual `Message` posted from the tool.** The dispatch posts custom `Message`s the app handles via `on_<message>`. Cleaner Textual idiom but couples the tool to Textual (breaks the "tools are UI-agnostic" rule in `CLAUDE.md`), and headless can't use it. Not recommended.

- **Option A3 — a shared event queue (`asyncio.Queue`) the tool writes and a UI task drains.** Decouples producer/consumer cleanly and is UI-agnostic (headless drains it differently). More moving parts (a consumer task started/stopped around the dispatch). Reasonable alternative if A1's callback-on-UI-task marshalling gets awkward.

**Recommendation: A1 (optional callback on ToolContext), with the callback responsible for thread/loop-safe widget updates.** It is the minimal, UI-agnostic, test-friendly choice and matches the 7a pattern.

**Event shape** (whatever A-option): a small dict/dataclass, e.g.
```python
{"worker": 0, "task": "nmap host A", "state": "running"|"done"|"timeout"|"error",
 "detail": "2 services recorded", "tokens": 1234, "cost": 0.001}
```
Emit at: dispatch start (one event per worker, state `queued`), worker start (`running`), optionally per worker tool-call (`detail` updates), worker finish (`done`/`timeout`/`error` with final tokens/cost).

**Where to emit in code:**
- In `DispatchChaklaTool.execute` (`subagent.py:118`), wrap `_one(task)` so it emits `queued`/`running`/terminal events with the worker index.
- Inside `run_chakla` (`agent/subagent.py:48`), to get *per-tool-call* detail ("running nmap…"), pass the progress callback down and emit after each `_run_chakla_tool` call (the loop is at `agent/subagent.py:71-84`). Optional refinement — start with worker-level events (running/done) and add per-tool detail later.

**The flock widget (TUI):**
- New widget in `riftor/tui/widgets.py`, e.g. `FlockPane(Static)` or a `DataTable`, one row per worker: `[idx] state-glyph task — detail (tokens/cost)`. Glyphs: `⋯` queued, `⟳` running, `✓` done, `✗` timeout/error. Reuse the palette pattern (`palette(self.app)`, like `StatusBar.refresh_bar`).
- Mount/show it only while a dispatch is in flight (mount on first event, remove/clear on dispatch completion), or keep a persistent collapsible region. Decision B below.
- The app method wired into `ctx.progress` updates the pane row for `event["worker"]` and triggers a refresh. Must run on the UI task.

**Decision B (pick): pane lifecycle** — (B1) ephemeral: appears during dispatch, disappears after; or (B2) persistent collapsible panel showing the last flock until the next dispatch. B1 is less clutter; B2 lets the operator review what just happened. Lean B1 for v1.

### Sub-feature 2 — live worker token/cost (small, mostly done)

The status-bar `🐦` segment already renders when `chakla_tokens` is non-zero:
- `widgets.py:41-42` — fields `chakla_tokens`/`chakla_cost`.
- `widgets.py:87-90` — `set_chakla_usage(tokens, cost)`.
- `widgets.py:119-125` — renders the `🐦 {tok} ${cost}` segment.
- `app.py:172` — `self.chakla_usage = Usage()` accumulator.
- `app.py:278` — `_refresh_usage` pushes `self.chakla_usage.total_tokens/cost` to the bar.

**What's missing:** nothing ever adds to `self.chakla_usage`. The dispatch tool returns text, not a `Usage`. Two ways to close it:

- **Option C1 (clean):** have `DispatchChaklaTool` expose the aggregated worker `Usage` to the app. The tool currently sums it locally in `_format` (`subagent.py:142-148`, `total = Usage(); total.add(r.usage)`). Surface that total — e.g. return it on the `ToolResult` (would need a new optional field on `ToolResult`), or stash it on `ToolContext` for the app to read after `execute` returns, or emit it as the final progress event (ties into Sub-feature 1's channel — the terminal events already carry per-worker tokens; the app sums them). **If 7b builds the progress channel, prefer summing from the progress events** — no new `ToolResult` field needed.
- **Option C2 (minimal, no channel):** add an optional `usage: Usage | None` field to `ToolResult`; `DispatchChaklaTool` sets it to the worker total; `_run_tool` (`app.py:~1316`) does `if result.usage: self.chakla_usage.add(result.usage); self._refresh_usage()`. This delivers live-ish cost (updates once per dispatch) **without** the full progress channel — a valid standalone improvement if you want cost-visibility before the flock pane.

**Recommendation:** if doing the whole of 7b, fold worker usage into the progress events (C1). If you want a quick partial win first, ship C2 alone.

### Sub-feature 3 — headless progress

Headless (`riftor/headless.py`) has no operator and prints to stdout/stderr. With the A1 callback, set `ctx.progress` to a function that writes a stderr line per terminal worker event, e.g. `  🐦 [2/5] nmap host A — done (2 services)`. Keep it on stderr (stdout is the agent's answer stream). Throttle if needed (one line per state change, not per token). This is a few lines once the channel exists.

---

## Open decisions to settle at pickup (summary)

- **A. Progress channel mechanism** — A1 callback (recommended) / A2 Textual Message / A3 asyncio.Queue.
- **B. Flock pane lifecycle** — B1 ephemeral (recommended) / B2 persistent collapsible.
- **C. Worker usage delivery** — C1 via progress events (recommended if building the channel) / C2 via a new `ToolResult.usage` field (standalone quick win).
- **Granularity** — worker-level events only (running/done) for v1, or per-tool-call detail ("running nmap…")? Start coarse, refine.
- **Thread/loop safety** — confirm whether `DispatchChaklaTool.execute` runs on the app's UI task or a worker thread; choose the right marshalling (`post_message` / `call_from_thread` / direct) for widget updates. Check how `_agent`/`_run_tool` are invoked (look for `@work` decorators in `app.py`).

## Files this will touch

- `riftor/tools/base.py` — add optional `progress` to `ToolContext` (Decision A1), and/or optional `usage` to `ToolResult` (Decision C2).
- `riftor/tools/subagent.py` — emit progress events around `_one`/`gather` (`subagent.py:118-139`); surface worker `Usage` (C1/C2).
- `riftor/agent/subagent.py` — thread the callback into `run_chakla` for per-tool detail (`agent/subagent.py:48-84`); optional.
- `riftor/tui/widgets.py` — new `FlockPane` widget; the `🐦` usage segment already exists.
- `riftor/tui/app.py` — wire `ctx.progress` to a flock-updating method; mount/clear the pane; sum worker usage into `self.chakla_usage`; call `_refresh_usage`. Construction site for `self.toolctx` is `app.py:173`.
- `riftor/headless.py` — set `ctx.progress` to a stderr printer (`headless.py` toolctx construction ~line 58-66).
- `dev/smoke.py` — extend to drive a dispatch and assert the flock pane mounts / progress fires offline.
- `docs/configuration.md` — document any new behavior if user-visible.

## Test plan (all offline via `RIFTOR_DEMO_RESPONSE`)

- **Progress events fire in order:** dispatch 3 tasks with a fake `progress` callback collecting events; assert each worker emits `running` then a terminal state, and counts match. (Pure logic, no UI.)
- **Worker usage sums:** after a dispatch, assert the accumulated worker tokens/cost equals the sum of per-worker `Usage` (mirror `_format`'s total at `subagent.py:142-148`).
- **`ToolContext.progress is None` is safe:** existing tools/tests with a bare `ToolContext` must be unaffected (the dispatch must no-op the callback when `None`). Mirror the 7a "optional field" tests.
- **Headless prints progress to stderr, answer to stdout:** capture both, assert separation.
- **TUI flock pane mounts and updates:** extend `dev/smoke.py` (it drives the real Textual app headlessly) to run a dispatch and assert the pane appears and shows the expected rows; tear down cleanly.
- **Regression:** the existing 7a subagent suite (`tests/test_subagent.py`) must still pass unchanged — the progress channel is additive.

## Effort & sequencing suggestion

Roughly three increments, each independently shippable:
1. **C2 alone** (worker cost in the status bar) — tiny, no channel. Optional quick win.
2. **A1 + Sub-feature 1** (the flock pane) — the bulk of the work; the progress channel is the foundation.
3. **Sub-feature 3** (headless stderr) — a few lines once the channel exists.

Use the brainstorming → writing-plans → subagent-driven-development flow as 7a did. The 7a plan is a good template for granularity (TDD, fresh-subagent-per-task, two-stage review).

## Roadmap pointer

Tracked in `todo.md` under **Phase 7 → 7b — live worker visibility (Approach B, follow-up)**. Update those checkboxes as items land. Note that the `🐦` status-bar segment item there is already wired (segment renders); only the *usage propagation* into it remains (Sub-feature 2 / Decision C).
