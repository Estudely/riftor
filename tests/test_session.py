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


def test_new_id_is_unique_within_same_second():
    """Two ids minted back-to-back must differ so same-second sessions don't
    collide onto one file (issue #112)."""
    ids = {session.new_id() for _ in range(50)}
    assert len(ids) == 50


def test_tmp_files_not_listed_as_sessions(tmp_workdir):
    """The unique .json.tmp files must not be picked up by list_sessions."""
    session.save(tmp_workdir, "20260101-000000-abcd", [{"role": "user", "content": "x"}], "m")
    rows = session.list_sessions(tmp_workdir)
    assert len(rows) == 1
    assert rows[0]["id"] == "20260101-000000-abcd"


def test_concurrent_saves_same_id_dont_corrupt(tmp_workdir):
    """Interleaved saves to the same session id must always leave a valid,
    fully-parseable file (unique tmp names prevent cross-writer clobbering)."""
    import json
    for i in range(20):
        session.save(tmp_workdir, "sid", [{"role": "user", "content": f"msg {i}"}], "m")
    path = tmp_workdir / ".riftor" / "sessions" / "sid.json"
    data = json.loads(path.read_text(encoding="utf-8"))  # must parse cleanly
    assert data["messages"][0]["content"] == "msg 19"
    # no tmp leftovers
    assert not list((tmp_workdir / ".riftor" / "sessions").glob("*.tmp"))


def test_branch_copies_prefix_and_sets_parent(tmp_workdir):
    msgs = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    session.save(tmp_workdir, "parent", msgs, "m")
    child_id = session.branch(tmp_workdir, "parent", at_index=2, label="try-b")
    child = session.load(tmp_workdir, child_id)
    assert child is not None
    assert child["messages"] == msgs[:2]
    assert child["parent_id"] == "parent"
    assert child["branch_label"] == "try-b"


def test_truncate_shortens_messages(tmp_workdir):
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    session.save(tmp_workdir, "sid", msgs, "m")
    out = session.truncate(tmp_workdir, "sid", 1)
    assert out is not None
    assert out["messages"] == msgs[:1]


def test_save_preserves_parent_id_across_updates(tmp_workdir):
    msgs = [{"role": "user", "content": "start"}]
    session.save(tmp_workdir, "sid", msgs, "m", parent_id="ancestor")
    session.save(tmp_workdir, "sid", msgs + [{"role": "assistant", "content": "ok"}], "m")
    loaded = session.load(tmp_workdir, "sid")
    assert loaded is not None
    assert loaded["parent_id"] == "ancestor"
    assert len(loaded["messages"]) == 2
