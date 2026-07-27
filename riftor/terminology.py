"""Display labels for the lead agent and worker subagents."""

MAIN_EMOJI = "🦅"
WORKER_EMOJI = "⚙"


def terminology() -> dict[str, str]:
    """Return resolved lead/worker display labels."""
    return {
        "main": "Lead",
        "worker": "Worker",
        "main_emoji": MAIN_EMOJI,
        "worker_emoji": WORKER_EMOJI,
    }
