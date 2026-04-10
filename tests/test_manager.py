"""Tests for the multi-workflow Manager."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stokowski.manager import Manager


@pytest.fixture
def tmp_workflows(tmp_path):
    """Create minimal workflow YAML files."""
    def _write(names: list[str]) -> dict[str, Path]:
        paths = {}
        for name in names:
            p = tmp_path / f"{name}.yaml"
            p.write_text(textwrap.dedent(f"""\
                tracker:
                  kind: linear
                  api_key: test_key
                  team_key: DEV
                states:
                  work:
                    type: agent
                    prompt: prompts/work.md
                    linear_state: active
                    transitions:
                      complete: done
                  done:
                    type: terminal
                    linear_state: terminal
            """))
            paths[name] = p
        return paths
    return _write


class TestManagerInit:
    def test_creates_orchestrators_for_each_workflow(self, tmp_workflows):
        paths = tmp_workflows(["alpha", "beta"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        assert "alpha" in mgr.orchestrators
        assert "beta" in mgr.orchestrators
        assert len(mgr.orchestrators) == 2

    def test_all_workflows_start_stopped(self, tmp_workflows):
        paths = tmp_workflows(["one"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        assert mgr._workflow_status["one"] == "stopped"

    def test_shared_raw_propagated(self, tmp_workflows):
        paths = tmp_workflows(["w"])
        shared = {"tracker": {"kind": "linear", "api_key": "shared_key"}}
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths, shared_raw=shared)
        assert mgr.shared_raw == shared

    def test_workflow_enabled_stored(self, tmp_workflows):
        paths = tmp_workflows(["a", "b"])
        enabled = {"a": True, "b": False}
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths, workflow_enabled=enabled)
        assert mgr._workflow_enabled == enabled


class TestOnWorkflowDone:
    def _make_manager(self, tmp_workflows):
        paths = tmp_workflows(["test"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            return Manager(paths)

    def test_cancelled_task_sets_stopped(self, tmp_workflows):
        mgr = self._make_manager(tmp_workflows)
        mgr._workflow_status["test"] = "running"

        task = MagicMock()
        task.cancelled.return_value = True
        task.exception.return_value = None

        mgr._on_workflow_done("test", task)
        assert mgr._workflow_status["test"] == "stopped"

    def test_failed_task_sets_failed(self, tmp_workflows):
        mgr = self._make_manager(tmp_workflows)
        mgr._workflow_status["test"] = "running"

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")

        mgr._on_workflow_done("test", task)
        assert mgr._workflow_status["test"] == "failed"

    def test_normal_completion_sets_stopped(self, tmp_workflows):
        mgr = self._make_manager(tmp_workflows)
        mgr._workflow_status["test"] = "running"

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        mgr._on_workflow_done("test", task)
        assert mgr._workflow_status["test"] == "stopped"

    def test_already_stopped_stays_stopped(self, tmp_workflows):
        """If stop_workflow already set status to stopped, don't overwrite."""
        mgr = self._make_manager(tmp_workflows)
        mgr._workflow_status["test"] = "stopped"

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        mgr._on_workflow_done("test", task)
        assert mgr._workflow_status["test"] == "stopped"


class TestStartWorkflow:
    def test_start_unknown_workflow_returns_false(self, tmp_workflows):
        paths = tmp_workflows(["real"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        assert mgr.start_workflow("nonexistent") is False

    @pytest.mark.asyncio
    async def test_start_workflow_sets_running(self, tmp_workflows):
        paths = tmp_workflows(["w"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        # Mock the orchestrator's start to be a simple coroutine
        mgr.orchestrators["w"].start = AsyncMock()
        result = mgr.start_workflow("w")
        assert result is True
        assert mgr._workflow_status["w"] == "running"
        assert "w" in mgr._workflow_tasks


class TestStopWorkflow:
    @pytest.mark.asyncio
    async def test_stop_unknown_returns_false(self, tmp_workflows):
        paths = tmp_workflows(["x"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        result = await mgr.stop_workflow("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_sets_stopped(self, tmp_workflows):
        paths = tmp_workflows(["x"])
        with patch("stokowski.manager.Manager._init_shared_tracker"):
            mgr = Manager(paths)
        mgr.orchestrators["x"].stop = AsyncMock()
        mgr._workflow_status["x"] = "running"
        result = await mgr.stop_workflow("x")
        assert result is True
        assert mgr._workflow_status["x"] == "stopped"
