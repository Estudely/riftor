"""Headless smoke test for the riftor TUI (no network / no model calls).

Verifies the app composes, the Rift theme loads, and local slash-commands work.
Run: uv run python dev/smoke.py
"""

import asyncio

from riftor.config import Config
from riftor.tui.app import RiftorApp
from riftor.tui.widgets import Banner, StatusBar
from textual.widgets import Input, Markdown, Static


async def main() -> None:
    cfg = Config(model="ollama_chat/smoke", api_base="http://localhost:11434", lore=True)
    app = RiftorApp(cfg)
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

        # /clear empties the chat
        inp.value = "/clear"
        await pilot.press("enter")
        await pilot.pause()
        assert len(list(app.query(Markdown))) == 0

    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
