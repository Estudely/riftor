"""Tests for the Context class — system prompt assembly, genz/lore composition."""

from riftor.agent.context import Context, GENZ_PREAMBLE, LORE_PREAMBLE


def test_system_prompt_no_genz_no_lore():
    """GENZ_PREAMBLE is absent when genz=False and lore=False."""
    ctx = Context(lore=False, genz=False)
    prompt = ctx.system_prompt
    assert GENZ_PREAMBLE not in prompt
    assert LORE_PREAMBLE not in prompt


def test_system_prompt_genz_on():
    """GENZ_PREAMBLE is present when genz=True."""
    ctx = Context(lore=False, genz=True)
    prompt = ctx.system_prompt
    assert GENZ_PREAMBLE in prompt


def test_system_prompt_genz_off():
    """GENZ_PREAMBLE is absent when genz=False (lore on)."""
    ctx = Context(lore=True, genz=False)
    prompt = ctx.system_prompt
    assert GENZ_PREAMBLE not in prompt
    assert LORE_PREAMBLE in prompt


def test_system_prompt_both_on():
    """Both preambles present when both lore and genz are True."""
    ctx = Context(lore=True, genz=True)
    prompt = ctx.system_prompt
    assert GENZ_PREAMBLE in prompt
    assert LORE_PREAMBLE in prompt
    # Genz should come after lore
    assert prompt.index(LORE_PREAMBLE) < prompt.index(GENZ_PREAMBLE)


def test_context_defaults():
    """Context defaults: lore=True, genz=False."""
    ctx = Context()
    assert ctx.lore is True
    assert ctx.genz is False


def test_context_mutable_genz():
    """genz property is mutable after construction."""
    ctx = Context(lore=True, genz=False)
    assert GENZ_PREAMBLE not in ctx.system_prompt
    ctx.genz = True
    assert GENZ_PREAMBLE in ctx.system_prompt
    ctx.genz = False
    assert GENZ_PREAMBLE not in ctx.system_prompt


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
