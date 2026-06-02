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


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(test_tools())
