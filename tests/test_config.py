"""Config: secure file perms, model validation, credential detection."""

from __future__ import annotations

import os
import stat

import riftor.config as cfgmod
from riftor.config import Config


def test_save_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(api_key="sk-secret")
    cfg.save()
    mode = stat.S_IMODE(os.stat(cfgmod.CONFIG_PATH).st_mode)
    assert mode == 0o600, oct(mode)
    assert "sk-secret" in cfgmod.CONFIG_PATH.read_text()


def test_model_warning():
    assert Config(model="anthropic/claude-sonnet-4-6").model_warning() is None
    assert Config(model="totally/madeup-xyz").model_warning() is not None


def test_provider_env():
    assert Config(model="anthropic/claude-sonnet-4-6").provider_env() == "ANTHROPIC_API_KEY"
    assert Config(model="openai/gpt-4o").provider_env() == "OPENAI_API_KEY"


def test_has_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not Config(model="anthropic/claude-sonnet-4-6").has_credentials()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert Config(model="anthropic/claude-sonnet-4-6").has_credentials()
    # local ollama always has "credentials"
    assert Config(model="ollama_chat/llama3").has_credentials()
    # explicit key
    assert Config(model="anthropic/claude-sonnet-4-6", api_key="k").has_credentials()


def test_roundtrip_new_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    Config(max_steps=24, rate_limit_per_min=10, max_result_chars=5000).save()
    loaded = Config.load()
    assert loaded.max_steps == 24
    assert loaded.rate_limit_per_min == 10
    assert loaded.max_result_chars == 5000


def test_reasoning_effort_clamps_invalid_to_medium(tmp_path, monkeypatch):
    # A hand-edited config with a bogus effort must not forward garbage to litellm;
    # it clamps to the safe default while keeping the rest of the config intact.
    assert Config(reasoning_effort="invalid").reasoning_effort == "medium"
    assert Config(reasoning_effort="").reasoning_effort == "medium"
    # valid levels pass through untouched
    for level in ("none", "low", "medium", "high"):
        assert Config(reasoning_effort=level).reasoning_effort == level
    # survives a round-trip from a manually-corrupted file (other fields preserved)
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text(
        '[riftor]\nmodel = "openai/gpt-5.5"\nreasoning_effort = "bogus"\n'
    )
    loaded = Config.load()
    assert loaded.reasoning_effort == "medium"
    assert loaded.model == "openai/gpt-5.5"


def test_load_keybindings(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "KEYBINDINGS_PATH", tmp_path / "keybindings.toml")
    assert cfgmod.load_keybindings() == {}
    (tmp_path / "keybindings.toml").write_text('[keybindings]\nclear = "ctrl+k"\n')
    assert cfgmod.load_keybindings() == {"clear": "ctrl+k"}


def test_providers_table_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {
        "anthropic": cfgmod.ProviderCreds(api_key="sk-ant-x"),
        "ollama": cfgmod.ProviderCreds(api_base="http://localhost:11434"),
    }
    cfg.save()
    loaded = Config.load()
    assert loaded.providers["anthropic"].api_key == "sk-ant-x"
    assert loaded.providers["ollama"].api_base == "http://localhost:11434"


def test_old_flat_config_loads_without_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text(
        '[riftor]\nmodel = "openai/gpt-5.5"\ntemperature = 0.3\n'
    )
    loaded = Config.load()
    assert loaded.model == "openai/gpt-5.5"
    assert loaded.providers == {}


def test_providers_table_is_owner_only(tmp_path, monkeypatch):
    import os
    import stat
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(model="anthropic/claude-opus-4-8")
    cfg.providers = {"anthropic": cfgmod.ProviderCreds(api_key="sk-secret-xyz")}
    cfg.save()
    mode = stat.S_IMODE(os.stat(cfgmod.CONFIG_PATH).st_mode)
    assert mode == 0o600, oct(mode)
    assert "sk-secret-xyz" in cfgmod.CONFIG_PATH.read_text()


def test_malformed_providers_table_degrades_to_defaults(tmp_path, monkeypatch):
    # A hand-corrupted [providers] table must not crash startup; fall back to defaults.
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text(
        '[riftor]\nmodel = "openai/gpt-5.5"\n\n[providers]\nanthropic = "not-a-table"\n'
    )
    cfg = Config.load()  # must NOT raise
    assert isinstance(cfg, Config)
    assert cfg.providers == {}


def test_malformed_toml_syntax_degrades_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("this is not valid toml = = =\n")
    cfg = Config.load()  # must NOT raise
    assert isinstance(cfg, Config)


def test_empty_providers_emits_no_section(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    Config(model="anthropic/claude-opus-4-8").save()
    text = (tmp_path / "config.toml").read_text()
    assert "[providers" not in text


def test_creds_for_precedence(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # 1. per-provider table wins
    cfg = Config(model="anthropic/claude-opus-4-8", api_key="legacy-global")
    cfg.providers = {"anthropic": cfgmod.ProviderCreds(api_key="from-table",
                                                       api_base="https://t/")}
    assert cfg.creds_for("anthropic/claude-opus-4-8") == ("from-table", "https://t/")

    # 2. legacy global key, no table entry
    cfg2 = Config(model="anthropic/claude-opus-4-8", api_key="legacy-global",
                  api_base="https://legacy/")
    assert cfg2.creds_for("anthropic/claude-opus-4-8") == ("legacy-global", "https://legacy/")

    # 3. env var fallback for the key
    cfg3 = Config(model="anthropic/claude-opus-4-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert cfg3.creds_for("anthropic/claude-opus-4-8") == ("from-env", None)

    # 4. nothing
    cfg4 = Config(model="anthropic/claude-opus-4-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cfg4.creds_for("anthropic/claude-opus-4-8") == (None, None)


def test_has_credentials_via_provider_table(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(model="openai/gpt-5.5")
    assert not cfg.has_credentials()
    cfg.providers = {"openai": cfgmod.ProviderCreds(api_key="sk-x")}
    assert cfg.has_credentials()


def test_creds_for_env_is_model_specific(monkeypatch):
    # The env tier must resolve the PASSED model's provider, not self.model's.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env")
    cfg = Config(model="anthropic/claude-opus-4-8")  # active model is anthropic
    # resolving an OPENAI model should find the OpenAI env key
    assert cfg.creds_for("openai/gpt-5.5") == ("sk-openai-env", None)


def test_creds_for_base_only_table_entry():
    # Ollama shape: table entry has only a base, no key.
    cfg = Config(model="ollama_chat/llama3")
    cfg.providers = {"ollama": cfgmod.ProviderCreds(api_base="http://localhost:11434")}
    assert cfg.creds_for("ollama_chat/llama3") == (None, "http://localhost:11434")
    # and ollama is always "credentialed" via its prefix branch
    assert cfg.has_credentials()


def test_display_settings_default_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.toml")
    # defaults
    fresh = Config()
    assert fresh.show_thinking is True
    assert fresh.show_tool_output is True
    assert fresh.reasoning_effort == "medium"
    # round-trip non-default values
    Config(show_thinking=False, show_tool_output=False, reasoning_effort="high").save()
    loaded = Config.load()
    assert loaded.show_thinking is False
    assert loaded.show_tool_output is False
    assert loaded.reasoning_effort == "high"


def test_codex_creds_are_none(monkeypatch):
    # Codex supplies no api_key/api_base from riftor; the litellm handler reads
    # ~/.codex/auth.json itself. A stale global key must never leak to it.
    cfg = Config(model="codex/gpt-5.5-codex", api_key="leftover-global")
    assert cfg.creds_for("codex/gpt-5.5-codex") == (None, None)


def test_codex_has_credentials_like_ollama():
    # Treated like ollama: never block the UI on a key. Real login validity is
    # surfaced as status, not a hard gate.
    cfg = Config(model="codex/gpt-5.5-codex")
    assert cfg.has_credentials()


def test_codex_model_warning_is_none():
    # codex/ is a known provider prefix; no spurious "unknown prefix" warning.
    assert Config(model="codex/gpt-5.5-codex").model_warning() is None


def test_codex_provider_env_is_none():
    assert Config(model="codex/gpt-5.5-codex").provider_env() is None


def test_creds_for_openrouter_routed_id_is_miskeyed_known_limitation(monkeypatch):
    # KNOWN LIMITATION (deferred to picker tasks): a slash-routed OpenRouter id like
    # "openai/gpt-5.5" is classified by prefix as the "openai" provider, so creds stored
    # under the "openrouter" table are NOT found. This test pins the CURRENT behavior so
    # the wrinkle stays visible until the picker/store layer resolves it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = Config(model="openrouter/auto")
    cfg.providers = {"openrouter": cfgmod.ProviderCreds(api_key="sk-or")}
    assert cfg.creds_for("openai/gpt-5.5") == (None, None)


def test_wordlists_dir_defaults_none_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr("riftor.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("riftor.config.CONFIG_PATH", tmp_path / "config.toml")
    from riftor.config import Config

    cfg = Config()
    assert cfg.wordlists_dir is None

    cfg.wordlists_dir = "/opt/wordlists"
    cfg.save()
    import tomllib
    data = tomllib.loads((tmp_path / "config.toml").read_text())
    assert data["riftor"]["wordlists_dir"] == "/opt/wordlists"


def test_plugin_fields_default_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("riftor.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("riftor.config.CONFIG_PATH", tmp_path / "config.toml")
    from riftor.config import Config

    cfg = Config()
    assert cfg.plugins_enabled is True
    assert cfg.plugins_allow == [] and cfg.plugins_deny == []

    cfg.plugins_enabled = False
    cfg.plugins_deny = ["sketchy"]
    cfg.save()

    import tomllib
    data = tomllib.loads((tmp_path / "config.toml").read_text())
    assert data["riftor"]["plugins_enabled"] is False
    assert data["riftor"]["plugins_deny"] == ["sketchy"]
