"""Tests for the runner module."""

import pytest


def test_run_mux_turn_exists():
    """Verify run_mux_turn function exists in runner module."""
    from stokowski.runner import run_mux_turn
    assert callable(run_mux_turn)


def test_build_mux_args_basic():
    """Build mux run arguments with minimal parameters."""
    from stokowski.runner import build_mux_args
    from pathlib import Path
    
    args = build_mux_args(
        model=None,
        workspace_path=Path("/tmp/workspace"),
    )
    
    assert "npx" in args
    assert "mux" in args
    assert "run" in args
    assert "--quiet" in args