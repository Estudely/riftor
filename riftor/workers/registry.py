"""Load bundled + user-defined worker agent specs from markdown files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from riftor.config import CONFIG_DIR

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

_DEFAULT_TOOLS: dict[str, list[str]] = {
    "recon": ["bash", "webfetch"],
    "scout": ["bash", "webfetch"],
    "tester": ["bash", "webfetch"],
    "analyst": ["read", "write", "record_finding", "generate_report"],
    "exploiter": ["bash", "webfetch", "record_finding"],
}


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    description: str
    model: str = ""
    tools: tuple[str, ...] = field(default_factory=tuple)
    prompt: str = ""


def _parse_md(path: Path) -> WorkerSpec | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER.match(text)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name") or path.stem).strip()
    if not name:
        return None
    desc = str(meta.get("description") or "").strip()
    model = str(meta.get("model") or "").strip()
    tools_raw = meta.get("tools")
    if isinstance(tools_raw, list) and tools_raw:
        tools = tuple(str(t) for t in tools_raw)
    else:
        tools = tuple(_DEFAULT_TOOLS.get(name, ["bash"]))
    return WorkerSpec(name=name, description=desc, model=model, tools=tools, prompt=m.group(2).strip())


def _bundled_dir() -> Path:
    return Path(str(resources.files("riftor.workers")))


def _user_dir() -> Path:
    return CONFIG_DIR / "workers"


def _load_all() -> dict[str, WorkerSpec]:
    specs: dict[str, WorkerSpec] = {}
    for directory in (_bundled_dir(), _user_dir()):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            spec = _parse_md(path)
            if spec:
                specs[spec.name] = spec  # user dir wins on collision (loaded second)
    return specs


_CACHE: dict[str, WorkerSpec] | None = None


def _workers() -> dict[str, WorkerSpec]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_all()
    return _CACHE


def list_workers() -> list[WorkerSpec]:
    return list(_workers().values())


def get_worker(name: str) -> WorkerSpec | None:
    return _workers().get(name.strip().lower())
