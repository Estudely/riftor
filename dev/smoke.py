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
    test_scope()
    asyncio.run(test_engagement())
