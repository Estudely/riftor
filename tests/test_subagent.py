"""Tests for the Baaj/Chakla subagent feature (all offline)."""
from __future__ import annotations

import asyncio

from riftor import tools as tools_mod
from riftor.agent.provider import Provider, ToolCall
from riftor.agent.subagent import ChaklaResult, run_chakla, _run_chakla_tool, worker_schemas
from riftor.config import Config, ProviderCreds
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import Permissions
from riftor.terminology import terminology
from riftor.tools.base import ToolContext
from riftor.tools.subagent import DispatchChaklaTool


def test_config_has_chakla_defaults():
    cfg = Config()
    assert cfg.chakla_model == ""
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
    return Provider(cfg.model_copy(update={"model": cfg.chakla_model or cfg.model}))


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
    cfg = Config(api_key="test-key")  # blank worker reuses main model; needs creds
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
    cfg = Config(chakla_max_workers=2, api_key="test-key")
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
    cfg = Config(chakla_timeout_s=1, api_key="test-key")
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
    from riftor.config import ProviderCreds
    cfg = Config()
    result = {
        "model": cfg.model, "provider": "anthropic", "api_base": None,
        "temperature": 0.3, "max_tokens": 2048, "theme": "rift", "lore": True,
        "chakla_model": "anthropic/claude-haiku-4-5-20251001",
        "chakla_provider": "anthropic", "api_key": "sk-anth",
        "label_main": "Hawk", "label_worker": "Finch",
    }
    # Mirror _open_config: persist worker model + main provider creds.
    cfg.chakla_model = result.get("chakla_model", cfg.chakla_model)
    cfg.label_main = result["label_main"]
    cfg.label_worker = result["label_worker"]
    provider = result.get("provider")
    if provider:
        entry = cfg.providers.get(provider) or ProviderCreds()
        if result.get("api_base") is not None:
            entry.api_base = result["api_base"]
        if result.get("api_key"):
            entry.api_key = result["api_key"]
        if entry.api_key or entry.api_base:
            cfg.providers[provider] = entry
    # Worker creds block (worker provider == main here, so main block covered it).
    w_provider = result.get("chakla_provider")
    if w_provider and w_provider != provider:
        w_entry = cfg.providers.get(w_provider) or ProviderCreds()
        if result.get("api_base") is not None:
            w_entry.api_base = result["api_base"]
        if result.get("api_key"):
            w_entry.api_key = result["api_key"]
        if w_entry.api_key or w_entry.api_base:
            cfg.providers[w_provider] = w_entry

    assert cfg.label_main == "Hawk"
    assert 'label_main = "Hawk"' in cfg._to_toml()
    assert cfg.chakla_model == "anthropic/claude-haiku-4-5-20251001"
    # The worker model's creds resolve from the stored provider table.
    assert cfg.creds_for(cfg.chakla_model)[0] == "sk-anth"


def test_worker_picker_creds_resolve_for_different_provider():
    # Main model on anthropic, worker pointed at a DIFFERENT provider (openai):
    # the worker provider gets the shared key stored and creds_for resolves it.
    from riftor.config import ProviderCreds
    cfg = Config(model="anthropic/claude-sonnet-4-6")
    result = {
        "model": "anthropic/claude-sonnet-4-6", "provider": "anthropic",
        "api_base": None, "api_key": "sk-openai-worker",
        "chakla_model": "openai/gpt-5.5-mini", "chakla_provider": "openai",
    }
    cfg.chakla_model = result.get("chakla_model", cfg.chakla_model)
    provider = result.get("provider")
    if provider:
        entry = cfg.providers.get(provider) or ProviderCreds()
        if result.get("api_base") is not None:
            entry.api_base = result["api_base"]
        if result.get("api_key"):
            entry.api_key = result["api_key"]
        if entry.api_key or entry.api_base:
            cfg.providers[provider] = entry
    w_provider = result.get("chakla_provider")
    if w_provider and w_provider != provider:
        w_entry = cfg.providers.get(w_provider) or ProviderCreds()
        if result.get("api_base") is not None:
            w_entry.api_base = result["api_base"]
        if result.get("api_key"):
            w_entry.api_key = result["api_key"]
        if w_entry.api_key or w_entry.api_base:
            cfg.providers[w_provider] = w_entry

    assert "openai" in cfg.providers
    assert cfg.creds_for(cfg.chakla_model)[0] == "sk-openai-worker"


def test_system_prompt_mentions_dispatch():
    from riftor.agent.context import _load_system_prompt
    prompt = _load_system_prompt()
    assert "dispatch_chakla" in prompt


def test_worker_does_not_inherit_session_grant(tmp_workdir, engagement):
    # Operator allowed `edit` for the session on the PARENT permissions. A worker
    # granted only `bash` must NOT be able to run `edit` via that session grant.
    cfg = Config()
    parent = Permissions()
    parent.allow_for_session("edit")
    worker_perms = parent.without_session_grants()
    ctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=worker_perms, audit=AuditLog(),
    )
    call = ToolCall(id="c9", name="edit",
                    arguments={"path": "x.txt", "old_string": "a", "new_string": "b"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, worker_perms, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant={"bash"})
    )
    assert "[denied]" in content  # edit was NOT granted and session-allow must not leak


def test_worker_standing_allow_rule_still_binds(tmp_workdir, engagement):
    # A STANDING allow rule (from permissions.toml) DOES still authorize a worker.
    cfg = Config()
    perms = Permissions(allow=[{"tool": "edit"}])
    worker_perms = perms.without_session_grants()
    # edit a real file so execute succeeds past the gate
    target = engagement.dir.parent / "note.txt"
    target.write_text("hello world\n")
    ctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=worker_perms, audit=AuditLog(),
    )
    call = ToolCall(id="c10", name="edit",
                    arguments={"path": "note.txt", "old_string": "hello", "new_string": "hi"})
    content = asyncio.run(
        _run_chakla_tool(call, ctx, worker_perms, ctx.audit,
                         yolo=False, db_lock=asyncio.Lock(), grant=set())
    )
    assert "[denied]" not in content  # standing allow rule authorizes it


def test_empty_chakla_model_reuses_main():
    cfg = Config(model="deepseek/deepseek-v4-pro", chakla_model="")
    assert (cfg.chakla_model or cfg.model) == "deepseek/deepseek-v4-pro"


def test_dispatch_refuses_explicit_worker_without_creds(tmp_workdir, engagement, monkeypatch):
    # Reproduce the reported bug: deepseek main (with key), explicit anthropic
    # worker, NO anthropic creds anywhere → must refuse clearly, not 401.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(model="deepseek/deepseek-v4-pro",
                 chakla_model="anthropic/claude-haiku-4-5-20251001")
    cfg.providers["deepseek"] = ProviderCreds(api_key="sk-deepseek-xxx")
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["echo hi"], "tools": []}, ctx))
    assert res.is_error
    assert "no credentials for worker model" in res.content
    assert "anthropic/claude-haiku" in res.content
    assert "reuse the main model" in res.content


def test_dispatch_blank_worker_reuses_main_creds(tmp_workdir, engagement, monkeypatch):
    # Same deepseek setup but blank chakla_model → reuses deepseek (credentialed) →
    # NO creds error (this is the out-of-box fix).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done")
    cfg = Config(model="deepseek/deepseek-v4-pro", chakla_model="")
    cfg.providers["deepseek"] = ProviderCreds(api_key="sk-deepseek-xxx")
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["recon"], "tools": []}, ctx))
    assert "no credentials for worker model" not in res.content


def test_dispatch_ollama_worker_needs_no_key(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(model="ollama_chat/llama3", chakla_model="ollama_chat/llama3")
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["x"], "tools": []}, ctx))
    assert "no credentials for worker model" not in res.content


def test_toolcontext_progress_defaults_to_none(tmp_workdir, engagement):
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement)
    assert ctx.progress is None


def test_toolcontext_progress_is_callable_when_set(tmp_workdir, engagement):
    seen = []
    ctx = ToolContext(workdir=tmp_workdir, engagement=engagement,
                      progress=lambda e: seen.append(e))
    assert ctx.progress is not None
    ctx.progress({"worker": 0, "state": "running"})
    assert seen == [{"worker": 0, "state": "running"}]


def test_run_chakla_emits_detail_events(tmp_workdir, engagement):
    from riftor.agent.provider import ToolCall, Turn, Usage

    class _StubProvider:
        """Yields one scope_list tool call, then a plain answer turn."""
        def __init__(self):
            self._calls = 0

        async def stream_turn(self, messages, schemas):
            self._calls += 1
            if self._calls == 1:
                tc = ToolCall(id="t1", name="scope_list", arguments={})
                yield ("done", Turn(
                    text="", tool_calls=[tc],
                    assistant_message={"role": "assistant", "content": None,
                                       "tool_calls": [{"id": "t1", "type": "function",
                                                       "function": {"name": "scope_list",
                                                                    "arguments": "{}"}}]},
                    usage=Usage(prompt_tokens=10, completion_tokens=5),
                ))
            else:
                yield ("text", "done.")
                yield ("done", Turn(
                    text="done.", tool_calls=[],
                    assistant_message={"role": "assistant", "content": "done."},
                    usage=Usage(prompt_tokens=4, completion_tokens=2),
                ))

    events = []
    cfg = Config()
    toolctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    result = asyncio.run(run_chakla(
        "recon 10.0.0.5",
        worker_provider=_StubProvider(),  # type: ignore[arg-type]
        toolctx=toolctx, permissions=toolctx.permissions, audit=toolctx.audit,
        max_steps=cfg.chakla_max_steps, yolo=False,
        db_lock=asyncio.Lock(), grant=set(),
        progress=lambda e: events.append(e),
    ))
    assert result.status == "done"
    detail_events = [e for e in events if e["state"] == "detail"]
    assert len(detail_events) == 1, events
    assert detail_events[0]["detail"]  # non-empty label like "scope_list…"
    assert "usage" in detail_events[0]


def test_run_chakla_detail_usage_is_snapshot(tmp_workdir, engagement):
    # The usage on a detail event must be a point-in-time snapshot, not the live
    # accumulator (which keeps growing across turns).
    from riftor.agent.provider import ToolCall, Turn, Usage

    class _StubProvider:
        def __init__(self):
            self._calls = 0

        async def stream_turn(self, messages, schemas):
            self._calls += 1
            if self._calls == 1:
                tc = ToolCall(id="t1", name="scope_list", arguments={})
                yield ("done", Turn(
                    text="", tool_calls=[tc],
                    assistant_message={"role": "assistant", "content": None,
                                       "tool_calls": [{"id": "t1", "type": "function",
                                                       "function": {"name": "scope_list",
                                                                    "arguments": "{}"}}]},
                    usage=Usage(prompt_tokens=10, completion_tokens=5),
                ))
            else:
                yield ("done", Turn(
                    text="done.", tool_calls=[],
                    assistant_message={"role": "assistant", "content": "done."},
                    usage=Usage(prompt_tokens=100, completion_tokens=200),
                ))

    import asyncio as _aio
    captured = {}
    cfg = Config()
    toolctx = tools_mod.ToolContext(
        workdir=engagement.dir.parent, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )

    def _grab(e):
        if e["state"] == "detail":
            captured["usage_tokens"] = e["usage"].total_tokens

    result = _aio.run(run_chakla(
        "recon", worker_provider=_StubProvider(),  # type: ignore[arg-type]
        toolctx=toolctx, permissions=toolctx.permissions, audit=toolctx.audit,
        max_steps=cfg.chakla_max_steps, yolo=False,
        db_lock=_aio.Lock(), grant=set(), progress=_grab,
    ))
    # At emission time, only turn-1 usage (15) had accumulated. After the run,
    # result.usage has grown to 15+300=315. The snapshot must still read 15.
    assert captured["usage_tokens"] == 15, captured
    assert result.usage.total_tokens == 315


def test_worker_provider_does_not_clobber_main_base():
    # Reproduce the review footgun: main=openai (real base+key), worker=deepseek.
    # The worker store must NOT overwrite the main openai entry's base, and must
    # give deepseek ITS OWN default base — not openai's and not a leaked one.
    from riftor.providers import PROVIDERS
    cfg = Config(model="openai/gpt-5.5")
    # main provider stored first (as _open_config's main block does)
    cfg.providers["openai"] = ProviderCreds(
        api_key="sk-openai", api_base=PROVIDERS["openai"].default_base)
    # Simulate _open_config's WORKER block for a different provider (deepseek),
    # using the FIXED logic (never copies the shared/main base):
    w_provider = "deepseek"
    provider = "openai"
    result = {"api_key": "sk-openai", "api_base": PROVIDERS["openai"].default_base}
    if w_provider and w_provider != provider:
        w_entry = cfg.providers.get(w_provider) or ProviderCreds()
        if not w_entry.api_key and result.get("api_key"):
            w_entry.api_key = result["api_key"]
        if not w_entry.api_base:
            w_entry.api_base = PROVIDERS[w_provider].default_base
        if w_entry.api_key or w_entry.api_base:
            cfg.providers[w_provider] = w_entry
    # INVARIANT: main openai base is intact (NOT deepseek's base)
    assert cfg.providers["openai"].api_base == PROVIDERS["openai"].default_base
    # worker deepseek got ITS OWN default base, not openai's
    assert cfg.providers["deepseek"].api_base == PROVIDERS["deepseek"].default_base


def test_dispatch_emits_ordered_lifecycle_events(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done")
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(), yolo=False,
        progress=lambda e: events.append(dict(e)),
    )
    res = asyncio.run(DispatchChaklaTool().execute(
        {"tasks": ["recon A", "recon B", "recon C"], "tools": []}, ctx))
    assert not res.is_error
    by_worker = {}
    for e in events:
        by_worker.setdefault(e["worker"], []).append(e["state"])
    assert set(by_worker) == {0, 1, 2}
    terminal = {"done", "timeout", "error"}
    for w, states in by_worker.items():
        assert states[0] == "queued", states
        assert "running" in states, states
        assert states[-1] in terminal, states
        assert sum(1 for s in states if s in terminal) == 1, states


def test_dispatch_terminal_events_carry_usage(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done")
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    asyncio.run(DispatchChaklaTool().execute({"tasks": ["a", "b"], "tools": []}, ctx))
    terminals = [e for e in events if e["state"] in ("done", "timeout", "error")]
    assert len(terminals) == 2
    for e in terminals:
        assert e["usage"] is not None


def test_dispatch_terminal_usage_sums_to_worker_total(tmp_workdir, engagement, monkeypatch):
    from riftor.agent.provider import Usage
    import riftor.tools.subagent as sub

    async def _fake(task, **k):
        return ChaklaResult(task=task, status="done",
                            usage=Usage(completion_tokens=23_600, cost=0.007), n_recorded=1)

    monkeypatch.setattr(sub, "run_chakla", _fake)
    cfg = Config(api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    asyncio.run(DispatchChaklaTool().execute({"tasks": ["a", "b"], "tools": []}, ctx))
    accumulated = Usage()
    for e in events:
        if e["state"] in ("done", "timeout", "error") and e["usage"] is not None:
            accumulated.add(e["usage"])
    assert accumulated.total_tokens == 47_200
    assert abs(accumulated.cost - 0.014) < 1e-9


def test_dispatch_progress_none_is_safe(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "worker done: nothing notable")
    cfg = Config(api_key="test-key")
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
    )
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["recon A"], "tools": []}, ctx))
    assert not res.is_error
    assert "recon A" in res.content


def test_dispatch_timeout_emits_timeout_event(tmp_workdir, engagement, monkeypatch):
    monkeypatch.setenv("RIFTOR_DEMO_RESPONSE", "ok")
    cfg = Config(chakla_timeout_s=1, api_key="test-key")
    events = []
    ctx = tools_mod.ToolContext(
        workdir=tmp_workdir, engagement=engagement, config=cfg,
        permissions=Permissions(), audit=AuditLog(),
        progress=lambda e: events.append(dict(e)),
    )
    import riftor.tools.subagent as sub

    async def _hang(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(sub, "run_chakla", _hang)
    res = asyncio.run(DispatchChaklaTool().execute({"tasks": ["slow"], "tools": []}, ctx))
    assert not res.is_error
    states = [e["state"] for e in events if e["worker"] == 0]
    assert "timeout" in states, states
