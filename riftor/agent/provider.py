"""LLM provider abstraction over litellm (cloud + local Ollama).

Phase 1 only needs streaming chat. The agent loop and tool-calling land in
Phase 2, where this same wrapper will also forward tool schemas.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, AsyncIterator

os.environ.setdefault("LITELLM_LOG", "ERROR")

import litellm  # noqa: E402

litellm.telemetry = False
litellm.drop_params = True
litellm.suppress_debug_info = True

if TYPE_CHECKING:
    from riftor.config import Config


class Provider:
    def __init__(self, config: "Config") -> None:
        self.config = config

    def _kwargs(self, messages: list[dict]) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        return kwargs

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield content deltas from the model as they arrive."""
        response = await litellm.acompletion(**self._kwargs(messages))
        async for chunk in response:
            try:
                content = chunk.choices[0].delta.content
            except (IndexError, AttributeError):
                content = None
            if content:
                yield content
