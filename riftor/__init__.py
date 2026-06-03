"""riftor — an open-source offensive-security AI agent for your terminal.

Find the rift. Open it. Cross through.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version declared in pyproject.toml, read from
    # the installed package metadata. Avoids drift between the package version
    # and what `riftor --version` reports.
    __version__ = version("riftor")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
