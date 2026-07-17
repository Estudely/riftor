"""Headless safety gate: with no operator present, _run_tool_headless must
auto-deny requires_permission tools without an allow rule, hard-block out-of-scope
scope-sensitive calls, honor deny rules, and let YOLO bypass all of it.

These are the unattended-mode guardrails — the path a CI/script run takes — so
they get direct coverage rather than relying on the TUI's interactive path.
"""

from __future__ import annotations

import pytest

from riftor.agent.context import Context
from riftor.agent.provider import ToolCall
from riftor.config import Config
from riftor.engagement import Engagement
from riftor.headless import _run_tool_headless
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import Permissions
from riftor.tools import ToolContext


def _last_tool_result(context: Context) -> str:
    for msg in reversed(context.messages):
        if msg.get("role") == "tool":
            return msg.get("content", "")
    raise AssertionError("no tool result recorded")


@pytest.fixture
def gate(tmp_workdir):
    """Build the dependencies _run_tool_headless needs, isolated to tmp."""
    engagement = Engagement(tmp_workdir)
    permissions = Permissions()
    audit = AuditLog(path=tmp_workdir / "audit.jsonl")
    toolctx = ToolContext(workdir=tmp_workdir, engagement=engagement, config=Config())
    context = Context()
    return engagement, permissions, audit, toolctx, context


async def _run(call, gate, *, yolo=False):
    engagement, permissions, audit, toolctx, context = gate
    await _run_tool_headless(
        call, engagement, permissions, audit, toolctx, context, yolo=yolo
    )
    return context


async def test_requires_permission_tool_auto_denied_without_allow_rule(gate):
    # write is requires_permission; no allow rule exists.
    toolctx = gate[3]
    call = ToolCall(id="1", name="write", arguments={"path": "x.txt", "content": "hi"},
                    raw_arguments='{}')
    context = await _run(call, gate)
    msg = _last_tool_result(context)
    assert "denied: headless" in msg
    assert "permissions.toml" in msg
    # and the file was NOT written
    assert not (toolctx.workdir / "x.txt").exists()


async def test_requires_permission_tool_runs_with_allow_rule(tmp_workdir):
    engagement = Engagement(tmp_workdir)
    permissions = Permissions(allow=[{"tool": "write"}])
    audit = AuditLog(path=tmp_workdir / "audit.jsonl")
    toolctx = ToolContext(workdir=tmp_workdir, engagement=engagement, config=Config())
    context = Context()
    call = ToolCall(id="1", name="write", arguments={"path": "x.txt", "content": "hi"},
                    raw_arguments='{}')
    await _run_tool_headless(call, engagement, permissions, audit, toolctx, context, yolo=False)
    msg = _last_tool_result(context)
    assert "denied" not in msg.lower()
    assert (tmp_workdir / "x.txt").read_text() == "hi"


async def test_out_of_scope_scope_sensitive_call_hard_blocked(gate):
    engagement = gate[0]
    engagement.add_scope("example.com", "in")  # bash is scope_sensitive
    call = ToolCall(id="1", name="bash", arguments={"command": "nmap evil.org"},
                    raw_arguments='{}')
    context = await _run(call, gate)
    msg = _last_tool_result(context)
    assert "out of scope" in msg.lower()
    assert "evil.org" in msg


async def test_deny_rule_blocks_in_headless(gate):
    # default deny rules include rm -rf; bash matches one.
    call = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /tmp/x"},
                    raw_arguments='{}')
    context = await _run(call, gate)
    msg = _last_tool_result(context)
    assert "blocked by policy" in msg.lower()


async def test_yolo_bypasses_scope_block(gate):
    engagement = gate[0]
    engagement.add_scope("example.com", "in")
    call = ToolCall(id="1", name="bash", arguments={"command": "echo out-of-scope evil.org"},
                    raw_arguments='{}')
    context = await _run(call, gate, yolo=True)
    msg = _last_tool_result(context)
    assert "out of scope" not in msg.lower()
    assert "blocked" not in msg.lower()


async def test_yolo_bypasses_requires_permission(gate):
    call = ToolCall(id="1", name="write", arguments={"path": "x.txt", "content": "yo"},
                    raw_arguments='{}')
    context = await _run(call, gate, yolo=True)
    msg = _last_tool_result(context)
    assert "denied" not in msg.lower()
    assert (gate[3].workdir / "x.txt").read_text() == "yo"


def test_headless_registers_plugins(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pdir = tmp_path / "riftor" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "demo.py").write_text(
        "from riftor.tools.base import Tool, ToolResult\n"
        "class T(Tool):\n"
        "    name='hello_plugin'; description='d'; parameters={'type':'object','properties':{}}\n"
        "    async def execute(self, args, ctx): return ToolResult('hi')\n"
        "TOOLS=[T()]\n"
    )
    import riftor.tools as tools_pkg

    snap_list, snap_map = list(tools_pkg.ALL_TOOLS), dict(tools_pkg._BY_NAME)
    try:
        tools_pkg.register_plugins(Config())
        assert tools_pkg.get("hello_plugin") is not None
    finally:
        tools_pkg.ALL_TOOLS[:] = snap_list
        tools_pkg._BY_NAME.clear()
        tools_pkg._BY_NAME.update(snap_map)


# --- session checkpointing (#120) ---------------------------------------------

def test_headless_run_checkpoints_session(monkeypatch, tmp_workdir):
    """A completed headless run must persist a resumable session marked complete
    (issue #120). Uses the offline demo mock so no model is called."""
    from riftor.agent import session as sessions
    from riftor.headless import _run

    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "recon complete, no cap")
    cfg = Config(model="anthropic/claude-sonnet-4-6", api_key="sk-demo")

    import asyncio
    rc = asyncio.run(_run(cfg, tmp_workdir, "enumerate the target", None))
    assert rc == 0

    rows = sessions.list_sessions(tmp_workdir)
    assert len(rows) == 1
    assert rows[0]["complete"] is True
    loaded = sessions.load(tmp_workdir, rows[0]["id"])
    assert loaded is not None
    # the user prompt and the assistant answer are both persisted
    assert any(m.get("role") == "user" and "enumerate" in m.get("content", "")
               for m in loaded["messages"])
    assert any(m.get("role") == "assistant" for m in loaded["messages"])


def test_headless_no_incomplete_left_after_success(monkeypatch, tmp_workdir):
    """After a clean run there must be no incomplete (crash) checkpoint lingering."""
    from riftor.agent import session as sessions
    from riftor.headless import _run

    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "done")
    cfg = Config(model="anthropic/claude-sonnet-4-6", api_key="sk-demo")

    import asyncio
    asyncio.run(_run(cfg, tmp_workdir, "scan", None))
    assert sessions.find_incomplete(tmp_workdir) == []


def test_headless_step_limit_returns_nonzero(monkeypatch, tmp_workdir, capsys):
    """Hitting max_steps must exit nonzero with a stderr note (CI-distinguishable)."""
    import asyncio

    from riftor.agent.provider import Provider, ToolCall, Turn
    from riftor.headless import _run

    async def _always_tool(*_a, **_k):
        call = ToolCall(id="c1", name="scope_list", arguments={}, raw_arguments="{}")
        turn = Turn(
            text="",
            tool_calls=[call],
            assistant_message={
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "scope_list", "arguments": "{}"},
                    }
                ],
            },
        )
        yield ("done", turn)

    monkeypatch.setattr(Provider, "stream_turn", _always_tool)
    cfg = Config(model="anthropic/claude-sonnet-4-6", api_key="sk-demo", max_steps=2)

    rc = asyncio.run(_run(cfg, tmp_workdir, "keep going forever", None))
    assert rc == 4
    err = capsys.readouterr().err
    assert "max_steps" in err.lower() or "step" in err.lower()


def test_run_headless_demo_skips_api_key(monkeypatch, tmp_workdir, capsys):
    """RIFTOR_DEMO_RESPONSE must allow --prompt without credentials (make demo-headless)."""
    import os

    from riftor.headless import run_headless

    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "hi from demo")
    for key in list(os.environ):
        if key.endswith("_API_KEY") or key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(key, raising=False)
    cfg = Config(model="anthropic/claude-sonnet-4-6")
    assert not cfg.has_credentials()
    rc = run_headless(cfg, tmp_workdir, prompt="say hi")
    assert rc == 0
    assert "hi from demo" in capsys.readouterr().out
