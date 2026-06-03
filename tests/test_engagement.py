"""Engagement store: dedup, edit/delete, tags/notes, activity log, export."""

from __future__ import annotations


def test_finding_dedup_skip(engagement):
    fid1, a1 = engagement.add_finding_dedup(
        dedup="skip", title="SQLi", severity="high", host="10.0.0.5", evidence="x"
    )
    fid2, a2 = engagement.add_finding_dedup(
        dedup="skip", title="SQLi", severity="high", host="10.0.0.5", evidence="x"
    )
    assert a1 == "added" and a2 == "skipped"
    assert fid1 == fid2
    assert engagement.findings_count() == 1


def test_finding_dedup_allow_all(engagement):
    engagement.add_finding_dedup(dedup="allow-all", title="X", severity="low", host="h")
    engagement.add_finding_dedup(dedup="allow-all", title="X", severity="low", host="h")
    assert engagement.findings_count() == 2


def test_finding_dedup_merge(engagement):
    fid, _ = engagement.add_finding_dedup(
        dedup="skip", title="X", severity="low", host="h", evidence="e"
    )
    fid2, action = engagement.add_finding_dedup(
        dedup="merge", title="X", severity="low", host="h", evidence="e",
        recommendation="patch it",
    )
    assert action == "merged" and fid2 == fid
    row = engagement.store.get_finding(fid)
    assert row["recommendation"] == "patch it"


def test_edit_and_delete_finding(engagement):
    fid = engagement.add_finding(title="Weak TLS", severity="medium", host="h")
    assert engagement.store.update_finding(fid, severity="high", tags="needs-validation")
    row = engagement.store.get_finding(fid)
    assert row["severity"] == "high" and row["tags"] == "needs-validation"
    assert engagement.store.delete_finding(fid)
    assert engagement.store.get_finding(fid) is None
    assert not engagement.store.delete_finding(fid)  # already gone


def test_service_dedup(engagement):
    _id, a1 = engagement.add_service_dedup(dedup="skip", host="h", port=443, proto="tcp")
    _id2, a2 = engagement.add_service_dedup(dedup="skip", host="h", port=443, proto="tcp")
    assert a1 == "added" and a2 == "skipped"


def test_activity_log(engagement):
    engagement.add_scope("example.com", "in")
    engagement.set_stage("I")
    engagement.add_finding(title="X", severity="low")
    events = {e["event"] for e in engagement.store.list_activity()}
    assert {"scope_add", "stage", "finding_add"} <= events


def test_scope_import_export(engagement):
    added_in, added_out = engagement.import_scope(
        "example.com\n# a comment\n10.0.0.0/24\nout:admin.example.com\n"
    )
    assert added_in == 2 and added_out == 1
    text = engagement.export_scope()
    assert "example.com" in text and "out:admin.example.com" in text


def test_export_dict_roundtrip(engagement):
    engagement.add_scope("example.com", "in")
    engagement.add_finding(title="X", severity="high", host="h")
    snap = engagement.store.export_dict()
    assert snap["findings"][0]["title"] == "X"
    assert {"target": "example.com", "mode": "in"} in snap["scope"]
