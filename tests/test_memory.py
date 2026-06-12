"""MemoryStore: per-engagement free-form notes in .riftor/memory.json."""

from __future__ import annotations

from riftor.engagement.memory import MemoryItem, MemoryStore


def test_add_and_list(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    entry = store.add("target uses JWT alg=none", tag="auth")
    assert isinstance(entry, MemoryItem)
    assert entry.id and entry.tag == "auth"
    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["text"] == "target uses JWT alg=none"
    assert rows[0]["source"] == "agent"


def test_add_dedups_on_text_and_tag(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    a = store.add("same fact", tag="x")
    b = store.add("SAME FACT", tag="x")  # case-insensitive dedup
    assert a.id == b.id
    assert len(store.list()) == 1


def test_add_empty_text_raises(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    import pytest
    with pytest.raises(ValueError):
        store.add("   ")


def test_remove_and_clear(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    e = store.add("a")
    store.add("b")
    assert store.remove(e.id) is True
    assert store.remove("nope") is False
    assert len(store.list()) == 1
    store.clear()
    assert store.list() == []


def test_malformed_file_degrades_to_empty(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    store.path.write_text("{ not json")
    assert store.list() == []


def test_format_for_prompt_caps_and_tags(tmp_workdir):
    store = MemoryStore(tmp_workdir)
    store.add("plain fact")
    store.add("tagged fact", tag="creds")
    out = store.format_for_prompt(max_items=50)
    assert "## MEMORY (durable notes for this engagement)" in out
    assert "- plain fact" in out
    assert "- [creds] tagged fact" in out


def test_format_for_prompt_empty_is_blank(tmp_workdir):
    assert MemoryStore(tmp_workdir).format_for_prompt() == ""
