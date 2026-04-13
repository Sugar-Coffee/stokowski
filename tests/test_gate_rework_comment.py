"""Tests for gate rework-on-comment feature."""

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from stokowski.config import (
    ServiceConfig,
    StateConfig,
    TrackerConfig,
    parse_workflow_file,
    validate_config,
)
from stokowski.models import Issue, RunAttempt
from stokowski.orchestrator import Orchestrator


@pytest.fixture
def tmp_yaml(tmp_path):
    """Helper to write a YAML file and return its path."""

    def _write(content: str, name: str = "workflow.yaml") -> Path:
        p = tmp_path / name
        p.write_text(textwrap.dedent(content))
        return p

    return _write


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


@pytest.fixture
def gate_rework_yaml(tmp_path):
    """Create a workflow with a gate that has rework_on_comment enabled."""

    def _make(rework_on_comment=True, max_rework=None):
        p = tmp_path / "workflow.yaml"
        max_rework_line = f"\n                max_rework: {max_rework}" if max_rework is not None else ""
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
                rework_on_comment: {str(rework_on_comment).lower()}{max_rework_line}
                transitions:
                  approve: done
              done:
                type: terminal
                linear_state: terminal
        """))
        return p

    return _make


class TestReworkOnCommentConfigParsed:
    def test_rework_on_comment_true(self, tmp_yaml):
        path = tmp_yaml("""
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
                rework_on_comment: true
                transitions:
                  approve: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["review"].rework_on_comment is True

    def test_rework_on_comment_false_default(self, tmp_yaml):
        path = tmp_yaml("""
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
                transitions:
                  approve: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["review"].rework_on_comment is False


class TestReworkOnCommentTriggersRework:
    @pytest.mark.asyncio
    async def test_rework_on_comment_triggers_rework(self, gate_rework_yaml):
        """When rework_on_comment is true and new comments arrive, rework is triggered."""
        orch = _make_orch(gate_rework_yaml(rework_on_comment=True))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.fetch_issues_by_states = AsyncMock(return_value=[])
        mock_client.update_issue_state = AsyncMock(return_value=True)

        # A comment posted after the gate was entered
        gate_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        comment_time = datetime.now(timezone.utc) - timedelta(minutes=1)
        mock_client.fetch_comments = AsyncMock(return_value=[
            {
                "id": "comment-1",
                "body": "I've answered your question about the API design.",
                "createdAt": comment_time.isoformat(),
            }
        ])

        orch._tracker = mock_client

        # Set up gate state
        orch._pending_gates[issue.id] = "review"
        orch._gate_entered_at[issue.id] = gate_time.isoformat()
        orch._issue_state_runs[issue.id] = 1
        orch._last_issues[issue.id] = issue

        await orch._handle_gate_responses()

        # Should have triggered rework
        assert issue.id not in orch._pending_gates
        assert issue.id not in orch._gate_entered_at
        assert orch._issue_current_state[issue.id] == "implement"
        assert orch._is_rework[issue.id] is True
        assert orch._issue_state_runs[issue.id] == 2
        mock_client.update_issue_state.assert_awaited()


class TestReworkOnCommentNoNewComments:
    @pytest.mark.asyncio
    async def test_no_new_comments_no_rework(self, gate_rework_yaml):
        """When rework_on_comment is true but no new comments, no rework."""
        orch = _make_orch(gate_rework_yaml(rework_on_comment=True))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.fetch_issues_by_states = AsyncMock(return_value=[])
        mock_client.update_issue_state = AsyncMock(return_value=True)

        # Comment posted before the gate was entered
        comment_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        gate_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        mock_client.fetch_comments = AsyncMock(return_value=[
            {
                "id": "comment-1",
                "body": "Some old comment",
                "createdAt": comment_time.isoformat(),
            }
        ])

        orch._tracker = mock_client

        orch._pending_gates[issue.id] = "review"
        orch._gate_entered_at[issue.id] = gate_time.isoformat()
        orch._issue_state_runs[issue.id] = 1
        orch._last_issues[issue.id] = issue

        await orch._handle_gate_responses()

        # Should still be pending
        assert issue.id in orch._pending_gates
        assert orch._issue_current_state.get(issue.id) != "implement"
        assert issue.id not in orch._is_rework


class TestReworkOnCommentMaxReworkExceeded:
    @pytest.mark.asyncio
    async def test_max_rework_exceeded_no_rework(self, gate_rework_yaml):
        """When max_rework is exceeded, rework-on-comment should not trigger."""
        orch = _make_orch(gate_rework_yaml(rework_on_comment=True, max_rework=2))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.fetch_issues_by_states = AsyncMock(return_value=[])
        mock_client.update_issue_state = AsyncMock(return_value=True)

        gate_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        comment_time = datetime.now(timezone.utc) - timedelta(minutes=1)
        mock_client.fetch_comments = AsyncMock(return_value=[
            {
                "id": "comment-1",
                "body": "Another answer",
                "createdAt": comment_time.isoformat(),
            }
        ])

        orch._tracker = mock_client

        orch._pending_gates[issue.id] = "review"
        orch._gate_entered_at[issue.id] = gate_time.isoformat()
        orch._issue_state_runs[issue.id] = 2  # equals max_rework
        orch._last_issues[issue.id] = issue

        await orch._handle_gate_responses()

        # Should still be pending (max_rework exceeded)
        assert issue.id in orch._pending_gates
        assert orch._issue_state_runs[issue.id] == 2  # unchanged


class TestReworkOnCommentDisabled:
    @pytest.mark.asyncio
    async def test_disabled_no_rework(self, gate_rework_yaml):
        """When rework_on_comment is false, comments do not trigger rework."""
        orch = _make_orch(gate_rework_yaml(rework_on_comment=False))
        issue = _make_issue()

        mock_client = AsyncMock()
        mock_client.fetch_issues_by_states = AsyncMock(return_value=[])
        mock_client.update_issue_state = AsyncMock(return_value=True)
        # Should never reach fetch_comments
        mock_client.fetch_comments = AsyncMock(return_value=[])

        orch._tracker = mock_client

        gate_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        orch._pending_gates[issue.id] = "review"
        orch._gate_entered_at[issue.id] = gate_time.isoformat()
        orch._issue_state_runs[issue.id] = 1
        orch._last_issues[issue.id] = issue

        await orch._handle_gate_responses()

        # Should still be pending
        assert issue.id in orch._pending_gates
        # fetch_comments should not have been called
        mock_client.fetch_comments.assert_not_awaited()


class TestReworkOnCommentValidationWarning:
    def test_rework_on_comment_without_rework_to(self):
        """Config validation should error when rework_on_comment is true but rework_to is not set."""
        cfg = ServiceConfig(
            tracker=TrackerConfig(kind="linear", api_key="test", team_key="DEV"),
            states={
                "work": StateConfig(
                    name="work",
                    type="agent",
                    prompt="prompts/work.md",
                    linear_state="active",
                    transitions={"complete": "review"},
                ),
                "review": StateConfig(
                    name="review",
                    type="gate",
                    linear_state="review",
                    rework_on_comment=True,
                    # rework_to intentionally missing
                    transitions={"approve": "done"},
                ),
                "done": StateConfig(
                    name="done",
                    type="terminal",
                    linear_state="terminal",
                ),
            },
        )
        errors = validate_config(cfg)
        assert any("rework_on_comment" in e for e in errors)

    def test_rework_on_comment_with_rework_to_no_error(self):
        """Config validation should not error when rework_on_comment has rework_to set."""
        cfg = ServiceConfig(
            tracker=TrackerConfig(kind="linear", api_key="test", team_key="DEV"),
            states={
                "work": StateConfig(
                    name="work",
                    type="agent",
                    prompt="prompts/work.md",
                    linear_state="active",
                    transitions={"complete": "review"},
                ),
                "review": StateConfig(
                    name="review",
                    type="gate",
                    linear_state="review",
                    rework_to="work",
                    rework_on_comment=True,
                    transitions={"approve": "done"},
                ),
                "done": StateConfig(
                    name="done",
                    type="terminal",
                    linear_state="terminal",
                ),
            },
        )
        errors = validate_config(cfg)
        assert not any("rework_on_comment" in e for e in errors)

    def test_rework_on_comment_on_non_gate_warns(self):
        """Setting rework_on_comment on a non-gate state should produce a log warning."""
        cfg = ServiceConfig(
            tracker=TrackerConfig(kind="linear", api_key="test", team_key="DEV"),
            states={
                "work": StateConfig(
                    name="work",
                    type="agent",
                    prompt="prompts/work.md",
                    linear_state="active",
                    rework_on_comment=True,  # should warn — agent state, not gate
                    transitions={"complete": "done"},
                ),
                "done": StateConfig(
                    name="done",
                    type="terminal",
                    linear_state="terminal",
                ),
            },
        )
        with patch("stokowski.config.log") as mock_log:
            errors = validate_config(cfg)
            mock_log.warning.assert_called_once()
            assert "rework_on_comment" in str(mock_log.warning.call_args)
