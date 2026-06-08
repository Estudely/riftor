"""LLM provider abstraction over litellm (cloud + local Ollama).

``stream`` is plain text streaming. ``stream_turn`` is the tool-aware agent
turn: it streams text deltas *and* accumulates any tool calls, yielding a final
assembled :class:`Turn`. Transient provider errors (rate limit, network, 5xx)
are retried with exponential backoff; the final error is classified into an
actionable message.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from riftor.config import Config

# litellm is heavy (~2.4s to import, pulling in openai/pydantic/etc.). It is only
# needed on the first model call, so we import + configure it lazily — keeping app
# startup fast. The cost then hides behind the network round-trip of that call.
_litellm = None


def _get_litellm():
    global _litellm
    if _litellm is None:
        os.environ.setdefault("LITELLM_LOG", "ERROR")
        import litellm

        litellm.telemetry = False
        litellm.drop_params = True
        litellm.suppress_debug_info = True
        _register_codex_provider(litellm)
        _litellm = litellm
    return _litellm


def _register_codex_provider(litellm) -> None:
    """Register riftor's vendored `codex/` handler (reads ~/.codex/auth.json).

    Guarded: if importing/instantiating the handler fails for any reason, Codex
    simply won't work, but every other provider still does — riftor's
    never-crash-on-an-optional-thing ethos.
    """
    try:
        from riftor.agent.codex_provider import codex_provider
    except Exception:  # noqa: BLE001 — Codex optional at runtime; never break the loop
        return
    existing = list(getattr(litellm, "custom_provider_map", None) or [])
    if any(entry.get("provider") == "codex" for entry in existing):
        return
    existing.append({"provider": "codex", "custom_handler": codex_provider})
    litellm.custom_provider_map = existing
    # Setting custom_provider_map alone is NOT enough: litellm gates custom-handler
    # dispatch on ``custom_llm_provider in litellm._custom_providers`` (main.py), a
    # list that stays empty until ``custom_llm_setup()`` runs. It idempotently
    # appends our provider to both ``litellm.provider_list`` and
    # ``litellm._custom_providers`` so routing by model id reaches our handler.
    try:
        litellm.utils.custom_llm_setup()
    except Exception:  # noqa: BLE001 — Codex optional at runtime; never break the loop
        pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost += other.cost


@dataclass
class Turn:
    """The result of one assistant turn."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)


class ProviderError(Exception):
    """A classified, operator-facing provider failure."""

    def __init__(self, kind: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


def classify_error(exc: Exception) -> ProviderError:
    """Map a raw litellm/network exception to an actionable ProviderError."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if "authentication" in name.lower() or "auth" in low or "api key" in low or "401" in text:
        return ProviderError(
            "auth",
            "authentication failed — check your API key (env var or /config). " + text[:200],
            retryable=False,
        )
    if "ratelimit" in name.lower() or "rate limit" in low or "429" in text:
        return ProviderError("rate_limit", "rate limited by the provider — backing off. " + text[:160],
                             retryable=True)
    if "timeout" in name.lower() or "timed out" in low:
        return ProviderError("timeout", "request timed out. " + text[:160], retryable=True)
    if any(code in text for code in ("500", "502", "503", "504", "529")) or "overloaded" in low:
        return ProviderError("server", "provider server error — retrying. " + text[:160], retryable=True)
    if "connection" in low or "network" in low or "getaddrinfo" in low:
        return ProviderError("network", "network error reaching the provider. " + text[:160],
                             retryable=True)
    if "context" in low and ("length" in low or "window" in low or "maximum" in low or "token" in low):
        return ProviderError(
            "context",
            "context window exceeded — clear/compact the conversation (/clear or /compact). " + text[:160],
            retryable=False,
        )
    if "badrequest" in name.lower() or "invalid" in low or "400" in text:
        return ProviderError("validation", "request rejected by the provider. " + text[:200],
                             retryable=False)
    return ProviderError("unknown", text[:240] or name, retryable=False)


def _extract_usage(chunk) -> Usage | None:
    raw = getattr(chunk, "usage", None)
    if raw is None:
        return None
    u = Usage(
        prompt_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
    )
    cost = getattr(raw, "cost", None)
    if cost:
        u.cost = float(cost)
    return u


class Provider:
    def __init__(self, config: "Config") -> None:
        self.config = config
        self.max_retries = 4

    def _kwargs(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        # Route codex/<real-model> through an opaque marker id so litellm dispatches
        # to our custom handler instead of registry-matching the bare name to its
        # built-in OpenAI provider. Imported lazily so importing provider.py never
        # loads litellm/codex_provider (see test_importing_provider_does_not_load_litellm).
        model = self.config.model
        if model.startswith("codex/"):
            from riftor.agent.codex_provider import to_litellm_model

            model = to_litellm_model(model)
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        api_key, api_base = self.config.creds_for(self.config.model)
        if api_base:
            kwargs["api_base"] = api_base
        if api_key:
            kwargs["api_key"] = api_key
        # Reasoning: ask the model to think only when the operator wants it shown.
        # litellm normalizes ``reasoning_effort`` across providers and drops it for
        # models that don't support it (drop_params=True). "none" => don't request.
        if self.config.show_thinking and self.config.reasoning_effort != "none":
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        # Offline demo hook: return canned streamed text instead of calling a model.
        # Only active when the env var is set (used by demo.tape / CI), so normal
        # runs are unaffected.
        demo = os.environ.get("RIFTOR_DEMO_RESPONSE")
        if demo:
            kwargs["mock_response"] = demo
        return kwargs

    async def _acompletion(self, **kwargs):
        """litellm.acompletion with classified retry/backoff for transient errors."""
        litellm = _get_litellm()
        last: ProviderError | None = None
        for attempt in range(self.max_retries):
            try:
                return await litellm.acompletion(**kwargs)
            except Exception as exc:  # noqa: BLE001
                err = classify_error(exc)
                last = err
                if not err.retryable or attempt == self.max_retries - 1:
                    raise err from exc
                # exponential backoff with deterministic jitter (no RNG dependency)
                delay = min(2.0 ** attempt, 8.0) + (attempt * 0.13)
                await asyncio.sleep(delay)
        raise last or ProviderError("unknown", "completion failed")

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield content deltas from the model as they arrive (no tools)."""
        response = await self._acompletion(**self._kwargs(messages))
        async for chunk in response:  # type: ignore[union-attr]  # litellm stream is async-iterable
            try:
                content = chunk.choices[0].delta.content
            except (IndexError, AttributeError):
                content = None
            if content:
                yield content

    async def stream_turn(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[tuple[str, object]]:
        """Yield ``("text"|"thinking", str)`` deltas, then a final ``("done", Turn)``.

        ``"thinking"`` carries the model's reasoning (litellm ``reasoning_content``)
        and is display-only — it is never folded into the returned ``Turn``.
        """
        response = await self._acompletion(**self._kwargs(messages, tools))
        text_parts: list[str] = []
        acc: dict[int, dict] = {}
        usage = Usage()

        async for chunk in response:  # type: ignore[union-attr]  # litellm stream is async-iterable
            seen = _extract_usage(chunk)
            if seen is not None:
                usage = seen
            try:
                delta = chunk.choices[0].delta
            except (IndexError, AttributeError):
                continue

            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield ("text", content)

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                # Display-only: surface the model's thinking to the UI but never
                # accumulate it into the assistant message (keeps history clean,
                # avoids provider replay issues with thinking blocks).
                yield ("thinking", reasoning)

            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", 0) or 0
                slot = acc.setdefault(idx, {"id": None, "name": None, "args": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments

        tool_calls: list[ToolCall] = []
        raw_tool_calls: list[dict] = []
        for idx in sorted(acc):
            slot = acc[idx]
            if not slot["name"]:
                continue
            raw_args = slot["args"] or "{}"
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed = {"__parse_error__": raw_args}
            call_id = slot["id"] or f"call_{idx}"
            tool_calls.append(
                ToolCall(id=call_id, name=slot["name"], arguments=parsed, raw_arguments=raw_args)
            )
            raw_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": raw_args},
                }
            )

        text = "".join(text_parts)
        assistant_message: dict = {"role": "assistant", "content": text or None}
        if raw_tool_calls:
            assistant_message["tool_calls"] = raw_tool_calls

        yield (
            "done",
            Turn(
                text=text,
                tool_calls=tool_calls,
                assistant_message=assistant_message,
                usage=usage,
            ),
        )
