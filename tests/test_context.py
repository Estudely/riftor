"""Tests for the Context class — system prompt assembly and repair."""

from riftor.agent.context import Context


def test_system_prompt_contains_base_methodology():
    """System prompt is the bundled system.md (methodology checklist, not RIFT stages)."""
    ctx = Context()
    prompt = ctx.system_prompt
    assert "list_methodology" in prompt
    assert "check_methodology" in prompt
    assert "dispatch_worker" in prompt


def test_context_workdir_optional():
    ctx = Context()
    assert ctx.workdir is None
    assert isinstance(ctx.system_prompt, str)
    assert len(ctx.system_prompt) > 100


# --- repair() (#115) ----------------------------------------------------------

def _tool_call_msg(*ids):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "bash", "arguments": "{}"}}
            for i in ids
        ],
    }


def _tool_result(tid, content="ok"):
    return {"role": "tool", "tool_call_id": tid, "content": content}


def test_repair_inserts_missing_result():
    ctx = Context()
    ctx.load([
        {"role": "user", "content": "go"},
        _tool_call_msg("a", "b"),
        _tool_result("a"),  # b is missing
    ])
    inserted = ctx.repair()
    assert inserted == 1
    tool_ids = [m["tool_call_id"] for m in ctx.messages if m.get("role") == "tool"]
    assert sorted(tool_ids) == ["a", "b"]


def test_repair_no_op_when_all_present():
    ctx = Context()
    ctx.load([
        {"role": "user", "content": "go"},
        _tool_call_msg("a"),
        _tool_result("a"),
    ])
    assert ctx.repair() == 0


def test_repair_does_not_duplicate_out_of_position_result():
    """If a real result for an id exists further down the history (e.g. a resumed
    session interleaved it), repair must NOT synthesize a second one — that would
    create a duplicate tool_call_id and the provider 400s (issue #115)."""
    ctx = Context()
    ctx.load([
        {"role": "user", "content": "go"},
        _tool_call_msg("a", "b"),
        _tool_result("a"),
        # b's real result is interleaved after a non-tool message
        {"role": "assistant", "content": "thinking..."},
        _tool_result("b", "real b result"),
    ])
    inserted = ctx.repair()
    assert inserted == 0, "must not synthesize a duplicate for the out-of-position result"
    b_results = [m for m in ctx.messages if m.get("tool_call_id") == "b"]
    assert len(b_results) == 1
    assert b_results[0]["content"] == "real b result"
