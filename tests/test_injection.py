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


def test_context_system_prompt_includes_injection(tmp_workdir):
    from riftor.agent.context import Context
    eng = Engagement(tmp_workdir)
    eng.set_template("network")
    MemoryStore(tmp_workdir).add("box at 10.0.0.5 runs redis")
    ctx = Context(lore=False, workdir=tmp_workdir)
    sp = ctx.system_prompt
    assert "box at 10.0.0.5 runs redis" in sp
    assert "NETWORK" in sp


def test_context_without_workdir_unaffected(tmp_workdir):
    from riftor.agent.context import Context
    MemoryStore(tmp_workdir).add("should not appear")
    ctx = Context(lore=False)  # no workdir
    assert "should not appear" not in ctx.system_prompt


def test_corrupt_db_is_blank(tmp_workdir):
    d = tmp_workdir / ".riftor"
    d.mkdir(parents=True, exist_ok=True)
    (d / "engagement.db").write_bytes(b"not a sqlite file")
    assert engagement_injection(tmp_workdir) == ""  # must not raise


def test_injection_creates_nothing_on_empty_workdir(tmp_workdir):
    # Assembling a prompt must not create .riftor/ as a side effect.
    engagement_injection(tmp_workdir)
    assert not (tmp_workdir / ".riftor").exists()


def test_injection_reads_template_without_ddl_churn(tmp_workdir):
    # Sanity: template injection still works after switching to a direct read.
    from riftor.engagement import Engagement
    Engagement(tmp_workdir).set_template("webapp")
    out = engagement_injection(tmp_workdir)
    assert "WEB APPLICATION" in out
