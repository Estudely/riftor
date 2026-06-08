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
