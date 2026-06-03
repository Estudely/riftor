# Agent `add_scope` Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `add_scope` agent tool that lets the agent *request* adding in-scope targets, gated behind the existing operator-approval flow (widen-only — never remove/clear/exclude).

**Architecture:** A new `AddScopeTool(Tool)` in `riftor/tools/engagement.py` with `requires_permission = True`, registered in `riftor/tools/__init__.py`. It reuses the existing approval machinery (`ConfirmScreen` interactively; headless allow-rule gating) — no new approval code. It calls the existing `engagement.add_scope(raw, "in")`.

**Tech Stack:** Python 3.11+, the riftor `Tool` ABC, pytest (`asyncio_mode=auto`), ruff (line-length 100), pyright (basic mode).

---

## Conventions (read once)

- Modules: `"""docstring"""` then `from __future__ import annotations`.
- Tool tests live in `tests/test_tools.py`, async, using the `toolctx` fixture
  (provides `toolctx.engagement`, a real `Engagement` on a temp dir) and
  `tools.get("<name>").execute(args, toolctx)`.
- Run one test: `uv run pytest tests/test_tools.py::test_name -v`
- Gate before each commit: `uv run pytest -q && uv run ruff check riftor tests && uv run pyright riftor`
- Lines ≤ 100 chars.

## Key existing APIs (verified)

- `ctx.engagement.add_scope(raw: str, mode: str = "in") -> Target` — adds to scope,
  persists to SQLite, logs `scope_add`. Returns the parsed `Target`.
- `ctx.engagement.scope.in_scope -> list[Target]`; each `Target` has `.raw` and
  `.kind` (`ip|cidr|domain|wildcard`).
- `Target.parse(raw)` (in `riftor.engagement.scope`) — never raises; classifies.
- `Tool` base: `name`, `description`, `parameters`, `requires_permission`,
  `scope_sensitive`, `preview(args) -> str`, `async execute(args, ctx) -> ToolResult`.
- Approval is automatic for `requires_permission = True` (app dispatch pops
  `ConfirmScreen`; headless blocks unless an allow-rule exists). **No code needed for that.**

## File Structure

- **Modify `riftor/tools/engagement.py`** — add `AddScopeTool`.
- **Modify `riftor/tools/__init__.py`** — import + register it.
- **Modify `tests/test_tools.py`** — unit tests.
- **Modify `docs/configuration.md`** — short doc note.

---

## Task 1: `AddScopeTool` — happy path

**Files:**
- Modify: `riftor/tools/engagement.py`
- Modify: `riftor/tools/__init__.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_add_scope_adds_in_scope_targets(toolctx):
    eng = toolctx.engagement
    r = await tools.get("add_scope").execute(
        {"targets": ["admin.example.com", "10.0.0.0/24"],
         "reason": "found in DNS of in-scope example.com"},
        toolctx,
    )
    assert not r.is_error, r.content
    raws = {t.raw for t in eng.scope.in_scope}
    assert "admin.example.com" in raws
    assert "10.0.0.0/24" in raws
    # added in-scope only — nothing landed in out-of-scope
    assert eng.scope.out_of_scope == []


def test_add_scope_tool_metadata():
    tool = tools.get("add_scope")
    assert tool is not None
    assert tool.requires_permission is True
    # must NOT be scope_sensitive (it edits the scope list, doesn't touch a host)
    assert tool.scope_sensitive is False
    props = tool.parameters["properties"]
    assert "targets" in props and "reason" in props
    assert set(tool.parameters["required"]) == {"targets", "reason"}
    prev = tool.preview({"targets": ["a.com", "b.com"], "reason": "why"})
    assert "a.com" in prev and "why" in prev
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_add_scope_tool_metadata -v`
Expected: FAIL — `tools.get("add_scope")` returns `None` → `AssertionError` / `AttributeError`.

- [ ] **Step 3: Add the tool class**

In `riftor/tools/engagement.py`, add this class (e.g. after `ScopeListTool`):

```python
class AddScopeTool(Tool):
    name = "add_scope"
    requires_permission = True
    description = (
        "Request adding one or more targets to the IN-SCOPE list so you can test "
        "them (e.g. a subdomain discovered on an in-scope host). Requires operator "
        "approval. This only WIDENS scope — you cannot remove or exclude targets. "
        "Give a clear reason so the operator can decide."
    )
    parameters = {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Targets to add: IP, CIDR, domain, or *.wildcard.",
            },
            "reason": {
                "type": "string",
                "description": "Why these belong in scope (shown to the operator).",
            },
        },
        "required": ["targets", "reason"],
    }

    def preview(self, args: dict) -> str:
        targets = args.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        joined = ", ".join(str(t) for t in targets) or "(none)"
        reason = str(args.get("reason") or "").strip()
        text = f"add to scope: {joined}"
        if reason:
            text += f' — "{reason}"'
        return text[:300]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        raw_targets = args.get("targets")
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        targets = [str(t).strip() for t in (raw_targets or []) if str(t).strip()]
        if not targets:
            return ToolResult("error: no targets given", is_error=True)

        existing = {t.raw for t in eng.scope.in_scope}
        added: list[str] = []
        already: list[str] = []
        for raw in targets:
            target = eng.add_scope(raw, "in")  # parse + persist + log
            if target.raw in existing:
                already.append(target.raw)
            else:
                existing.add(target.raw)
                added.append(target.raw)

        if not added and already:
            return ToolResult(f"already in scope: {', '.join(already)}")
        msg = f"added {len(added)} target(s) to scope: {', '.join(added)}"
        if already:
            msg += f" · {len(already)} already present"
        return ToolResult(msg)
```

- [ ] **Step 4: Register the tool**

In `riftor/tools/__init__.py`, add `AddScopeTool` to the engagement-tools import
block:

```python
from riftor.tools.engagement import (
    AddScopeTool,
    DeleteFindingTool,
    EditFindingTool,
    GenerateReportTool,
    ImportScanTool,
    ListHostsTool,
    RecordFindingTool,
    RecordServiceTool,
    ScopeListTool,
    SetStageTool,
)
```

And add it to `ALL_TOOLS` in the mutating/needs-approval region — place it right
before `WriteTool()`:

```python
    GenerateReportTool(),
    AddScopeTool(),
    WriteTool(),
    EditTool(),
    BashTool(),
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py::test_add_scope_tool_metadata tests/test_tools.py::test_add_scope_adds_in_scope_targets -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Full gate**

Run: `uv run pytest -q && uv run ruff check riftor tests && uv run pyright riftor`
Expected: all pass; pyright 0 errors (pre-existing warnings in headless.py/theme.py are fine).

- [ ] **Step 7: Commit**

```bash
git add riftor/tools/engagement.py riftor/tools/__init__.py tests/test_tools.py
git commit -m "feat(tools): add_scope tool — agent requests in-scope additions (approval-gated)"
```

---

## Task 2: Edge cases — no engagement, empty targets, dedupe, scalar coercion

**Files:**
- Test: `tests/test_tools.py`
- (No production change expected — these lock in behavior already implemented in Task 1.)

- [ ] **Step 1: Write the tests**

Append to `tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_add_scope_no_engagement():
    from riftor.tools.base import ToolContext
    ctx = ToolContext()  # engagement defaults to None
    r = await tools.get("add_scope").execute(
        {"targets": ["x.com"], "reason": "r"}, ctx
    )
    assert r.is_error
    assert "no active engagement" in r.content


@pytest.mark.asyncio
async def test_add_scope_empty_targets(toolctx):
    r = await tools.get("add_scope").execute({"targets": [], "reason": "r"}, toolctx)
    assert r.is_error
    assert "no targets" in r.content


@pytest.mark.asyncio
async def test_add_scope_reports_already_present(toolctx):
    eng = toolctx.engagement
    eng.add_scope("dup.example.com", "in")
    r = await tools.get("add_scope").execute(
        {"targets": ["dup.example.com"], "reason": "r"}, toolctx
    )
    assert not r.is_error
    assert "already in scope" in r.content
    # not duplicated in the in-scope list
    assert sum(t.raw == "dup.example.com" for t in eng.scope.in_scope) == 1


@pytest.mark.asyncio
async def test_add_scope_accepts_scalar_target(toolctx):
    # a model may pass a single string instead of a list
    r = await tools.get("add_scope").execute(
        {"targets": "solo.example.com", "reason": "r"}, toolctx
    )
    assert not r.is_error
    assert any(t.raw == "solo.example.com" for t in toolctx.engagement.scope.in_scope)
```

- [ ] **Step 2: Run to verify they pass (behavior already implemented)**

Run: `uv run pytest tests/test_tools.py -k add_scope -v`
Expected: PASS (all add_scope tests). If `test_add_scope_reports_already_present`
fails because the dedupe message differs, re-check the Task-1 `execute` branch
(`if not added and already:` returns `already in scope: …`). If
`test_add_scope_accepts_scalar_target` fails, confirm the `isinstance(raw_targets, str)`
coercion is present in both `preview` and `execute`.

- [ ] **Step 3: Full gate**

Run: `uv run pytest -q && uv run ruff check riftor tests`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_tools.py
git commit -m "test(tools): add_scope edge cases — no engagement, empty, dedupe, scalar"
```

---

## Task 3: Headless gating regression

**Files:**
- Test: `tests/test_tools.py` (or wherever headless tests live — check `tests/` for an existing headless test; if none, put it in `tests/test_tools.py`).

This asserts the safety guarantee: in headless mode, `add_scope` is blocked unless
an allow-rule exists — i.e. the agent cannot silently self-scope without an operator.

- [ ] **Step 1: Inspect the headless gate**

Read `riftor/headless.py` around the dispatch (the `tool.requires_permission and
not permissions.is_allowed(...)` block). Confirm a `requires_permission` tool with
no allow-rule is denied with a `[denied: headless]` message and `execute` is NOT
called. The test below asserts via the public `Permissions` API.

- [ ] **Step 2: Write the test**

Append to `tests/test_tools.py`:

```python
def test_add_scope_blocked_headless_without_allow_rule():
    # The headless gate (headless.py) denies a requires_permission tool unless an
    # allow-rule exists — so the agent cannot self-scope unattended. Assert via the
    # exact Permissions check headless.py uses:
    #   `tool.requires_permission and not permissions.is_allowed(tool.name, preview)`
    from riftor.safety.permissions import Permissions
    perms = Permissions()  # no-arg ctor: empty allow-rules + safe default deny
    tool = tools.get("add_scope")
    preview = tool.preview({"targets": ["x.com"], "reason": "r"})
    # no allow-rule yet -> headless would deny (requires_permission and not allowed)
    assert tool.requires_permission is True
    assert not perms.is_allowed(tool.name, preview)
    # operator opts in -> now allowed
    perms.add_allow_rule(tool.name)
    assert perms.is_allowed(tool.name, preview)
```

> Verified API (`riftor/safety/permissions.py`): `Permissions()` takes no path and
> starts with empty allow-rules + the safe default deny list; `is_allowed(tool_name,
> preview)` and `add_allow_rule(tool, pattern=None)` are public. `add_allow_rule`
> calls `save()`, but with the no-arg ctor `self._path is None` so `save()` is a
> no-op — no temp file needed.

- [ ] **Step 3: Run to verify it passes**

Run: `uv run pytest tests/test_tools.py::test_add_scope_blocked_headless_without_allow_rule -v`
Expected: PASS.

- [ ] **Step 4: Full gate + commit**

Run: `uv run pytest -q && uv run ruff check riftor tests`
```bash
git add tests/test_tools.py
git commit -m "test(tools): add_scope is headless-gated without an allow-rule"
```

---

## Task 4: Docs

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Add a note**

In `docs/configuration.md`, in or near the scope/permissions discussion, add:

```markdown
The agent can **request** adding in-scope targets itself via the `add_scope` tool
(e.g. a subdomain it discovered on an in-scope host). Like other privileged tools
it is **approval-gated**: you confirm it in the prompt, and in headless mode it is
blocked unless you add an `allow` rule for `add_scope`. The agent can only *widen*
scope this way — removing, excluding, and clearing remain operator-only via `/scope`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: note the agent add_scope tool (approval-gated, widen-only)"
```

---

## Task 5: Final verification

- [ ] **Step 1: Full gate**

Run: `uv run pytest -q && uv run ruff check riftor tests && uv run pyright riftor`
Expected: all pass; pyright 0 errors.

- [ ] **Step 2: Sanity — tool is registered and advertised**

Run:
```bash
uv run python -c "from riftor import tools; t=tools.get('add_scope'); print(t.name, t.requires_permission, t.scope_sensitive); print('add_scope' in [s['function']['name'] for s in tools.schemas()])"
```
Expected: `add_scope True False` then `True`.

- [ ] **Step 3: Final review**

Dispatch a holistic code review of the whole feature diff vs the spec.

---

## Self-Review notes (for the implementer)

- **Spec coverage:** tool class + approval reuse (T1), widen-only/in-scope-only (T1 asserts `out_of_scope == []`), not-scope_sensitive (T1 metadata test), edge cases (T2), headless safety (T3), docs (T4), verification (T5). All spec sections mapped.
- **Type consistency:** `AddScopeTool`, `add_scope`, `targets`/`reason`, `engagement.add_scope(raw, "in")`, `Target.raw` used identically across tasks.
- **No new approval machinery:** the feature relies entirely on `requires_permission = True` + existing dispatch; the plan adds no app.py/headless.py changes.
- **Risk:** the only uncertain API is `Permissions` construction in T3 — the task tells the implementer to verify it against `riftor/safety/permissions.py` and adapt construction while keeping the assertion.
