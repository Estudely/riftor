"""Tests for the Baaj/Chakla subagent feature (all offline)."""
from __future__ import annotations

import asyncio

from riftor import tools as tools_mod
from riftor.agent.provider import Provider, ToolCall
from riftor.agent.subagent import ChaklaResult, run_chakla, _run_chakla_tool, worker_schemas
from riftor.config import Config
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import Permissions
from riftor.terminology import terminology
from riftor.tools.base import ToolContext
from riftor.tools.subagent import DispatchChaklaTool


def test_config_has_chakla_defaults():
    cfg = Config()
    assert cfg.chakla_model == "anthropic/claude-haiku-4-5-20251001"
    assert cfg.chakla_max_workers == 5
    assert cfg.chakla_max_steps == 8
    assert cfg.chakla_timeout_s == 300
    assert cfg.label_main == "Baaj"
    assert cfg.label_worker == "Chakla"


def test_config_toml_roundtrips_chakla_fields():
    cfg = Config(chakla_model="anthropic/claude-haiku-4-5-20251001", chakla_max_workers=3)
    toml = cfg._to_toml()
    assert 'chakla_model = "anthropic/claude-haiku-4-5-20251001"' in toml
    assert "chakla_max_workers = 3" in toml
    assert "chakla_max_steps = 8" in toml
    assert "chakla_timeout_s = 300" in toml
    assert 'label_main = "Baaj"' in toml
    assert 'label_worker = "Chakla"' in toml


def test_terminology_defaults():
    t = terminology(Config())
    assert t["main"] == "Baaj"
    assert t["worker"] == "Chakla"
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"


def test_terminology_respects_renamed_labels():
    t = terminology(Config(label_main="Hawk", label_worker="Finch"))
    assert t["main"] == "Hawk"
    assert t["worker"] == "Finch"
    # emoji are fixed branding; only the text labels are renameable
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"


def test_toolcontext_new_fields_default_to_none(tmp_workdir, engagement):
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement)
    assert ctx.config is None
    assert ctx.permissions is None
    assert ctx.audit is None
    assert ctx.yolo is False


def _worker_provider(cfg: Config) -> Provider:
    return Provider(cfg.model_copy(update={"model": cfg.chakla_model}))


async def _run_one(task, *, cfg, engagement, grant, yolo=False, monkeypatch_env):
    # RIFTOR_DEMO_RESPONSE makes the provider stream canned text with no network.
    monkeypatch_env("RIFTOR_DEMO_RESPONSE", "worker reporting: recon complete, no open ports")
    toolctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent,
        engagement=engagement,
        config=cfg,
        permissions=Permissions(),
        audit=AuditLog(),
        yolo=yolo,
    )
    return await run_chakla(
        task,
        worker_provider=_worker_provider(cfg),
        toolctx=toolctx,
        permissions=toolctx.permissions,
        audit=toolctx.audit,
        max_steps=cfg.chakla_max_steps,
        yolo=yolo,
        db_lock=asyncio.Lock(),
        grant=grant,
    )


def test_run_chakla_returns_result_with_text(tmp_workdir, engagement, monkeypatch):
    cfg = Config()
    result = asyncio.run(
        _run_one(
            "recon 10.0.0.5",
            cfg=cfg,
            engagement=engagement,
            grant=set(),
            monkeypatch_env=monkeypatch.setenv,
        )
    )
    assert isinstance(result, ChaklaResult)
    assert result.status == "done"
    assert "recon complete" in result.text
    assert result.error is None


def _ctx(cfg, engagement):
    return tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )


def test_worker_schemas_exclude_dispatch():
    names = [s["function"]["name"] for s in worker_schemas()]
    assert "dispatch_chakla" not in names


def test_worker_readonly_tool_runs_without_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c1", name="scope_list", arguments={})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant=set())
    )
    assert "[denied]" not in content


def test_worker_bash_denied_without_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c2", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant=set())
    )
    assert "[denied]" in content


def test_worker_bash_allowed_with_grant(tmp_workdir, engagement):
    cfg = Config()
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c3", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[denied]" not in content
    assert "hi" in content


def test_worker_deny_rule_wins_over_grant(tmp_workdir, engagement):
    cfg = Config()
    perms = Permissions(deny=[{"tool": "bash"}])
    ctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=perms, audit=AuditLog(),
    )
    call = ToolCall(id="c4", name="bash", arguments={"command": "echo hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, perms, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[blocked by policy]" in content


def test_worker_out_of_scope_hard_blocked(tmp_workdir, engagement):
    cfg = Config()
    engagement.scope.add("10.0.0.0/24", "in")
    engagement.enforce = True
    ctx = _ctx(cfg, engagement)
    call = ToolCall(id="c5", name="bash", arguments={"command": "nmap 8.8.8.8"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, ctx.permissions, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[blocked: out of scope]" in content


def test_dispatch_requires_config(tmp_workdir, engagement):
    tool = DispatchChaklaTool()
    bare = tools_mod.ToolContext(workdir=tmp_workdir, engagement=engagement)
    res = asyncio.run(tool.execute({"tasks": ["recon"]}, bare))
    assert res.is_error
    assert "unavailable" in res.content


def test_dispatch_runs_workers_and_aggregates(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done: nothing notable")
    cfg = Config()
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(), yolo=False,
    )
    res = asyncio.run(tool.execute({"tasks": ["recon A", "recon B"]}, ctx))
    assert not res.is_error
    assert "2" in res.content  # mentions 2 workers
    assert "recon A" in res.content
    assert "recon B" in res.content


def test_dispatch_clamps_to_max_workers(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_max_workers=2)
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(tool.execute({"tasks": ["a", "b", "c", "d"]}, ctx))
    assert not res.is_error
    assert "clamped" in res.content.lower() or "capped" in res.content.lower()


def test_dispatch_tool_is_registered():
    names = [t.name for t in tools_mod.all_tools()]
    assert "dispatch_chakla" in names
    # registered before the mutating core tools (write/edit/bash)
    assert names.index("dispatch_chakla") < names.index("bash")


def test_dispatch_timeout_is_reported(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_timeout_s=1)
    tool = DispatchChaklaTool()
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )

    # Patch run_chakla to hang, so wait_for fires the timeout path.
    import riftor.tools.subagent as sub

    async def _hang(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(sub, "run_chakla", _hang)
    res = asyncio.run(tool.execute({"tasks": ["slow task"]}, ctx))
    assert not res.is_error
    assert "timed out" in res.content


def test_headless_toolctx_carries_config(tmp_workdir):
    # Build the headless toolctx the way run_headless does and confirm the new
    # fields are populated so dispatch_chakla is usable end-to-end.
    from riftor.engagement import Engagement
    from riftor.tools.base import ToolContext as TC
    from riftor.safety.permissions import Permissions as P
    from riftor.safety.audit import AuditLog as A

    eng = Engagement(tmp_workdir)
    cfg = Config()
    ctx = TC(workdir=tmp_workdir, engagement=eng, max_result_chars=cfg.max_result_chars,
             config=cfg, permissions=P(), audit=A(), yolo=False)
    assert ctx.config is cfg
    assert ctx.permissions is not None
    assert ctx.audit is not None


def test_statusbar_has_chakla_usage_setter():
    from riftor.tui.widgets import StatusBar
    bar = StatusBar("anthropic/claude-sonnet-4-6")
    # refresh_bar() raises NoActiveAppError when the widget is not mounted;
    # the setter must set the fields BEFORE calling refresh_bar so the values
    # are visible even if the render fails.
    try:
        bar.set_chakla_usage(1500, 0.012)
    except Exception:
        pass  # NoActiveAppError is expected on an unmounted widget
    assert bar.chakla_tokens == 1500
    assert bar.chakla_cost == 0.012


def test_config_screen_result_keys_persist():
    # Simulate the dict ConfigScreen.dismiss returns, then apply it like _open_config.
    cfg = Config()
    result = {
        "model": cfg.model, "provider": "anthropic", "api_base": None,
        "temperature": 0.3, "max_tokens": 2048, "theme": "rift", "lore": True,
        "chakla_model": "anthropic/claude-haiku-4-5-20251001",
        "label_main": "Hawk", "label_worker": "Finch",
    }
    cfg.chakla_model = result["chakla_model"]
    cfg.label_main = result["label_main"]
    cfg.label_worker = result["label_worker"]
    assert cfg.label_main == "Hawk"
    assert 'label_main = "Hawk"' in cfg._to_toml()


def test_system_prompt_mentions_dispatch():
    from riftor.agent.context import _load_system_prompt
    prompt = _load_system_prompt()
    assert "dispatch_chakla" in prompt
