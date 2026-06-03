"""Agent layer: error classification, usage accounting, context compaction."""

from __future__ import annotations

from riftor.agent.context import Context
from riftor.agent.provider import Usage, classify_error


def test_classify_auth():
    err = classify_error(Exception("AuthenticationError: invalid x-api-key (401)"))
    assert err.kind == "auth" and not err.retryable


def test_classify_rate_limit():
    err = classify_error(Exception("RateLimitError: 429 Too Many Requests"))
    assert err.kind == "rate_limit" and err.retryable


def test_classify_server_retryable():
    err = classify_error(Exception("APIError: 503 overloaded"))
    assert err.kind == "server" and err.retryable


def test_classify_context():
    err = classify_error(Exception("maximum context length is 200000 tokens"))
    assert err.kind == "context" and not err.retryable


def test_usage_add():
    u = Usage(prompt_tokens=10, completion_tokens=5, cost=0.01)
    u.add(Usage(prompt_tokens=3, completion_tokens=2, cost=0.02))
    assert u.total_tokens == 20
    assert round(u.cost, 4) == 0.03


def test_context_token_estimate_and_compact():
    ctx = Context(lore=False)
    ctx.add_user("scan it")
    ctx.add_message({"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
    ]})
    ctx.add_tool_result("a", "X" * 5000)
    ctx.add_user("again")  # recent turns kept
    before = ctx.estimated_tokens()
    changed = ctx.compact(keep_recent=1, clip=100)
    assert changed == 1
    assert ctx.estimated_tokens() < before


def test_pop_last_user_turn():
    ctx = Context(lore=False)
    ctx.add_user("first")
    ctx.add_assistant("reply")
    ctx.add_user("second")
    ctx.add_assistant("reply2")
    text = ctx.pop_last_user_turn()
    assert text == "second"
    # only the first exchange remains
    roles = [m["role"] for m in ctx._messages]
    assert roles == ["user", "assistant"]
