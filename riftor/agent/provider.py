"""LLM provider abstraction over litellm (cloud + local Ollama).

``stream`` is plain text streaming. ``stream_turn`` is the tool-aware agent
turn: it streams text deltas *and* accumulates any tool calls, yielding a final
assembled :class:`Turn`.
"""

from __future__ import annotations

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
class Turn:
    """The result of one assistant turn."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict = field(default_factory=dict)


class Provider:
    def __init__(self, config: "Config") -> None:
        self.config = config

    def _kwargs(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
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

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield content deltas from the model as they arrive (no tools)."""
        response = await litellm.acompletion(**self._kwargs(messages))
        async for chunk in response:
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
        response = await litellm.acompletion(**self._kwargs(messages, tools))
        text_parts: list[str] = []
        acc: dict[int, dict] = {}

        async for chunk in response:
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

        yield ("done", Turn(text=text, tool_calls=tool_calls, assistant_message=assistant_message))
