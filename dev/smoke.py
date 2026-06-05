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
    # redirect the config path so /theme and /config saves never touch the real one
    import riftor.config as cfgmod

    cfgmod.CONFIG_DIR = Path(workdir)
    cfgmod.CONFIG_PATH = Path(workdir) / "config.toml"
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

        orig_wait = app.push_screen_wait
        app.push_screen_wait = fake_wait  # type: ignore[method-assign]
        await app._run_tool(
            ToolCall(id="t1", name="bash", arguments={"command": "curl http://evil.example.net"})
        )
        app.push_screen_wait = orig_wait  # restore so later modals work
        assert seen["warn"] and "evil.example.net" in seen["warn"], seen["warn"]
        assert any(
            m.get("role") == "tool" and "out of scope" in (m.get("content") or "")
            for m in app.context._messages
        ), "expected out-of-scope block fed back to model"

        # show_tool_output gate: when off, the result block is NOT rendered, but
        # the ⛏ call line still is, and the result still reaches the model context.
        from textual.widgets import Static as _Static
        app.config.show_tool_output = False
        app.permissions.allow_for_session("bash")
        before_results = len([w for w in app.query(_Static)
                              if "tool-result" in (w.classes or set())])
        await app._show_tool_result("SECRET-OUTPUT-LINE", is_error=False)
        after_results = len([w for w in app.query(_Static)
                             if "tool-result" in (w.classes or set())])
        assert after_results == before_results, "tool-result must not render when hidden"
        # still revealable on demand via /show
        assert any("SECRET-OUTPUT-LINE" in v for v in app._tool_results.values()), \
            "hidden result must still be registered for /show"
        app.config.show_tool_output = True

        # Phase 7b: a dispatch mounts the live flock pane, updates it during
        # flight, then clears it; the 🐦 status segment reflects worker usage.
        # Offline via RIFTOR_DEMO_RESPONSE. Capture max rows seen mid-flight by
        # spying on the progress callback. Save/restore every global+config
        # mutation so later smoke steps see a clean app (mirrors the scope block).
        import os
        _saved_demo = os.environ.get("RIFTOR_DEMO_RESPONSE")
        _saved_key, _saved_chakla_model = app.config.api_key, app.config.chakla_model
        os.environ["RIFTOR_DEMO_RESPONSE"] = "worker reporting: recon complete"
        app.engagement.add_scope("10.0.0.0/24", "in")
        app.permissions.allow_for_session("dispatch_chakla")
        app.config.api_key = "smoke-key"  # blank worker model reuses main; needs creds
        app.config.chakla_model = ""
        seen_rows = {"max": 0}
        orig_progress = app._on_chakla_progress

        def _spy(event):
            orig_progress(event)
            if app._flock is not None:
                seen_rows["max"] = max(seen_rows["max"], app._flock[1].row_count)

        app.toolctx.progress = _spy
        await app._run_tool(
            ToolCall(id="d1", name="dispatch_chakla",
                     arguments={"tasks": ["recon 10.0.0.5", "recon 10.0.0.6"], "tools": []})
        )
        await pilot.pause()
        assert seen_rows["max"] >= 2, f"expected >=2 flock rows during flight, saw {seen_rows['max']}"
        assert app._flock is None, "flock pane should be cleared after the dispatch"
        assert app.status.chakla_tokens >= 0  # 🐦 usage segment fed (0 ok for demo)
        # restore callback + every mutation so later steps aren't polluted
        app.toolctx.progress = app._on_chakla_progress
        app.config.api_key, app.config.chakla_model = _saved_key, _saved_chakla_model
        if _saved_demo is None:
            os.environ.pop("RIFTOR_DEMO_RESPONSE", None)
        else:
            os.environ["RIFTOR_DEMO_RESPONSE"] = _saved_demo

        # /theme switches the live theme
        inp.value = "/theme void"
        await pilot.press("enter")
        await pilot.pause()
        assert app.theme == "void", app.theme

        # /config opens the modal; escape cancels it
        from riftor.tui.config_screen import ConfigScreen

        inp.value = "/config"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen), type(app.screen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfigScreen)

        # /report writes a file; /sessions lists the auto-saved session
        inp.value = "/report md"
        await pilot.press("enter")
        await pilot.pause()
        assert list((Path(workdir) / ".riftor" / "reports").glob("*.md")), "report not written"
        inp.value = "/sessions"
        await pilot.press("enter")
        await pilot.pause()

        # new QoL commands don't crash and do the right thing
        app.engagement.add_finding(title="Test finding", severity="high", host="10.0.0.1")
        for cmd in (
            "/findings", "/hosts", "/services", "/timeline", "/permissions",
            "/cost", "/audit", "/doctor", "/scope dry", "/scope on",
        ):
            inp.value = cmd
            await pilot.press("enter")
            await pilot.pause()

        # fuzzy "did you mean" on a typo
        inp.value = "/findngs"
        await pilot.press("enter")
        await pilot.pause()

        # input history recall via ↑
        inp.value = "first task"
        await pilot.press("enter")
        await pilot.pause()
        app.action_cancel()
        app.query_one("#prompt", Input).focus()
        await pilot.press("up")
        await pilot.pause()
        assert app.query_one("#prompt", Input).value == "first task", app.query_one("#prompt", Input).value
        app.query_one("#prompt", Input).clear()

        # /export writes an archive
        inp.value = "/export"
        await pilot.press("enter")
        await pilot.pause()
        assert list((Path(workdir) / ".riftor" / "exports").glob("*.zip")), "export not written"

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


def test_parsers() -> None:
    from riftor.engagement.parsers import parse

    nmap = (
        "Nmap scan report for scanme.example.com (10.0.0.5)\n"
        "PORT    STATE  SERVICE VERSION\n"
        "22/tcp  open   ssh     OpenSSH 8.2p1 Ubuntu\n"
        "80/tcp  open   http    nginx 1.18.0\n"
        "443/tcp closed https\n"
    )
    s = parse("nmap", nmap)
    assert len(s.services) == 2, s.services
    assert s.services[0]["host"] == "10.0.0.5" and s.services[0]["port"] == 22
    assert s.services[1]["service"] == "http" and "nginx" in s.services[1]["version"]

    httpx_txt = (
        "https://example.com [200] [Example Domain] [nginx]\n"
        "http://test.example.com:8080 [403] [Forbidden]\n"
    )
    h = parse("httpx", httpx_txt)
    assert len(h.services) == 2
    assert h.services[0]["host"] == "example.com" and h.services[0]["port"] == 443
    assert h.services[1]["port"] == 8080

    hj = parse("httpx", '{"url":"https://api.example.com","status_code":200,"webserver":"nginx"}')
    assert len(hj.services) == 1 and hj.services[0]["host"] == "api.example.com"

    nuclei_txt = (
        "[tech-detect:nginx] [http] [info] https://example.com\n"
        "[CVE-2021-41773] [http] [critical] https://example.com/cgi-bin/x\n"
    )
    n = parse("nuclei", nuclei_txt)
    assert len(n.findings) == 2 and n.findings[1]["severity"] == "critical"

    nj = parse(
        "nuclei",
        '{"template-id":"CVE-2021-41773","info":{"name":"Apache RCE","severity":"critical"},'
        '"host":"https://x","matched-at":"https://x/cgi-bin"}',
    )
    assert len(nj.findings) == 1 and nj.findings[0]["severity"] == "critical"

    print("PARSERS OK")


def test_themes() -> None:
    from riftor.tui.theme import THEMES, css_variable_defaults

    # dark signatures + the new light/mid themes
    assert {"rift", "void", "fracture", "singularity", "dusk", "dawn", "paper"} <= set(THEMES)
    required = {
        "violet", "cyan", "magenta", "danger", "muted", "dim", "faint", "border",
        "user-bg", "user-fg", "assistant-bg", "tool-bg",
    }
    for name, theme in THEMES.items():
        missing = required - set(theme.variables)
        assert not missing, (name, missing)
    assert required <= set(css_variable_defaults())
    # at least one genuinely light theme exists (dark=False)
    assert any(not t.dark for t in THEMES.values()), "expected a light theme"
    assert THEMES["paper"].dark is False and THEMES["rift"].dark is True
    print("THEMES OK")


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

        # import_scan bulk-records parsed services
        before = len(eng.store.list_services())
        r = await tools.get("import_scan").execute(
            {
                "tool": "nmap",
                "output": (
                    "Nmap scan report for h (10.0.0.9)\n"
                    "PORT   STATE SERVICE VERSION\n22/tcp open ssh OpenSSH\n"
                ),
            },
            ctx,
        )
        assert not r.is_error and len(eng.store.list_services()) == before + 1, r.content

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
    test_parsers()
    test_themes()
    test_cvss()
    test_report()
    test_session()
