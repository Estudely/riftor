"""Pure anti-loop detection for the agent loop.

Kept UI-free and side-effect-free so the decision rule can be unit-tested
without driving the whole Textual app. The loop in ``tui/app.py`` owns the
side effects (running tools, injecting tool results); this module only decides
*what* should happen for a given tool call given the recent history.
"""

from __future__ import annotations

from dataclasses import dataclass

WARN_AT = 3   # operator gets a heads-up; the tool still runs
STOP_AT = 5   # hard stop: skip the call and end the turn
WINDOW = 20   # how many recent call signatures to remember


def call_signature(name: str, arguments: dict) -> str:
    """Stable signature for a tool call: name + whitespace-normalized args."""
    sig = f"{name}:{' '.join(str(v) for v in arguments.values())}"
    return " ".join(sig.split()).strip()[:200]


@dataclass
class LoopDecision:
    """What the loop should do with one tool call.

    Exactly one of ``run`` / ``stop`` drives a tool result: when ``run`` is
    True the loop runs the tool (its result is the call's single tool message);
    when ``stop`` is True the loop injects one synthetic result and ends the
    turn. They are never both True — that double-result is the bug this guards.
    """

    run: bool          # run the tool (produces its one real result)
    stop: bool         # hard stop: inject one synthetic result, end the turn
    warn: bool         # surface an operator-only notice (no tool message)
    repeat_count: int  # how many times this signature has now been seen


def classify(recent: list[str], sig: str, *, window: int = WINDOW) -> LoopDecision:
    """Record ``sig`` into ``recent`` (mutating, bounded to ``window``) and
    decide what to do with the call it represents."""
    recent.append(sig)
    if len(recent) > window:
        del recent[: len(recent) - window]
    count = recent.count(sig)
    if count >= STOP_AT:
        return LoopDecision(run=False, stop=True, warn=False, repeat_count=count)
    return LoopDecision(run=True, stop=False, warn=count >= WARN_AT, repeat_count=count)
