"""Tool behavior: diff preview, finding tools, import dedup, list_hosts."""

from __future__ import annotations

import pytest

from riftor import tools


@pytest.mark.asyncio
async def test_write_then_edit_diff_preview(toolctx, tmp_workdir):
    write = tools.get("write")
    (tmp_workdir / "a.txt").write_text("line one\nline two\n")
    detail = tools.get("edit").confirm_detail(
        {"path": "a.txt", "old_string": "line two", "new_string": "line 2"}, toolctx
    )
    assert detail and "-line two" in detail and "+line 2" in detail

    new_file_detail = write.confirm_detail({"path": "b.txt", "content": "fresh\n"}, toolctx)
    assert "new file" in new_file_detail


@pytest.mark.asyncio
async def test_edit_finding_tool(toolctx):
    eng = toolctx.engagement
    fid = eng.add_finding(title="X", severity="low", host="h")
    r = await tools.get("edit_finding").execute(
        {"id": fid, "severity": "high", "tags": "fp"}, toolctx
    )
    assert not r.is_error
    assert eng.store.get_finding(fid)["severity"] == "high"


@pytest.mark.asyncio
async def test_edit_finding_bad_severity(toolctx):
    fid = toolctx.engagement.add_finding(title="X", severity="low")
    r = await tools.get("edit_finding").execute({"id": fid, "severity": "spicy"}, toolctx)
    assert r.is_error


@pytest.mark.asyncio
async def test_delete_finding_tool(toolctx):
    eng = toolctx.engagement
    fid = eng.add_finding(title="X", severity="low")
    r = await tools.get("delete_finding").execute({"id": fid}, toolctx)
    assert not r.is_error
    r2 = await tools.get("delete_finding").execute({"id": fid}, toolctx)
    assert r2.is_error  # already gone


@pytest.mark.asyncio
async def test_import_scan_dedup(toolctx):
    eng = toolctx.engagement
    out = (
        "Nmap scan report for h (10.0.0.9)\n"
        "PORT STATE SERVICE VERSION\n22/tcp open ssh OpenSSH\n"
    )
    r1 = await tools.get("import_scan").execute({"tool": "nmap", "output": out}, toolctx)
    assert "1 service" in r1.content
    r2 = await tools.get("import_scan").execute({"tool": "nmap", "output": out}, toolctx)
    assert "skipped" in r2.content
    assert len(eng.store.list_services()) == 1


@pytest.mark.asyncio
async def test_list_hosts_tool(toolctx):
    eng = toolctx.engagement
    eng.add_service(host="10.0.0.5", port=443, service="https")
    r = await tools.get("list_hosts").execute({}, toolctx)
    assert "10.0.0.5" in r.content


@pytest.mark.asyncio
async def test_record_finding_dedup_skip(toolctx):
    rf = tools.get("record_finding")
    await rf.execute({"title": "Dup", "severity": "high", "host": "h"}, toolctx)
    r = await rf.execute({"title": "Dup", "severity": "high", "host": "h"}, toolctx)
    assert "skipped" in r.content
    assert toolctx.engagement.findings_count() == 1


@pytest.mark.asyncio
async def test_add_scope_adds_in_scope_targets(toolctx):
    eng = toolctx.engagement
    r = await tools.get("add_scope").execute(
        {"targets": ["admin.example.com", "10.0.0.0/24"],
         "reason": "found in DNS of in-scope example.com"},
        toolctx,
    )
    assert not r.is_error, r.content
    raws = {t.raw for t in eng.scope.in_scope}
    assert "admin.example.com" in raws
    assert "10.0.0.0/24" in raws
    # added in-scope only — nothing landed in out-of-scope
    assert eng.scope.out_of_scope == []


def test_add_scope_tool_metadata():
    tool = tools.get("add_scope")
    assert tool is not None
    assert tool.requires_permission is True
    # must NOT be scope_sensitive (it edits the scope list, doesn't touch a host)
    assert tool.scope_sensitive is False
    props = tool.parameters["properties"]
    assert "targets" in props and "reason" in props
    assert set(tool.parameters["required"]) == {"targets", "reason"}
    prev = tool.preview({"targets": ["a.com", "b.com"], "reason": "why"})
    assert "a.com" in prev and "why" in prev


@pytest.mark.asyncio
async def test_add_scope_no_engagement():
    from riftor.tools.base import ToolContext
    ctx = ToolContext()  # engagement defaults to None
    r = await tools.get("add_scope").execute(
        {"targets": ["x.com"], "reason": "r"}, ctx
    )
    assert r.is_error
    assert "no active engagement" in r.content


@pytest.mark.asyncio
async def test_add_scope_empty_targets(toolctx):
    r = await tools.get("add_scope").execute({"targets": [], "reason": "r"}, toolctx)
    assert r.is_error
    assert "no targets" in r.content


@pytest.mark.asyncio
async def test_add_scope_reports_already_present(toolctx):
    eng = toolctx.engagement
    eng.add_scope("dup.example.com", "in")
    r = await tools.get("add_scope").execute(
        {"targets": ["dup.example.com"], "reason": "r"}, toolctx
    )
    assert not r.is_error
    assert "already in scope" in r.content
    assert sum(t.raw == "dup.example.com" for t in eng.scope.in_scope) == 1


@pytest.mark.asyncio
async def test_add_scope_accepts_scalar_target(toolctx):
    r = await tools.get("add_scope").execute(
        {"targets": "solo.example.com", "reason": "r"}, toolctx
    )
    assert not r.is_error
    assert any(t.raw == "solo.example.com" for t in toolctx.engagement.scope.in_scope)


def test_add_scope_blocked_headless_without_allow_rule():
    # The headless gate (headless.py) denies a requires_permission tool unless an
    # allow-rule exists — so the agent cannot self-scope unattended. Assert via the
    # exact Permissions check headless.py uses:
    #   `tool.requires_permission and not permissions.is_allowed(tool.name, preview)`
    from riftor.safety.permissions import Permissions
    perms = Permissions()  # empty allow-rules + safe default deny
    tool = tools.get("add_scope")
    preview = tool.preview({"targets": ["x.com"], "reason": "r"})
    assert tool.requires_permission is True
    assert not perms.is_allowed(tool.name, preview)   # no allow-rule -> headless denies
    perms.add_allow_rule(tool.name)
    assert perms.is_allowed(tool.name, preview)        # operator opts in -> allowed
