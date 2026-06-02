"""Conversation context: system prompt + message history.

Phase 1 keeps the whole history in memory. Compaction / persistence arrive in
later phases.
"""

from __future__ import annotations

from importlib import resources

LORE_PREAMBLE = (
    "\n\nVoice: speak with the calm precision of something that watches through "
    "the rift — the seam between a system's hardened surface and its soft "
    "interior. Keep the flavor to a light touch; never let it get in the way of "
    "accurate, actionable work."
)


def _load_system_prompt() -> str:
    return (
        resources.files("riftor.agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
    )


class Context:
    def __init__(self, lore: bool = True) -> None:
        self._base = _load_system_prompt()
        self.lore = lore
        self._messages: list[dict] = []

    @property
    def system_prompt(self) -> str:
        return self._base + (LORE_PREAMBLE if self.lore else "")

    @property
    def messages(self) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt}, *self._messages]

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def clear(self) -> None:
        self._messages.clear()
