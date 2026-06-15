"""Plugin discovery + validation. Pure: load_plugins never mutates the registry."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import riftor.tools as tools_pkg
from riftor.config import Config
from riftor.plugins import PluginError, load_plugins, plugins_dir
from riftor.tools import all_tools, get, schemas


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


@pytest.fixture
def restore_registry():
    snap_list = list(tools_pkg.ALL_TOOLS)
    snap_map = dict(tools_pkg._BY_NAME)
    yield
    tools_pkg.ALL_TOOLS[:] = snap_list
    tools_pkg._BY_NAME.clear()
    tools_pkg._BY_NAME.update(snap_map)


def test_import_does_not_load_plugins(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    import importlib

    importlib.reload(tools_pkg)
    assert tools_pkg.get("hello_plugin") is None


def test_register_updates_all_three_seams(monkeypatch, tmp_path, restore_registry):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    errors = tools_pkg.register_plugins(Config())
    assert errors == []
    assert get("hello_plugin") is not None
    assert any(t.name == "hello_plugin" for t in all_tools())
    assert any(s["function"]["name"] == "hello_plugin" for s in schemas())


def test_register_is_idempotent(monkeypatch, tmp_path, restore_registry):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write_plugin(tmp_path / "riftor" / "plugins", "demo", _GOOD)
    tools_pkg.register_plugins(Config())
    before = len(all_tools())
    tools_pkg.register_plugins(Config())
    assert len(all_tools()) == before
