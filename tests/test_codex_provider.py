"""Offline tests for the Codex provider auth section (Task 4a).

Every network/filesystem seam is monkeypatched: no real network, no real
``~/.codex``. We point ``CODEX_HOME`` at ``tmp_path`` and patch the single
``_http_post_json`` network seam.
"""

from __future__ import annotations

import base64
import json
import stat
import time
import urllib.error

import pytest

from riftor.agent import codex_provider


def _make_jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


def _write_auth(tmp_path, tokens: dict, **extra) -> None:
    data = {"tokens": tokens, **extra}
    (tmp_path / "auth.json").write_text(json.dumps(data))


@pytest.fixture(autouse=True)
def _codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


# --- read_tokens -----------------------------------------------------------


def test_read_tokens_returns_pair(tmp_path):
    _write_auth(tmp_path, {"access_token": "at-1", "refresh_token": "rt-1"})
    assert codex_provider.read_tokens() == ("at-1", "rt-1")


def test_read_tokens_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError) as ei:
        codex_provider.read_tokens()
    assert "codex login" in str(ei.value)


def test_read_tokens_absent_tokens_raises(tmp_path):
    _write_auth(tmp_path, {})  # tokens present but no access/refresh
    with pytest.raises(RuntimeError) as ei:
        codex_provider.read_tokens()
    assert "codex login" in str(ei.value)


# --- account_id ------------------------------------------------------------


def test_account_id_prefers_tokens_field(tmp_path):
    _write_auth(
        tmp_path,
        {"access_token": "at", "refresh_token": "rt", "account_id": "acc-from-field"},
    )
    assert codex_provider.account_id() == "acc-from-field"


def test_account_id_decoded_from_jwt_when_absent(tmp_path):
    at = _make_jwt(
        {
            "https://api.openai.com/auth": {"chatgpt_account_id": "acc-123"},
            "exp": int(time.time()) + 3600,
        }
    )
    _write_auth(tmp_path, {"access_token": at, "refresh_token": "rt"})
    assert codex_provider.account_id() == "acc-123"


def test_account_id_none_when_unresolvable(tmp_path):
    _write_auth(tmp_path, {"access_token": "not-a-jwt", "refresh_token": "rt"})
    assert codex_provider.account_id() is None


# --- should_refresh --------------------------------------------------------


def test_should_refresh_within_window():
    at = _make_jwt({"exp": int(time.time()) + 60})
    assert codex_provider.should_refresh(at) is True


def test_should_refresh_no_exp():
    at = _make_jwt({"foo": "bar"})
    assert codex_provider.should_refresh(at) is True


def test_should_refresh_far_future():
    at = _make_jwt({"exp": int(time.time()) + 3600})
    assert codex_provider.should_refresh(at) is False


# --- refresh_tokens --------------------------------------------------------


def test_refresh_tokens_writes_back(tmp_path, monkeypatch):
    _write_auth(
        tmp_path,
        {"access_token": "old-at", "refresh_token": "old-rt", "account_id": "acc-x"},
        last_refresh="2020-01-01T00:00:00Z",
    )

    captured: dict = {}

    def fake_post(url, body, timeout=30.0):
        captured["url"] = url
        captured["body"] = body
        return {"access_token": "new-at", "refresh_token": "new-rt"}

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    result = codex_provider.refresh_tokens()
    assert result == "new-at"

    on_disk = json.loads((tmp_path / "auth.json").read_text())
    assert on_disk["tokens"]["access_token"] == "new-at"
    assert on_disk["tokens"]["refresh_token"] == "new-rt"
    # Preserved other keys
    assert on_disk["tokens"]["account_id"] == "acc-x"
    # last_refresh updated to something other than the stale value
    assert on_disk["last_refresh"] != "2020-01-01T00:00:00Z"
    assert on_disk["last_refresh"]

    # File mode is owner-only.
    mode = stat.S_IMODE((tmp_path / "auth.json").stat().st_mode)
    assert mode == 0o600

    # Wire protocol body is exact.
    assert captured["url"] == codex_provider.AUTH_TOKEN_URL
    assert captured["body"] == {
        "client_id": codex_provider.CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": "old-rt",
    }


def test_refresh_tokens_falls_back_to_old_values(tmp_path, monkeypatch):
    _write_auth(
        tmp_path,
        {"access_token": "old-at", "refresh_token": "old-rt", "id_token": "old-id"},
    )

    def fake_post(url, body, timeout=30.0):
        return {"access_token": "new-at"}  # no refresh_token / id_token

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    assert codex_provider.refresh_tokens() == "new-at"
    on_disk = json.loads((tmp_path / "auth.json").read_text())
    assert on_disk["tokens"]["access_token"] == "new-at"
    assert on_disk["tokens"]["refresh_token"] == "old-rt"  # preserved
    assert on_disk["tokens"]["id_token"] == "old-id"  # preserved


def test_refresh_tokens_missing_access_token_raises(tmp_path, monkeypatch):
    _write_auth(tmp_path, {"access_token": "old-at", "refresh_token": "old-rt"})

    def fake_post(url, body, timeout=30.0):
        return {"refresh_token": "new-rt"}  # no access_token

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    with pytest.raises(RuntimeError):
        codex_provider.refresh_tokens()


def test_refresh_http_error_becomes_auth_error(tmp_path, monkeypatch):
    _write_auth(tmp_path, {"access_token": "old-at", "refresh_token": "old-rt"})

    def fake_post(url, body, timeout=30.0):
        raise urllib.error.HTTPError(url, 401, "unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    with pytest.raises(RuntimeError) as ei:
        codex_provider.refresh_tokens()
    assert "codex login" in str(ei.value)


def test_refresh_non_json_becomes_auth_error(tmp_path, monkeypatch):
    _write_auth(tmp_path, {"access_token": "old-at", "refresh_token": "old-rt"})

    def fake_post(url, body, timeout=30.0):
        raise json.JSONDecodeError("x", "", 0)

    monkeypatch.setattr(codex_provider, "_http_post_json", fake_post)

    with pytest.raises(RuntimeError) as ei:
        codex_provider.refresh_tokens()
    assert "codex login" in str(ei.value)


# --- instructions prompt (Task 4b) -----------------------------------------


def test_bundled_instructions_loads():
    text = codex_provider._bundled_instructions()
    assert text
    assert text.startswith("You are Codex")


def test_instructions_falls_back_to_bundle_offline(monkeypatch):
    codex_provider._INSTRUCTIONS_CACHE.clear()
    monkeypatch.setattr(codex_provider, "_fetch_remote_instructions", lambda family: None)
    text = codex_provider.instructions_for("gpt-5.5-codex")
    assert text
    assert text.startswith("You are Codex")

    # And a variant where the remote seam raises — still falls back, never crashes.
    codex_provider._INSTRUCTIONS_CACHE.clear()

    def boom(family):
        raise RuntimeError("network down")

    monkeypatch.setattr(codex_provider, "_fetch_remote_instructions", boom)
    text2 = codex_provider.instructions_for("gpt-5.5-codex")
    assert text2
    assert text2.startswith("You are Codex")


def test_instructions_uses_remote_when_available(monkeypatch):
    codex_provider._INSTRUCTIONS_CACHE.clear()
    monkeypatch.setattr(
        codex_provider, "_fetch_remote_instructions", lambda family: "REMOTE PROMPT TEXT"
    )
    assert codex_provider.instructions_for("gpt-5.5-codex") == "REMOTE PROMPT TEXT"


def test_instructions_caches(monkeypatch):
    codex_provider._INSTRUCTIONS_CACHE.clear()
    calls = {"n": 0}

    def counting(family):
        calls["n"] += 1
        return "X"

    monkeypatch.setattr(codex_provider, "_fetch_remote_instructions", counting)
    first = codex_provider.instructions_for("gpt-5.5-codex")
    second = codex_provider.instructions_for("gpt-5.5-codex")
    assert first == second == "X"
    assert calls["n"] == 1


# --- build_request_body (Task 4c) ------------------------------------------


def test_build_request_body_system_becomes_developer_item():
    body = codex_provider.build_request_body(
        "gpt-5.5-codex",
        [{"role": "system", "content": "RIFT system prompt"}],
        instructions="canonical codex instructions",
    )
    # The RIFT system text rides along as the first developer item in input.
    first = body["input"][0]
    assert first["type"] == "message"
    assert first["role"] == "developer"
    assert first["content"][0]["type"] == "input_text"
    assert first["content"][0]["text"] == "RIFT system prompt"
    # instructions stays exactly what was passed in (not the system text).
    assert body["instructions"] == "canonical codex instructions"


def test_build_request_body_multiple_system_messages_concatenated():
    body = codex_provider.build_request_body(
        "m",
        [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
        ],
        instructions="ci",
    )
    first = body["input"][0]
    assert first["role"] == "developer"
    assert first["content"][0]["text"] == "first\n\nsecond"


def test_build_request_body_user_message():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": "hello"}],
        instructions="ci",
    )
    item = body["input"][0]
    assert item == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    }


def test_build_request_body_assistant_tool_calls():
    body = codex_provider.build_request_body(
        "m",
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
                    }
                ],
            }
        ],
        instructions="ci",
    )
    item = body["input"][0]
    assert item == {
        "type": "function_call",
        "name": "bash",
        "arguments": '{"cmd":"ls"}',
        "call_id": "call_1",
    }


def test_build_request_body_assistant_text_and_tool_calls():
    body = codex_provider.build_request_body(
        "m",
        [
            {
                "role": "assistant",
                "content": "thinking out loud",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            }
        ],
        instructions="ci",
    )
    # Text message comes before the function_call item.
    assert body["input"][0] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "input_text", "text": "thinking out loud"}],
    }
    assert body["input"][1]["type"] == "function_call"
    assert body["input"][1]["call_id"] == "call_2"


def test_build_request_body_tool_message():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "tool", "tool_call_id": "call_1", "content": "output text"}],
        instructions="ci",
    )
    assert body["input"][0] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "output text",
    }


def test_build_request_body_top_level_fields():
    body = codex_provider.build_request_body(
        "gpt-5.5-codex",
        [{"role": "user", "content": "hi"}],
        instructions="ci",
        reasoning_effort="high",
        verbosity="low",
    )
    assert body["model"] == "gpt-5.5-codex"
    assert body["store"] is False
    assert body["stream"] is True
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["reasoning"]["summary"] == "auto"
    assert body["reasoning"]["effort"] == "high"
    assert body["text"]["verbosity"] == "low"


def test_build_request_body_strips_sampling_params():
    # Even if sampling params sneak in via message dicts, they must not appear.
    body = codex_provider.build_request_body(
        "m",
        [
            {
                "role": "user",
                "content": "hi",
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 100,
            }
        ],
        instructions="ci",
    )
    for key in (
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
    ):
        assert key not in body


def test_build_request_body_flattens_tools():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "d",
                    "parameters": {"type": "object"},
                },
            }
        ],
        instructions="ci",
    )
    tool = body["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "bash"
    assert tool["description"] == "d"
    assert tool["parameters"] == {"type": "object"}
    assert "function" not in tool


def test_build_request_body_tool_strict_preserved():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "d",
                    "parameters": {"type": "object"},
                    "strict": True,
                },
            }
        ],
        instructions="ci",
    )
    assert body["tools"][0]["strict"] is True


def test_build_request_body_no_tools_omits_key():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": "hi"}],
        instructions="ci",
    )
    assert "tools" not in body


def test_build_request_body_content_as_list():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        instructions="ci",
    )
    assert body["input"][0]["content"][0]["text"] == "hi"


def test_build_request_body_content_as_list_input_text_variant():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "user", "content": [{"type": "input_text", "text": "yo"}]}],
        instructions="ci",
    )
    assert body["input"][0]["content"][0]["text"] == "yo"


def test_build_request_body_empty_assistant_text_skipped():
    body = codex_provider.build_request_body(
        "m",
        [{"role": "assistant", "content": ""}],
        instructions="ci",
    )
    assert body["input"] == []


# --- parse_events (Task 4d) ------------------------------------------------


def test_parse_events_text_deltas():
    events = [
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
    ]
    chunks = list(codex_provider.parse_events(events))
    assert len(chunks) == 2
    assert chunks[0].text == "Hel"
    assert chunks[0].reasoning == ""
    assert chunks[0].is_finished is False
    assert chunks[1].text == "lo"


def test_parse_events_reasoning_delta():
    for etype in (
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
    ):
        chunks = list(
            codex_provider.parse_events([{"type": etype, "delta": "thinking"}])
        )
        assert len(chunks) == 1
        assert chunks[0].reasoning == "thinking"
        assert chunks[0].text == ""


def test_parse_events_function_call_sequence():
    events = [
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "bash",
                "call_id": "call_1",
                "id": "item_1",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": '{"cmd"',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": ':"ls"}',
        },
    ]
    chunks = list(codex_provider.parse_events(events))
    assert len(chunks) == 2
    fragments = "".join(c.tool_call["arguments_delta"] for c in chunks)
    assert fragments == '{"cmd":"ls"}'
    for c in chunks:
        assert c.tool_call["id"] == "call_1"
        assert c.tool_call["name"] == "bash"


def test_parse_events_completed_with_usage_tool_calls():
    events = [
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "arguments": "{}",
                        "call_id": "call_1",
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        }
    ]
    chunks = list(codex_provider.parse_events(events))
    assert len(chunks) == 1
    final = chunks[0]
    assert final.is_finished is True
    assert final.finish_reason == "tool_calls"
    assert final.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_parse_events_completed_text_only_is_stop():
    events = [
        {"type": "response.output_text.delta", "delta": "hi"},
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hi"}],
                    }
                ],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 1,
                    "total_tokens": 4,
                },
            },
        },
    ]
    chunks = list(codex_provider.parse_events(events))
    final = chunks[-1]
    assert final.is_finished is True
    assert final.finish_reason == "stop"
    assert final.usage["total_tokens"] == 4


def test_parse_events_done_accepted_like_completed():
    events = [
        {
            "type": "response.done",
            "response": {"output": [], "usage": {"input_tokens": 1, "output_tokens": 2}},
        }
    ]
    chunks = list(codex_provider.parse_events(events))
    assert len(chunks) == 1
    assert chunks[0].is_finished is True
    assert chunks[0].finish_reason == "stop"
    assert chunks[0].usage["total_tokens"] == 3


def test_parse_events_unknown_type_yields_nothing():
    chunks = list(
        codex_provider.parse_events([{"type": "response.something.weird", "x": 1}])
    )
    assert chunks == []


def test_parse_events_missing_fields_never_raises():
    # Odd/empty events must be tolerated via .get, not crash.
    events = [
        {},
        {"type": "response.output_text.delta"},  # no delta
        {"type": "response.function_call_arguments.delta"},  # no item_id/delta
    ]
    chunks = list(codex_provider.parse_events(events))
    # Text delta with no "delta" coerces to empty string; tool-call delta still
    # emits a (best-effort) chunk. Nothing raises.
    assert all(isinstance(c, codex_provider.CodexChunk) for c in chunks)


# --- iter_sse_lines (Task 4d) ----------------------------------------------


def test_iter_sse_lines_basic_and_done():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        "",
        "data: [DONE]",
    ]
    events = list(codex_provider.iter_sse_lines(lines))
    assert events == [{"type": "response.output_text.delta", "delta": "hi"}]


def test_iter_sse_lines_skips_malformed_frame():
    lines = [
        "data: {not json",
        "",
        'data: {"type":"response.output_text.delta","delta":"ok"}',
        "",
    ]
    events = list(codex_provider.iter_sse_lines(lines))
    assert events == [{"type": "response.output_text.delta", "delta": "ok"}]


def test_iter_sse_lines_ignores_event_and_comment_lines():
    lines = [
        ": this is a comment",
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"x"}',
        "",
    ]
    events = list(codex_provider.iter_sse_lines(lines))
    assert events == [{"type": "response.output_text.delta", "delta": "x"}]


def test_iter_sse_lines_skips_blank_data_frame():
    lines = [
        "data: ",
        "",
        'data: {"type":"response.output_text.delta","delta":"y"}',
        "",
    ]
    events = list(codex_provider.iter_sse_lines(lines))
    assert events == [{"type": "response.output_text.delta", "delta": "y"}]


# --- CustomLLM handler (Task 4e) -------------------------------------------


def _future_auth(tmp_path) -> str:
    """Write an auth.json with a far-future access token (no refresh fires)."""
    at = _make_jwt(
        {
            "https://api.openai.com/auth": {"chatgpt_account_id": "acc-xyz"},
            "exp": int(time.time()) + 3600,
        }
    )
    _write_auth(tmp_path, {"access_token": at, "refresh_token": "rt-1"})
    return at


def _sse(event: dict) -> list[str]:
    return [f"data: {json.dumps(event)}", ""]


def test_bare_model_strips_prefix():
    assert codex_provider._bare_model("codex/gpt-5.5-codex") == "gpt-5.5-codex"
    assert codex_provider._bare_model("gpt-5.5-codex") == "gpt-5.5-codex"
    # The internal route marker is stripped too, so the REAL model id reaches the
    # Codex backend (litellm sees the marked id; the handler sees the real one).
    assert codex_provider._bare_model("codex/riftorcodex-gpt-5.5") == "gpt-5.5"
    assert codex_provider._bare_model("riftorcodex-gpt-5.5-codex") == "gpt-5.5-codex"


def test_to_litellm_model_adds_marker():
    # `codex/<real>` becomes a registry-opaque id so litellm routes to our handler
    # instead of hijacking the bare name to its built-in OpenAI provider.
    assert (
        codex_provider.to_litellm_model("codex/gpt-5.5")
        == "codex/riftorcodex-gpt-5.5"
    )
    assert (
        codex_provider.to_litellm_model("codex/gpt-5.3-codex")
        == "codex/riftorcodex-gpt-5.3-codex"
    )


def test_to_litellm_model_idempotent():
    once = codex_provider.to_litellm_model("codex/gpt-5.5")
    assert codex_provider.to_litellm_model(once) == once


def test_to_litellm_model_passes_through_non_codex():
    assert (
        codex_provider.to_litellm_model("anthropic/claude-opus-4-8")
        == "anthropic/claude-opus-4-8"
    )
    assert codex_provider.to_litellm_model("gpt-5.5") == "gpt-5.5"


# --- end-to-end litellm routing (the gap that let the bug through) ---------


@pytest.mark.parametrize("real", ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.2-codex"])
def test_real_codex_model_routes_to_our_handler(tmp_path, monkeypatch, real):
    """Drive a REAL codex model name through litellm's actual dispatch.

    litellm registry-matches the bare model name, so for known names (gpt-5.5,
    gpt-5.3-codex, ...) it would otherwise hijack the call to its built-in OpenAI
    provider and fail with a credentials error. We send litellm the marked,
    registry-opaque id (exactly as riftor's ``_kwargs`` does) and assert the call
    reaches our custom handler AND the backend receives the REAL (un-marked) name.
    """
    _future_auth(tmp_path)

    recorded: dict = {}

    lines: list[str] = []
    lines += _sse({"type": "response.output_text.delta", "delta": "Hel"})
    lines += _sse({"type": "response.output_text.delta", "delta": "lo"})
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        }
    )

    def fake_stream(payload, headers, timeout=120.0):
        recorded["model"] = payload["model"]
        return iter(lines)

    monkeypatch.setattr(codex_provider, "_stream_responses", fake_stream)

    from riftor.agent.codex_provider import to_litellm_model
    from riftor.agent.provider import _get_litellm

    lit = _get_litellm()
    litellm_model = to_litellm_model(f"codex/{real}")
    # NOTE: no explicit custom_llm_provider — we rely on litellm routing from the
    # model id alone, which is exactly what triggers the bug for known names.
    resp = lit.completion(
        model=litellm_model,
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    chunks = list(resp)

    # Our handler was reached (no "Missing credentials"/CodexException) and the
    # backend got the REAL model id, not the marked one.
    assert recorded["model"] == real
    text = "".join((c.choices[0].delta.content or "") for c in chunks if c.choices)
    assert text == "Hello"


def test_streaming_text_reply(tmp_path, monkeypatch):
    _future_auth(tmp_path)
    captured: dict = {}

    lines: list[str] = []
    lines += _sse({"type": "response.output_text.delta", "delta": "Hel"})
    lines += _sse({"type": "response.output_text.delta", "delta": "lo"})
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Hello"}],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13},
            },
        }
    )

    def fake_stream(payload, headers, timeout=120.0):
        captured["payload"] = payload
        captured["headers"] = headers
        return iter(lines)

    monkeypatch.setattr(codex_provider, "_stream_responses", fake_stream)

    chunks = list(
        codex_provider.codex_provider.streaming(
            "codex/gpt-5.5-codex", [{"role": "user", "content": "hi"}]
        )
    )

    text = "".join(c["text"] or "" for c in chunks)
    assert text == "Hello"

    final = chunks[-1]
    assert final["is_finished"] is True
    usage = final["usage"]
    assert usage is not None
    assert int(usage["prompt_tokens"]) == 11
    assert int(usage["completion_tokens"]) == 2

    # The request body carried the bundled instructions prompt.
    assert captured["payload"]["instructions"]
    # Headers carry auth + the responses beta header.
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["headers"]["OpenAI-Beta"] == "responses=experimental"
    # Account id resolved from the JWT becomes the chatgpt-account-id header.
    assert captured["headers"]["chatgpt-account-id"] == "acc-xyz"
    # Model prefix stripped before going on the wire.
    assert captured["payload"]["model"] == "gpt-5.5-codex"


async def test_astreaming_text_reply(tmp_path, monkeypatch):
    _future_auth(tmp_path)

    lines: list[str] = []
    lines += _sse({"type": "response.output_text.delta", "delta": "foo"})
    lines += _sse({"type": "response.output_text.delta", "delta": "bar"})
    lines += _sse(
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 4, "output_tokens": 2}},
        }
    )

    monkeypatch.setattr(
        codex_provider, "_stream_responses", lambda payload, headers, timeout=120.0: iter(lines)
    )

    collected = []
    async for c in codex_provider.codex_provider.astreaming(
        "gpt-5.5-codex", [{"role": "user", "content": "hi"}]
    ):
        collected.append(c)

    assert "".join(c["text"] or "" for c in collected) == "foobar"
    assert collected[-1]["is_finished"] is True


def test_streaming_through_litellm_custom_stream_wrapper(tmp_path, monkeypatch):
    """End-to-end: drive our handler's chunks through litellm's real
    ``CustomStreamWrapper`` (as riftor does via ``lit.completion(..., stream=True)``).

    This is the integration gap that unit tests on the chunk dict miss: the
    terminal chunk's ``usage`` must be a plain mapping, because
    ``CustomStreamWrapper`` does ``Usage(**chunk["usage"])``. A litellm ``Usage``
    object there raises ``argument after ** must be a mapping, not Usage``
    (surfaced as a ``MidStreamFallbackError``).
    """
    _future_auth(tmp_path)

    lines: list[str] = []
    lines += _sse({"type": "response.output_text.delta", "delta": "Hel"})
    lines += _sse({"type": "response.output_text.delta", "delta": "lo"})
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        }
    )

    monkeypatch.setattr(
        codex_provider,
        "_stream_responses",
        lambda payload, headers, timeout=120.0: iter(lines),
    )

    from riftor.agent.provider import _get_litellm

    lit = _get_litellm()
    resp = lit.completion(
        model="codex/gpt-5.5-codex",
        custom_llm_provider="codex",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )

    # Iterating the stream exercises CustomStreamWrapper end-to-end.
    # Before the fix this raises (Usage not a mapping / MidStreamFallbackError).
    chunks = list(resp)

    text = "".join(
        (c.choices[0].delta.content or "") for c in chunks if c.choices
    )
    assert text == "Hello"

    # If litellm surfaces usage on the final chunk, the token counts round-trip.
    usage = getattr(chunks[-1], "usage", None)
    if usage is not None:
        assert int(usage.prompt_tokens) == 5
        assert int(usage.completion_tokens) == 3


def test_streaming_tool_call_reply(tmp_path, monkeypatch):
    _future_auth(tmp_path)

    lines: list[str] = []
    lines += _sse(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "bash",
                "call_id": "call_1",
                "id": "item_1",
            },
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": '{"cmd"',
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": ':"ls"}',
        }
    )
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "arguments": '{"cmd":"ls"}',
                        "call_id": "call_1",
                    }
                ],
                "usage": {"input_tokens": 9, "output_tokens": 5},
            },
        }
    )

    monkeypatch.setattr(
        codex_provider, "_stream_responses", lambda payload, headers, timeout=120.0: iter(lines)
    )

    chunks = list(
        codex_provider.codex_provider.streaming(
            "gpt-5.5-codex", [{"role": "user", "content": "list files"}]
        )
    )

    tool_chunks = [c for c in chunks if c["tool_use"]]
    assert tool_chunks
    names = [c["tool_use"]["function"]["name"] for c in tool_chunks if c["tool_use"]["function"]["name"]]
    assert "bash" in names
    args = "".join(c["tool_use"]["function"]["arguments"] or "" for c in tool_chunks)
    assert args == '{"cmd":"ls"}'
    # A single call streams under one stable index (0) so the consumer
    # reassembles exactly one call.
    assert all(c["tool_use"]["index"] == 0 for c in tool_chunks)

    final = chunks[-1]
    assert final["is_finished"] is True
    assert final["finish_reason"] == "tool_calls"


def test_streaming_two_parallel_tool_calls_distinct_index(tmp_path, monkeypatch):
    _future_auth(tmp_path)

    lines: list[str] = []
    # Two parallel function calls announced up-front.
    lines += _sse(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "a",
                "call_id": "call_a",
                "id": "item_a",
            },
        }
    )
    lines += _sse(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "b",
                "call_id": "call_b",
                "id": "item_b",
            },
        }
    )
    # Argument fragments interleaved between the two calls.
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_a",
            "delta": '{"x"',
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_b",
            "delta": '{"y"',
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_a",
            "delta": ":1}",
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_b",
            "delta": ":2}",
        }
    )
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "a",
                        "arguments": '{"x":1}',
                        "call_id": "call_a",
                    },
                    {
                        "type": "function_call",
                        "name": "b",
                        "arguments": '{"y":2}',
                        "call_id": "call_b",
                    },
                ],
                "usage": {"input_tokens": 9, "output_tokens": 5},
            },
        }
    )

    monkeypatch.setattr(
        codex_provider, "_stream_responses", lambda payload, headers, timeout=120.0: iter(lines)
    )

    chunks = list(
        codex_provider.codex_provider.streaming(
            "gpt-5.5-codex", [{"role": "user", "content": "do two things"}]
        )
    )

    tool_chunks = [c for c in chunks if c["tool_use"]]
    # Group streamed fragments by their tool_use index.
    by_index: dict[int, list[dict]] = {}
    for c in tool_chunks:
        by_index.setdefault(c["tool_use"]["index"], []).append(c)

    # Two distinct calls -> two distinct indices, 0 and 1 in first-seen order.
    assert set(by_index) == {0, 1}

    # Index 0 belongs to call_a, index 1 to call_b, and arguments accumulate
    # INDEPENDENTLY per index (no concatenation across calls).
    def _args(idx: int) -> str:
        return "".join(c["tool_use"]["function"]["arguments"] or "" for c in by_index[idx])

    def _id(idx: int) -> str:
        return next(c["tool_use"]["id"] for c in by_index[idx])

    assert _id(0) == "call_a"
    assert _id(1) == "call_b"
    assert _args(0) == '{"x":1}'
    assert _args(1) == '{"y":2}'

    final = chunks[-1]
    assert final["is_finished"] is True
    assert final["finish_reason"] == "tool_calls"


def test_completion_non_stream_text(tmp_path, monkeypatch):
    _future_auth(tmp_path)

    lines: list[str] = []
    lines += _sse({"type": "response.output_text.delta", "delta": "Hel"})
    lines += _sse({"type": "response.output_text.delta", "delta": "lo!"})
    lines += _sse(
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 6, "output_tokens": 3}},
        }
    )

    monkeypatch.setattr(
        codex_provider, "_stream_responses", lambda payload, headers, timeout=120.0: iter(lines)
    )

    resp = codex_provider.codex_provider.completion(
        "gpt-5.5-codex", [{"role": "user", "content": "hi"}]
    )
    assert resp.choices[0].message.content == "Hello!"
    assert int(resp.usage.prompt_tokens) == 6
    assert int(resp.usage.completion_tokens) == 3


def test_completion_non_stream_tool_calls(tmp_path, monkeypatch):
    _future_auth(tmp_path)

    lines: list[str] = []
    lines += _sse(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "name": "bash",
                "call_id": "call_1",
                "id": "item_1",
            },
        }
    )
    lines += _sse(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": '{"cmd":"ls"}',
        }
    )
    lines += _sse(
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "arguments": '{"cmd":"ls"}',
                        "call_id": "call_1",
                    }
                ],
                "usage": {"input_tokens": 9, "output_tokens": 5},
            },
        }
    )

    monkeypatch.setattr(
        codex_provider, "_stream_responses", lambda payload, headers, timeout=120.0: iter(lines)
    )

    resp = codex_provider.codex_provider.completion(
        "gpt-5.5-codex", [{"role": "user", "content": "list"}]
    )
    calls = resp.choices[0].message.tool_calls
    assert calls
    assert calls[0].function.name == "bash"
    assert calls[0].function.arguments == '{"cmd":"ls"}'
    assert calls[0].id == "call_1"
    assert resp.choices[0].finish_reason == "tool_calls"


def test_streaming_auth_required_raises(tmp_path, monkeypatch):
    # Empty CODEX_HOME -> no auth.json -> RuntimeError on iteration.
    with pytest.raises(RuntimeError) as ei:
        list(
            codex_provider.codex_provider.streaming(
                "gpt-5.5-codex", [{"role": "user", "content": "hi"}]
            )
        )
    assert "codex login" in str(ei.value)


def test_build_headers_omits_account_id_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_provider, "account_id", lambda: None)
    headers = codex_provider._build_headers("tok-1")
    assert headers["Authorization"] == "Bearer tok-1"
    assert "chatgpt-account-id" not in headers


def test_resolve_access_token_refreshes_when_stale(tmp_path, monkeypatch):
    _write_auth(tmp_path, {"access_token": "stale", "refresh_token": "rt-1"})
    monkeypatch.setattr(codex_provider, "should_refresh", lambda at: True)
    monkeypatch.setattr(codex_provider, "refresh_tokens", lambda: "fresh-token")
    assert codex_provider._resolve_access_token() == "fresh-token"
