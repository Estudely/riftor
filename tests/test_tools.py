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
