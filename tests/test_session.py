"""Session persistence: round-trip plus the crash-safe ``complete`` flag."""

from __future__ import annotations

from riftor.agent import session


def test_save_load_roundtrip(tmp_workdir):
    msgs = [{"role": "user", "content": "enumerate"}, {"role": "assistant", "content": "ok"}]
    session.save(tmp_workdir, "20260101-000000", msgs, "anthropic/claude-sonnet-4-6")
    loaded = session.load(tmp_workdir, "20260101-000000")
    assert loaded["messages"] == msgs
    assert loaded["title"] == "enumerate"
    assert loaded["complete"] is True


def test_incomplete_flag(tmp_workdir):
    session.save(tmp_workdir, "sid", [{"role": "user", "content": "x"}], "m", complete=False)
    rows = session.list_sessions(tmp_workdir)
    assert rows[0]["complete"] is False


def test_atomic_write_leaves_no_tmp(tmp_workdir):
    session.save(tmp_workdir, "sid", [{"role": "user", "content": "x"}], "m")
    leftovers = list((tmp_workdir / ".riftor" / "sessions").glob("*.tmp"))
    assert not leftovers
