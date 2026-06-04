"""New-feature coverage: lessons store, hypotheses CRUD, finding confidence,
and the record_lesson / record_hypothesis tools. Everything runs offline."""

from __future__ import annotations

import pytest

from riftor import tools
from riftor.engagement.lessons import Lesson, LessonStore


# -- LessonStore ---------------------------------------------------------------

@pytest.fixture
def lessons(tmp_workdir):
    # Explicit path keeps tests off the real ~/.config/riftor/lessons.json.
    return LessonStore(tmp_workdir / "lessons.json")


def test_lesson_add_and_list(lessons):
    entry = lessons.add("testing JWT", "check alg=none first", source="operator")
    assert isinstance(entry, Lesson)
    assert entry.id and entry.trigger == "testing JWT"
    rows = lessons.list()
    assert len(rows) == 1
    assert rows[0]["lesson"] == "check alg=none first"
    assert rows[0]["source"] == "operator"


def test_lesson_dedup_same_trigger_and_text(lessons):
    a = lessons.add("scanning ports", "use -sV", source="operator")
    b = lessons.add("Scanning Ports", "USE -sV", source="agent")  # case-insensitive dup
    assert a.id == b.id  # returned the existing entry
    assert len(lessons.list()) == 1


def test_lesson_distinct_entries_not_deduped(lessons):
    lessons.add("a", "lesson one")
    lessons.add("a", "lesson two")  # same trigger, different text → kept
    assert len(lessons.list()) == 2


def test_lesson_remove(lessons):
    e = lessons.add("trig", "text")
    assert lessons.remove(e.id) is True
    assert lessons.list() == []
    assert lessons.remove(e.id) is False  # already gone


def test_lesson_requires_some_content(lessons):
    with pytest.raises(ValueError):
        lessons.add("", "")


def test_lesson_format_for_prompt(lessons):
    lessons.add("testing JWT", "check alg=none", source="operator")
    lessons.add("", "always confirm OOB", source="agent")
    text = lessons.format_for_prompt()
    assert "LESSONS" in text
    assert "WHEN testing JWT → check alg=none (operator-taught)" in text
    assert "always confirm OOB (agent-taught)" in text


def test_lesson_format_empty_is_blank(lessons):
    assert lessons.format_for_prompt() == ""


def test_lesson_corrupt_file_degrades(tmp_workdir):
    path = tmp_workdir / "lessons.json"
    path.write_text("{ this is not valid json")
    store = LessonStore(path)
    assert store.list() == []  # never raises
    assert store.format_for_prompt() == ""


# -- hypotheses CRUD -----------------------------------------------------------

def test_hypothesis_add_and_list(engagement):
    hid = engagement.store.add_hypothesis("SSRF in /webhook", rationale="reflects URL")
    assert hid > 0
    rows = engagement.store.list_hypotheses()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["statement"] == "SSRF in /webhook"


def test_hypothesis_resolve(engagement):
    hid = engagement.store.add_hypothesis("idor on /users")
    assert engagement.store.resolve_hypothesis(hid, "refuted", "auth enforced") is True
    row = engagement.store.list_hypotheses("refuted")[0]
    assert row["id"] == hid and row["status"] == "refuted"
    assert engagement.store.count_hypotheses("open") == 0


def test_hypothesis_resolve_bad_status_rejected(engagement):
    hid = engagement.store.add_hypothesis("x")
    assert engagement.store.resolve_hypothesis(hid, "maybe") is False
    assert engagement.store.list_hypotheses("open")[0]["id"] == hid


def test_hypothesis_resolve_missing_id(engagement):
    assert engagement.store.resolve_hypothesis(9999, "confirmed") is False


def test_hypothesis_count_by_status(engagement):
    engagement.store.add_hypothesis("a")
    engagement.store.add_hypothesis("b")
    h = engagement.store.add_hypothesis("c")
    engagement.store.resolve_hypothesis(h, "confirmed")
    assert engagement.store.count_hypotheses("open") == 2
    assert engagement.store.count_hypotheses("confirmed") == 1


# -- finding confidence + verification_method ----------------------------------

def test_add_finding_persists_confidence(engagement):
    fid = engagement.store.add_finding(
        title="RCE", severity="critical", confidence=9,
        verification_method="OOB callback",
    )
    row = engagement.store.get_finding(fid)
    assert row["confidence"] == 9
    assert row["verification_method"] == "OOB callback"


def test_add_finding_confidence_defaults_null(engagement):
    fid = engagement.store.add_finding(title="X", severity="low")
    row = engagement.store.get_finding(fid)
    assert row["confidence"] is None  # unset stays NULL, not 0


def test_update_finding_confidence(engagement):
    fid = engagement.store.add_finding(title="X", severity="high")
    assert engagement.store.update_finding(fid, confidence=8, verification_method="canary")
    row = engagement.store.get_finding(fid)
    assert row["confidence"] == 8 and row["verification_method"] == "canary"


# -- tools ---------------------------------------------------------------------

async def test_record_finding_tool_with_confidence(toolctx):
    r = await tools.get("record_finding").execute(
        {"title": "SQLi", "severity": "high", "host": "h",
         "evidence": "error-based extraction of version()",
         "confidence": 8, "verification_method": "exact value match"},
        toolctx,
    )
    assert not r.is_error
    row = toolctx.engagement.store.list_findings()[0]
    assert row["confidence"] == 8
    assert row["verification_method"] == "exact value match"


async def test_record_finding_confidence_clamped(toolctx):
    await tools.get("record_finding").execute(
        {"title": "Over", "severity": "low", "confidence": 99}, toolctx
    )
    assert toolctx.engagement.store.list_findings()[0]["confidence"] == 10


async def test_record_finding_confidence_invalid_is_null(toolctx):
    await tools.get("record_finding").execute(
        {"title": "Bad", "severity": "low", "confidence": "high"}, toolctx
    )
    assert toolctx.engagement.store.list_findings()[0]["confidence"] is None


async def test_edit_finding_tool_sets_confidence(toolctx):
    fid = toolctx.engagement.add_finding(title="X", severity="high")
    r = await tools.get("edit_finding").execute(
        {"id": fid, "confidence": 7, "verification_method": "timing delta"}, toolctx
    )
    assert not r.is_error
    row = toolctx.engagement.store.get_finding(fid)
    assert row["confidence"] == 7 and row["verification_method"] == "timing delta"


async def test_record_hypothesis_tool(toolctx):
    r = await tools.get("record_hypothesis").execute(
        {"statement": "SSRF possible", "rationale": "URL reflected"}, toolctx
    )
    assert not r.is_error
    assert toolctx.engagement.store.count_hypotheses("open") == 1


async def test_resolve_hypothesis_tool(toolctx):
    hid = toolctx.engagement.store.add_hypothesis("lead")
    r = await tools.get("resolve_hypothesis").execute(
        {"id": hid, "status": "confirmed", "rationale": "proven"}, toolctx
    )
    assert not r.is_error
    assert toolctx.engagement.store.list_hypotheses("confirmed")[0]["id"] == hid


async def test_list_hypotheses_tool_filters(toolctx):
    eng = toolctx.engagement
    h = eng.store.add_hypothesis("open one")
    eng.store.add_hypothesis("another open")
    eng.store.resolve_hypothesis(h, "refuted")
    r = await tools.get("list_hypotheses").execute({"status": "open"}, toolctx)
    assert "another open" in r.content and "open one" not in r.content


async def test_load_skill_missing_returns_listing_not_error(toolctx):
    # No skills ship by default; the tool should degrade gracefully, not error.
    r = await tools.get("load_skill").execute({"name": "recon"}, toolctx)
    assert not r.is_error
    assert "not found" in r.content
