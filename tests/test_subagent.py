"""Tests for the Baaj/Chakla subagent feature (all offline)."""
from __future__ import annotations

from riftor.config import Config
from riftor.terminology import terminology


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


def test_terminology_defaults():
    t = terminology(Config())
    assert t["main"] == "Baaj"
    assert t["worker"] == "Chakla"
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"


def test_terminology_respects_renamed_labels():
    t = terminology(Config(label_main="Hawk", label_worker="Finch"))
    assert t["main"] == "Hawk"
    assert t["worker"] == "Finch"
    # emoji are fixed branding; only the text labels are renameable
    assert t["main_emoji"] == "🦅"
    assert t["worker_emoji"] == "🐦"
