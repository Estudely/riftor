"""CVSS v3.1 base score — pure-python, no dependencies.

Implements the base metric equations from the CVSS v3.1 specification so a
finding can carry a vector like ``CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H``
and we can derive a 0.0-10.0 score and a qualitative severity band.
"""

from __future__ import annotations

import math

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}
_REQUIRED = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")

# Allowed metric values — any vector with an unrecognised value is rejected
# (returns None) rather than silently mis-scored. Only S has values outside the
# metric→weight dicts (U/C), so it gets its own set.
_VALID_VALUES: dict[str, set[str]] = {
    "AV": set(_AV),
    "AC": set(_AC),
    "PR": set(_PR_UNCHANGED),
    "UI": set(_UI),
    "S": {"U", "C"},
    "C": set(_CIA),
    "I": set(_CIA),
    "A": set(_CIA),
}


def parse_vector(vector: str) -> dict[str, str] | None:
    if not vector:
        return None
    metrics: dict[str, str] = {}
    for part in vector.strip().split("/"):
        key, sep, value = part.partition(":")
        if sep:
            metrics[key.upper()] = value.upper()
    if not all(m in metrics for m in _REQUIRED):
        return None
    # Reject vectors with invalid metric values (e.g. S:X) instead of silently
    # treating them as a default. The official spec says invalid → reject.
    for m, val in metrics.items():
        if m in _VALID_VALUES and val not in _VALID_VALUES[m]:
            return None
    return metrics


def _roundup(value: float) -> float:
    """CVSS v3.1 roundup: smallest 1-decimal number >= value."""
    scaled = round(value * 100000)
    if scaled % 10000 == 0:
        return scaled / 100000.0
    return (math.floor(scaled / 10000) + 1) / 10.0


def base_score(vector: str) -> float | None:
    """Return the CVSS v3.1 base score for ``vector``, or None if invalid."""
    metrics = parse_vector(vector)
    if metrics is None:
        return None
    try:
        scope_changed = metrics["S"] == "C"
        av = _AV[metrics["AV"]]
        ac = _AC[metrics["AC"]]
        ui = _UI[metrics["UI"]]
        pr = (_PR_CHANGED if scope_changed else _PR_UNCHANGED)[metrics["PR"]]
        conf = _CIA[metrics["C"]]
        integ = _CIA[metrics["I"]]
        avail = _CIA[metrics["A"]]
    except KeyError:
        return None

    iss = 1 - (1 - conf) * (1 - integ) * (1 - avail)
    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss
    exploitability = 8.22 * av * ac * pr * ui

    if impact <= 0:
        return 0.0
    raw = 1.08 * (impact + exploitability) if scope_changed else (impact + exploitability)
    return _roundup(min(raw, 10.0))


def severity_from_score(score: float) -> str:
    """Map a base score to riftor's severity band."""
    if score <= 0:
        return "info"
    if score < 4.0:
        return "low"
    if score < 7.0:
        return "medium"
    if score < 9.0:
        return "high"
    return "critical"
