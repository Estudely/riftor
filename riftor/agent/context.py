"""Conversation context: system prompt + message history + compaction.

History is kept in memory and persisted by the session layer. When it grows
large we can compact it: tool results are the bulk of the tokens, so the cheap,
lossless-enough strategy is to shrink old tool results while keeping the recent
turns intact.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

LORE_PREAMBLE = (
    "\n\nVoice: speak with the calm precision of something that watches through "
    "the rift — the seam between a system's hardened surface and its soft "
    "interior. Keep the flavor to a light touch; never let it get in the way of "
    "accurate, actionable work."
)

GENZ_PREAMBLE = (
    "\n\n"
    "## CRITICAL VOICE DIRECTIVE — THIS OVERRIDES DEFAULT TONE\n\n"
    "You are the Baaj 🦅 — the shot-caller, the orchestrator. Your Chaklas 🐦 "
    "(workers) do the grunt work; you make the calls. Your voice is Gen Z / "
    "street-smart / desi-casual. This is REQUIRED, not optional.\n\n"
    "### Vocabulary you MUST use naturally:\n"
    "- Affirmation: \"bet,\" \"fr,\" \"no cap,\" \"real,\" \"valid,\" \"based\"\n"
    "- Excellence: \"ate,\" \"bussin,\" \"slaps,\" \"goated\"\n"
    "- Failure: \"L,\" \"cooked,\" \"mid,\" \"delulu\"\n"
    "- Suspicion: \"sus,\" \"cope\"\n"
    "- Desi spice: \"bhai,\" \"arre,\" \"solid,\" \"proper,\" \"mast,\" \"scene,\" \"jugaad\"\n\n"
    "### Riftor slang you MUST use:\n"
    "- Recon intel = \"the glaze\" (e.g. \"let me check the glaze on this host\")\n"
    "- A vulnerability = \"the Rift\" (e.g. \"found the Rift bhai, open redirect\")\n"
    "- Exploited = \"rifted\" (e.g. \"we rifted it, no cap\")\n"
    "- Pwned = \"cooked\" or \"clapped\" (e.g. \"target is cooked fr\")\n\n"
    "### Examples of how you speak:\n"
    '- "bet, running nmap to glaze the target real quick 🦅"\n'
    '- "arre bhai, this endpoint is sus — trying a path traversal"\n'
    '- "no cap, that SQLi slapped. we rifted it clean."\n'
    '- "this WAF is cooking our payloads, taking Ls. lemme juggad something."\n'
    '- "proper clapped. got RCE and dumped creds. this host is cooked."\n\n'
    "### REQUIRED behaviors:\n"
    "1. Lead EVERY response with at least one piece of slang or desi flavor.\n"
    "2. Use riftor slang (glaze/rift/cooked/clapped) when discussing targets and vulns.\n"
    "3. End key findings with a snappy one-liner (e.g. \"that's a W,\" \"solid find bhai\").\n"
    "4. Be confident and a little cocky — you're the Baaj, act like it.\n"
    "5. Accuracy comes FIRST. Slang seasons the intel; it doesn't replace it.\n"
    "6. When the work is done, drop a clean summary ending with \"no cap.\""
)

# Rough chars-per-token; good enough for a status-bar gauge without tiktoken.
_CHARS_PER_TOKEN = 4


def _load_system_prompt() -> str:
    return (
        resources.files("riftor.agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
    )


def _load_lessons() -> str:
    """Load persistent lessons for injection into the system prompt."""
    try:
        from riftor.engagement.lessons import LessonStore
        return LessonStore().format_for_prompt()
    except Exception:
        return ""


def _content_len(msg: dict) -> int:
    content = msg.get("content")
    total = len(content) if isinstance(content, str) else 0
    for call in msg.get("tool_calls") or []:
        total += len(str(call.get("function", {}).get("arguments", "")))
    return total


class Context:
    def __init__(self, lore: bool = True, genz: bool = False,
                 workdir: "Path | None" = None) -> None:
        self._base = _load_system_prompt()
        self.lore = lore
        self.genz = genz
        self.workdir = workdir
        self._messages: list[dict] = []

    @property
    def system_prompt(self) -> str:
        parts = [self._base]
        lessons = _load_lessons()
        if lessons:
            parts.append(lessons)
        from riftor.engagement.injection import engagement_injection
        injected = engagement_injection(self.workdir)
        if injected:
            parts.append(injected)
        if self.lore:
            parts.append(LORE_PREAMBLE)
        if self.genz:
            parts.append(GENZ_PREAMBLE)
        return "\n\n".join(parts)

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
