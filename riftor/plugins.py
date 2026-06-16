"""Operator-authored plugins: drop a .py file or package into the XDG plugins dir
exporting a module-level ``TOOLS: list[Tool]``. They are discovered + validated at
app/headless startup (see riftor.tools.register_plugins).

Pure: ``load_plugins`` never mutates the tool registry — it returns the accepted
tools and a list of per-plugin errors. A bad plugin is warned + skipped, never
fatal (mirrors "bad config never crashes"). Plugin code runs with the SAME trust as
riftor itself — the operator-owned XDG config dir is the trust boundary.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from riftor.tools.base import Tool


@dataclass
class PluginError:
    module: str
    error: str


def plugins_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "riftor" / "plugins"


def _module_paths(pdir: Path) -> list[tuple[str, Path]]:
    """(module_name, import_target) for each top-level .py file and package dir."""
    out: list[tuple[str, Path]] = []
    for entry in sorted(pdir.iterdir()):
        name = entry.name
        if name.startswith(("_", ".")):
            continue
        if entry.is_file() and name.endswith(".py"):
            out.append((entry.stem, entry))
        elif entry.is_dir() and (entry / "__init__.py").exists():
            out.append((name, entry))
    return out


def _import_module(mod_name: str, target: Path):
    if target.is_dir():
        parent = str(target.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        return importlib.import_module(mod_name)
    spec = importlib.util.spec_from_file_location(f"riftor_plugin_{mod_name}", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {target}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugins(config, builtin_names: set[str]) -> tuple[list[Tool], list[PluginError]]:
    """Discover + validate plugins. Returns (accepted_tools, errors). Never raises."""
    enabled = getattr(config, "plugins_enabled", True)
    if not enabled:
        return [], []
    pdir = plugins_dir()
    if not pdir.is_dir():
        return [], []

    allow = set(getattr(config, "plugins_allow", []) or [])
    deny = set(getattr(config, "plugins_deny", []) or [])

    accepted: list[Tool] = []
    errors: list[PluginError] = []
    taken = set(builtin_names)

    for mod_name, target in _module_paths(pdir):
        if mod_name in deny:  # deny wins over allow
            continue
        if allow and mod_name not in allow:
            continue
        try:
            module = _import_module(mod_name, target)
        except Exception:  # noqa: BLE001 — one bad plugin must not break the rest
            errors.append(PluginError(mod_name, traceback.format_exc(limit=3).strip()))
            continue

        tools_attr = getattr(module, "TOOLS", None)
        if not isinstance(tools_attr, list):
            errors.append(PluginError(mod_name, "missing or non-list TOOLS"))
            continue

        for item in tools_attr:
            if not isinstance(item, Tool):
                errors.append(PluginError(mod_name, f"TOOLS item is not a Tool: {item!r}"))
                continue
            if not isinstance(item.name, str) or not item.name.strip():
                errors.append(PluginError(mod_name, "tool has empty/invalid name"))
                continue
            if not isinstance(item.parameters, dict):
                errors.append(PluginError(mod_name, f"tool {item.name}: parameters not a dict"))
                continue
            if item.name in taken:
                errors.append(PluginError(mod_name, f"tool name '{item.name}' collides; skipped"))
                continue
            taken.add(item.name)
            accepted.append(item)

    return accepted, errors
