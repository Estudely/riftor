# Agent `add_scope` tool — design

**Date:** 2026-06-03
**Status:** approved

## Goal

Give the agent a tool to **request** adding targets to its own **in-scope** list,
so it can act on hosts it discovers (e.g. a subdomain found in DNS of an in-scope
domain) without the operator hand-editing scope. The request is gated behind the
existing **operator-approval** flow: the agent can only ask; the operator's
approval is what actually widens the guardrail.

## Safety model (the core of this feature)

The scope guardrail is what stops the agent from touching out-of-scope targets.
Letting the agent mutate it must not weaken that leash, so:

- **Operator-approved, not auto-add.** The tool sets `requires_permission = True`,
  which routes it through the existing `ConfirmScreen` approval prompt
  interactively, and blocks it in headless mode unless a standing `allow` rule
  exists in `permissions.toml` (identical to how bash/write/edit behave).
- **Widen-only.** The tool adds **in-scope** targets only. It cannot remove,
  clear, or add out-of-scope/exclusion entries. The agent can never shrink or
  disable the guardrail — only request to extend it. Removing/clearing/excluding
  stay operator-only via `/scope`.
- The operator sees **what** and **why** before approving (target list + reason).

## Non-goals (YAGNI)

- No out-of-scope / remove / clear via the agent (operator-only via `/scope`).
- No new approval machinery — reuse `requires_permission` + `ConfirmScreen`.
- No auto-add / "derived target" inference. Every addition needs approval.

## Component

One new tool in `riftor/tools/engagement.py` (alongside the other engagement
tools), registered in `riftor/tools/__init__.py`.

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
```

### `preview(args)`
One-line summary for the approval prompt + audit log, e.g.:

```
add to scope: admin.example.com, 10.0.0.5 — "found in DNS of in-scope example.com"
```

Truncated to the Tool.preview convention (≤300 chars).

### `execute(args, ctx)`
- If `ctx.engagement is None` → `ToolResult("error: no active engagement", is_error=True)`
  (matches the other engagement tools).
- Normalize `targets` to a list of non-empty strings; if empty →
  `ToolResult("error: no targets given", is_error=True)`.
- For each target: validate via `Target.parse(raw)` (never raises — worst case it
  classifies as a domain). Track three buckets: **added**, **already present**
  (target already in `engagement.scope.in_scope`), **invalid** (empty after parse).
- Add via `ctx.engagement.add_scope(raw, "in")` — the existing method that
  persists to SQLite and logs a `scope_*` activity entry.
- Return a summary, e.g.
  `added 2 target(s) to scope: admin.example.com, 10.0.0.5` plus, when relevant,
  `· N already present` / `· N skipped (unparseable)`.

### Not `scope_sensitive`
`scope_sensitive` means "this tool *touches* a target host, so check its args
against scope before running." `add_scope` edits the scope list itself; its
arguments are not hosts to be reached. So it is **not** `scope_sensitive` — it
must not trip the out-of-scope blocker on its own target arguments (that would be
circular). Approval is the gate here, via `requires_permission`.

## Approval flow (reused, no new code)

- **Interactive (`riftor/tui/app.py`):** the dispatch path already pops
  `ConfirmScreen(tool.name, preview, …)` when `permissions.needs_prompt(name,
  requires_permission, preview)` is true. Operator picks Approve / Always / Never;
  `execute()` runs only on approval.
- **Headless (`riftor/headless.py`):** already blocks a `requires_permission` tool
  unless `permissions.is_allowed(...)` — so `add_scope` is denied headlessly
  unless the operator pre-authorizes it in `permissions.toml`.

## Registration

Add `AddScopeTool()` to `ALL_TOOLS` in `riftor/tools/__init__.py`, in the
mutating/needs-approval region (near `WriteTool`/`EditTool`/`BashTool`), so its
ordering reflects its privilege. Import it in the engagement-tools import block.

## Testing

**Unit (`tests/test_tools.py` or `tests/test_engagement.py`, matching where tool
tests live):**
- `add_scope` adds valid in-scope targets; they appear in
  `engagement.scope.in_scope` with the right `Target.kind` (ip/cidr/domain/wildcard).
- Reports already-present targets and unparseable/empty ones without erroring the
  whole call.
- Errors cleanly with no active engagement and with empty `targets`.
- The tool advertises `requires_permission is True`, is **not** `scope_sensitive`,
  and its schema requires `targets` + `reason`.
- `preview()` includes the targets and the reason.

**Integration (Textual pilot, if the suite has approval-flow tests):**
- An `add_scope` tool call triggers `ConfirmScreen`; **declining** leaves
  `engagement.scope` unchanged; **approving** adds the target.

**Headless:**
- Without an `allow` rule, an `add_scope` call is blocked with the standard
  headless-denied message; scope is unchanged.

## Docs

Update `docs/configuration.md` (or the tools/agent section) to note the agent can
request in-scope additions via `add_scope`, that it is approval-gated, and that
removing/excluding/clearing remain operator-only via `/scope`.

## Backward compatibility

Purely additive: a new tool. No change to existing tools, scope semantics, the
`/scope` command, or the enforcement path. Engagements without the tool in use
behave exactly as before.
