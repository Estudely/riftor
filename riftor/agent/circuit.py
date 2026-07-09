"""Barren-round / progress circuit breaker for the agent loop.

Pure helpers so the rule can be unit-tested without driving Textual.
The loop in ``tui/app.py`` owns the side effects (stopping, operator notes).

Issue #104: the breaker used to watch *findings only*, so recon that records
hosts/services looked "stuck" and forced ``/continue`` every few rounds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from riftor.engagement import Engagement

#: Consecutive tool-using rounds with no new hosts/services/findings before stop.
BARREN_LIMIT = 8

#: When the operator explicitly ``/continue``s, raise the barren ceiling so the
#: same recon stretch is not immediately cut short again.
_CONTINUE_BARREN_BONUS = 16


def progress_score(engagement: Engagement | None) -> int:
    """Monotonic engagement progress: hosts + services + findings.

    Recon that only discovers infrastructure still counts as progress.
    """
    if engagement is None:
        return 0
    store = engagement.store
    return (
        len(store.list_hosts())
        + len(store.list_services())
        + engagement.findings_count()
    )


def barren_limit(*, extra_steps: int = 0) -> int:
    """Barren-round ceiling for this agent run.

    A positive ``extra_steps`` means the operator asked to continue — give the
    run more room before the barren breaker fires again.
    """
    if extra_steps > 0:
        return BARREN_LIMIT + _CONTINUE_BARREN_BONUS
    return BARREN_LIMIT


def should_stop_barren(barren_rounds: int, *, limit: int | None = None) -> bool:
    """True when ``barren_rounds`` has reached the stop threshold."""
    ceiling = BARREN_LIMIT if limit is None else limit
    return barren_rounds >= ceiling


def note_for_barren_stop() -> str:
    return (
        "⚠ circuit breaker: no new hosts/services/findings for several rounds — "
        "stopping. /continue to resume or change approach."
    )
