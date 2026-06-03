"""Provider registry, curated defaults, prefix helpers, and dynamic model fetch."""

from __future__ import annotations

import riftor.providers as pv


def test_registry_has_expected_providers():
    keys = set(pv.PROVIDERS)
    assert {"anthropic", "openai", "openrouter", "gemini", "groq",
            "deepseek", "mistral", "ollama", "custom"} <= keys
    # every provider has a curated-defaults entry (possibly empty)
    for key in pv.PROVIDERS:
        assert key in pv.PROVIDER_DEFAULTS


def test_curated_anthropic_ids_are_dateless():
    assert pv.PROVIDER_DEFAULTS["anthropic"][0] == "claude-opus-4-8"
    assert all("-2026" not in m for m in pv.PROVIDER_DEFAULTS["anthropic"])


def test_provider_key_for_model():
    assert pv.provider_key_for_model("anthropic/claude-opus-4-8") == "anthropic"
    assert pv.provider_key_for_model("ollama_chat/llama3") == "ollama"
    assert pv.provider_key_for_model("openrouter/auto") == "openrouter"
    # unknown prefix -> "custom"
    assert pv.provider_key_for_model("weird/model") == "custom"


def test_apply_prefix():
    assert pv.apply_prefix("anthropic", "claude-opus-4-8") == "anthropic/claude-opus-4-8"
    # openrouter ids already contain a slash -> passed through unchanged
    assert pv.apply_prefix("openrouter", "openrouter/auto") == "openrouter/auto"
    assert pv.apply_prefix("openrouter", "anthropic/claude-opus-4.8") == "anthropic/claude-opus-4.8"
    # custom -> no prefix
    assert pv.apply_prefix("custom", "my-model") == "my-model"
    # ollama uses ollama_chat/ prefix
    assert pv.apply_prefix("ollama", "llama3") == "ollama_chat/llama3"
