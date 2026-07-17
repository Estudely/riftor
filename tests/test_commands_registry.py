"""Slash-command registry must stay in sync with live dispatch handlers.

Autocomplete and "did you mean" read ``_COMMANDS``; if a handler exists but the
name is missing there, operators get a false "unknown command" suggestion miss.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import riftor.config as cfgmod
from riftor.config import Config
from riftor.tui.app import _COMMANDS, HELP, RiftorApp


def _patch_paths(tmp: Path) -> None:
    cfgmod.CONFIG_DIR = tmp
    cfgmod.CONFIG_PATH = tmp / "config.toml"
    cfgmod.PERMISSIONS_PATH = tmp / "permissions.toml"
    cfgmod.KEYBINDINGS_PATH = tmp / "kb.toml"


def test_handler_keys_subset_of_commands():
    """Every dispatch key (plus /exit /quit) must appear in ``_COMMANDS``."""
    with tempfile.TemporaryDirectory() as d:
        workdir = Path(d)
        _patch_paths(workdir)
        app = RiftorApp(Config(), workdir=workdir)
        handler_keys = set(app._command_handlers("").keys()) | {"/exit", "/quit"}
        missing = handler_keys - set(_COMMANDS)
        assert not missing, f"handlers missing from _COMMANDS: {sorted(missing)}"


def test_help_lists_hypotheses_and_lessons_separately_from_memory():
    """HELP must not claim hypotheses/lessons live under /memory."""
    assert "/hypotheses" in HELP
    assert "/lesson" in HELP
    assert "/lessons" in HELP
    # /memory line should describe notes only — not hypotheses/lessons
    memory_line = next(line for line in HELP.splitlines() if "`/memory" in line)
    assert "hypothes" not in memory_line.lower()
    assert "lesson" not in memory_line.lower()
