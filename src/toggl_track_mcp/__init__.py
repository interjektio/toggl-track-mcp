"""Model Context Protocol server for Toggl Track."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("toggl-track-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
