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
