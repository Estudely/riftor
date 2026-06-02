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
from riftor.agent.context import Context
from riftor.agent.provider import Provider, ToolCall, Turn
from riftor.engagement import Engagement
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import ConfirmScreen, Permissions
from riftor.tools import ToolContext, ToolResult
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

    def compose(self) -> ComposeResult:
        yield Banner(id="banner")
        yield VerticalScroll(id="chat")
        yield StatusBar(self.config.model, lore=self.config.lore)
        yield Input(placeholder="task riftor — or /help", id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.status.set_stage(self.engagement.stage)
        self._refresh_status()
        self._note(
            "rift online · set scope with /scope add <target> before tasking the agent"
        )

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
    def _note(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style="italic #5a5a6a"), classes="note"))
        self.chat.scroll_end(animate=False)

    def _error(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style="bold #fca5a5"), classes="note"))
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
        else:
            self._note(f"unknown command: {cmd} — try /help")

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

    def action_clear(self) -> None:
        self.context.clear()
        self.chat.remove_children()
        self._note("conversation cleared")

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
        line = Text()
        line.append("⛏ ", style="#22d3ee")
        line.append(name, style="bold #f0abfc" if danger else "bold #22d3ee")
        if preview:
            line.append("  ")
            line.append(preview, style="#8b8ba7")
        await self._mount(Static(line, classes="tool"))

    async def _show_tool_result(self, content: str, is_error: bool = False, max_lines: int = 25) -> None:
        lines = content.splitlines() or [""]
        shown = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            shown += f"\n…(+{len(lines) - max_lines} more lines)"
        classes = "tool-result error" if is_error else "tool-result"
        await self._mount(Static(Text(shown), classes=classes))
