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
