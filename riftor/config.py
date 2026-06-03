"""riftor configuration: load/save + first-run default detection.

Config lives at ``$XDG_CONFIG_HOME/riftor/config.toml`` (falls back to
``~/.config/riftor/config.toml``). riftor is cloud-first: on first run we pick a
cloud provider from your environment keys (Anthropic, OpenAI, OpenRouter, …).
A local Ollama server is supported as a fallback if one happens to be running.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.request
from pathlib import Path

from pydantic import BaseModel, field_validator


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "riftor"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
PERMISSIONS_PATH = CONFIG_DIR / "permissions.toml"
KEYBINDINGS_PATH = CONFIG_DIR / "keybindings.toml"
OLLAMA_DEFAULT_BASE = "http://localhost:11434"

# Provider prefixes litellm understands; used for a soft model-id sanity check.
_KNOWN_PROVIDERS = (
    "anthropic/", "openai/", "openrouter/", "ollama/", "ollama_chat/", "gemini/",
    "groq/", "mistral/", "cohere/", "azure/", "bedrock/", "vertex_ai/", "together_ai/",
    "deepseek/", "xai/", "perplexity/", "fireworks_ai/", "huggingface/", "replicate/",
)

# Env var per provider, for first-run key detection / graceful onboarding.
_PROVIDER_ENV = {
    "anthropic/": "ANTHROPIC_API_KEY",
    "openai/": "OPENAI_API_KEY",
    "openrouter/": "OPENROUTER_API_KEY",
    "gemini/": "GEMINI_API_KEY",
    "groq/": "GROQ_API_KEY",
    "mistral/": "MISTRAL_API_KEY",
    "deepseek/": "DEEPSEEK_API_KEY",
    "xai/": "XAI_API_KEY",
}


class ProviderCreds(BaseModel):
    """Per-provider credentials, stored in the [providers.<key>] TOML table."""

    api_key: str | None = None
    api_base: str | None = None


class Config(BaseModel):
    """Runtime configuration for riftor."""

    model: str = "anthropic/claude-sonnet-4-6"
    api_base: str | None = None
    api_key: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2048
    theme: str = "rift"
    lore: bool = True
    # Agent-loop tuning
    max_steps: int = 16
    max_result_chars: int = 30_000
    result_preview_lines: int = 25
    # Politeness / safety toward targets and APIs
    rate_limit_per_min: int = 0  # 0 = unlimited
    # Tracks whether we've shown the first-run onboarding.
    onboarded: bool = False
    # Per-provider credentials, keyed by provider key (see riftor.providers.PROVIDERS).
    providers: dict[str, ProviderCreds] = {}

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        value = (value or "").strip()
        if value and "/" not in value:
            # Bare ids (e.g. "gpt-4o") still work for some providers; just warn-shape.
            return value
        return value

    def model_warning(self) -> str | None:
        """A human hint if the model id looks unusual (not a hard error)."""
        if self.model and not any(self.model.startswith(p) for p in _KNOWN_PROVIDERS):
            return (
                f"model '{self.model}' has no known provider prefix; "
                f"expected e.g. {', '.join(p.rstrip('/') for p in _KNOWN_PROVIDERS[:4])}…"
            )
        return None

    def provider_env(self) -> str | None:
        """The env var name expected for this model's provider, if known."""
        for prefix, env in _PROVIDER_ENV.items():
            if self.model.startswith(prefix):
                return env
        return None

    def has_credentials(self) -> bool:
        """True if a key is configured (explicit, env, or local Ollama)."""
        if self.api_key:
            return True
        if self.model.startswith(("ollama/", "ollama_chat/")):
            return True
        env = self.provider_env()
        if env and os.environ.get(env):
            return True
        # Unknown provider: don't block; litellm may resolve it from its own env.
        return self.provider_env() is None

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                with CONFIG_PATH.open("rb") as fh:
                    data = tomllib.load(fh)
                section = dict(data.get("riftor", data))
                section.pop("providers", None)  # never let a stray key shadow the table
                providers = data.get("providers", {})
                return cls(**section, providers=providers)
            except Exception:  # noqa: BLE001 — a bad config must never crash startup
                # Fall through to detected defaults rather than failing to launch.
                return cls.detect_defaults()
        cfg = cls.detect_defaults()
        cfg.save()
        return cfg

    @classmethod
    def detect_defaults(cls) -> "Config":
        # Cloud-first: prefer a provider key from the environment.
        if os.environ.get("ANTHROPIC_API_KEY"):
            return cls(model="anthropic/claude-sonnet-4-6")
        if os.environ.get("OPENAI_API_KEY"):
            return cls(model="openai/gpt-4o")
        if os.environ.get("OPENROUTER_API_KEY"):
            return cls(model="openrouter/auto")
        # Optional local fallback if an Ollama server is already running.
        models = _ollama_models()
        if models:
            return cls(model=f"ollama_chat/{models[0]}", api_base=OLLAMA_DEFAULT_BASE)
        # Default to a cloud model; the operator adds a key on first run.
        return cls(model="anthropic/claude-sonnet-4-6")

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Write with owner-only perms — the file may hold an API key.
        fd = os.open(str(CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, self._to_toml().encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

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
            f"max_steps = {self.max_steps}",
            f"max_result_chars = {self.max_result_chars}",
            f"result_preview_lines = {self.result_preview_lines}",
            f"rate_limit_per_min = {self.rate_limit_per_min}",
            f"onboarded = {str(self.onboarded).lower()}",
        ]
        for key, creds in self.providers.items():
            if not (creds.api_key or creds.api_base):
                continue
            lines.append("")
            lines.append(f"[providers.{key}]")
            if creds.api_key:
                lines.append(f'api_key = "{creds.api_key}"')
            if creds.api_base:
                lines.append(f'api_base = "{creds.api_base}"')
        return "\n".join(lines) + "\n"


def load_keybindings() -> dict[str, str]:
    """Operator key overrides from ``keybindings.toml`` ([keybindings] action=key).

    Returns ``{action: key}``; empty if the file is missing or malformed.
    """
    if not KEYBINDINGS_PATH.exists():
        return {}
    try:
        with KEYBINDINGS_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:  # noqa: BLE001 — bad config must never crash the app
        return {}
    section = data.get("keybindings", data)
    return {str(k): str(v) for k, v in section.items() if isinstance(v, str)}


def _ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_DEFAULT_BASE}/api/tags", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []
