"""The riftor Textual app — Phase 1 walking skeleton.

A themed chat that streams from a model (local Ollama by default). No tools yet;
that's Phase 2.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown, Static

from riftor.agent.context import Context
from riftor.agent.provider import Provider
from riftor.tui.widgets import Banner, StatusBar

if TYPE_CHECKING:
    from riftor.config import Config

HELP = """\
**riftor — commands**

- `/help` — show this help
- `/clear` — clear the conversation (also `Ctrl+L`)
- `/model [name]` — show or switch the model
- `/lore` — toggle the rift persona
- `/exit` — leave riftor (also `Ctrl+C`)

Type anything else to talk to the model. `Esc` cancels a running response.
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

    def compose(self) -> ComposeResult:
        yield Banner(id="banner")
        yield VerticalScroll(id="chat")
        yield StatusBar(self.config.model, lore=self.config.lore)
        yield Input(placeholder="talk to riftor — or /help", id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._note(
            "rift online · local-first by default · you are responsible for staying in scope"
        )

    # ---- convenience accessors -------------------------------------------------
    @property
    def chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    @property
    def status(self) -> StatusBar:
        return self.query_one(StatusBar)

    # ---- rendering helpers -----------------------------------------------------
    def _note(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style="italic #5a5a6a"), classes="note"))
        self.chat.scroll_end(animate=False)

    def _add_user(self, text: str) -> None:
        self.chat.mount(Static(Text(text), classes="user"))
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
        self._stream(text)

    def _command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self.chat.mount(Markdown(HELP, classes="assistant"))
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
        else:
            self._note(f"unknown command: {cmd} — try /help")

    def action_clear(self) -> None:
        self.context.clear()
        self.chat.remove_children()
        self._note("conversation cleared")

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.status.set_busy(False)

    # ---- streaming -------------------------------------------------------------
    @work(exclusive=True)
    async def _stream(self, user_text: str) -> None:
        self.context.add_user(user_text)
        msg = Markdown("", classes="assistant")
        await self.chat.mount(msg)
        self.status.set_busy(True)

        buffer: list[str] = []
        last_render = 0.0
        try:
            async for chunk in self.provider.stream(self.context.messages):
                buffer.append(chunk)
                now = time.monotonic()
                if now - last_render > 0.08:
                    await msg.update("".join(buffer))
                    self.chat.scroll_end(animate=False)
                    last_render = now
            final = "".join(buffer).strip() or "_(no output)_"
            await msg.update(final)
            self.context.add_assistant(final)
        except Exception as exc:  # noqa: BLE001
            await msg.update(f"**rift collapsed** — `{exc}`")
        finally:
            self.status.set_busy(False)
            self.chat.scroll_end(animate=False)
