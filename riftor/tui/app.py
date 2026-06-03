"""The riftor Textual app.

Phase 3: an offensive-security agent. The model calls tools (bash/read/write/
edit/grep/glob/webfetch + engagement tools); dangerous tools prompt for
permission; scope-sensitive tools are blocked against out-of-scope targets
(with an explicit per-call override); RIFT stage, scope and findings live in the
engagement state and show in the status bar.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown, Static

from riftor import tools
from riftor.agent import session as sessions
from riftor.agent.context import Context
from riftor.agent.provider import Provider, ToolCall, Turn
from riftor.engagement import Engagement
from riftor.engagement.report import write_reports
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import ConfirmScreen, Permissions
from riftor.tools import ToolContext, ToolResult
from riftor.tui.config_screen import ConfigScreen
from riftor.tui.theme import THEMES, css_variable_defaults, palette
from riftor.tui.widgets import STAGE_NAMES, Banner, StatusBar

if TYPE_CHECKING:
    from riftor.config import Config

HELP = """\
**riftor — commands**

- `/help` — show this help
- `/clear` — clear the conversation (also `Ctrl+L`)
- `/model [name]` — show or switch the model
- `/stage [R|I|F|T]` — show or set the RIFT stage (Recon/Intrusion/Foothold/Takeover)
- `/scope [add|out|rm <t>|clear|on|off]` — manage in/out-of-scope targets
- `/findings` — list recorded findings
- `/report [md|html|both]` — write a pentest report to `.riftor/reports/`
- `/sessions` — list saved sessions · `/resume <id>` · `/new` — start fresh
- `/theme [name]` — show or switch theme (rift/void/fracture/singularity)
- `/config` — open the settings panel
- `/tools` — list available tools
- `/lore` — toggle the rift persona
- `/exit` — leave riftor (also `Ctrl+C`)

Type anything else to task the agent. `Esc` cancels a running response.
"""


class RiftorApp(App):
    CSS_PATH = "themes/rift.tcss"
    TITLE = "riftor"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, config: "Config", workdir: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self.workdir = workdir or Path.cwd()
        self.context = Context(lore=config.lore)
        self.provider = Provider(config)
        self.tools = tools.all_tools()
        self.tool_schemas = tools.schemas()
        self.engagement = Engagement(self.workdir)
        self.toolctx = ToolContext(workdir=self.workdir, engagement=self.engagement)
        self.permissions = Permissions()
        self.audit = AuditLog()
        self.max_steps = 16
        self.session_id = sessions.new_id()

    def compose(self) -> ComposeResult:
        yield Banner(id="banner")
        yield VerticalScroll(id="chat")
        yield StatusBar(self.config.model, lore=self.config.lore)
        yield Input(placeholder="task riftor — or /help", id="prompt")

    def get_css_variables(self) -> dict[str, str]:
        # Ensure $violet/$user-bg/etc. always resolve, even on the first paint
        # before our theme is active (built-in themes lack these variables).
        variables = dict(css_variable_defaults())
        variables.update(super().get_css_variables())
        return variables

    def _apply_theme(self, name: str) -> None:
        if name not in THEMES:
            name = "rift"
        self.theme = name
        self.refresh_css(animate=False)
        try:
            self.query_one(Banner).refresh()
            self.status.refresh_bar()
        except Exception:  # noqa: BLE001 — widgets may not be mounted yet
            pass

    def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self._apply_theme(self.config.theme)
        self.query_one("#prompt", Input).focus()
        self.status.set_stage(self.engagement.stage)
        self._refresh_status()
        if not self._resume_latest():
            self._note(
                "rift online · set scope with /scope add <target> before tasking the agent"
            )

    def _resume_latest(self) -> bool:
        data = sessions.latest(self.workdir)
        if not data or not data.get("messages"):
            return False
        self.session_id = data["id"]
        self.context.load(data["messages"])
        self.context.repair()
        self._replay_transcript(self.context.messages)
        self._note(f"resumed session {data['id']} ({len(data['messages'])} messages) · /new for fresh")
        return True

    def _refresh_status(self) -> None:
        self.status.set_stage(self.engagement.stage)
        self.status.set_scope(self.engagement.scope_count(), self.engagement.enforce)
        self.status.set_findings(self.engagement.findings_count())

    # ---- accessors -------------------------------------------------------------
    @property
    def chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    @property
    def status(self) -> StatusBar:
        return self.query_one(StatusBar)

    # ---- mount helpers ---------------------------------------------------------
    def _pal(self) -> dict[str, str]:
        return palette(self)

    def _note(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style=f"italic {self._pal()['dim']}"), classes="note"))
        self.chat.scroll_end(animate=False)

    def _error(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style=f"bold {self._pal()['danger']}"), classes="note"))
        self.chat.scroll_end(animate=False)

    def _add_user(self, text: str) -> None:
        self.chat.mount(Static(Text(text), classes="user"))
        self.chat.scroll_end(animate=False)

    async def _mount(self, widget) -> None:
        await self.chat.mount(widget)
        self.chat.scroll_end(animate=False)

    # ---- events ----------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).clear()
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
            return
        self._add_user(text)
        self._agent(text)

    def _command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            self._save_session()
            self.exit()
        elif cmd == "/help":
            self.chat.mount(Markdown(HELP, classes="assistant"))
            self.chat.scroll_end(animate=False)
        elif cmd == "/tools":
            listing = "\n".join(
                f"- `{t.name}`{'  ⚠ needs approval' if t.requires_permission else ''} — {t.description}"
                for t in self.tools
            )
            self.chat.mount(Markdown(f"**tools**\n\n{listing}", classes="assistant"))
            self.chat.scroll_end(animate=False)
        elif cmd == "/clear":
            self.action_clear()
        elif cmd == "/lore":
            self.config.lore = not self.config.lore
            self.context.lore = self.config.lore
            self.status.set_lore(self.config.lore)
            self._note(f"lore {'engaged' if self.config.lore else 'disengaged'}")
        elif cmd == "/model":
            if arg:
                self.config.model = arg
                self.provider = Provider(self.config)
                self.status.set_model(arg)
                self._note(f"model → {arg}")
            else:
                self._note(f"model: {self.config.model}")
        elif cmd == "/stage":
            self._set_stage(arg)
        elif cmd == "/scope":
            self._scope_cmd(arg)
        elif cmd == "/findings":
            self._show_findings()
        elif cmd == "/report":
            self._report_cmd(arg)
        elif cmd == "/sessions":
            self._sessions_cmd()
        elif cmd == "/resume":
            self._resume_cmd(arg)
        elif cmd == "/new":
            self._new_session()
        elif cmd == "/theme":
            self._theme_cmd(arg)
        elif cmd == "/config":
            self._open_config()
        else:
            self._note(f"unknown command: {cmd} — try /help")

    def _theme_cmd(self, arg: str) -> None:
        name = arg.strip().lower()
        if not name:
            self._note(f"theme: {self.config.theme} · available: {', '.join(THEMES)}")
            return
        if name not in THEMES:
            self._note(f"unknown theme: {name} · available: {', '.join(THEMES)}")
            return
        self.config.theme = name
        self.config.save()
        self._apply_theme(name)
        self._note(f"theme → {name}")

    @work(group="config")
    async def _open_config(self) -> None:
        result = await self.push_screen_wait(ConfigScreen(self.config))
        if not isinstance(result, dict):
            self._note("config unchanged")
            return
        self.config.model = result["model"]
        self.config.temperature = result["temperature"]
        self.config.max_tokens = result["max_tokens"]
        self.config.lore = result["lore"]
        if result.get("api_key"):
            self.config.api_key = result["api_key"]
        self.provider = Provider(self.config)
        self.context.lore = self.config.lore
        self.status.set_lore(self.config.lore)
        self.status.set_model(self.config.model)
        self.config.theme = result["theme"]
        self._apply_theme(result["theme"])
        self.config.save()
        self._note("config saved")

    def _set_stage(self, arg: str) -> None:
        if not arg:
            cur = self.engagement.stage
            stages = " · ".join(f"{k} {v}" for k, v in STAGE_NAMES.items())
            self._note(f"stage: {cur} ({STAGE_NAMES[cur]})   ·   {stages}")
            return
        name_to_letter = {v.lower(): k for k, v in STAGE_NAMES.items()}
        token = arg.strip()
        letter = token.upper() if token.upper() in STAGE_NAMES else name_to_letter.get(token.lower())
        if letter:
            self.engagement.set_stage(letter)
            self._refresh_status()
            self._note(f"stage → {letter} ({STAGE_NAMES[letter]})")
        else:
            self._note(f"unknown stage: {arg} — use R/I/F/T or recon/intrusion/foothold/takeover")

    def _scope_cmd(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            ins = ", ".join(t.raw for t in self.engagement.scope.in_scope) or "(none)"
            outs = self.engagement.scope.out_of_scope
            line = f"scope · enforce {'on' if self.engagement.enforce else 'off'} · in: {ins}"
            if outs:
                line += " · out: " + ", ".join(t.raw for t in outs)
            self._note(line)
            return
        sub, rest = parts[0].lower(), parts[1:]
        if sub in ("add", "in") and rest:
            for target in rest:
                self.engagement.add_scope(target, "in")
            self._note(f"in-scope += {', '.join(rest)}")
        elif sub == "out" and rest:
            for target in rest:
                self.engagement.add_scope(target, "out")
            self._note(f"out-of-scope += {', '.join(rest)}")
        elif sub in ("rm", "remove", "del") and rest:
            for target in rest:
                self.engagement.remove_scope(target)
            self._note(f"removed {', '.join(rest)}")
        elif sub == "clear":
            self.engagement.clear_scope()
            self._note("scope cleared")
        elif sub == "on":
            self.engagement.set_enforce(True)
            self._note("scope enforcement ON")
        elif sub == "off":
            self.engagement.set_enforce(False)
            self._error("scope enforcement OFF — riftor will not block out-of-scope actions")
        else:
            self._note("usage: /scope [add <t>|out <t>|rm <t>|clear|on|off]")
        self._refresh_status()

    def _show_findings(self) -> None:
        findings = self.engagement.store.list_findings()
        if not findings:
            self._note("no findings recorded yet")
            return
        rows = []
        for i, f in enumerate(findings, 1):
            host = f" — `{f['host']}`" if f["host"] else ""
            rows.append(f"{i}. **[{f['severity']}]** {f['title']}{host}")
        self.chat.mount(Markdown("**findings**\n\n" + "\n".join(rows), classes="assistant"))
        self.chat.scroll_end(animate=False)

    def _report_cmd(self, arg: str) -> None:
        fmt = (arg or "both").strip().lower()
        if fmt not in ("md", "html", "both"):
            self._note("usage: /report [md|html|both]")
            return
        try:
            paths = write_reports(self.engagement, fmt)
        except Exception as exc:  # noqa: BLE001
            self._error(f"report failed — {exc}")
            return
        self._note("report written: " + ", ".join(str(p) for p in paths))

    # ---- sessions --------------------------------------------------------------
    def _save_session(self) -> None:
        try:
            sessions.save(self.workdir, self.session_id, self.context.dump(), self.config.model)
        except Exception:  # noqa: BLE001 — persistence must never crash the app
            pass

    def _sessions_cmd(self) -> None:
        rows = sessions.list_sessions(self.workdir)
        if not rows:
            self._note("no saved sessions")
            return
        lines = []
        for s in rows:
            marker = "→ " if s["id"] == self.session_id else "  "
            lines.append(f"{marker}`{s['id']}` · {s['messages']} msgs · {s['title']}")
        self.chat.mount(Markdown("**sessions**\n\n" + "\n".join(lines), classes="assistant"))
        self.chat.scroll_end(animate=False)

    def _resume_cmd(self, arg: str) -> None:
        sid = arg.strip()
        if not sid:
            self._note("usage: /resume <id> — see /sessions")
            return
        data = sessions.load(self.workdir, sid)
        if not data:
            self._note(f"no such session: {sid}")
            return
        self.session_id = data["id"]
        self.context.load(data.get("messages", []))
        self.context.repair()
        self.chat.remove_children()
        self._replay_transcript(self.context.messages)
        self._note(f"resumed session {sid} ({len(data.get('messages', []))} messages)")

    def _new_session(self) -> None:
        self._save_session()
        self.session_id = sessions.new_id()
        self.context.clear()
        self.chat.remove_children()
        self._note(f"new session {self.session_id}")

    def _replay_transcript(self, messages: list[dict]) -> None:
        """Re-render a saved conversation (compact) so the screen reflects history."""
        p = self._pal()
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    self.chat.mount(Static(Text(content), classes="user"))
            elif role == "assistant":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    self.chat.mount(Markdown(content, classes="assistant"))
                for call in msg.get("tool_calls") or []:
                    name = call.get("function", {}).get("name", "tool")
                    self.chat.mount(Static(Text(f"⛏ {name}", style=p["cyan"]), classes="tool"))
            elif role == "tool":
                content = str(msg.get("content", ""))
                first = content.splitlines()[0] if content else ""
                self.chat.mount(Static(Text(first[:200], style=p["dim"]), classes="tool-result"))
        self.chat.scroll_end(animate=False)

    def action_clear(self) -> None:
        self.context.clear()
        self.chat.remove_children()
        self._note("conversation cleared")

    def action_quit(self) -> None:
        self._save_session()
        self.exit()

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self._close_modals()
        self.status.set_busy(False)
        self._note("cancelled")

    def _close_modals(self) -> None:
        """Pop any permission modal left over from a cancelled worker."""
        while isinstance(self.screen, ConfirmScreen):
            self.pop_screen()

    # ---- agent loop ------------------------------------------------------------
    @work(exclusive=True)
    async def _agent(self, user_text: str) -> None:
        self._close_modals()  # a prior run may have been cancelled mid-prompt
        self.context.add_user(user_text)
        self.status.set_busy(True)
        try:
            for _ in range(self.max_steps):
                turn = await self._assistant_turn()
                self.context.add_message(turn.assistant_message)
                if not turn.tool_calls:
                    if not turn.text.strip():
                        self._note("(no output)")
                    break
                for call in turn.tool_calls:
                    await self._run_tool(call)
            else:
                self._note(f"reached step limit ({self.max_steps}); stopping")
        except Exception as exc:  # noqa: BLE001
            self._error(f"rift collapsed — {exc}")
        finally:
            self.status.set_busy(False)
            self.chat.scroll_end(animate=False)
            self._save_session()

    async def _assistant_turn(self) -> Turn:
        # Guarantee a valid history: any dangling tool_use (from an interrupted
        # or cancelled turn) gets a synthetic result before we call the model.
        self.context.repair()

        bubble = Markdown("", classes="assistant")
        await self._mount(bubble)
        buffer: list[str] = []
        last_render = 0.0
        turn: Turn | None = None

        async for event, payload in self.provider.stream_turn(
            self.context.messages, self.tool_schemas
        ):
            if event == "text":
                buffer.append(str(payload))
                now = time.monotonic()
                if now - last_render > 0.08:
                    await bubble.update("".join(buffer))
                    self.chat.scroll_end(animate=False)
                    last_render = now
            elif event == "done":
                turn = payload  # type: ignore[assignment]

        text = "".join(buffer).strip()
        if text:
            await bubble.update(text)
        else:
            await bubble.remove()
        self.chat.scroll_end(animate=False)
        return turn or Turn(text=text, assistant_message={"role": "assistant", "content": text})

    async def _run_tool(self, call: ToolCall) -> None:
        tool = tools.get(call.name)
        if tool is None:
            await self._show_tool_call(call.name, "unknown tool", danger=True)
            msg = f"error: unknown tool '{call.name}'"
            await self._show_tool_result(msg, is_error=True)
            self.context.add_tool_result(call.id, msg)
            return

        preview = tool.preview(call.arguments)
        await self._show_tool_call(tool.name, preview, danger=tool.danger)

        # scope enforcement for target-touching tools (bash, webfetch)
        scope_warning: list[str] = []
        if getattr(tool, "scope_sensitive", False):
            probe = " ".join(str(v) for v in call.arguments.values())
            scope_warning = self.engagement.violations(probe)

        if scope_warning or self.permissions.needs_prompt(tool.name, tool.requires_permission):
            decision = await self.push_screen_wait(
                ConfirmScreen(tool.name, preview, scope_warning=scope_warning)
            )
            if decision == "deny":
                reason = "blocked — out of scope" if scope_warning else "denied by operator"
                await self._show_tool_result(reason, is_error=True)
                if scope_warning:
                    hint = (
                        f"[blocked: out of scope] {', '.join(scope_warning)} not in scope. "
                        "Do not attempt these targets; work only within the defined scope."
                    )
                else:
                    hint = (
                        "[denied by operator] The action was refused. Do not retry it; "
                        "explain the limitation or propose a safer, in-scope alternative."
                    )
                self.context.add_tool_result(call.id, hint)
                self.audit.record(tool.name, preview, allowed=False)
                return
            if decision == "session":
                self.permissions.allow_for_session(tool.name)

        audit_preview = preview + (" [scope-override]" if scope_warning else "")
        start = time.monotonic()
        try:
            result = await tool.execute(call.arguments, self.toolctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(f"error: {exc}", is_error=True)
        result = result.truncated()
        duration = time.monotonic() - start

        self.audit.record(
            tool.name,
            audit_preview,
            allowed=True,
            is_error=result.is_error,
            duration=duration,
            result_len=len(result.content),
        )
        await self._show_tool_result(result.content, is_error=result.is_error)
        self.context.add_tool_result(call.id, result.content)
        self._refresh_status()

    # ---- tool rendering --------------------------------------------------------
    async def _show_tool_call(self, name: str, preview: str, danger: bool = False) -> None:
        p = self._pal()
        line = Text()
        line.append("⛏ ", style=p["cyan"])
        line.append(name, style=f"bold {p['magenta']}" if danger else f"bold {p['cyan']}")
        if preview:
            line.append("  ")
            line.append(preview, style=p["muted"])
        await self._mount(Static(line, classes="tool"))

    async def _show_tool_result(self, content: str, is_error: bool = False, max_lines: int = 25) -> None:
        lines = content.splitlines() or [""]
        shown = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            shown += f"\n…(+{len(lines) - max_lines} more lines)"
        classes = "tool-result error" if is_error else "tool-result"
        await self._mount(Static(Text(shown), classes=classes))
