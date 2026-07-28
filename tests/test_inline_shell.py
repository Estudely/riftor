"""Direct `!` shell shortcut renders inline in chat — no permanent shell pane."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from textual.widget import Widget
from textual.widgets import Static

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import HELP, _COMMANDS, _PALETTE_COMMANDS, RiftorApp


def _make_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    audit_path: Path | None = None,
) -> RiftorApp:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", config_dir / "config.toml")
    monkeypatch.setattr(cfgmod, "PERMISSIONS_PATH", config_dir / "permissions.toml")
    monkeypatch.setattr(cfgmod, "KEYBINDINGS_PATH", config_dir / "keybindings.toml")
    app = RiftorApp(Config(onboarded=True, model="openai/gpt-5.5"), workdir=tmp_path)
    if audit_path is not None:
        from riftor.safety.audit import AuditLog

        app.audit = AuditLog(path=audit_path)
        app.toolctx.audit = app.audit
    return app


def _chat_text(app: RiftorApp) -> str:
    chat = app.query_one("#chat", Widget)
    parts: list[str] = []
    for node in chat.query(Static):
        parts.append(str(node.content))
        parts.append(str(node.render()))
    return "\n".join(parts)


def _chat_class_texts(app: RiftorApp, css_class: str) -> list[str]:
    chat = app.query_one("#chat", Widget)
    return [
        " ".join(str(node.render()).split())
        for node in chat.query(f".{css_class}")
    ]


async def _run_bang(pilot, command: str) -> None:
    await pilot.press("ctrl+shift+a", "backspace")
    for character in f"!{command}":
        await pilot.press("space" if character == " " else character)
    await pilot.press("enter")
    await pilot.pause()


async def _wait_until(pilot, condition: Callable[[], bool]) -> None:
    for _ in range(80):
        if condition():
            return
        await pilot.pause()
    assert condition()


@pytest.mark.asyncio
async def test_idle_app_has_no_permanent_shell_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query("#shell-pane") == []
        assert app.query("#shell-log") == []
        assert not hasattr(app, "_shell_history")


@pytest.mark.asyncio
async def test_bang_stdout_renders_inline_header_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await _run_bang(pilot, "echo hello-inline-shell")
        await _wait_until(
            pilot,
            lambda: any("hello-inline-shell" in text for text in _chat_class_texts(app, "shell-output")),
        )

        headers = _chat_class_texts(app, "shell-cmd")
        assert any(
            "$ echo hello-inline-shell" in text and "exit 0" in text for text in headers
        ), headers
        outputs = _chat_class_texts(app, "shell-output")
        assert any("hello-inline-shell" in text for text in outputs), outputs
        assert app.query("#shell-pane") == []
        # Direct shell output must not enter the model conversation.
        assert all(
            "hello-inline-shell" not in str(message.get("content", ""))
            for message in app.context.messages
        )


@pytest.mark.asyncio
async def test_bang_silent_success_shows_header_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await _run_bang(pilot, "true")
        await _wait_until(
            pilot,
            lambda: any("exit 0" in text for text in _chat_class_texts(app, "shell-cmd")),
        )

        headers = _chat_class_texts(app, "shell-cmd")
        assert any("$ true" in text and "exit 0" in text for text in headers), headers
        assert _chat_class_texts(app, "shell-output") == []


@pytest.mark.asyncio
async def test_bang_stderr_and_nonzero_exit_render_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await _run_bang(pilot, "echo boom >&2; exit 7")
        await _wait_until(
            pilot,
            lambda: any("exit 7" in text for text in _chat_class_texts(app, "shell-cmd")),
        )

        headers = _chat_class_texts(app, "shell-cmd")
        assert any("$ echo boom >&2; exit 7" in text and "exit 7" in text for text in headers)
        outputs = _chat_class_texts(app, "shell-output")
        assert any("boom" in text for text in outputs), outputs
        assert any(
            node.has_class("error")
            for node in app.query_one("#chat", Widget).query(".shell-output")
        )


@pytest.mark.asyncio
async def test_bang_records_audit_without_model_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    app = _make_app(tmp_path, monkeypatch, audit_path=audit_path)
    async with app.run_test() as pilot:
        await _run_bang(pilot, "echo audited-shell")
        await _wait_until(
            pilot,
            lambda: any("audited-shell" in text for text in _chat_class_texts(app, "shell-output")),
        )

        assert audit_path.exists()
        lines = audit_path.read_text().strip().splitlines()
        assert any('"tool": "shell_cmd"' in line and "echo audited-shell" in line for line in lines)
        assert all(
            "audited-shell" not in str(message.get("content", ""))
            for message in app.context.messages
        )


@pytest.mark.asyncio
async def test_clearlog_command_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    assert "/clearlog" not in _COMMANDS
    assert all(cmd != "/clearlog" for cmd, _title, _help in _PALETTE_COMMANDS)
    assert "/clearlog" not in HELP

    async with app.run_test() as pilot:
        await pilot.press("ctrl+shift+a", "backspace")
        for character in "/clearlog":
            await pilot.press(character)
        await pilot.press("enter")
        await pilot.pause()

        note = _chat_text(app).casefold()
        assert "unknown" in note or "did you mean" in note or "clearlog" in note
        assert app.query("#shell-pane") == []
