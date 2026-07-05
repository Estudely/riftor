"""CVSS v3.1 base score calculation and vector validation.

Verifies the pure-python implementation against official FIRST.org test vectors
and confirms that invalid metric values are rejected (not silently mis-scored).
"""

from __future__ import annotations

import math

from riftor.engagement.cvss import base_score, parse_vector, severity_from_score


# Official CVSS v3.1 base-score test vectors (FIRST.org spec §A.2).
OFFICIAL_VECTORS = [
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N", 9.1),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
    ("CVSS:3.1/AV:P/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 6.8),
    ("CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H", 7.2),
    ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N", 4.3),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
    ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:N/A:N", 3.7),
    ("CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H", 7.8),
    ("CVSS:3.1/AV:A/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L", 4.3),
]


def test_official_vectors():
    """Spot-check against FIRST.org published base scores."""
    for vector, expected in OFFICIAL_VECTORS:
        actual = base_score(vector)
        assert actual is not None, f"got None for {vector}"
        assert math.isclose(actual, expected, abs_tol=0.05), (
            f"{vector}: expected {expected}, got {actual}"
        )


def test_invalid_scope_value_rejected():
    """S:X is not a valid CVSS metric value → must return None, not silently
    treat it as scope-unchanged (issue #125)."""
    assert parse_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:X/C:H/I:H/A:H") is None
    assert base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:X/C:H/I:H/A:H") is None


def test_invalid_attack_vector_rejected():
    assert parse_vector("CVSS:3.1/AV:Q/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") is None
    assert base_score("CVSS:3.1/AV:Q/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") is None


def test_missing_metric_rejected():
    assert parse_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H") is None


def test_empty_vector_returns_none():
    assert base_score("") is None
    assert base_score("not-a-vector") is None


def test_severity_bands():
    assert severity_from_score(0.0) == "info"
    assert severity_from_score(3.5) == "low"
    assert severity_from_score(5.0) == "medium"
    assert severity_from_score(8.0) == "high"
    assert severity_from_score(9.8) == "critical"
