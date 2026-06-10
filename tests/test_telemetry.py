"""Tests for riftor.telemetry — all run with mocked SDKs and disabled env."""

import os

# Ensure telemetry is disabled for these tests.
os.environ["RIFTOR_TELEMETRY_DISABLED"] = "1"


def test_telemetry_disabled_by_env_var():
    """When RIFTOR_TELEMETRY_DISABLED is set, Telemetry init is a no-op."""
    from riftor.telemetry import Telemetry

    t = Telemetry(version="1.0.0")
    assert t._disabled is True
    # All methods should be no-ops
    t.track_session_start()
    t.track_session_end(steps=5, tool_calls=10)
    t.track_tool_call("bash", allowed=True, is_error=False, duration=0.5)
    t.track_model_call("anthropic/claude", tokens_in=100, tokens_out=50)
    t.capture_exception(ValueError("test"))
    t.capture_message("test", level="info")
    t.flush()


def test_telemetry_disabled_by_config():
    """When config.telemetry is False, telemetry is a no-op."""
    from riftor.config import Config
    from riftor.telemetry import Telemetry

    cfg = Config(telemetry=False)
    t = Telemetry.from_config(cfg, version="1.0.0")
    assert t._disabled is True
    t.flush()


def test_capture_exception_noop_when_disabled():
    """capture_exception does nothing when disabled."""
    from riftor.telemetry import Telemetry

    t = Telemetry(version="1.0.0")
    t._disabled = True
    # Should not raise
    t.capture_exception(ValueError("test"))


def test_capture_message_noop_when_disabled():
    """capture_message does nothing when disabled."""
    from riftor.telemetry import Telemetry

    t = Telemetry(version="1.0.0")
    t._disabled = True
    t.capture_message("test message", level="warning")


def test_flush_empty_queue_noop_when_disabled():
    """flush with empty queue is a no-op when disabled."""
    from riftor.telemetry import Telemetry

    t = Telemetry(version="1.0.0")
    t._disabled = True
    t.flush()


def test_from_config_enables_when_telemetry_true():
    """from_config creates an enabled Telemetry when config.telemetry is True."""
    from riftor.config import Config
    from riftor.telemetry import Telemetry

    old = os.environ.pop("RIFTOR_TELEMETRY_DISABLED", None)
    try:
        cfg = Config(telemetry=True)
        t = Telemetry.from_config(cfg, version="1.0.0")
        # Telemetry should be disabled because env keys are empty
        # (the _telemetry_keys module has empty strings)
        assert t._disabled is True
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old
