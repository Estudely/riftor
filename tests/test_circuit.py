"""Barren-round / progress circuit breaker (issue #104).

The agent must treat hosts and services as progress during recon — not only
findings — otherwise /continue is needed every few rounds of enumeration.
"""

from __future__ import annotations

from riftor.agent import circuit
from riftor.engagement import Engagement


def test_progress_counts_hosts_and_services_not_just_findings(tmp_path):
    eng = Engagement(tmp_path)
    assert circuit.progress_score(eng) == 0

    eng.store.add_host("10.0.0.1")
    assert circuit.progress_score(eng) == 1

    eng.add_service(host="10.0.0.1", port=80, proto="tcp", service="http")
    assert circuit.progress_score(eng) == 2

    eng.add_finding(title="xss", severity="high", host="10.0.0.1")
    assert circuit.progress_score(eng) == 3
    eng.close()


def test_progress_score_none_engagement_is_zero():
    assert circuit.progress_score(None) == 0


def test_barren_resets_when_hosts_grow_without_findings():
    """Recon that only records hosts must not trip the barren breaker."""
    barren = 0
    prev = 0
    # Simulate 7 rounds of host discovery (no findings).
    for n_hosts in range(1, 8):
        score = n_hosts  # stand-in for progress_score
        if score > prev:
            barren = 0
            prev = score
        else:
            barren += 1
        assert not circuit.should_stop_barren(barren)


def test_barren_stops_only_after_limit_with_no_progress():
    barren = 0
    for _ in range(circuit.BARREN_LIMIT - 1):
        barren += 1
        assert not circuit.should_stop_barren(barren)
    barren += 1
    assert circuit.should_stop_barren(barren)


def test_continue_disables_barren_for_that_run():
    """Explicit /continue means the operator wants more work — don't re-trip
    the barren breaker on the same run (issue #104)."""
    assert circuit.barren_limit(extra_steps=0) == circuit.BARREN_LIMIT
    assert circuit.barren_limit(extra_steps=16) > circuit.BARREN_LIMIT
    assert not circuit.should_stop_barren(
        circuit.BARREN_LIMIT, limit=circuit.barren_limit(extra_steps=16)
    )
