"""Tests for riftor.telemetry — all run with mocked SDKs and disabled env."""

import os
import sys
from unittest.mock import MagicMock, patch

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
        # With real keys baked, telemetry should be enabled
        # (env var already cleared above)
        assert t._disabled is not True
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old


def test_from_config_forwards_kwargs():
    """from_config passes through constructor keyword arguments."""
    from riftor.config import Config
    from riftor.telemetry import Telemetry

    old = os.environ.pop("RIFTOR_TELEMETRY_DISABLED", None)
    try:
        cfg = Config(telemetry=True)
        t = Telemetry.from_config(
            cfg,
            version="2.0.0",
            posthog_api_key="phc_test",
        )
        assert t._version == "2.0.0"
        assert t._posthog_key == "phc_test"
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old


def test_capture_exception_queues_event():
    """capture_exception enqueues an exception event."""
    from riftor.telemetry import Telemetry

    mock_posthog = MagicMock()

    old = os.environ.pop("RIFTOR_TELEMETRY_DISABLED", None)
    try:
        t = Telemetry(
            version="1.0.0",
            posthog_api_key="phc_test",
        )
        t._disabled = False

        with patch.dict(sys.modules, {"posthog": mock_posthog}):
            t._init_posthog()
            t.capture_exception(ValueError("something broke"))
            t.flush()

        mock_posthog.capture.assert_called_once()
        call = mock_posthog.capture.call_args.kwargs
        assert call["event"] == "exception"
        assert call["properties"]["type"] == "ValueError"
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old


def test_queue_and_flush_with_mock_posthog():
    """Events are queued and flushed via posthog.capture."""
    from riftor.telemetry import Telemetry

    mock_posthog = MagicMock()

    old = os.environ.pop("RIFTOR_TELEMETRY_DISABLED", None)
    try:
        t = Telemetry(
            version="1.0.0",
            posthog_api_key="phc_test",
            posthog_host="https://example.posthog.com",
        )
        t._disabled = False

        with patch.dict(sys.modules, {"posthog": mock_posthog}):
            t._init_posthog()

            t.track_session_start(model="test-model", theme="rift", yolo=False)
            t.track_tool_call("bash", allowed=True, is_error=False, duration=0.5)
            t.track_model_call("test-model", tokens_in=100, tokens_out=50)
            t.track_session_end(steps=3, tool_calls=1)
            t.capture_exception(ValueError("test err"))
            t.capture_message("test msg", level="warning")

            t.flush()

        assert mock_posthog.capture.call_count == 6
        events = [call.kwargs["event"] for call in mock_posthog.capture.call_args_list]
        assert events == [
            "session_start", "tool_call", "model_call",
            "session_end", "exception", "message",
        ]

        assert mock_posthog.host == "https://example.posthog.com"
        assert mock_posthog.api_key == "phc_test"
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old


def test_flush_handles_posthog_errors():
    """flush does not propagate posthog errors."""
    from riftor.telemetry import Telemetry

    mock_posthog = MagicMock()
    mock_posthog.capture.side_effect = RuntimeError("network down")

    old = os.environ.pop("RIFTOR_TELEMETRY_DISABLED", None)
    try:
        t = Telemetry(
            version="1.0.0",
            posthog_api_key="phc_test",
        )
        t._disabled = False

        with patch.dict(sys.modules, {"posthog": mock_posthog}):
            t._init_posthog()
            t.track_tool_call("bash", allowed=True)
            t.flush()

        mock_posthog.capture.assert_called_once()
    finally:
        if old is not None:
            os.environ["RIFTOR_TELEMETRY_DISABLED"] = old
