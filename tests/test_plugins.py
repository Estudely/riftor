"""Plugin discovery + validation. Pure: load_plugins never mutates the registry."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from riftor.config import Config
from riftor.plugins import PluginError, load_plugins, plugins_dir


def _write_plugin(dirpath: Path, name: str, body: str) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{name}.py").write_text(textwrap.dedent(body))


_GOOD = '''
    from riftor.tools.base import Tool, ToolContext, ToolResult

    class HelloTool(Tool):
        name = "hello_plugin"
        description = "demo"
        parameters = {"type": "object", "properties": {}}
        async def execute(self, args, ctx):
            return ToolResult("hi")

    TOOLS = [HelloTool()]
'''


def test_plugins_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert plugins_dir() == tmp_path / "riftor" / "plugins"


def test_missing_dir_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == [] and errors == []


def test_valid_single_file_plugin_loads(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert errors == []
    assert [t.name for t in tools] == ["hello_plugin"]


# TODO(Task 3): remove this skip once Config gains the `plugins_enabled` field.
@pytest.mark.skip(reason="needs Config plugin fields from Task 3")
def test_disabled_loads_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    tools, errors = load_plugins(Config(plugins_enabled=False), builtin_names=set())
    assert tools == [] and errors == []


# TODO(Task 3): remove this skip once Config gains the `plugins_deny` field.
@pytest.mark.skip(reason="needs Config plugin fields from Task 3")
def test_deny_skips_module(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    tools, errors = load_plugins(Config(plugins_deny=["demo"]), builtin_names=set())
    assert tools == []


# TODO(Task 3): remove this skip once Config gains the `plugins_allow` field.
@pytest.mark.skip(reason="needs Config plugin fields from Task 3")
def test_allowlist_loads_only_listed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pdir = tmp_path / "riftor" / "plugins"
    _write_plugin(pdir, "demo", _GOOD)
    _write_plugin(pdir, "other", _GOOD.replace("hello_plugin", "other_plugin"))
    tools, _ = load_plugins(Config(plugins_allow=["demo"]), builtin_names=set())
    assert [t.name for t in tools] == ["hello_plugin"]


# TODO(Task 3): remove this skip once Config gains the plugin allow/deny fields.
@pytest.mark.skip(reason="needs Config plugin fields from Task 3")
def test_deny_wins_over_allow(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    cfg = Config(plugins_allow=["demo"], plugins_deny=["demo"])
    tools, _ = load_plugins(cfg, builtin_names=set())
    assert tools == []


def test_missing_TOOLS_is_error_and_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "bad", "X = 1\n")
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == []
    assert len(errors) == 1 and errors[0].module == "bad"
    assert isinstance(errors[0], PluginError)


def test_TOOLS_not_a_list_is_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "bad", "TOOLS = 'nope'\n")
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == [] and len(errors) == 1


def test_non_tool_item_is_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "bad", "TOOLS = [object()]\n")
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == [] and len(errors) == 1


def test_collision_with_builtin_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    body = _GOOD.replace("hello_plugin", "bash")  # collide with built-in
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", body)
    tools, errors = load_plugins(Config(), builtin_names={"bash"})
    assert tools == [] and len(errors) == 1


def test_import_error_is_caught(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "boom", "raise RuntimeError('x')\n")
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == [] and len(errors) == 1 and "x" in errors[0].error


def test_underscore_files_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "_private", _GOOD)
    tools, errors = load_plugins(Config(), builtin_names=set())
    assert tools == [] and errors == []
