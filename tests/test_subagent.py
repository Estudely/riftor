"""Tests for the Baaj/Chakla subagent feature (all offline)."""
from __future__ import annotations

from riftor.config import Config


def test_config_has_chakla_defaults():
    cfg = Config()
    assert cfg.chakla_model == "anthropic/claude-haiku-4-5-20251001"
    assert cfg.chakla_max_workers == 5
    assert cfg.chakla_max_steps == 8
    assert cfg.chakla_timeout_s == 300
    assert cfg.label_main == "Baaj"
    assert cfg.label_worker == "Chakla"


def test_config_toml_roundtrips_chakla_fields():
    cfg = Config(chakla_model="anthropic/claude-haiku-4-5-20251001", chakla_max_workers=3)
    toml = cfg._to_toml()
    assert 'chakla_model = "anthropic/claude-haiku-4-5-20251001"' in toml
    assert "chakla_max_workers = 3" in toml
    assert "chakla_max_steps = 8" in toml
    assert "chakla_timeout_s = 300" in toml
    assert 'label_main = "Baaj"' in toml
    assert 'label_worker = "Chakla"' in toml
