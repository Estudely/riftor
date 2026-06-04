"""Renameable terminology for the subagent feature.

The orchestrator is "Baaj" (eagle) and the workers are "Chakla" (sparrows) by
default. The text labels live in Config (label_main / label_worker) so operators
can rename them; the emoji are fixed branding. Read labels through this helper so
renaming is a single source of truth.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from riftor.config import Config

MAIN_EMOJI = "🦅"
WORKER_EMOJI = "🐦"


def terminology(config: "Config") -> dict[str, str]:
    """Return the resolved {main, worker, main_emoji, worker_emoji} labels."""
    return {
        "main": config.label_main,
        "worker": config.label_worker,
        "main_emoji": MAIN_EMOJI,
        "worker_emoji": WORKER_EMOJI,
    }
