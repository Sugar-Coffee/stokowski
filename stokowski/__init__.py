"""Stokowski: Orchestrate Claude Code agents driven by Linear issues."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("stokowski")
except PackageNotFoundError:
    __version__ = "0.7.0"  # not installed as a package
