"""engagement_injection: combined per-engagement prompt text."""

from __future__ import annotations

from riftor.engagement import Engagement
from riftor.engagement.injection import engagement_injection
from riftor.engagement.memory import MemoryStore


def test_none_workdir_is_blank():
    assert engagement_injection(None) == ""


def test_missing_db_is_blank(tmp_workdir):
    # no .riftor/engagement.db yet, no memory.json → empty
    assert engagement_injection(tmp_workdir) == ""


def test_includes_memory(tmp_workdir):
    MemoryStore(tmp_workdir).add("jwt alg=none works", tag="auth")
    out = engagement_injection(tmp_workdir)
    assert "jwt alg=none works" in out


def test_includes_active_template_methodology(tmp_workdir):
    eng = Engagement(tmp_workdir)
    eng.set_template("webapp")
    out = engagement_injection(tmp_workdir)
    assert "WEB APPLICATION" in out


def test_combines_memory_and_template(tmp_workdir):
    eng = Engagement(tmp_workdir)
    eng.set_template("api")
    MemoryStore(tmp_workdir).add("rate limit is 5/s")
    out = engagement_injection(tmp_workdir)
    assert "rate limit is 5/s" in out
    assert "API" in out
