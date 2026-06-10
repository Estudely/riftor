"""Shared pytest fixtures for riftor's offline test suite."""

from __future__ import annotations

import tempfile
from pathlib import Path

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
