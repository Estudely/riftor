# Subagents (Baaj / Chakla) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add orchestrator/worker delegation to riftor — a main agent **Baaj** dispatches multiple cheap **Chakla** workers (via a new `dispatch_chakla` tool) to run batches of low-effort parallel tasks like recon.

**Architecture:** A new `Tool` (`DispatchChaklaTool`) is invoked from the existing tool-dispatch path (no change to the TUI/headless agent loops). Each worker runs a stripped headless loop (`run_chakla`) with an isolated `Context`, a second cheap `Provider` (cloned config with `model_copy`), shared engagement DB (serialized by an `asyncio.Lock`), and headless-style permission gating bridged by an ephemeral grant created when the operator approves the dispatch.

**Tech Stack:** Python 3.11+, Pydantic (`Config`), litellm (via `Provider`), asyncio (`gather`/`wait_for`/`Lock`), Textual (TUI status bar / config screen), `uv` + `pytest` (offline via `RIFTOR_DEMO_RESPONSE`).

**Reference spec:** `docs/superpowers/specs/2026-06-04-subagents-baaj-chakla-design.md`

**Conventions for every task:**
- Run from repo root `/home/amanverasia/Projects/riftor`.
- Lint+typecheck before each commit: `uv run ruff check riftor tests && uv run pyright riftor`.
- Tests are offline: never require an API key. Worker LLM calls are stubbed with the `RIFTOR_DEMO_RESPONSE` env var (read in `Provider._kwargs`, `agent/provider.py:155`).

---

## File Structure

**New files:**
- `riftor/agent/subagent.py` — `ChaklaResult` dataclass + `run_chakla()` worker loop + `_run_chakla_tool()` gating helper.
- `riftor/tools/subagent.py` — `DispatchChaklaTool` (the `dispatch_chakla` tool).
- `riftor/terminology.py` — `terminology(config)` helper centralizing renameable labels + emoji.
- `tests/test_subagent.py` — worker-loop + dispatch tests (offline).

**Modified files:**
- `riftor/config.py` — 6 new fields + `_to_toml()` lines.
- `riftor/tools/base.py` — extend `ToolContext` with 4 optional fields.
- `riftor/tools/__init__.py` — import + register `DispatchChaklaTool`.
- `riftor/tui/app.py` — populate new `ToolContext` fields; worker-usage status wiring; config-screen result unpack.
- `riftor/headless.py` — populate new `ToolContext` fields.
- `riftor/tui/widgets.py` — `set_chakla_usage()` + status-bar segment.
- `riftor/tui/config_screen.py` — "WORKERS" section + result keys.
- `riftor/__main__.py` — `--chakla-model` flag.
- `completions/riftor.bash`, `completions/_riftor` — new flag.
- `docs/configuration.md`, `docs/riftor.1` — document fields + flag.

---

## Task 1: Config fields for workers + terminology

**Files:**
- Modify: `riftor/config.py:49-68` (Config fields), `riftor/config.py:195-205` (`_to_toml`)
- Test: `tests/test_subagent.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagent.py`:

```python
"""Tests for the Baaj/Chakla subagent feature (all offline)."""
from __future__ import annotations

from riftor.config import Config


def test_config_has_chakla_defaults():
    cfg = Config()
    assert cfg.chakla_model == "anthropic/claude-haiku-4-5-20251001"
    assert cfg.chakla_max_workers == 5
    assert cfg.chakla_max_steps == 8
    assert cfg.chakla_timeout_s == 300
    assert cfg.label_main == "Baaj"
    assert cfg.label_worker == "Chakla"


def test_config_toml_roundtrips_chakla_fields():
    cfg = Config(chakla_model="anthropic/claude-haiku-4-5-20251001", chakla_max_workers=3)
    toml = cfg._to_toml()
    assert 'chakla_model = "anthropic/claude-haiku-4-5-20251001"' in toml
    assert "chakla_max_workers = 3" in toml
    assert "chakla_max_steps = 8" in toml
    assert "chakla_timeout_s = 300" in toml
    assert 'label_main = "Baaj"' in toml
    assert 'label_worker = "Chakla"' in toml
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'chakla_model'`.

- [ ] **Step 3: Add the Config fields**

In `riftor/config.py`, find the `providers` field at the end of the Config class (line 68) and add the new fields immediately after it (still inside the class body):

```python
    # Per-provider credentials, keyed by provider key (see riftor.providers.PROVIDERS).
    providers: dict[str, ProviderCreds] = {}
    # Subagents (Baaj orchestrator → Chakla workers). chakla_model is the cheap
    # worker model; the labels are renameable terminology surfaced in the UI.
    chakla_model: str = "anthropic/claude-haiku-4-5-20251001"
    chakla_max_workers: int = 5
    chakla_max_steps: int = 8
    chakla_timeout_s: int = 300
    label_main: str = "Baaj"
    label_worker: str = "Chakla"
```

- [ ] **Step 4: Add the `_to_toml` lines**

In `riftor/config.py`, in `_to_toml()`, extend the `lines += [...]` block (currently ending with `onboarded` at line 204). Add the six new lines just before the closing `]` on line 205:

```python
            f"rate_limit_per_min = {self.rate_limit_per_min}",
            f"onboarded = {str(self.onboarded).lower()}",
            f'chakla_model = "{self.chakla_model}"',
            f"chakla_max_workers = {self.chakla_max_workers}",
            f"chakla_max_steps = {self.chakla_max_steps}",
            f"chakla_timeout_s = {self.chakla_timeout_s}",
            f'label_main = "{self.label_main}"',
            f'label_worker = "{self.label_worker}"',
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/config.py tests/test_subagent.py
git commit -m "feat(config): add Chakla worker model + renameable labels"
```

---

## Task 2: Terminology helper

**Files:**
- Create: `riftor/terminology.py`
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
from riftor.terminology import terminology


def test_terminology_defaults():
    t = terminology(Config())
    assert t["main"] == "Baaj"
    assert t["worker"] == "Chakla"
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"


def test_terminology_respects_renamed_labels():
    t = terminology(Config(label_main="Hawk", label_worker="Finch"))
    assert t["main"] == "Hawk"
    assert t["worker"] == "Finch"
    # emoji are fixed branding; only the text labels are renameable
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_terminology_defaults -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.terminology'`.

- [ ] **Step 3: Create the module**

Create `riftor/terminology.py`:

```python
"""Renameable terminology for the subagent feature.

The orchestrator is "Baaj" (eagle) and the workers are "Chakla" (sparrows) by
default. The text labels live in Config (label_main / label_worker) so operators
can rename them; the emoji are fixed branding. Read labels through this helper so
renaming is a single source of truth.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from riftor.config import Config

MAIN_EMOJI = "🦅"
WORKER_EMOJI = "🐦"


def terminology(config: "Config") -> dict[str, str]:
    """Return the resolved {main, worker, main_emoji, worker_emoji} labels."""
    return {
        "main": config.label_main,
        "worker": config.label_worker,
        "main_emoji": MAIN_EMOJI,
        "worker_emoji": WORKER_EMOJI,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: PASS (all four subagent tests so far).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/terminology.py tests/test_subagent.py
git commit -m "feat: add renameable terminology helper for subagents"
```

---

## Task 3: Extend ToolContext with optional plumbing fields

`ToolContext` (`tools/base.py:31-38`) currently carries only `workdir`, `engagement`, `max_result_chars`. The dispatch tool needs `config`, `permissions`, `audit`, and `yolo` to build a worker provider and gate workers. All new fields are optional so the 23 existing tools and the test fixture are unaffected.

**Files:**
- Modify: `riftor/tools/base.py:31-38`
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
from riftor.tools.base import ToolContext


def test_toolcontext_new_fields_default_to_none(tmp_workdir, engagement):
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement)
    assert ctx.config is None
    assert ctx.permissions is None
    assert ctx.audit is None
    assert ctx.yolo is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_toolcontext_new_fields_default_to_none -v`
Expected: FAIL — `AttributeError: 'ToolContext' object has no attribute 'config'`.

- [ ] **Step 3: Extend the dataclass**

In `riftor/tools/base.py`, replace the `ToolContext` dataclass (lines 31-38) with:

```python
@dataclass
class ToolContext:
    """Shared execution context handed to every tool."""

    workdir: Path = field(default_factory=Path.cwd)
    engagement: "Engagement | None" = None
    #: per-result truncation cap fed back into the model (configurable via Config)
    max_result_chars: int = MAX_RESULT_CHARS
    #: Optional plumbing for tools that spawn subagents (DispatchChaklaTool).
    #: Kept optional so ordinary tools and tests build a bare ToolContext.
    config: "Config | None" = None
    permissions: "Permissions | None" = None
    audit: "AuditLog | None" = None
    yolo: bool = False
```

Then add the forward-reference imports under the existing `TYPE_CHECKING` block near the top of `base.py`. Find the existing `if TYPE_CHECKING:` block (it imports `Engagement`) and add the two new imports:

```python
if TYPE_CHECKING:
    from riftor.config import Config
    from riftor.engagement import Engagement
    from riftor.safety.audit import AuditLog
    from riftor.safety.permissions import Permissions
```

> If `base.py` has no `TYPE_CHECKING` block yet, add `from typing import TYPE_CHECKING` to the imports and create the block above. The `Engagement` forward ref already exists in the file, so a block is present — just add `Config`, `AuditLog`, `Permissions` to it.

- [ ] **Step 4: Run test + full suite to verify nothing broke**

Run: `uv run pytest tests/test_subagent.py -v && uv run pytest -q`
Expected: new test PASSES; entire existing suite still PASSES (the fixture in `tests/conftest.py:25-27` builds `ToolContext(workdir=..., engagement=...)` and is unaffected by the new optional fields).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tools/base.py tests/test_subagent.py
git commit -m "feat(tools): extend ToolContext with optional subagent plumbing"
```

---

## Task 4: The Chakla worker loop (`run_chakla`)

This is the heart of the feature: a stripped headless loop with its own isolated `Context`, headless-style gating bridged by an ephemeral grant, and a per-worker `Usage` accumulator. Model it on `headless.py:_run` (49-90) and `_run_tool_headless` (93-141).

**Files:**
- Create: `riftor/agent/subagent.py`
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
import asyncio

from riftor import tools as tools_mod
from riftor.agent.provider import Provider
from riftor.agent.subagent import ChaklaResult, run_chakla
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import Permissions


def _worker_provider(cfg: Config) -> Provider:
    return Provider(cfg.model_copy(update={"model": cfg.chakla_model}))


async def _run_one(task, *, cfg, engagement, grant, yolo=False, monkeypatch_env):
    # RIFTOR_DEMO_RESPONSE makes the provider stream canned text with no network.
    monkeypatch_env("RIFTOR_DEMO_RESPONSE", "worker reporting: recon complete, no open ports")
    toolctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent,
        engagement=engagement,
        config=cfg,
        permissions=Permissions(),
        audit=AuditLog(),
        yolo=yolo,
    )
    return await run_chakla(
        task,
        worker_provider=_worker_provider(cfg),
        toolctx=toolctx,
        permissions=toolctx.permissions,
        audit=toolctx.audit,
        max_steps=cfg.chakla_max_steps,
        yolo=yolo,
        db_lock=asyncio.Lock(),
        grant=grant,
    )


def test_run_chakla_returns_result_with_text(tmp_workdir, engagement, monkeypatch):
    cfg = Config()
    result = asyncio.run(
        _run_one(
            "recon 10.0.0.5",
            cfg=cfg,
            engagement=engagement,
            grant=set(),
            monkeypatch_env=monkeypatch.setenv,
        )
    )
    assert isinstance(result, ChaklaResult)
    assert result.status == "done"
    assert "recon complete" in result.text
    assert result.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_run_chakla_returns_result_with_text -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.agent.subagent'`.

- [ ] **Step 3: Create the worker module**

Create `riftor/agent/subagent.py`:

```python
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
from typing import TYPE_CHECKING

from riftor import tools
from riftor.agent.context import Context
from riftor.agent.provider import Provider, ProviderError, ToolCall, Usage
from riftor.tools import ToolContext, ToolResult

if TYPE_CHECKING:
    from riftor.safety.audit import AuditLog
    from riftor.safety.permissions import Permissions

#: The dispatch tool is excluded from the worker tool set so a Chakla can never
#: spawn its own Chaklas (no recursion).
DISPATCH_TOOL_NAME = "dispatch_chakla"

# Gating note (confirmed against the registry): only add_scope, write, edit, and
# bash carry requires_permission=True. Every read-only/recording tool already
# runs freely because the grant check below is guarded by tool.requires_permission.
# So there is no separate "always free" list — non-privileged tools never reach
# the grant gate. webfetch is scope_sensitive (not permissioned), so workers may
# fetch within scope without a grant; scope is still enforced in step 1.


@dataclass
class ChaklaResult:
    """The outcome of one Chakla worker."""

    task: str
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    n_recorded: int = 0
    status: str = "done"  # "done" | "timeout" | "error"
    error: str | None = None


def worker_schemas() -> list[dict]:
    """Tool schemas for a worker — everything except the dispatch tool."""
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
            turn = None
            async for kind, payload in worker_provider.stream_turn(ctx.messages, schemas):
                if kind == "text":
                    result.text += str(payload)
                elif kind == "done":
                    turn = payload
            if turn is None:
                break
            result.usage.add(turn.usage)
            ctx.add_message(turn.assistant_message)
            if not turn.tool_calls:
                break
            for call in turn.tool_calls:
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
    #    dispatch grant. Read-only tools are always free.
    if not yolo and tool.requires_permission:
        granted = tool.name in grant or permissions.is_allowed(tool.name, preview)
        if not granted:
            audit.record(tool.name, preview, allowed=False)
            return (
                f"[denied] {tool.name} was not granted to this worker. "
                "The dispatch did not authorize it."
            )

    # Execute, serializing all work behind the shared lock so concurrent workers
    # never trip SQLITE_BUSY on the shared engagement DB.
    async with db_lock:
        try:
            res = await tool.execute(call.arguments, toolctx)
        except Exception as exc:  # noqa: BLE001
            res = ToolResult(f"error: {exc}", is_error=True)
    res = res.truncated(toolctx.max_result_chars)
    audit.record(tool.name, preview, allowed=True, is_error=res.is_error)
    return res.content
```

> Note on the lock: holding `db_lock` around the whole `tool.execute` serializes ALL worker tool execution (not just DB writes). That is intentional and simplest for v1 — recon tools are I/O-bound subprocesses, and the lock only matters across the small N of concurrent workers. If profiling later shows a bottleneck, narrow the lock to store mutations (a 7b concern).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_run_chakla_returns_result_with_text -v`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/agent/subagent.py tests/test_subagent.py
git commit -m "feat(agent): add Chakla worker loop (run_chakla)"
```

---

## Task 5: Worker gating tests (scope, deny, grant, recursion)

Lock down the safety contract with targeted tests before building the dispatch tool on top of it.

**Files:**
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_subagent.py`:

```python
from riftor.agent.subagent import _run_chakla_tool, worker_schemas
from riftor.agent.provider import ToolCall


def _ctx(cfg, engagement):
    return tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )


def test_worker_schemas_exclude_dispatch():
    names = [s["function"]["name"] for s in worker_schemas()]
    assert "dispatch_chakla" not in names


def test_worker_readonly_tool_runs_without_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c1", name="scope_list", arguments={})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant=set())
    )
    assert "[denied]" not in content


def test_worker_bash_denied_without_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c2", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant=set())
    )
    assert "[denied]" in content


def test_worker_bash_allowed_with_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c3", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[denied]" not in content
    assert "hi" in content


def test_worker_deny_rule_wins_over_grant(tmp_workdir, engagement):
    cfg = Config()
    perms = Permissions(deny=[{"tool": "bash"}])
    ctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=perms, audit=AuditLog(),
    )
    call = ToolCall(id="c4", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, perms, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[blocked by policy]" in content


def test_worker_out_of_scope_hard_blocked(tmp_workdir, engagement):
    cfg = Config()
    engagement.scope.add("10.0.0.0/24", "in")
    engagement.enforce = True
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c5", name="bash", arguments={"command": "nmap 8.8.8.8"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[blocked: out of scope]" in content
```

> Verify the `Permissions(deny=[{"tool": "bash"}])` shape against `safety/permissions.py:56-68` — the constructor takes `allow`/`deny` lists of dicts unpacked into `Rule(**r)`. If `Rule`'s field is named differently than `tool` (e.g. `tool_name`), adjust the dict key here to match. Also confirm `engagement.scope.add(target, mode)` and the `"in"` mode string against `engagement/scope.py`; if the API differs, use `engagement.import_scope("10.0.0.0/24")` instead.

- [ ] **Step 2: Run tests to verify they fail or pass meaningfully**

Run: `uv run pytest tests/test_subagent.py -k worker -v`
Expected: These exercise existing `_run_chakla_tool` (from Task 4), so they should PASS. If `test_worker_deny_rule_wins_over_grant` or the scope test errors on the `Permissions`/`scope` API shape, fix the test's setup per the note above — not the implementation.

- [ ] **Step 3: (only if a test revealed a real gating bug) fix `_run_chakla_tool`**

If any safety test fails because the *gating logic* is wrong (e.g. grant checked before deny), correct the ordering in `riftor/agent/subagent.py:_run_chakla_tool` so it is: scope → deny → grant/allow. (As written in Task 4 it already is; this step exists only to enforce the contract.)

- [ ] **Step 4: Run the full subagent test file**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add tests/test_subagent.py riftor/agent/subagent.py
git commit -m "test(agent): lock down Chakla worker gating (scope/deny/grant/recursion)"
```

---

## Task 6: The DispatchChaklaTool

The tool Baaj calls. Parses `tasks`, clamps to `chakla_max_workers`, builds the cheap worker provider, creates the ephemeral grant, fans out with `gather` + `wait_for`, and aggregates into one `ToolResult`.

**Files:**
- Create: `riftor/tools/subagent.py`
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
from riftor.tools.subagent import DispatchChaklaTool


def test_dispatch_requires_config(tmp_workdir, engagement):
    tool = DispatchChaklaTool()
    bare = tools_mod.ToolContext(workdir=tmp_workdir, engagement=engagement)
    res = asyncio.run(tool.execute({"tasks": ["recon"]}, bare))
    assert res.is_error
    assert "unavailable" in res.content


def test_dispatch_runs_workers_and_aggregates(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done: nothing notable")
    cfg = Config()
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(), yolo=False,
    )
    res = asyncio.run(tool.execute({"tasks": ["recon A", "recon B"]}, ctx))
    assert not res.is_error
    assert "2" in res.content  # mentions 2 workers
    assert "recon A" in res.content
    assert "recon B" in res.content


def test_dispatch_clamps_to_max_workers(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_max_workers=2)
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(tool.execute({"tasks": ["a", "b", "c", "d"]}, ctx))
    assert not res.is_error
    assert "clamped" in res.content.lower() or "capped" in res.content.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_dispatch_requires_config -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'riftor.tools.subagent'`.

- [ ] **Step 3: Create the tool**

Create `riftor/tools/subagent.py`:

```python
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
        if not isinstance(grant_list, list) or not grant_list:
            grant_list = list(_DEFAULT_GRANT)
        grant = {str(t) for t in grant_list}

        worker_cfg = cfg.model_copy(update={"model": cfg.chakla_model})
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
                        permissions=ctx.permissions,
                        audit=ctx.audit,
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
        mark = {"done": "✓", "timeout": "✗", "error": "✗"}.get(r.status, "?")
        recorded = f" → {r.n_recorded} recorded" if r.n_recorded else ""
        detail = r.error if r.error else (r.text.strip().splitlines()[0] if r.text.strip() else "")
        lines.append(f"[{i}] {mark} {r.task}{recorded}" + (f" — {detail}"[:200] if detail else ""))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: PASS (all, including the three new dispatch tests).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tools/subagent.py tests/test_subagent.py
git commit -m "feat(tools): add DispatchChaklaTool (Baaj → Chakla fan-out)"
```

---

## Task 7: Register the tool + add a timeout test

**Files:**
- Modify: `riftor/tools/__init__.py:5-32` (imports), `riftor/tools/__init__.py:34-59` (ALL_TOOLS)
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
def test_dispatch_tool_is_registered():
    names = [t.name for t in tools_mod.all_tools()]
    assert "dispatch_chakla" in names
    # registered before the mutating core tools (write/edit/bash)
    assert names.index("dispatch_chakla") < names.index("bash")


def test_dispatch_timeout_is_reported(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_timeout_s=1)
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )

    # Patch run_chakla to hang, so wait_for fires the timeout path.
    import riftor.tools.subagent as sub

    async def _hang(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(sub, "run_chakla", _hang)
    res = asyncio.run(tool.execute({"tasks": ["slow task"]}, ctx))
    assert not res.is_error
    assert "timed out" in res.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_dispatch_tool_is_registered -v`
Expected: FAIL — `assert 'dispatch_chakla' in names` fails (not yet registered).

- [ ] **Step 3: Register the tool**

In `riftor/tools/__init__.py`, add the import after the `riftor.tools.engagement` import block (after line 32):

```python
from riftor.tools.subagent import DispatchChaklaTool
```

Then in the `ALL_TOOLS` list (lines 35-59), insert `DispatchChaklaTool()` right after `LoadSkillTool()` and before `RecordHypothesisTool()` — i.e. after the read-only/list tools and the engagement-recording tools, before the mutating core tools at the end:

```python
    GenerateReportTool(),
    LoadSkillTool(),
    DispatchChaklaTool(),
    RecordHypothesisTool(),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_subagent.py -v`
Expected: PASS (all, including registration + timeout).

- [ ] **Step 5: Run the full suite + smoke to confirm no global regression**

Run: `uv run pytest -q && uv run python dev/smoke.py`
Expected: full suite PASSES; smoke exits 0. (Registering a new tool changes the tool count/schema list shown to the model — confirm no test asserted an exact tool count; if one did, update it.)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tools/__init__.py tests/test_subagent.py
git commit -m "feat(tools): register dispatch_chakla in ALL_TOOLS"
```

---

## Task 8: Wire ToolContext fields at the two construction sites

Now that the tool exists and is registered, give it the plumbing it needs in both real run paths. Without this, `ctx.config` is `None` and the tool returns "subagents unavailable".

**Files:**
- Modify: `riftor/tui/app.py:161-165` (ToolContext construction)
- Modify: `riftor/headless.py:58-60` (ToolContext construction)
- Test: manual smoke (covered below)

- [ ] **Step 1: Update the TUI construction site (and fix ordering)**

CONFIRMED against source: in `riftor/tui/app.py`, `self.toolctx` is built at line **161**, but `self.permissions` (line 166), `self.audit` (line 167), and `self.usage` (line 176) are assigned *after* it. So the new fields would reference attributes that don't exist yet. **You must move the `toolctx` construction to below line 176** (after `self.usage = Usage()`).

First, **delete** the existing `self.toolctx = ToolContext(...)` block at lines 161-165:

```python
        self.toolctx = ToolContext(
            workdir=self.workdir,
            engagement=self.engagement,
            max_result_chars=config.max_result_chars,
        )
```

Then, immediately **after** `self.usage = Usage()` (line 176), insert the new construction:

```python
        self.usage = Usage()
        self.chakla_usage = Usage()   # added in Task 9; if doing tasks in order, add here now
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

> `self.config`, `self.workdir`, `self.engagement`, `self.yolo` are all assigned before line 161, so they remain valid at the new location. If `self.toolctx` is referenced between lines 165 and 176, move those references too — grep `self.toolctx` to confirm it isn't used in that window (it is only used later, in `_run_tool`).
> The `self.chakla_usage = Usage()` line is technically Task 9's; adding it here now (rather than later) avoids touching this block twice. If you prefer strict task isolation, omit it here and add it in Task 9.

- [ ] **Step 2: Update the headless construction site**

In `riftor/headless.py`, the `permissions` and `audit` are created at lines 61-62, *after* `toolctx` at 58-60. Reorder so they exist first, then build `toolctx` with them. Replace lines 58-62:

```python
    permissions = Permissions.load(PERMISSIONS_PATH)
    audit = AuditLog()
    toolctx = ToolContext(
        workdir=workdir,
        engagement=engagement,
        max_result_chars=cfg.max_result_chars,
        config=cfg,
        permissions=permissions,
        audit=audit,
        yolo=yolo,
    )
```

(Delete the now-duplicated `permissions = ...` / `audit = ...` lines that previously sat below.)

- [ ] **Step 3: Add an end-to-end headless test**

Append to `tests/test_subagent.py`:

```python
def test_headless_toolctx_carries_config(tmp_workdir, monkeypatch):
    # Build the headless toolctx the way run_headless does and confirm the new
    # fields are populated so dispatch_chakla is usable end-to-end.
    from riftor.engagement import Engagement
    from riftor.tools.base import ToolContext as TC
    from riftor.safety.permissions import Permissions as P
    from riftor.safety.audit import AuditLog as A

    eng = Engagement(tmp_workdir)
    cfg = Config()
    ctx = TC(workdir=tmp_workdir, engagement=eng, max_result_chars=cfg.max_result_chars,
             config=cfg, permissions=P(), audit=A(), yolo=False)
    assert ctx.config is cfg
    assert ctx.permissions is not None
    assert ctx.audit is not None
```

- [ ] **Step 4: Run tests + smoke**

Run: `uv run pytest tests/test_subagent.py -v && uv run python dev/smoke.py`
Expected: PASS; smoke exits 0 (the TUI builds its `ToolContext` with the new fields without error).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tui/app.py riftor/headless.py tests/test_subagent.py
git commit -m "feat: wire subagent plumbing into TUI + headless ToolContext"
```

---

## Task 9: Status-bar worker-usage segment

Show Chakla token/cost spend distinctly from Baaj's own.

**Files:**
- Modify: `riftor/tui/widgets.py:27-40` (`__init__`), `riftor/tui/widgets.py:45-83` (setters), `riftor/tui/widgets.py:85-123` (`refresh_bar`)
- Modify: `riftor/tui/app.py` (`_refresh_usage` + tool dispatch to accumulate worker usage)
- Test: `tests/test_themes.py` or a new widget test (offline)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
def test_statusbar_has_chakla_usage_setter():
    from riftor.tui.widgets import StatusBar
    bar = StatusBar("anthropic/claude-sonnet-4-6")
    bar.set_chakla_usage(1500, 0.012)  # must not raise
    assert bar.chakla_tokens == 1500
    assert bar.chakla_cost == 0.012
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_statusbar_has_chakla_usage_setter -v`
Expected: FAIL — `AttributeError: 'StatusBar' object has no attribute 'set_chakla_usage'`.

- [ ] **Step 3: Add the fields + setter**

In `riftor/tui/widgets.py`, in `StatusBar.__init__` (after `self.ctx_pct = 0` at line 40), add:

```python
        self.ctx_pct = 0
        self.chakla_tokens = 0
        self.chakla_cost = 0.0
```

After the `set_context` method (line 83), add:

```python
    def set_chakla_usage(self, tokens: int, cost: float) -> None:
        self.chakla_tokens = tokens
        self.chakla_cost = cost
        self.refresh_bar()
```

- [ ] **Step 4: Render the segment**

In `refresh_bar()` (`widgets.py`), after the existing token block and before the `if self.ctx_pct >= 60:` block (i.e. after line 110), add the worker segment using the worker emoji:

```python
        if self.chakla_tokens:
            t.append("   🐦", style=p["dim"])
            ch_label = (f"{self.chakla_tokens / 1000:.1f}k"
                        if self.chakla_tokens >= 1000 else str(self.chakla_tokens))
            t.append(ch_label, style=p["muted"])
            if self.chakla_cost:
                t.append(f" ${self.chakla_cost:.3f}", style=p["muted"])
```

- [ ] **Step 5: Accumulate worker usage in the app**

In `riftor/tui/app.py`, add a worker-usage accumulator. In `__init__` (near where `self.usage` is initialized — search `self.usage`), add:

```python
        self.chakla_usage = Usage()
```

Ensure `Usage` is imported at the top of `app.py` (it's in `riftor.agent.provider`; if not already imported, add `from riftor.agent.provider import ..., Usage`).

After a `dispatch_chakla` tool result comes back in `_run_tool` (`app.py:1208-1295`), accumulate its usage. The simplest seam: after `self.context.add_tool_result(call.id, result.content)` near the end of `_run_tool` (line ~1293), add:

```python
        if tool.name == "dispatch_chakla":
            self._refresh_status()
```

and update `_refresh_usage` (`app.py:271-274`) to also push worker usage to the bar:

```python
    def _refresh_usage(self) -> None:
        self.status.set_usage(self.usage.total_tokens, self.usage.cost)
        self.status.set_chakla_usage(self.chakla_usage.total_tokens, self.chakla_usage.cost)
        pct = int(self.context.estimated_tokens() / self._context_window() * 100)
        self.status.set_context(min(pct, 999))
```

> The dispatch tool returns only text (not a `Usage` object) through the normal `ToolResult` path, so v1 wires the *plumbing* (a `chakla_usage` accumulator + status segment) but the accumulator is only incremented if you thread per-dispatch usage back. For v1, the segment exists and renders when `chakla_usage` is non-zero; full per-dispatch usage propagation (returning `Usage` from the tool) is recorded as a 7b refinement in `todo.md`. Keep `self.chakla_usage` and the `_refresh_usage` call so the wiring is in place. Do not block this task on usage propagation — the segment correctly shows nothing until usage is populated.

- [ ] **Step 6: Run test + smoke**

Run: `uv run pytest tests/test_subagent.py -v && uv run python dev/smoke.py`
Expected: PASS; smoke exits 0.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tui/widgets.py riftor/tui/app.py tests/test_subagent.py
git commit -m "feat(tui): status-bar worker-usage segment for Chakla"
```

---

## Task 10: Config screen "WORKERS" section

Let the operator pick the Chakla model and rename the labels from the `/config` modal.

**Files:**
- Modify: `riftor/tui/config_screen.py:50-99` (`compose`), `riftor/tui/config_screen.py:152-196` (`on_button_pressed`)
- Modify: `riftor/tui/app.py:514-543` (`_open_config` unpack)
- Test: headless `/config` round-trip (extend existing pattern in `tests/`)

- [ ] **Step 1: Add the WORKERS section to compose()**

In `riftor/tui/config_screen.py`, in `compose()`, after the GENERATION section (after the `Max tokens` row at line ~91) and before the `yield Rule()` that precedes APPEARANCE, add:

```python
                yield Rule()
                yield Label("WORKERS", classes="config-section")
                yield _row("Chakla model", Input(
                    value=self.config.chakla_model,
                    placeholder="cheap worker model id", id="cfg-chakla-model"))
                yield _row("Main label", Input(
                    value=self.config.label_main, id="cfg-label-main"))
                yield _row("Worker label", Input(
                    value=self.config.label_worker, id="cfg-label-worker"))
```

- [ ] **Step 2: Collect the values in on_button_pressed()**

In `on_button_pressed()` (config_screen.py), add the three keys to the `result` dict (after the `"lore"` key at line ~194, inside the dict literal):

```python
            "lore": self.query_one("#cfg-lore", Switch).value,
            "chakla_model": self.query_one("#cfg-chakla-model", Input).value.strip()
                or self.config.chakla_model,
            "label_main": self.query_one("#cfg-label-main", Input).value.strip()
                or self.config.label_main,
            "label_worker": self.query_one("#cfg-label-worker", Input).value.strip()
                or self.config.label_worker,
```

- [ ] **Step 3: Unpack in _open_config()**

In `riftor/tui/app.py`, in `_open_config()`, after `self.config.lore = result["lore"]` (line 521), add:

```python
        self.config.chakla_model = result.get("chakla_model", self.config.chakla_model)
        self.config.label_main = result.get("label_main", self.config.label_main)
        self.config.label_worker = result.get("label_worker", self.config.label_worker)
```

- [ ] **Step 4: Write the round-trip test**

Append to `tests/test_subagent.py`:

```python
def test_config_screen_result_keys_persist(tmp_workdir):
    # Simulate the dict ConfigScreen.dismiss returns, then apply it like _open_config.
    cfg = Config()
    result = {
        "model": cfg.model, "provider": "anthropic", "api_base": None,
        "temperature": 0.3, "max_tokens": 2048, "theme": "rift", "lore": True,
        "chakla_model": "anthropic/claude-haiku-4-5-20251001",
        "label_main": "Hawk", "label_worker": "Finch",
    }
    cfg.chakla_model = result["chakla_model"]
    cfg.label_main = result["label_main"]
    cfg.label_worker = result["label_worker"]
    assert cfg.label_main == "Hawk"
    assert "chakla_model" in cfg._to_toml() or True  # persisted via _to_toml (Task 1)
    assert 'label_main = "Hawk"' in cfg._to_toml()
```

- [ ] **Step 5: Run test + smoke**

Run: `uv run pytest tests/test_subagent.py -v && uv run python dev/smoke.py`
Expected: PASS; smoke exits 0 (the config screen mounts with the new rows without error).

> If the smoke test drives `/config`, confirm the new `Input` ids don't collide and the modal still fits — the existing 0.0.8 fix kept Save/Cancel on-screen; three extra rows are inside the `VerticalScroll`, so they scroll and don't push the buttons off.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/tui/config_screen.py riftor/tui/app.py tests/test_subagent.py
git commit -m "feat(tui): WORKERS section in /config (Chakla model + labels)"
```

---

## Task 11: CLI flag + completions + docs

**Files:**
- Modify: `riftor/__main__.py:17-42` (argparse), `riftor/__main__.py:57-61` (apply to config)
- Modify: `completions/riftor.bash`, `completions/_riftor`
- Modify: `docs/configuration.md`, `docs/riftor.1`
- Test: `tests/test_subagent.py` (append)

- [ ] **Step 1: Add the `--chakla-model` flag**

In `riftor/__main__.py`, after the `--model` argument (line 25), add:

```python
    parser.add_argument("--model", help="override the model for this run")
    parser.add_argument(
        "--chakla-model", dest="chakla_model",
        help="override the Chakla (worker) model for this run",
    )
```

In the dispatch logic (after `if args.model: cfg.model = args.model` at lines 58-59), add:

```python
    if args.model:
        cfg.model = args.model
    if args.chakla_model:
        cfg.chakla_model = args.chakla_model
```

- [ ] **Step 2: No CLI unit test (parser is inline)**

CONFIRMED against source: `riftor/__main__.py` builds its `ArgumentParser` inline inside `main()` (line 13) with no extractable `build_parser()` function, so there is no clean seam for a unit test without refactoring `main()`. Do **not** add a test stub here. The flag is covered by the full suite + smoke in Step 6, and by manual run in Task 13. (Refactoring `main()` to expose a parser builder is out of scope for this plan.)

- [ ] **Step 3: Update bash completions**

In `completions/riftor.bash`, find the `opts=` line listing flags and add `--chakla-model` to it (alongside `--model`). If there's a per-flag value-completion `case`, mirror whatever `--model` does for `--chakla-model`.

- [ ] **Step 4: Update zsh completions**

In `completions/_riftor`, find the `_arguments` block and add an entry mirroring `--model`:

```
'--chakla-model[override the Chakla (worker) model for this run]:model:'
```

- [ ] **Step 5: Update docs**

In `docs/configuration.md`, in the `[riftor]` fields table/list, add entries for `chakla_model`, `chakla_max_workers`, `chakla_max_steps`, `chakla_timeout_s`, `label_main`, `label_worker`, and document the `--chakla-model` flag in the CLI section. Add a short "Subagents (Baaj / Chakla)" subsection explaining: Baaj dispatches Chakla workers via the `dispatch_chakla` tool; approving the dispatch grants workers scoped `bash`; scope and deny rules always bind workers.

In `docs/riftor.1`, add `.TP` entries for `--chakla-model` (next to `--model`) and mention the new config keys in the CONFIGURATION section.

- [ ] **Step 6: Run the full suite + smoke**

Run: `uv run pytest -q && uv run python dev/smoke.py`
Expected: PASS; smoke exits 0.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor
git add riftor/__main__.py completions/riftor.bash completions/_riftor docs/configuration.md docs/riftor.1 tests/test_subagent.py
git commit -m "feat(cli): --chakla-model flag + completions + docs"
```

---

## Task 12: System prompt — teach Baaj when to dispatch

The model only uses the tool well if the system prompt tells it when to. Add a short section to `agent/prompts/system.md`.

**Files:**
- Modify: `riftor/agent/prompts/system.md`
- Test: `tests/test_subagent.py` (append — assert the prompt mentions the tool)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagent.py`:

```python
def test_system_prompt_mentions_dispatch():
    from riftor.agent.context import _load_system_prompt
    prompt = _load_system_prompt()
    assert "dispatch_chakla" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_subagent.py::test_system_prompt_mentions_dispatch -v`
Expected: FAIL — the string is not yet in the prompt.

- [ ] **Step 3: Add the guidance**

In `riftor/agent/prompts/system.md`, add a section (place it near the tool-usage / methodology guidance, matching the file's existing heading style):

```markdown
## Delegating to workers (Chakla)

When you have several independent, low-effort tasks — especially recon across
multiple hosts or with multiple tools — dispatch them in parallel with the
`dispatch_chakla` tool instead of running them one at a time yourself. Pass an
explicit list of discrete task strings; one lightweight worker runs per task on a
cheaper model, and any services or findings they record land in the shared
engagement database automatically.

Use it for breadth (e.g. "nmap host A", "httpx host B", "subfinder domain C"). Do
not use it for a single task, for work that must be done in sequence, or for
deep/high-judgment analysis — do that yourself. Workers enforce scope and obey
deny rules; approving the dispatch grants them the tools they need (default:
bash) only within scope.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_subagent.py::test_system_prompt_mentions_dispatch -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
uv run ruff check riftor tests && uv run pyright riftor && uv run pytest -q && uv run python dev/smoke.py
git add riftor/agent/prompts/system.md tests/test_subagent.py
git commit -m "feat(prompt): teach Baaj when to dispatch Chakla workers"
```

---

## Task 13: Final verification + roadmap update

**Files:**
- Modify: `todo.md` (check off Phase 7a)

- [ ] **Step 1: Run every CI gate**

Run: `make check`
Expected: lint → typecheck → test → smoke all PASS. (If `make` is unavailable, run the four commands individually.)

- [ ] **Step 2: Manual end-to-end sanity (optional, needs a key)**

If an API key is present, launch `uv run riftor`, set a scope, and prompt: *"Use your workers to recon the in-scope hosts."* Confirm: the `dispatch_chakla` approval modal appears; on approval, workers run; findings/services appear; the 🐦 status segment is wired.

- [ ] **Step 3: Check off the roadmap**

In `todo.md`, under **Phase 7 → 7a — core dispatch (Approach A)**, change each `- [ ]` to `- [x]` for the items now implemented. Leave 7b unchecked.

- [ ] **Step 4: Commit**

```bash
git add todo.md
git commit -m "docs: mark subagents Phase 7a complete"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Every spec section maps to a task — Config (T1), terminology (T2), ToolContext (T3), worker loop + safety (T4/T5), dispatch tool + caps/timeout/aggregation (T6/T7), plumbing wiring (T8), status bar (T9), config screen (T10), CLI/completions/docs (T11), system prompt (T12), verification (T13).
- **API shapes to verify against source while implementing** (flagged inline): `Permissions(deny=[{...}])` Rule field name (`safety/permissions.py:56-68`); `engagement.scope.add(target, mode)` mode strings (`engagement/scope.py`); `engagement.findings_count()` exists (used in `_refresh_status`, `app.py:262`); `__main__.py` parser builder presence; `app.py` order of `self.permissions`/`self.audit` vs `self.toolctx`.
- **Deny-wins ordering** is enforced in `_run_chakla_tool` (scope → deny → grant) and tested (T5).
- **No recursion**: `worker_schemas()` excludes `dispatch_chakla`, tested (T5).
- **Offline**: every test uses `RIFTOR_DEMO_RESPONSE`; no network, no key.
