"""Provider registry, curated default models, and dynamic model-list fetch.

Kept free of Textual and config coupling so it is cheap to import and trivial to
unit-test. ``fetch_models`` is the only function that touches the network; it
never raises into callers — failures degrade to the curated list.
"""

from __future__ import annotations

import json  # noqa: F401
import urllib.request  # noqa: F401
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderMeta:
    key: str
    label: str
    prefix: str            # litellm prefix; "" for custom
    env: str | None        # env var holding the key, if any
    list_kind: str         # "openai" | "ollama" | "none"
    default_base: str | None


# Order here = dropdown order in /config.
PROVIDERS: dict[str, ProviderMeta] = {
    "anthropic": ProviderMeta("anthropic", "Anthropic", "anthropic/", "ANTHROPIC_API_KEY",
                              "none", None),
    "openai": ProviderMeta("openai", "OpenAI", "openai/", "OPENAI_API_KEY",
                           "openai", "https://api.openai.com/v1"),
    "openrouter": ProviderMeta("openrouter", "OpenRouter", "openrouter/", "OPENROUTER_API_KEY",
                               "openai", "https://openrouter.ai/api/v1"),
    "gemini": ProviderMeta("gemini", "Gemini", "gemini/", "GEMINI_API_KEY", "none", None),
    "groq": ProviderMeta("groq", "Groq", "groq/", "GROQ_API_KEY",
                         "openai", "https://api.groq.com/openai/v1"),
    "deepseek": ProviderMeta("deepseek", "DeepSeek", "deepseek/", "DEEPSEEK_API_KEY",
                             "openai", "https://api.deepseek.com/v1"),
    "mistral": ProviderMeta("mistral", "Mistral", "mistral/", "MISTRAL_API_KEY",
                            "openai", "https://api.mistral.ai/v1"),
    "ollama": ProviderMeta("ollama", "Ollama", "ollama_chat/", None,
                           "ollama", "http://localhost:11434"),
    "custom": ProviderMeta("custom", "Custom…", "", None, "openai", None),
}

# Curated, best-first, bare ids (no provider prefix). Researched/verified 2026-06-03.
PROVIDER_DEFAULTS: dict[str, list[str]] = {
    "anthropic": ["claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"],
    "openai": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"],
    "openrouter": ["openrouter/auto", "anthropic/claude-opus-4.8",
                   "anthropic/claude-sonnet-4.6", "openai/gpt-5.5"],
    "gemini": ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
             "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
    "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "mistral": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    "ollama": [],
    "custom": [],
}


def provider_key_for_model(model: str) -> str:
    """Best-effort provider key from a litellm model id; 'custom' if unknown."""
    for key, meta in PROVIDERS.items():
        if meta.prefix and model.startswith(meta.prefix):
            return key
    return "custom"


def apply_prefix(provider_key: str, bare_id: str) -> str:
    """Build the full litellm model id from a provider + a (possibly bare) id.

    Ids that already contain a '/' (OpenRouter, or anything pre-prefixed) pass
    through unchanged so we never double-prefix.
    """
    if "/" in bare_id:
        return bare_id
    prefix = PROVIDERS[provider_key].prefix if provider_key in PROVIDERS else ""
    return f"{prefix}{bare_id}"


@dataclass
class FetchResult:
    models: list[str] = field(default_factory=list)
    source: str = "curated"          # "live" | "curated" | "merged"
    error: str | None = None
