"""Conversation context: system prompt + message history + compaction.

History is kept in memory and persisted by the session layer. When it grows
large we can compact it: tool results are the bulk of the tokens, so the cheap,
lossless-enough strategy is to shrink old tool results while keeping the recent
turns intact.
"""

from __future__ import annotations

from importlib import resources

LORE_PREAMBLE = (
    "\n\nVoice: speak with the calm precision of something that watches through "
    "the rift — the seam between a system's hardened surface and its soft "
    "interior. Keep the flavor to a light touch; never let it get in the way of "
    "accurate, actionable work."
)

# Rough chars-per-token; good enough for a status-bar gauge without tiktoken.
_CHARS_PER_TOKEN = 4


def _load_system_prompt() -> str:
    return (
        resources.files("riftor.agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
    )


def _content_len(msg: dict) -> int:
    content = msg.get("content")
    total = len(content) if isinstance(content, str) else 0
    for call in msg.get("tool_calls") or []:
        total += len(str(call.get("function", {}).get("arguments", "")))
    return total


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

    def add_message(self, message: dict) -> None:
        """Append a raw provider message (e.g. assistant turn with tool_calls)."""
        self._messages.append(message)

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )

    # -- token accounting -------------------------------------------------------
    def estimated_tokens(self) -> int:
        chars = len(self.system_prompt)
        for msg in self._messages:
            chars += _content_len(msg)
        return chars // _CHARS_PER_TOKEN

    def pop_last_user_turn(self) -> str | None:
        """Remove everything back through (and including) the last user message.

        Returns that user message's text, so the caller can resend/edit it.
        """
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].get("role") == "user":
                text = self._messages[i].get("content")
                del self._messages[i:]
                return text if isinstance(text, str) else ""
        return None

    def compact(self, keep_recent: int = 8, clip: int = 400) -> int:
        """Shrink old tool results to ``clip`` chars, keeping the last ``keep_recent``
        messages untouched. Returns the number of messages compacted."""
        cutoff = max(0, len(self._messages) - keep_recent)
        changed = 0
        for msg in self._messages[:cutoff]:
            if msg.get("role") == "tool":
                content = msg.get("content")
                if isinstance(content, str) and len(content) > clip:
                    dropped = len(content) - clip
                    msg["content"] = content[:clip] + f"\n…[compacted {dropped} chars]"
                    changed += 1
        return changed

    def repair(self) -> int:
        """Ensure every assistant tool_call has a following tool result.

        If a turn was interrupted (cancel, new message mid-run, error), an
        assistant ``tool_use`` can be left without its ``tool_result``, which
        Anthropic rejects. We insert a synthetic result for each missing id,
        immediately after that assistant message's existing tool results.
        Returns the number of synthetic results inserted.
        """
        repaired: list[dict] = []
        inserted = 0
        messages = self._messages
        i = 0
        while i < len(messages):
            msg = messages[i]
            repaired.append(msg)
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                expected = [tc.get("id") for tc in msg["tool_calls"] if tc.get("id")]
                j = i + 1
                provided: set[str] = set()
                while j < len(messages) and messages[j].get("role") == "tool":
                    repaired.append(messages[j])
                    provided.add(messages[j].get("tool_call_id"))
                    j += 1
                for tid in expected:
                    if tid not in provided:
                        repaired.append(
                            {
                                "role": "tool",
                                "tool_call_id": tid,
                                "content": "[interrupted: no tool result was recorded]",
                            }
                        )
                        inserted += 1
                i = j
                continue
            i += 1
        if inserted:
            self._messages = repaired
        return inserted

    def dump(self) -> list[dict]:
        """Serializable copy of the message history (without the system prompt)."""
        return [dict(m) for m in self._messages]

    def load(self, messages: list[dict]) -> None:
        self._messages = [dict(m) for m in messages]

    def clear(self) -> None:
        self._messages.clear()
