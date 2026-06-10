"""Shared pytest fixtures for riftor's offline test suite."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Disable telemetry for all tests — no network, no keys needed.
os.environ["RIFTOR_TELEMETRY_DISABLED"] = "1"

import pytest

from riftor.engagement import Engagement
from riftor.tools import ToolContext


@pytest.fixture
def tmp_workdir():
    with tempfile.TemporaryDirectory(prefix="riftor-test-") as d:
        yield Path(d)


@pytest.fixture
def engagement(tmp_workdir):
    return Engagement(tmp_workdir)


@pytest.fixture
def toolctx(tmp_workdir, engagement):
    return ToolContext(workdir=tmp_workdir, engagement=engagement)
