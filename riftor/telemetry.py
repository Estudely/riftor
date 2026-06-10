"""Opt-out telemetry via PostHog for usage and error analytics.

Disabled when:
  1. ``RIFTOR_TELEMETRY_DISABLED=1`` env var is set
  2. ``config.telemetry`` is ``False``
  3. Keys are empty / SDK import fails

All operations are wrapped in try/except — telemetry errors never propagate.
"""

from __future__ import annotations

import hashlib
import os
import queue
import socket
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from riftor.config import Config


class Telemetry:
    """Thin facade over PostHog with silent degradation."""

    def __init__(
        self,
        version: str = "0.0.0",
        *,
        posthog_api_key: str | None = None,
        posthog_host: str = "",
    ) -> None:
        self._version = version
        self._disabled = bool(os.environ.get("RIFTOR_TELEMETRY_DISABLED"))
        self._queue: queue.Queue = queue.Queue()
        self._started_at = time.monotonic()

        if self._disabled:
            return

        try:
            from riftor._telemetry_keys import POSTHOG_API_KEY, POSTHOG_HOST
        except ImportError:
            self._disabled = True
            return

        self._posthog_key = posthog_api_key or POSTHOG_API_KEY
        self._posthog_host = posthog_host or POSTHOG_HOST

        if not self._posthog_key:
            self._disabled = True
            return

        self._init_posthog()

    @classmethod
    def from_config(
        cls,
        config: "Config",
        version: str = "0.0.0",
        **kwargs: object,
    ) -> "Telemetry":
        t = cls(version=version, **kwargs)  # type: ignore[arg-type]
        if not config.telemetry:
            t._disabled = True
        return t

    # -- PostHog ---------------------------------------------------------------

    def _init_posthog(self) -> None:
        if not self._posthog_key:
            return
        try:
            import posthog  # type: ignore[import-untyped]

            posthog.api_key = self._posthog_key
            posthog.host = self._posthog_host
        except Exception:  # noqa: BLE001
            pass

    # -- Session events --------------------------------------------------------

    def track_session_start(
        self, *, model: str = "", theme: str = "", yolo: bool = False
    ) -> None:
        if self._disabled:
            return
        self._enqueue("session_start", {
            "version": self._version,
            "platform": self._platform(),
            "model": model,
            "theme": theme,
            "yolo": yolo,
        })

    def track_session_end(self, *, steps: int = 0, tool_calls: int = 0) -> None:
        if self._disabled:
            return
        duration = time.monotonic() - self._started_at
        self._enqueue("session_end", {
            "duration_s": round(duration, 1),
            "steps": steps,
            "tool_calls": tool_calls,
        })

    # -- Tool events -----------------------------------------------------------

    def track_tool_call(
        self,
        name: str,
        *,
        allowed: bool,
        is_error: bool = False,
        duration: float = 0.0,
    ) -> None:
        if self._disabled:
            return
        self._enqueue("tool_call", {
            "tool": name,
            "allowed": allowed,
            "is_error": is_error,
            "duration_s": round(duration, 3),
        })

    # -- Model events ----------------------------------------------------------

    def track_model_call(
        self,
        model: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        if self._disabled:
            return
        self._enqueue("model_call", {
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        })

    # -- Error reporting -------------------------------------------------------

    def capture_exception(self, exc: BaseException) -> None:
        if self._disabled:
            return
        self._enqueue("exception", {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        })

    def capture_message(self, message: str, level: str = "info") -> None:
        if self._disabled:
            return
        self._enqueue("message", {
            "message": message[:500],
            "level": level,
        })

    # -- Queue + flush ---------------------------------------------------------

    def _enqueue(self, event: str, properties: dict) -> None:
        try:
            self._queue.put((event, properties))
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        if self._disabled:
            return
        try:
            import posthog  # type: ignore[import-untyped]
        except ImportError:
            return

        while True:
            try:
                event, properties = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                posthog.capture(
                    distinct_id=self._distinct_id(),
                    event=event,
                    properties=properties,
                )
            except Exception:  # noqa: BLE001
                pass

    # -- Helpers ---------------------------------------------------------------

    def _distinct_id(self) -> str:
        raw = socket.gethostname() + (
            os.environ.get("USER", os.environ.get("LOGNAME", ""))
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _platform(self) -> str:
        return (
            f"{sys.platform}-"
            f"{sys.implementation.name}{sys.version_info.major}.{sys.version_info.minor}"
        )
