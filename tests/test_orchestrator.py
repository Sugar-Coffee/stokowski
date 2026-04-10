"""Tests for Orchestrator internals using mocks."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stokowski.models import BlockerRef, Issue, RunAttempt
from stokowski.orchestrator import Orchestrator, _is_rate_limit_error


@pytest.fixture
def workflow_yaml(tmp_path):
    """Create a minimal workflow file and return the Orchestrator."""
    p = tmp_path / "workflow.yaml"
    p.write_text(textwrap.dedent("""\
        tracker:
          kind: linear
          api_key: test_key
          team_key: DEV
        states:
          implement:
            type: agent
            prompt: prompts/impl.md
            linear_state: active
            transitions:
              complete: done
          done:
            type: terminal
            linear_state: terminal
    """))
    return p


def _make_orch(workflow_path: Path) -> Orchestrator:
    orch = Orchestrator(workflow_path)
    orch._load_workflow()
    return orch


def _make_issue(**kwargs) -> Issue:
    defaults = dict(
        id="issue-1",
        identifier="DEV-1",
        title="Test issue",
        state="In Progress",
    )
    defaults.update(kwargs)
    return Issue(**defaults)


class TestSpawnBackground:
    @pytest.mark.asyncio
    async def test_task_added_and_cleaned_up(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        completed = asyncio.Event()

        async def quick():
            completed.set()

        orch._spawn_background(quick())
        assert len(orch._background_tasks) == 1

        # Let the task complete
        await asyncio.sleep(0.05)
        assert completed.is_set()
        # done_callback should have removed it
        assert len(orch._background_tasks) == 0


class TestIsEligible:
    def test_valid_issue_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue()
        assert orch._is_eligible(issue) is True

    def test_missing_id_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(id="")
        assert orch._is_eligible(issue) is False

    def test_missing_title_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(title="")
        assert orch._is_eligible(issue) is False

    def test_missing_state_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(state="")
        assert orch._is_eligible(issue) is False

    def test_completed_issue_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue()
        orch.completed.add(issue.id)
        assert orch._is_eligible(issue) is False

    def test_running_issue_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue()
        orch.running[issue.id] = RunAttempt(issue_id=issue.id, issue_identifier=issue.identifier)
        assert orch._is_eligible(issue) is False

    def test_claimed_issue_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue()
        orch.claimed.add(issue.id)
        assert orch._is_eligible(issue) is False

    def test_terminal_state_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(state="Done")
        assert orch._is_eligible(issue) is False

    def test_wrong_state_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(state="Backlog")
        assert orch._is_eligible(issue) is False

    def test_todo_state_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(state="Todo")
        assert orch._is_eligible(issue) is True

    def test_filter_labels_match(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch.cfg.filter_labels = ["bug"]
        issue = _make_issue(labels=["bug", "urgent"])
        assert orch._is_eligible(issue) is True

    def test_filter_labels_no_match(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch.cfg.filter_labels = ["bug"]
        issue = _make_issue(labels=["feature"])
        assert orch._is_eligible(issue) is False

    def test_exclude_labels_blocks(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch.cfg.exclude_labels = ["wontfix"]
        issue = _make_issue(labels=["wontfix"])
        assert orch._is_eligible(issue) is False

    def test_exclude_labels_allows(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch.cfg.exclude_labels = ["wontfix"]
        issue = _make_issue(labels=["bug"])
        assert orch._is_eligible(issue) is True

    def test_blocked_todo_not_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(
            state="Todo",
            blocked_by=[BlockerRef(id="b1", identifier="DEV-0", state="In Progress")],
        )
        assert orch._is_eligible(issue) is False

    def test_blocked_todo_resolved_eligible(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(
            state="Todo",
            blocked_by=[BlockerRef(id="b1", identifier="DEV-0", state="Done")],
        )
        assert orch._is_eligible(issue) is True


class TestIsRateLimitError:
    def test_rate_limit_detected(self):
        assert _is_rate_limit_error("Error: rate limit exceeded") is True

    def test_429_detected(self):
        assert _is_rate_limit_error("HTTP 429 Too Many Requests") is True

    def test_overloaded_detected(self):
        assert _is_rate_limit_error("Service overloaded, try later") is True

    def test_normal_error_not_rate_limit(self):
        assert _is_rate_limit_error("Syntax error in code") is False

    def test_empty_string(self):
        assert _is_rate_limit_error("") is False


class TestReconciliationFiltersSyntheticIds:
    @pytest.mark.asyncio
    async def test_synthetic_ids_excluded_from_reconciliation(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch._running = True

        # Add synthetic and real issues to running
        orch.running["schedule:learn:2026-04-10"] = MagicMock()
        orch.running["pr:3366"] = MagicMock()
        orch.running["real-uuid-123"] = MagicMock()

        mock_client = AsyncMock()
        mock_client.fetch_issue_states_by_ids = AsyncMock(return_value={})
        orch._tracker = mock_client

        await orch._reconcile()

        # Only the real UUID should be passed to the API
        mock_client.fetch_issue_states_by_ids.assert_called_once()
        call_args = mock_client.fetch_issue_states_by_ids.call_args[0][0]
        assert "schedule:learn:2026-04-10" not in call_args
        assert "pr:3366" not in call_args
        assert "real-uuid-123" in call_args

    @pytest.mark.asyncio
    async def test_all_synthetic_skips_reconciliation(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)
        orch._running = True

        orch.running["schedule:learn:2026-04-10"] = MagicMock()
        orch.running["pr:3366"] = MagicMock()

        mock_client = AsyncMock()
        orch._tracker = mock_client

        await orch._reconcile()

        # Should not call API at all
        mock_client.fetch_issue_states_by_ids.assert_not_called()
