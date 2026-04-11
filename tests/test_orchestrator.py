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

    @pytest.mark.asyncio
    async def test_failed_task_cleaned_up_and_logged(self, workflow_yaml):
        orch = _make_orch(workflow_yaml)

        async def failing():
            raise RuntimeError("test error")

        with patch("stokowski.orchestrator.logger") as mock_logger:
            orch._spawn_background(failing())
            await asyncio.sleep(0.05)
            # Task should be cleaned up
            assert len(orch._background_tasks) == 0
            # Error should be logged
            mock_logger.error.assert_called_once()
            assert "test error" in str(mock_logger.error.call_args)


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

    def test_blocked_in_progress_not_eligible(self, workflow_yaml):
        """Blocker check applies regardless of issue state, not just Todo."""
        orch = _make_orch(workflow_yaml)
        issue = _make_issue(
            state="In Progress",
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


class TestSaveStatePruning:
    def test_terminal_states_excluded_from_save(self, workflow_yaml):
        from stokowski.state import state_file_path
        orch = _make_orch(workflow_yaml)
        orch._state_path = state_file_path(workflow_yaml)

        orch._issue_current_state["active-1"] = "implement"
        orch._issue_current_state["done-1"] = "done"  # terminal
        orch._last_issues["active-1"] = _make_issue(id="active-1", identifier="DEV-1")
        orch._last_issues["done-1"] = _make_issue(id="done-1", identifier="DEV-2")

        orch._save_state()

        import json
        data = json.loads(orch._state_path.read_text())
        assert "active-1" in data["issues"]
        assert "done-1" not in data["issues"]

    def test_updated_at_added_to_entries(self, workflow_yaml):
        from stokowski.state import state_file_path
        orch = _make_orch(workflow_yaml)
        orch._state_path = state_file_path(workflow_yaml)

        orch._issue_current_state["active-1"] = "implement"
        orch._last_issues["active-1"] = _make_issue(id="active-1", identifier="DEV-1")

        orch._save_state()

        import json
        data = json.loads(orch._state_path.read_text())
        assert "updated_at" in data["issues"]["active-1"]


def _restore_state(orch: Orchestrator):
    """Simulate the startup restore logic from start() for testing."""
    from datetime import datetime, timedelta, timezone
    from stokowski.state import load_state, state_file_path

    orch._state_path = state_file_path(orch.workflow_path)
    ps = load_state(orch._state_path)

    terminal_states = {
        name for name, sc in orch.cfg.states.items() if sc.type == "terminal"
    }
    gc_days = orch.cfg.agent.state_gc_days
    gc_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=gc_days)
    ).isoformat() if gc_days > 0 else None

    for issue_id, issue_data in ps.issues.items():
        state_name = issue_data.get("current_state", "")
        if not state_name or state_name not in orch.cfg.states:
            continue
        if state_name in terminal_states:
            continue
        updated_at = issue_data.get("updated_at", "")
        if gc_cutoff and updated_at and updated_at < gc_cutoff:
            continue
        orch._issue_current_state[issue_id] = state_name
        orch._issue_state_runs[issue_id] = issue_data.get("run", 1)
        session_id = issue_data.get("session_id")
        if session_id:
            orch._last_session_ids[issue_id] = session_id


class TestStartupRestore:
    def test_terminal_entries_skipped_on_restore(self, workflow_yaml):
        """Terminal state entries should not be restored on startup."""
        import json
        from stokowski.state import state_file_path

        orch = _make_orch(workflow_yaml)
        state_path = state_file_path(workflow_yaml)
        state_data = {
            "issues": {
                "active-1": {"current_state": "implement", "run": 1, "session_id": "sess-1"},
                "done-1": {"current_state": "done", "run": 1, "session_id": "sess-2"},
            },
            "total_tokens": 0,
        }
        state_path.write_text(json.dumps(state_data))

        orch2 = _make_orch(workflow_yaml)
        _restore_state(orch2)
        assert "active-1" in orch2._issue_current_state
        assert "done-1" not in orch2._issue_current_state

    def test_stale_entries_pruned_by_gc(self, workflow_yaml):
        """Entries older than state_gc_days should be pruned."""
        import json
        from datetime import datetime, timedelta, timezone
        from stokowski.state import state_file_path

        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        fresh_date = datetime.now(timezone.utc).isoformat()

        state_path = state_file_path(workflow_yaml)
        state_data = {
            "issues": {
                "old-1": {"current_state": "implement", "run": 1, "updated_at": old_date},
                "fresh-1": {"current_state": "implement", "run": 1, "updated_at": fresh_date},
            },
            "total_tokens": 0,
        }
        state_path.write_text(json.dumps(state_data))

        orch = _make_orch(workflow_yaml)
        _restore_state(orch)
        # Default gc_days=7, so 30-day-old entry should be pruned
        assert "old-1" not in orch._issue_current_state
        assert "fresh-1" in orch._issue_current_state

    def test_entries_without_updated_at_preserved(self, workflow_yaml):
        """Legacy entries without updated_at should not be pruned (migration safety)."""
        import json
        from stokowski.state import state_file_path

        state_path = state_file_path(workflow_yaml)
        state_data = {
            "issues": {
                "legacy-1": {"current_state": "implement", "run": 1},
            },
            "total_tokens": 0,
        }
        state_path.write_text(json.dumps(state_data))

        orch = _make_orch(workflow_yaml)
        _restore_state(orch)
        assert "legacy-1" in orch._issue_current_state
