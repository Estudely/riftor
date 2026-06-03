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
    # OpenRouter: litellm needs the openrouter/ prefix prepended to the full slug.
    assert pv.apply_prefix("openrouter", "openrouter/auto") == "openrouter/auto"  # already prefixed
    assert pv.apply_prefix("openrouter", "anthropic/claude-opus-4.8") == "openrouter/anthropic/claude-opus-4.8"
    assert pv.apply_prefix("openrouter", "openai/gpt-5.5") == "openrouter/openai/gpt-5.5"
    # custom -> no prefix
    assert pv.apply_prefix("custom", "my-model") == "my-model"
    # ollama uses ollama_chat/ prefix
    assert pv.apply_prefix("ollama", "llama3") == "ollama_chat/llama3"
    # non-openrouter ids that already contain a slash still pass through unchanged
    assert pv.apply_prefix("custom", "my-org/my-model") == "my-org/my-model"


def test_openrouter_round_trips_to_openrouter_key():
    full = pv.apply_prefix("openrouter", "openai/gpt-5.5")
    assert full == "openrouter/openai/gpt-5.5"
    assert pv.provider_key_for_model(full) == "openrouter"


def test_merge_pins_curated_first_and_dedupes():
    merged = pv._merge(["claude-opus-4-8", "claude-sonnet-4-6"],
                       ["zzz-model", "claude-opus-4-8", "aaa-model"])
    # curated first, then live with curated removed, order otherwise preserved
    assert merged == ["claude-opus-4-8", "claude-sonnet-4-6", "zzz-model", "aaa-model"]


def test_fetch_none_kind_returns_curated_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not hit the network for list_kind=none")
    monkeypatch.setattr(pv.urllib.request, "urlopen", boom)
    res = pv.fetch_models("anthropic", None, None)
    assert res.source == "curated"
    assert res.error is None
    assert res.models == pv.PROVIDER_DEFAULTS["anthropic"]


def test_fetch_openai_parses_and_merges(monkeypatch):
    payload = {"data": [{"id": "gpt-5.5"}, {"id": "gpt-9-new"}]}
    monkeypatch.setattr(pv, "_http_get_json", lambda url, headers, timeout: payload)
    res = pv.fetch_models("openai", "https://api.openai.com/v1", "sk-x")
    assert res.source == "merged"
    assert res.error is None
    assert res.models[0] == "gpt-5.5"           # curated pinned first
    assert "gpt-9-new" in res.models            # live id surfaced


def test_fetch_ollama_parses_tags(monkeypatch):
    payload = {"models": [{"name": "llama3.3"}, {"name": "qwen3"}]}
    monkeypatch.setattr(pv, "_http_get_json", lambda url, headers, timeout: payload)
    res = pv.fetch_models("ollama", "http://localhost:11434", None)
    assert res.source == "live"                 # ollama has no curated list
    assert res.models == ["llama3.3", "qwen3"]


def test_fetch_network_error_falls_back_to_curated(monkeypatch):
    def boom(url, headers, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr(pv, "_http_get_json", boom)
    res = pv.fetch_models("openai", "https://api.openai.com/v1", "sk-x")
    assert res.error is not None
    assert res.models == pv.PROVIDER_DEFAULTS["openai"]   # curated fallback
    assert res.source == "curated"


def test_fetch_curated_result_is_a_copy_not_the_module_list():
    res = pv.fetch_models("anthropic", None, None)
    assert res.models == pv.PROVIDER_DEFAULTS["anthropic"]
    assert res.models is not pv.PROVIDER_DEFAULTS["anthropic"]  # defensive copy
