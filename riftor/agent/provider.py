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
import re
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
        _litellm = _init_litellm()
    return _litellm


def _init_litellm():
    """Import and configure litellm once. Guarded by the caller's None-check so
    a concurrent first-call race at worst imports litellm twice (harmless — the
    codex registration has its own dedup guard)."""
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    import litellm

    litellm.telemetry = False
    litellm.drop_params = True
    litellm.suppress_debug_info = True
    _register_codex_provider(litellm)
    return litellm


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


def _status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status from a litellm/httpx exception.

    litellm's typed exceptions carry ``status_code``; httpx nests it under
    ``.response.status_code``. Using the real code avoids the substring-matching
    false positives (issue #117) where a message mentioning a port or byte count
    like "5000" was misread as a 500 server error.
    """
    for attr in ("status_code", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def classify_error(exc: Exception) -> ProviderError:
    """Map a raw litellm/network exception to an actionable ProviderError.

    Prefers the exception's typed name and HTTP status code; only falls back to
    scanning the message text (with word-boundary anchors) so status-like digits
    embedded in a message can't cause a misclassification (issue #117).
    """
    name = type(exc).__name__.lower()
    text = str(exc)
    low = text.lower()
    status = _status_code(exc)

    # A standalone HTTP status token in the message, only as a fallback when the
    # exception carries no real status_code attribute.
    def _msg_has_status(*codes: str) -> bool:
        return any(re.search(rf"\b{c}\b", text) for c in codes)

    if status == 401 or status == 403 or "authentication" in name or "auth" in low \
            or "api key" in low or (status is None and _msg_has_status("401", "403")):
        return ProviderError(
            "auth",
            "authentication failed — check your API key (env var or /config). " + text[:200],
            retryable=False,
        )
    if status == 429 or "ratelimit" in name or "rate limit" in low \
            or (status is None and _msg_has_status("429")):
        return ProviderError("rate_limit", "rate limited by the provider — backing off. " + text[:160],
                             retryable=True)
    if "timeout" in name or "timed out" in low:
        return ProviderError("timeout", "request timed out. " + text[:160], retryable=True)
    if (status is not None and 500 <= status <= 599) or "overloaded" in low \
            or (status is None and _msg_has_status("500", "502", "503", "504", "529")):
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
    if status == 400 or "badrequest" in name or "invalid" in low \
            or (status is None and _msg_has_status("400")):
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
            except asyncio.CancelledError:
                raise  # never retry a cancel — the operator hit Esc
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
        # Keyed by the provider's tool-call ``index`` when present. Insertion order
        # (dict guarantees it) preserves emission order, so we never sort mixed key
        # types. ``_seq`` mints unique keys for streams that omit ``index``.
        acc: dict[object, dict] = {}
        _seq = 0
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
                tc_id = getattr(tc, "id", None)
                fn = getattr(tc, "function", None)
                fn_name = getattr(fn, "name", None) if fn is not None else None
                # Spec-conformant streams (OpenAI/litellm) tag every fragment with a
                # stable ``index`` so fragments of one call reassemble correctly.
                # Some providers omit it; defaulting all of them to 0 (the old bug)
                # collapsed distinct calls onto one slot. When ``index`` is absent we
                # resolve the slot by the strongest signal available — id, then a
                # fresh-name fragment, then a same-name continuation — and only fall
                # back to "most recent" as a last resort (issue #116).
                raw_idx = getattr(tc, "index", None)
                if raw_idx is not None:
                    key: object = ("idx", raw_idx)
                elif tc_id is not None:
                    # Match an existing slot with this id (continuation), else new.
                    key = next(
                        (k for k, s in acc.items() if s.get("id") == tc_id), None
                    )  # type: ignore[assignment]
                    if key is None:
                        key = ("seq", _seq)
                        _seq += 1
                elif fn_name is not None:
                    # No id/index but a name present → this fragment starts (or is)
                    # a distinct call. Reuse a same-named slot only if it's still
                    # waiting for its arguments; otherwise start a fresh slot so two
                    # same-named calls don't merge.
                    key = next(
                        (k for k, s in acc.items()
                         if s.get("name") == fn_name and not s.get("args")),
                        None,
                    )  # type: ignore[assignment]
                    if key is None:
                        key = ("seq", _seq)
                        _seq += 1
                elif acc:
                    # Pure continuation fragment (no id, no index, no name): the only
                    # safe assumption is it continues the most recently touched call.
                    key = next(reversed(acc))
                else:
                    key = ("seq", _seq)
                    _seq += 1
                slot = acc.setdefault(key, {"id": None, "name": None, "args": ""})
                if tc_id:
                    slot["id"] = tc_id
                if fn is not None:
                    if fn_name:
                        slot["name"] = fn_name
                    fn_args = getattr(fn, "arguments", None)
                    if fn_args:
                        slot["args"] += fn_args

        tool_calls: list[ToolCall] = []
        raw_tool_calls: list[dict] = []
        # Iterate in insertion order — the order fragments first appeared, which is
        # emission order. (Keys are tuples now, so they aren't directly sortable.)
        for n, slot in enumerate(acc.values()):
            if not slot["name"]:
                continue
            raw_args = slot["args"] or "{}"
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed = {"__parse_error__": raw_args}
            call_id = slot["id"] or f"call_{n}"
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
