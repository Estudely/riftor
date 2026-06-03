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
