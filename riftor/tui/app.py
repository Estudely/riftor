"""The riftor Textual app.

Phase 2: a real agent loop. The model can call tools (bash/read/write/edit/
grep/glob/webfetch); dangerous tools prompt for permission; every call is
audited. The RIFT status bar is still driven manually via /stage (Phase 3 wires
it to the engagement engine).
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

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self.context = Context(lore=config.lore)
        self.provider = Provider(config)
        self.tools = tools.all_tools()
        self.tool_schemas = tools.schemas()
        self.toolctx = ToolContext(workdir=Path.cwd())
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
        self._note(
            "rift online · agent can use tools (with your approval) · stay in scope"
        )

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
        else:
            self._note(f"unknown command: {cmd} — try /help")

    def _set_stage(self, arg: str) -> None:
        if not arg:
            cur = self.status.stage
            stages = " · ".join(f"{k} {v}" for k, v in STAGE_NAMES.items())
            self._note(f"stage: {cur} ({STAGE_NAMES[cur]})   ·   {stages}")
            return
        name_to_letter = {v.lower(): k for k, v in STAGE_NAMES.items()}
        token = arg.strip()
        letter = token.upper() if token.upper() in STAGE_NAMES else name_to_letter.get(token.lower())
        if letter:
            self.status.set_stage(letter)
            self._note(f"stage → {letter} ({STAGE_NAMES[letter]})")
        else:
            self._note(f"unknown stage: {arg} — use R/I/F/T or recon/intrusion/foothold/takeover")

    def action_clear(self) -> None:
        self.context.clear()
        self.chat.remove_children()
        self._note("conversation cleared")

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.status.set_busy(False)
        self._note("cancelled")

    # ---- agent loop ------------------------------------------------------------
    @work(exclusive=True)
    async def _agent(self, user_text: str) -> None:
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

        if self.permissions.needs_prompt(tool.name, tool.requires_permission):
            decision = await self.push_screen_wait(ConfirmScreen(tool.name, preview))
            if decision == "deny":
                await self._show_tool_result("denied by operator", is_error=True)
                self.context.add_tool_result(
                    call.id,
                    "[denied by operator] The action was refused. Do not retry it; "
                    "explain the limitation or propose a safer, in-scope alternative.",
                )
                self.audit.record(tool.name, preview, allowed=False)
                return
            if decision == "session":
                self.permissions.allow_for_session(tool.name)

        start = time.monotonic()
        try:
            result = await tool.execute(call.arguments, self.toolctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(f"error: {exc}", is_error=True)
        result = result.truncated()
        duration = time.monotonic() - start

        self.audit.record(
            tool.name,
            preview,
            allowed=True,
            is_error=result.is_error,
            duration=duration,
            result_len=len(result.content),
        )
        await self._show_tool_result(result.content, is_error=result.is_error)
        self.context.add_tool_result(call.id, result.content)

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
