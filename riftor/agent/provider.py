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

os.environ.setdefault("LITELLM_LOG", "ERROR")

import litellm  # noqa: E402

litellm.telemetry = False
litellm.drop_params = True
litellm.suppress_debug_info = True

if TYPE_CHECKING:
    from riftor.config import Config


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
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        return kwargs

    async def _acompletion(self, **kwargs):
        """litellm.acompletion with classified retry/backoff for transient errors."""
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
        """Yield ``("text", str)`` deltas, then a final ``("done", Turn)``."""
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
