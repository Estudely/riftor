"""Engagement DB merge (#50 collaborative MVP)."""

from __future__ import annotations

from pathlib import Path

from riftor.engagement import Engagement
from riftor.engagement.merge import merge_engagement_db


def test_merge_imports_missing_rows(tmp_path: Path, engagement):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = Engagement(other_dir)
    other.add_scope("shared.example.com", "in")
    other.add_scope("oos.example.com", "out")
    other.store.add_host("shared.example.com")
    other.store.add_service("shared.example.com", 443, "tcp", "https")
    other.store.add_finding(
        title="XSS",
        severity="medium",
        host="shared.example.com",
        evidence="reflected",
    )
    other.close()

    # Local already has one overlapping finding — should be skipped.
    engagement.add_scope("local.example.com", "in")
    engagement.store.add_finding(
        title="XSS",
        severity="medium",
        host="shared.example.com",
        evidence="reflected",
    )

    stats = merge_engagement_db(engagement, other.dir / "engagement.db")
    assert stats.scope_in >= 1
    assert stats.scope_out >= 1
    assert stats.services >= 1
    assert stats.skipped_findings >= 1
    hosts = {h["host"] for h in engagement.store.list_hosts()}
    assert "shared.example.com" in hosts
    ins = {t.raw for t in engagement.scope.in_scope}
    assert "shared.example.com" in ins
    assert "local.example.com" in ins


def test_merge_missing_file_raises(engagement, tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        merge_engagement_db(engagement, tmp_path / "nope.db")
