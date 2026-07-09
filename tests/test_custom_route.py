"""Unit tests for riftor.agent.custom_route."""

from riftor.agent import custom_route


def test_route_marker_codex():
    assert custom_route.route_marker("codex") == "riftorcodex-"


def test_route_marker_sanitizes_non_alnum():
    assert custom_route.route_marker("my-provider") == "riftormyprovider-"


def test_to_litellm_model_adds_marker():
    assert (
        custom_route.to_litellm_model("codex/gpt-5.5", provider_key="codex")
        == "codex/riftorcodex-gpt-5.5"
    )


def test_to_litellm_model_idempotent():
    once = custom_route.to_litellm_model("codex/gpt-5.5", provider_key="codex")
    assert custom_route.to_litellm_model(once, provider_key="codex") == once


def test_to_litellm_model_passes_through_non_matching():
    assert (
        custom_route.to_litellm_model("anthropic/claude-opus-4-8", provider_key="codex")
        == "anthropic/claude-opus-4-8"
    )
    assert custom_route.to_litellm_model("gpt-5.5", provider_key="codex") == "gpt-5.5"


def test_bare_model_strips_prefix_and_marker():
    assert custom_route.bare_model("codex/gpt-5.5-codex", provider_key="codex") == "gpt-5.5-codex"
    assert custom_route.bare_model("gpt-5.5-codex", provider_key="codex") == "gpt-5.5-codex"
    assert custom_route.bare_model("codex/riftorcodex-gpt-5.5", provider_key="codex") == "gpt-5.5"
    assert (
        custom_route.bare_model("riftorcodex-gpt-5.5-codex", provider_key="codex")
        == "gpt-5.5-codex"
    )


def test_round_trip():
    original = "codex/gpt-5.3-codex"
    marked = custom_route.to_litellm_model(original, provider_key="codex")
    assert custom_route.bare_model(marked, provider_key="codex") == "gpt-5.3-codex"
