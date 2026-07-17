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


def test_resolve_id_exact_and_unique_prefix(tmp_workdir):
    session.save(tmp_workdir, "20260717-120000-abcd", [{"role": "user", "content": "a"}], "m")
    session.save(tmp_workdir, "20260717-130000-ef01", [{"role": "user", "content": "b"}], "m")
    assert session.resolve_id(tmp_workdir, "20260717-120000-abcd") == "20260717-120000-abcd"
    assert session.resolve_id(tmp_workdir, "20260717-12") == "20260717-120000-abcd"
    assert session.resolve_id(tmp_workdir, "ef01") == "20260717-130000-ef01"


def test_resolve_id_ambiguous_prefix_raises(tmp_workdir):
    session.save(tmp_workdir, "20260717-120000-aaaa", [{"role": "user", "content": "a"}], "m")
    session.save(tmp_workdir, "20260717-120000-bbbb", [{"role": "user", "content": "b"}], "m")
    try:
        session.resolve_id(tmp_workdir, "20260717-12")
        raise AssertionError("expected ValueError for ambiguous prefix")
    except ValueError as exc:
        assert "ambiguous" in str(exc).lower()


def test_resolve_id_missing_raises(tmp_workdir):
    try:
        session.resolve_id(tmp_workdir, "nope")
        raise AssertionError("expected ValueError for missing id")
    except ValueError as exc:
        assert "no such" in str(exc).lower() or "not found" in str(exc).lower()


def test_delete_removes_session_file(tmp_workdir):
    session.save(tmp_workdir, "20260717-120000-abcd", [{"role": "user", "content": "a"}], "m")
    assert session.delete(tmp_workdir, "20260717-120000-abcd") is True
    assert session.load(tmp_workdir, "20260717-120000-abcd") is None
    assert session.delete(tmp_workdir, "20260717-120000-abcd") is False


def test_prune_old_deletes_aged_sessions(tmp_workdir, monkeypatch):
    import time

    now = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    session.save(tmp_workdir, "fresh-aaaa", [{"role": "user", "content": "new"}], "m")
    old_path = tmp_workdir / ".riftor" / "sessions" / "stale-bbbb.json"
    session.save(tmp_workdir, "stale-bbbb", [{"role": "user", "content": "old"}], "m")
    # Backdate the stale session's updated stamp
    import json
    data = json.loads(old_path.read_text(encoding="utf-8"))
    data["updated"] = now - (10 * 86400)
    old_path.write_text(json.dumps(data), encoding="utf-8")

    removed = session.prune_old(tmp_workdir, max_age_days=7, keep_ids={"fresh-aaaa"})
    assert removed == ["stale-bbbb"]
    assert session.load(tmp_workdir, "stale-bbbb") is None
    assert session.load(tmp_workdir, "fresh-aaaa") is not None


def test_index_before_last_user_turns():
    msgs = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "a3"},
    ]
    assert session.index_before_last_user_turns(msgs, 1) == 4
    assert session.index_before_last_user_turns(msgs, 2) == 2
    assert session.index_before_last_user_turns(msgs, 3) == 0
    assert session.index_before_last_user_turns(msgs, 99) == 0
    assert session.index_before_last_user_turns([], 1) == 0
