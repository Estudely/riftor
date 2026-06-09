"""Browser tool behavior: context wiring, lifecycle, snapshot, scope, screenshots."""

from __future__ import annotations

import pytest  # noqa: F401  # used by browser tests added in later tasks

from riftor.tools import ToolContext


def test_toolcontext_has_browser_field_default_none():
    ctx = ToolContext()
    assert ctx.browser is None
