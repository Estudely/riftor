"""riftor configuration: load/save + first-run default detection.

Config lives at ``$XDG_CONFIG_HOME/riftor/config.toml`` (falls back to
``~/.config/riftor/config.toml``). On first run we detect a sensible default:
local Ollama if it's reachable, otherwise a cloud provider from env keys.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.request
from pathlib import Path

from pydantic import BaseModel


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "riftor"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
OLLAMA_DEFAULT_BASE = "http://localhost:11434"


class Config(BaseModel):
    """Runtime configuration for riftor."""

    model: str = "ollama_chat/llama3.1"
    api_base: str | None = None
    api_key: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2048
    theme: str = "rift"
    lore: bool = True

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("rb") as fh:
                data = tomllib.load(fh)
            return cls(**data.get("riftor", data))
        cfg = cls.detect_defaults()
        cfg.save()
        return cfg

    @classmethod
    def detect_defaults(cls) -> "Config":
        models = _ollama_models()
        if models:
            return cls(model=f"ollama_chat/{models[0]}", api_base=OLLAMA_DEFAULT_BASE)
        if os.environ.get("ANTHROPIC_API_KEY"):
            return cls(model="anthropic/claude-3-5-sonnet-latest")
        if os.environ.get("OPENAI_API_KEY"):
            return cls(model="openai/gpt-4o")
        if os.environ.get("OPENROUTER_API_KEY"):
            return cls(model="openrouter/auto")
        return cls(model="ollama_chat/llama3.1", api_base=OLLAMA_DEFAULT_BASE)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(self._to_toml(), encoding="utf-8")

    def _to_toml(self) -> str:
        lines = [
            "# riftor configuration",
            "# https://github.com/Estudely/riftor",
            "",
            "[riftor]",
            f'model = "{self.model}"',
        ]
        if self.api_base:
            lines.append(f'api_base = "{self.api_base}"')
        else:
            lines.append('# api_base = "http://localhost:11434"')
        if self.api_key:
            lines.append(f'api_key = "{self.api_key}"')
        else:
            lines.append("# api_key = \"sk-...\"  # prefer the provider's env var instead")
        lines += [
            f"temperature = {self.temperature}",
            f"max_tokens = {self.max_tokens}",
            f'theme = "{self.theme}"',
            f"lore = {str(self.lore).lower()}",
        ]
        return "\n".join(lines) + "\n"


def _ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_DEFAULT_BASE}/api/tags", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []
