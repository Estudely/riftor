"""Headless smoke test for the riftor TUI (no network / no model calls).

Verifies the app composes, the Rift theme loads, and local slash-commands work.
Run: uv run python dev/smoke.py
"""

import asyncio
import tempfile
from pathlib import Path

from riftor.config import Config
from riftor.tui.app import RiftorApp
from riftor.tui.widgets import Banner, StatusBar
from textual.widgets import Input, Markdown, Static


async def main() -> None:
    cfg = Config(model="ollama_chat/smoke", api_base="http://localhost:11434", lore=True)
    workdir = tempfile.mkdtemp(prefix="riftor-smoke-")
    app = RiftorApp(cfg, workdir=Path(workdir))
    async with app.run_test() as pilot:
        # composes with the core widgets
        assert app.query_one(Banner)
        assert app.query_one(StatusBar)
        assert app.query_one("#chat")
        inp = app.query_one("#prompt", Input)

        # /help renders a Markdown block
        inp.value = "/help"
        await pilot.press("enter")
        await pilot.pause()
        assert len(list(app.query(Markdown))) == 1, "expected /help to render markdown"

        # /model with no arg posts a note; switching updates the status bar
        inp.value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        inp.value = "/model ollama_chat/other"
        await pilot.press("enter")
        await pilot.pause()
        assert app.status.model == "ollama_chat/other"

        # /stage moves through the RIFT stages (by name or letter)
        assert app.status.stage == "R"
        inp.value = "/stage intrusion"
        await pilot.press("enter")
        await pilot.pause()
        assert app.status.stage == "I", app.status.stage
        inp.value = "/stage T"
        await pilot.press("enter")
        await pilot.pause()
        assert app.status.stage == "T", app.status.stage

        # /lore toggles persona
        assert app.config.lore is True
        inp.value = "/lore"
        await pilot.press("enter")
        await pilot.pause()
        assert app.config.lore is False

        # a normal user message is added (worker will try to stream; we cancel it)
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        app.action_cancel()
        assert any("user" in (w.classes or set()) for w in app.query(Static))

        # scope enforcement: an out-of-scope bash call is blocked (operator denies)
        from riftor.agent.provider import ToolCall

        app.engagement.add_scope("127.0.0.1", "in")
        app.permissions.allow_for_session("bash")  # ensure scope, not permission, is the gate
        seen = {"warn": None}

        async def fake_wait(screen):
            seen["warn"] = list(getattr(screen, "scope_warning", []) or [])
            return "deny"

        app.push_screen_wait = fake_wait  # type: ignore[method-assign]
        await app._run_tool(
            ToolCall(id="t1", name="bash", arguments={"command": "curl http://evil.example.net"})
        )
        assert seen["warn"] and "evil.example.net" in seen["warn"], seen["warn"]
        assert any(
            m.get("role") == "tool" and "out of scope" in (m.get("content") or "")
            for m in app.context._messages
        ), "expected out-of-scope block fed back to model"

        # /report writes a file; /sessions lists the auto-saved session
        inp.value = "/report md"
        await pilot.press("enter")
        await pilot.pause()
        assert list((Path(workdir) / ".riftor" / "reports").glob("*.md")), "report not written"
        inp.value = "/sessions"
        await pilot.press("enter")
        await pilot.pause()

        # /clear empties the chat
        inp.value = "/clear"
        await pilot.press("enter")
        await pilot.pause()
        assert len(list(app.query(Markdown))) == 0

    print("SMOKE OK")


async def test_tools() -> None:
    """Exercise the core tools offline (no model involved)."""
    from riftor import tools
    from riftor.tools import ToolContext

    with tempfile.TemporaryDirectory() as d:
        ctx = ToolContext(workdir=Path(d))

        r = await tools.get("write").execute(
            {"path": "a.txt", "content": "hello rift\nsecond line\n"}, ctx
        )
        assert not r.is_error, r.content

        r = await tools.get("read").execute({"path": "a.txt"}, ctx)
        assert "hello rift" in r.content, r.content

        r = await tools.get("edit").execute(
            {"path": "a.txt", "old_string": "hello", "new_string": "opened"}, ctx
        )
        assert not r.is_error, r.content
        r = await tools.get("read").execute({"path": "a.txt"}, ctx)
        assert "opened rift" in r.content, r.content

        r = await tools.get("glob").execute({"pattern": "*.txt"}, ctx)
        assert "a.txt" in r.content, r.content

        r = await tools.get("grep").execute({"pattern": "opened"}, ctx)
        assert "opened" in r.content, r.content

        r = await tools.get("bash").execute({"command": "echo rift-ok"}, ctx)
        assert "rift-ok" in r.content, r.content

        # edit must refuse a missing string
        r = await tools.get("edit").execute(
            {"path": "a.txt", "old_string": "nope", "new_string": "x"}, ctx
        )
        assert r.is_error, "edit should fail on missing old_string"

        # schemas are well-formed for litellm
        for s in tools.schemas():
            assert s["type"] == "function" and s["function"]["name"]

    print("TOOLS OK")


def test_cvss() -> None:
    from riftor.engagement.cvss import base_score, severity_from_score

    assert base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert severity_from_score(9.8) == "critical"
    # scope-changed XSS, well-known 6.1 medium
    assert base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N") == 6.1
    assert severity_from_score(6.1) == "medium"
    assert base_score("not-a-vector") is None
    assert severity_from_score(0.0) == "info"
    print("CVSS OK")


def test_report() -> None:
    from riftor.engagement import Engagement
    from riftor.engagement.report import build_html, build_markdown, report_data, write_reports

    with tempfile.TemporaryDirectory() as d:
        eng = Engagement(Path(d))
        eng.add_scope("example.com", "in")
        eng.add_service(host="10.0.0.5", port=443, service="https", version="nginx")
        eng.add_finding(
            title="SQL Injection",
            severity="high",
            host="10.0.0.5",
            evidence="' OR 1=1 --",
            recommendation="Use parameterized queries",
            cvss="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        )

        data = report_data(eng)
        assert data["findings"][0]["cvss_score"] == 9.8

        md = build_markdown(data)
        for needle in ("SQL Injection", "9.8", "10.0.0.5", "Use parameterized queries"):
            assert needle in md, needle

        html = build_html(data)
        assert "<html" in html and "SQL Injection" in html and "9.8" in html

        paths = write_reports(eng, "both")
        assert len(paths) == 2 and all(p.exists() for p in paths)

    print("REPORT OK")


def test_session() -> None:
    from riftor.agent import session

    with tempfile.TemporaryDirectory() as d:
        wd = Path(d)
        msgs = [{"role": "user", "content": "enumerate the host"}, {"role": "assistant", "content": "ok"}]
        path = session.save(wd, "20260101-000000", msgs, "anthropic/claude-sonnet-4-6")
        assert path.exists()

        loaded = session.load(wd, "20260101-000000")
        assert loaded["messages"] == msgs
        assert loaded["title"] == "enumerate the host"

        listed = session.list_sessions(wd)
        assert listed and listed[0]["id"] == "20260101-000000"
        assert session.latest(wd)["messages"] == msgs

    print("SESSION OK")


def test_repair() -> None:
    """A dangling tool_use (interrupted turn) gets a synthetic result, in order."""
    from riftor.agent.context import Context

    ctx = Context(lore=False)
    ctx.add_user("scan it")
    ctx.add_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        }
    )
    ctx.add_tool_result("a", "done A")
    # tool 'b' never got a result; then a new user turn arrives (the interruption)
    ctx.add_user("you got cut off")

    assert ctx.repair() == 1
    msgs = ctx._messages
    ai = next(i for i, m in enumerate(msgs) if m.get("role") == "assistant" and m.get("tool_calls"))
    assert msgs[ai + 1]["role"] == "tool" and msgs[ai + 1]["tool_call_id"] == "a"
    assert msgs[ai + 2]["role"] == "tool" and msgs[ai + 2]["tool_call_id"] == "b"
    assert msgs[ai + 3]["role"] == "user"  # synthetic result inserted *before* next user msg
    assert ctx.repair() == 0  # idempotent

    print("REPAIR OK")


def test_scope() -> None:
    """Scope matching + host extraction (no model)."""
    from riftor.engagement.scope import Scope, extract_hosts

    s = Scope()
    s.add("example.com", "in")
    s.add("10.0.0.0/24", "in")
    s.add("admin.example.com", "out")

    assert s.is_in_scope("example.com")
    assert s.is_in_scope("api.example.com")  # subdomain in scope
    assert s.is_in_scope("10.0.0.5")
    assert not s.is_in_scope("10.0.1.5")  # outside CIDR
    assert not s.is_in_scope("evil.com")  # not in scope
    assert not s.is_in_scope("admin.example.com")  # out-of-scope overrides

    bad = s.violations("nmap -sV 10.0.0.5 evil.com https://api.example.com/x")
    assert "evil.com" in bad
    assert "10.0.0.5" not in bad and "api.example.com" not in bad

    hosts = extract_hosts("cat config.py && nmap scanme.example.com 1.2.3.4")
    assert "scanme.example.com" in hosts and "1.2.3.4" in hosts
    assert "config.py" not in hosts  # local file, not a target

    print("SCOPE OK")


async def test_engagement() -> None:
    """Engagement tools + persistence (no model)."""
    from riftor import tools
    from riftor.engagement import Engagement
    from riftor.tools import ToolContext

    with tempfile.TemporaryDirectory() as d:
        eng = Engagement(Path(d))
        ctx = ToolContext(workdir=Path(d), engagement=eng)

        r = await tools.get("set_stage").execute({"stage": "I"}, ctx)
        assert not r.is_error and eng.stage == "I", r.content

        r = await tools.get("record_service").execute(
            {"host": "10.0.0.5", "port": 443, "service": "https", "version": "nginx"}, ctx
        )
        assert not r.is_error, r.content

        r = await tools.get("record_finding").execute(
            {"title": "Exposed admin panel", "severity": "high", "host": "10.0.0.5"}, ctx
        )
        assert not r.is_error and eng.findings_count() == 1, r.content

        r = await tools.get("scope_list").execute({}, ctx)
        assert "in-scope" in r.content

        # persistence: a fresh Engagement on the same dir restores stage
        eng2 = Engagement(Path(d))
        assert eng2.stage == "I", eng2.stage

    print("ENGAGEMENT OK")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(test_tools())
    test_repair()
    test_scope()
    asyncio.run(test_engagement())
    test_cvss()
    test_report()
    test_session()
