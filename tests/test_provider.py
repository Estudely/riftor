"""Provider: lazy litellm loading + the offline demo mock hook."""

from __future__ import annotations

import sys

import pytest

from riftor.agent import provider as prov
from riftor.config import Config


def test_importing_provider_does_not_load_litellm():
    # The whole point of the lazy accessor: importing the module is cheap.
    # (It may already be loaded by another test; this asserts the module import
    # path itself doesn't force it — checked via the accessor cache being lazy.)
    assert "litellm" not in sys.modules or prov._litellm is sys.modules.get("litellm")


def test_get_litellm_configures_and_caches():
    lit = prov._get_litellm()
    assert lit.telemetry is False
    assert lit.drop_params is True
    assert prov._get_litellm() is lit  # cached


def test_kwargs_no_mock_by_default(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    p = Provider_for_test()
    kw = p._kwargs([{"role": "user", "content": "hi"}])
    assert "mock_response" not in kw
    assert kw["stream"] is True


def test_kwargs_injects_mock_when_env_set(monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "canned reply")
    p = Provider_for_test()
    kw = p._kwargs([{"role": "user", "content": "hi"}])
    assert kw["mock_response"] == "canned reply"


@pytest.mark.asyncio
async def test_mock_response_streams_offline(monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "The rift opens.")
    p = Provider_for_test()
    text = []
    turn = None
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        if event == "text":
            text.append(str(payload))
        elif event == "done":
            turn = payload
    assert "".join(text) == "The rift opens."
    assert turn is not None and turn.text == "The rift opens."


def Provider_for_test() -> "prov.Provider":
    return prov.Provider(Config(model="anthropic/claude-sonnet-4-6", api_key="sk-demo"))


def test_kwargs_includes_reasoning_effort_when_thinking_on(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=True, reasoning_effort="high")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["reasoning_effort"] == "high"


def test_kwargs_omits_reasoning_effort_when_thinking_off(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=False, reasoning_effort="high")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw


def test_kwargs_omits_reasoning_effort_when_none(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo",
                 show_thinking=True, reasoning_effort="none")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw


def test_kwargs_uses_provider_table_creds(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from riftor.config import ProviderCreds
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {"anthropic": ProviderCreds(api_key="sk-table",
                                                api_base="https://table/")}
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["api_key"] == "sk-table"
    assert kw["api_base"] == "https://table/"


def test_kwargs_marks_codex_model_for_litellm(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="codex/gpt-5.5")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["model"] == "codex/riftorcodex-gpt-5.5"


def test_kwargs_leaves_non_codex_model_unchanged(monkeypatch):
    monkeypatch.delenv("RIFTOR_DEMO_RESPONSE", raising=False)
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="sk-demo")
    kw = prov.Provider(cfg)._kwargs([{"role": "user", "content": "hi"}])
    assert kw["model"] == "anthropic/claude-opus-4-8"


def test_get_litellm_registers_codex():
    """_get_litellm() must register the codex custom handler into litellm."""
    # Run registration fresh so the codex entry is guaranteed to be present.
    saved = prov._litellm
    try:
        prov._litellm = None
        lit = prov._get_litellm()
        entries = list(getattr(lit, "custom_provider_map", None) or [])
        providers = [e.get("provider") for e in entries]
        assert "codex" in providers, f"'codex' not in custom_provider_map providers: {providers}"
        codex_entry = next(e for e in entries if e.get("provider") == "codex")
        assert codex_entry.get("custom_handler") is not None
    finally:
        prov._litellm = saved


def test_get_litellm_registration_is_idempotent():
    """Calling _get_litellm() multiple times must not duplicate the codex entry."""
    saved = prov._litellm
    try:
        prov._litellm = None
        lit = prov._get_litellm()
        # Second call: _litellm is already cached, so registration won't re-run,
        # but the guard must also prevent duplication if somehow called again.
        prov._register_codex_provider(lit)  # call the guard directly a second time
        entries = list(getattr(lit, "custom_provider_map", None) or [])
        codex_count = sum(1 for e in entries if e.get("provider") == "codex")
        assert codex_count == 1, f"Expected 1 codex entry, got {codex_count}"
    finally:
        prov._litellm = saved


@pytest.mark.asyncio
async def test_stream_turn_yields_thinking_and_excludes_it_from_message(monkeypatch):
    # Fake litellm streaming chunks: reasoning_content deltas, then content.
    class _Delta:
        def __init__(self, content=None, reasoning_content=None):
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = None

    class _Choice:
        def __init__(self, delta):
            self.delta = delta

    class _Chunk:
        def __init__(self, delta):
            self.choices = [_Choice(delta)]
            self.usage = None

    async def _fake_stream():
        yield _Chunk(_Delta(reasoning_content="let me "))
        yield _Chunk(_Delta(reasoning_content="think"))
        yield _Chunk(_Delta(content="the answer"))

    async def _fake_acompletion(self, **kwargs):
        return _fake_stream()

    monkeypatch.setattr(prov.Provider, "_acompletion", _fake_acompletion)
    p = Provider_for_test()

    thinking, text, turn = [], [], None
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        if event == "thinking":
            thinking.append(str(payload))
        elif event == "text":
            text.append(str(payload))
        elif event == "done":
            turn = payload

    assert "".join(thinking) == "let me think"
    assert "".join(text) == "the answer"
    assert turn is not None
    # reasoning is display-only: never persisted into the assistant message
    assert turn.assistant_message["content"] == "the answer"
    assert "reasoning_content" not in turn.assistant_message
    assert "let me think" not in (turn.assistant_message.get("content") or "")


# --- tool-call stream reassembly (#72) ----------------------------------------

class _TCFn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _TC:
    """A streamed tool-call fragment. ``index`` may be omitted (set to None) to
    simulate a provider that doesn't tag fragments — the case that used to
    collapse distinct calls onto one slot."""
    def __init__(self, *, id=None, name=None, arguments=None, index=None):
        self.id = id
        self.index = index
        self.function = _TCFn(name, arguments)


class _TCDelta:
    def __init__(self, tool_calls=None):
        self.content = None
        self.reasoning_content = None
        self.tool_calls = tool_calls


class _TCChoice:
    def __init__(self, delta):
        self.delta = delta


class _TCChunk:
    def __init__(self, tool_calls):
        self.choices = [_TCChoice(_TCDelta(tool_calls))]
        self.usage = None


async def _run_tool_stream(monkeypatch, chunks):
    async def _fake_stream():
        for c in chunks:
            yield c

    async def _fake_acompletion(self, **kwargs):
        return _fake_stream()

    monkeypatch.setattr(prov.Provider, "_acompletion", _fake_acompletion)
    p = Provider_for_test()
    turn = None
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        if event == "done":
            turn = payload
    assert turn is not None
    return turn


@pytest.mark.asyncio
async def test_tool_calls_reassemble_by_index(monkeypatch):
    """Two interleaved calls, fragmented across chunks, tagged with index 0/1 —
    they must reassemble into two calls with intact arguments."""
    chunks = [
        _TCChunk([_TC(id="a", name="nmap", arguments='{"ho', index=0)]),
        _TCChunk([_TC(name="httpx", arguments='{"ur', index=1, id="b")]),
        _TCChunk([_TC(arguments='st":"x"}', index=0)]),
        _TCChunk([_TC(arguments='l":"y"}', index=1)]),
    ]
    turn = await _run_tool_stream(monkeypatch, chunks)
    by_name = {c.name: c for c in turn.tool_calls}
    assert set(by_name) == {"nmap", "httpx"}
    assert by_name["nmap"].arguments == {"host": "x"}
    assert by_name["httpx"].arguments == {"url": "y"}


@pytest.mark.asyncio
async def test_tool_calls_without_index_do_not_collide(monkeypatch):
    """A provider that omits ``index`` must still yield two distinct calls — the
    old `index or 0` default merged them onto slot 0 (last-wins + invalid JSON)."""
    chunks = [
        _TCChunk([_TC(id="a", name="nmap", arguments='{"host":"x"}')]),
        _TCChunk([_TC(id="b", name="httpx", arguments='{"url":"y"}')]),
    ]
    turn = await _run_tool_stream(monkeypatch, chunks)
    names = sorted(c.name for c in turn.tool_calls)
    assert names == ["httpx", "nmap"], f"calls collided: {names}"
    by_name = {c.name: c for c in turn.tool_calls}
    assert by_name["nmap"].arguments == {"host": "x"}
    assert by_name["httpx"].arguments == {"url": "y"}


@pytest.mark.asyncio
async def test_tool_call_fragments_without_index_coalesce_by_id(monkeypatch):
    """Fragments of one indexless call (same id, args split across chunks) must
    coalesce — not split into two calls."""
    chunks = [
        _TCChunk([_TC(id="a", name="nmap", arguments='{"ho')]),
        _TCChunk([_TC(id="a", arguments='st":"x"}')]),
    ]
    turn = await _run_tool_stream(monkeypatch, chunks)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].arguments == {"host": "x"}


@pytest.mark.asyncio
async def test_indexless_interleaved_continuation_goes_to_right_call(monkeypatch):
    """Two indexless calls interleaved: call #0 opens, call #1 opens, then a
    pure continuation fragment (no id, no index, no name) for call #1. The
    continuation must land on the most-recently-touched call (#1), and the two
    calls' arguments must stay separate (issue #116)."""
    chunks = [
        _TCChunk([_TC(id="a", name="nmap", arguments='{"host":"x"}')]),
        _TCChunk([_TC(id="b", name="httpx", arguments='{"ur')]),
        _TCChunk([_TC(arguments='l":"y"}')]),  # continues httpx (most recent)
    ]
    turn = await _run_tool_stream(monkeypatch, chunks)
    by_name = {c.name: c for c in turn.tool_calls}
    assert set(by_name) == {"nmap", "httpx"}
    assert by_name["nmap"].arguments == {"host": "x"}
    assert by_name["httpx"].arguments == {"url": "y"}


@pytest.mark.asyncio
async def test_two_same_named_indexless_calls_do_not_merge(monkeypatch):
    """Two distinct calls to the same tool, each id-tagged but index-less, must
    not merge into one (issue #116)."""
    chunks = [
        _TCChunk([_TC(id="a", name="bash", arguments='{"command":"id"}')]),
        _TCChunk([_TC(id="b", name="bash", arguments='{"command":"whoami"}')]),
    ]
    turn = await _run_tool_stream(monkeypatch, chunks)
    assert len(turn.tool_calls) == 2
    cmds = sorted(c.arguments["command"] for c in turn.tool_calls)
    assert cmds == ["id", "whoami"]


# --- classify_error status-code precedence (#117) -----------------------------

class _StatusExc(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_classify_uses_status_code_over_message_digits():
    """A connection error mentioning port 5000 must NOT be misread as a 500
    server error (issue #117)."""
    err = prov.classify_error(Exception("connection refused on port 5000"))
    assert err.kind == "network"
    assert err.retryable is True


def test_classify_real_500_is_server_error():
    err = prov.classify_error(_StatusExc("upstream boom", status_code=503))
    assert err.kind == "server"
    assert err.retryable is True


def test_classify_real_400_is_validation_not_auth():
    """A 400 whose message happens to contain '401' must classify by the real
    status code (validation), not the stray digits (auth)."""
    err = prov.classify_error(_StatusExc("bad request near token 401xyz", status_code=400))
    assert err.kind == "validation"
    assert err.retryable is False


def test_classify_429_status_is_rate_limit():
    err = prov.classify_error(_StatusExc("slow down", status_code=429))
    assert err.kind == "rate_limit"
    assert err.retryable is True


def test_classify_message_fallback_when_no_status():
    """With no status_code attribute, fall back to a word-boundary match."""
    err = prov.classify_error(Exception("HTTP 502 Bad Gateway"))
    assert err.kind == "server"


# --- cost estimation (#114) ---------------------------------------------------

@pytest.mark.asyncio
async def test_cost_estimated_from_tokens_when_provider_omits_it(monkeypatch):
    """litellm's streamed usage has no .cost; the Turn must still carry a cost
    derived from the token counts (issue #114)."""
    class _Usage:
        prompt_tokens = 1000
        completion_tokens = 500
        cost = None  # provider did not supply cost

    class _Delta:
        content = "hi"
        reasoning_content = None
        tool_calls = None

    class _Choice:
        def __init__(self):
            self.delta = _Delta()

    class _Chunk:
        def __init__(self, usage=None):
            self.choices = [_Choice()]
            self.usage = usage

    async def _fake_stream():
        yield _Chunk()
        yield _Chunk(usage=_Usage())

    async def _fake_acompletion(self, **kwargs):
        return _fake_stream()

    # Stub _estimate_cost so the test doesn't depend on litellm's pricing table.
    monkeypatch.setattr(prov.Provider, "_acompletion", _fake_acompletion)
    monkeypatch.setattr(prov.Provider, "_estimate_cost", lambda self, usage: 0.0123)

    p = Provider_for_test()
    turn = None
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        if event == "done":
            turn = payload
    assert turn is not None
    assert turn.usage.prompt_tokens == 1000
    assert turn.usage.completion_tokens == 500
    assert turn.usage.cost == 0.0123


@pytest.mark.asyncio
async def test_cost_not_estimated_when_no_tokens(monkeypatch):
    """No token usage → don't call the pricing table (keeps offline tests offline)."""
    called = {"n": 0}

    class _Delta:
        content = "hi"
        reasoning_content = None
        tool_calls = None

    class _Chunk:
        def __init__(self):
            self.choices = [type("Ch", (), {"delta": _Delta()})()]
            self.usage = None  # no usage at all

    async def _fake_stream():
        yield _Chunk()

    async def _fake_acompletion(self, **kwargs):
        return _fake_stream()

    def _spy(self, usage):
        called["n"] += 1
        return 0.0

    monkeypatch.setattr(prov.Provider, "_acompletion", _fake_acompletion)
    monkeypatch.setattr(prov.Provider, "_estimate_cost", _spy)
    p = Provider_for_test()
    async for event, payload in p.stream_turn([{"role": "user", "content": "go"}]):
        pass
    assert called["n"] == 0


def test_provider_supplied_cost_is_kept(monkeypatch):
    """When litellm DOES supply a cost, we keep it (don't override)."""
    u = prov._extract_usage(type("C", (), {"usage": type("U", (), {
        "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.5})()})())
    assert u is not None and u.cost == 0.5
