"""Anti-loop decision rule. Guards the invariant that each tool call yields
exactly one tool result (run XOR synthetic-stop), never both."""

from __future__ import annotations

from riftor.agent import antiloop


def test_signature_normalizes_whitespace():
    a = antiloop.call_signature("bash", {"cmd": "ls   -la"})
    b = antiloop.call_signature("bash", {"cmd": "ls -la"})
    assert a == b == "bash:ls -la"


def test_first_calls_run_without_warning():
    recent: list[str] = []
    for _ in range(antiloop.WARN_AT - 1):
        d = antiloop.classify(recent, "bash:x")
        assert d.run and not d.stop and not d.warn


def test_warns_at_threshold_but_still_runs():
    recent: list[str] = []
    last = None
    for _ in range(antiloop.WARN_AT):
        last = antiloop.classify(recent, "bash:x")
    assert last is not None
    assert last.run is True          # tool still runs → its one real result
    assert last.stop is False
    assert last.warn is True
    assert last.repeat_count == antiloop.WARN_AT


def test_hard_stops_at_threshold_and_does_not_run():
    recent: list[str] = []
    last = None
    for _ in range(antiloop.STOP_AT):
        last = antiloop.classify(recent, "bash:x")
    assert last is not None
    assert last.stop is True         # synthetic result injected instead
    assert last.run is False         # never both → no duplicate tool_call_id
    assert last.repeat_count == antiloop.STOP_AT


def test_run_and_stop_are_mutually_exclusive_every_step():
    # The core invariant: across an entire repeated sequence, no single
    # decision ever has both run and stop set.
    recent: list[str] = []
    for _ in range(antiloop.STOP_AT + 3):
        d = antiloop.classify(recent, "bash:x")
        assert not (d.run and d.stop)


def test_distinct_calls_tracked_independently():
    recent: list[str] = []
    for _ in range(antiloop.STOP_AT):
        antiloop.classify(recent, "bash:a")
    d = antiloop.classify(recent, "bash:b")  # different sig, fresh count
    assert d.run and not d.stop and d.repeat_count == 1


def test_window_bounds_memory():
    recent: list[str] = []
    # Flood the window with unique sigs so an old repeat falls out of memory.
    antiloop.classify(recent, "bash:old")
    for i in range(antiloop.WINDOW):
        antiloop.classify(recent, f"bash:{i}")
    assert len(recent) <= antiloop.WINDOW
    assert "bash:old" not in recent
