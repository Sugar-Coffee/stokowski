"""Tests for Orchestrator internals using mocks."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stokowski.config import StateConfig
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


@pytest.fixture
def gate_workflow_yaml(tmp_path):
    """Create a workflow with a gate state for auto-approve tests."""
    def _make(auto_approve="never"):
        p = tmp_path / "workflow.yaml"
        p.write_text(textwrap.dedent(f"""\
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
                  complete: review
              review:
                type: gate
                linear_state: review
                rework_to: implement
                auto_approve: {auto_approve}
                transitions:
                  approve: done
              done:
                type: terminal
                linear_state: terminal
        """))
        return p
    return _make


class TestAutoApproveGate:
    @pytest.mark.asyncio
    async def test_auto_approve_always(self, gate_workflow_yaml):
        orch = _make_orch(gate_workflow_yaml("always"))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state = AsyncMock(return_value=True)
        orch._tracker = mock_client

        # Set up state: issue is completing the implement state
        orch._issue_current_state[issue.id] = "review"
        orch._issue_state_runs[issue.id] = 1

        await orch._enter_gate(issue, "review")

        # Should NOT be in pending_gates (auto-approved)
        assert issue.id not in orch._pending_gates
        # Should have transitioned to terminal
        assert orch._issue_current_state.get(issue.id) != "review"

    @pytest.mark.asyncio
    async def test_auto_approve_when_no_questions_no_marker(self, gate_workflow_yaml):
        orch = _make_orch(gate_workflow_yaml("when_no_questions"))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state = AsyncMock(return_value=True)
        orch._tracker = mock_client

        orch._issue_current_state[issue.id] = "review"
        orch._issue_state_runs[issue.id] = 1
        orch._issue_needs_review[issue.id] = False

        await orch._enter_gate(issue, "review")

        # Should auto-approve (no NEEDS_REVIEW marker)
        assert issue.id not in orch._pending_gates

    @pytest.mark.asyncio
    async def test_auto_approve_when_no_questions_with_marker(self, gate_workflow_yaml):
        orch = _make_orch(gate_workflow_yaml("when_no_questions"))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state = AsyncMock(return_value=True)
        orch._tracker = mock_client

        orch._issue_current_state[issue.id] = "review"
        orch._issue_state_runs[issue.id] = 1
        orch._issue_needs_review[issue.id] = True  # Agent flagged NEEDS_REVIEW

        await orch._enter_gate(issue, "review")

        # Should stay in pending_gates (needs review)
        assert issue.id in orch._pending_gates

    @pytest.mark.asyncio
    async def test_auto_approve_never(self, gate_workflow_yaml):
        orch = _make_orch(gate_workflow_yaml("never"))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state = AsyncMock(return_value=True)
        orch._tracker = mock_client

        orch._issue_current_state[issue.id] = "review"
        orch._issue_state_runs[issue.id] = 1

        await orch._enter_gate(issue, "review")

        # Should stay in pending_gates (never auto-approve)
        assert issue.id in orch._pending_gates

    @pytest.mark.asyncio
    async def test_needs_review_flag_cleaned_up(self, gate_workflow_yaml):
        orch = _make_orch(gate_workflow_yaml("when_no_questions"))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.update_issue_state = AsyncMock(return_value=True)
        orch._tracker = mock_client

        orch._issue_current_state[issue.id] = "review"
        orch._issue_state_runs[issue.id] = 1
        orch._issue_needs_review[issue.id] = True

        await orch._enter_gate(issue, "review")

        # Flag should be cleaned up regardless
        assert issue.id not in orch._issue_needs_review


@pytest.fixture
def concurrent_workflow_yaml(tmp_path):
    """Create a workflow with per-state max_concurrent."""
    p = tmp_path / "workflow.yaml"
    p.write_text(textwrap.dedent("""\
        tracker:
          kind: linear
          api_key: test_key
          team_key: DEV
        states:
          triage:
            type: agent
            prompt: prompts/triage.md
            linear_state: active
            max_concurrent: 1
            transitions:
              complete: implement
          implement:
            type: agent
            prompt: prompts/impl.md
            linear_state: active
            max_concurrent: 2
            transitions:
              complete: done
          done:
            type: terminal
            linear_state: terminal
    """))
    return p


class TestPerStateConcurrency:
    def test_max_concurrent_parsed(self, concurrent_workflow_yaml):
        orch = _make_orch(concurrent_workflow_yaml)
        assert orch.cfg.states["triage"].max_concurrent == 1
        assert orch.cfg.states["implement"].max_concurrent == 2
        assert orch.cfg.states["done"].max_concurrent is None

    def test_state_limit_blocks_dispatch(self, concurrent_workflow_yaml):
        """When max_concurrent is reached for a state, additional issues are skipped."""
        orch = _make_orch(concurrent_workflow_yaml)

        # Simulate one agent already running in triage
        running_attempt = RunAttempt(issue_id="issue-running", issue_identifier="DEV-0")
        orch.running["issue-running"] = running_attempt
        orch._issue_current_state["issue-running"] = "triage"
        orch._last_issues["issue-running"] = _make_issue(id="issue-running", identifier="DEV-0")

        # New issue wants to enter triage
        issue = _make_issue(id="issue-new", identifier="DEV-1")
        orch._issue_current_state["issue-new"] = "triage"

        # Manually run the per-state concurrency check logic
        state_key = "triage"
        state_cfg = orch.cfg.states.get(state_key)
        state_limit = state_cfg.max_concurrent if state_cfg and state_cfg.max_concurrent is not None else None
        state_count = sum(
            1 for r in orch.running.values()
            if orch._issue_current_state.get(r.issue_id, "") == state_key
        )

        assert state_limit == 1
        assert state_count == 1
        assert state_count >= state_limit  # Would be skipped in dispatch

    def test_different_state_not_blocked(self, concurrent_workflow_yaml):
        """An issue in a different state is not blocked by another state's limit."""
        orch = _make_orch(concurrent_workflow_yaml)

        # One agent running in triage (limit=1)
        running_attempt = RunAttempt(issue_id="issue-running", issue_identifier="DEV-0")
        orch.running["issue-running"] = running_attempt
        orch._issue_current_state["issue-running"] = "triage"

        # New issue wants implement (limit=2)
        state_key = "implement"
        state_cfg = orch.cfg.states.get(state_key)
        state_limit = state_cfg.max_concurrent
        state_count = sum(
            1 for r in orch.running.values()
            if orch._issue_current_state.get(r.issue_id, "") == state_key
        )

        assert state_limit == 2
        assert state_count == 0
        assert state_count < state_limit  # Would be dispatched

    def test_inline_overrides_agent_level(self, concurrent_workflow_yaml):
        """StateConfig.max_concurrent takes precedence over agent.max_concurrent_agents_by_state."""
        orch = _make_orch(concurrent_workflow_yaml)
        # Set a conflicting agent-level limit
        orch.cfg.agent.max_concurrent_agents_by_state["triage"] = 10

        state_cfg = orch.cfg.states.get("triage")
        # Inline should win
        state_limit = (
            state_cfg.max_concurrent
            if state_cfg and state_cfg.max_concurrent is not None
            else orch.cfg.agent.max_concurrent_agents_by_state.get("triage")
        )
        assert state_limit == 1  # inline, not 10
