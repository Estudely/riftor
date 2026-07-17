"""Incomplete-session resume must always nudge the operator toward /retry."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import riftor.config as cfgmod
from riftor.agent import session as sessions
from riftor.config import Config
from riftor.tui.app import RiftorApp


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


@pytest.mark.asyncio
async def test_resume_cmd_incomplete_hints_retry():
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test():
            # Save after mount so _offer_recovery does not fire on this sid.
            msgs = [
                {"role": "user", "content": "scan it"},
                {"role": "assistant", "content": "working…"},
            ]
            sessions.save(workdir, "crash-sid", msgs, "m", complete=False)
            notes: list[str] = []
            real_note = app._note

            def _capture(text: str) -> None:
                notes.append(text)
                real_note(text)

            app._note = _capture  # type: ignore[method-assign]
            app._resume_cmd("crash-sid")
            assert any("resumed session crash-sid" in n for n in notes)
            assert any("/retry" in n for n in notes)


@pytest.mark.asyncio
async def test_resume_cmd_complete_omits_retry_hint():
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        async with app.run_test():
            msgs = [
                {"role": "user", "content": "done"},
                {"role": "assistant", "content": "ok"},
            ]
            sessions.save(workdir, "ok-sid", msgs, "m", complete=True)
            notes: list[str] = []
            real_note = app._note

            def _capture(text: str) -> None:
                notes.append(text)
                real_note(text)

            app._note = _capture  # type: ignore[method-assign]
            app._resume_cmd("ok-sid")
            assert any("resumed session ok-sid" in n for n in notes)
            assert not any("/retry" in n for n in notes)
