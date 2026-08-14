import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from stokowski.config import (
    ProjectConfig,
    ServiceConfig,
    StateConfig,
    TrackerConfig,
    WorkflowDefinition,
    _parse_tracker,
    parse_workflow_file,
    validate_config,
)
from stokowski.linear import (
    CANDIDATE_QUERY,
    ISSUES_BY_IDS_QUERY,
    ISSUES_BY_STATES_QUERY,
    LinearClient,
)
from stokowski.models import Issue, RunAttempt
from stokowski.orchestrator import Orchestrator


class TrackerAssigneeConfigTests(unittest.TestCase):
    def test_assignee_defaults_to_no_filter(self):
        self.assertIsNone(_parse_tracker({}).assignee)

    def test_assignee_me_is_normalized(self):
        self.assertEqual(_parse_tracker({"assignee": " ME "}).assignee, "me")

    def test_single_project_workflow_parses_assignee_scope(self):
        workflow = self.parse_workflow(
            """
tracker:
  project_slug: abc123
  api_key: lin_api_test
  assignee: me
states:
  work:
    type: agent
    prompt: prompts/work.md
    transitions: {complete: done}
  done:
    type: terminal
    linear_state: terminal
"""
        )

        self.assertEqual(workflow.config.tracker.assignee, "me")
        self.assertEqual(workflow.config.projects[0].tracker.assignee, "me")

    def test_multi_project_workflow_parses_assignee_scope(self):
        workflow = self.parse_workflow(
            """
projects:
  - name: example
    tracker:
      project_slug: abc123
      api_key: lin_api_test
      assignee: me
    states:
      work:
        type: agent
        prompt: prompts/work.md
        transitions: {complete: done}
      done:
        type: terminal
        linear_state: terminal
"""
        )

        self.assertEqual(workflow.config.projects[0].tracker.assignee, "me")

    def parse_workflow(self, contents: str) -> WorkflowDefinition:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yaml"
            path.write_text(contents)
            return parse_workflow_file(path)

    def test_validation_rejects_unsupported_assignee(self):
        project = ProjectConfig(
            name="example",
            tracker=TrackerConfig(
                api_key="lin_api_test",
                project_slug="abc123",
                assignee="someone-else",
            ),
            states={
                "work": StateConfig(
                    name="work",
                    type="agent",
                    prompt="prompts/work.md",
                    transitions={"complete": "done"},
                ),
                "done": StateConfig(
                    name="done",
                    type="terminal",
                    linear_state="terminal",
                ),
            },
        )

        errors = validate_config(ServiceConfig(projects=[project]))

        self.assertIn(
            "project 'example': unsupported tracker.assignee: "
            "'someone-else' (only 'me' is supported)",
            errors,
        )


class LinearAssigneeFilterTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, response: dict) -> LinearClient:
        client = object.__new__(LinearClient)
        client._graphql = AsyncMock(return_value=response)
        return client

    async def test_candidate_query_filters_for_authenticated_user(self):
        client = self.make_client({"issues": {"nodes": [], "pageInfo": {}}})

        await client.fetch_candidate_issues(
            "abc123",
            ["Todo", "In Progress"],
            assignee="me",
        )

        client._graphql.assert_awaited_once_with(
            CANDIDATE_QUERY,
            {
                "filter": {
                    "project": {"slugId": {"eq": "abc123"}},
                    "state": {"name": {"in": ["Todo", "In Progress"]}},
                    "assignee": {"isMe": {"eq": True}},
                }
            },
        )

    async def test_candidate_query_preserves_project_wide_default(self):
        client = self.make_client({"issues": {"nodes": [], "pageInfo": {}}})

        await client.fetch_candidate_issues("abc123", ["Todo"])

        _, variables = client._graphql.await_args.args
        self.assertNotIn("assignee", variables["filter"])

    async def test_state_query_filters_for_authenticated_user(self):
        client = self.make_client({"issues": {"nodes": [], "pageInfo": {}}})

        await client.fetch_issues_by_states(
            "abc123",
            ["Human Review"],
            assignee="me",
        )

        client._graphql.assert_awaited_once_with(
            ISSUES_BY_STATES_QUERY,
            {
                "filter": {
                    "project": {"slugId": {"eq": "abc123"}},
                    "state": {"name": {"in": ["Human Review"]}},
                    "assignee": {"isMe": {"eq": True}},
                }
            },
        )

    async def test_id_query_filters_for_authenticated_user(self):
        client = self.make_client(
            {
                "issues": {
                    "nodes": [
                        {"id": "issue-1", "state": {"name": "In Progress"}}
                    ]
                }
            }
        )

        states = await client.fetch_issue_states_by_ids(
            ["issue-1"],
            assignee="me",
        )

        self.assertEqual(states, {"issue-1": "In Progress"})
        client._graphql.assert_awaited_once_with(
            ISSUES_BY_IDS_QUERY,
            {
                "filter": {
                    "id": {"in": ["issue-1"]},
                    "assignee": {"isMe": {"eq": True}},
                }
            },
        )

    async def test_client_rejects_unsupported_assignee(self):
        client = self.make_client({})

        with self.assertRaisesRegex(ValueError, "Unsupported assignee filter"):
            await client.fetch_candidate_issues(
                "abc123",
                ["Todo"],
                assignee="someone-else",
            )

        client._graphql.assert_not_awaited()


class OrchestratorAssigneeReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def make_orchestrator(self, assignee: str | None) -> tuple[Orchestrator, AsyncMock]:
        orchestrator = Orchestrator("unused-workflow.yaml")
        orchestrator.workflow = WorkflowDefinition(
            config=ServiceConfig(tracker=TrackerConfig(assignee=assignee)),
            prompt_template="",
        )
        fetch_states = AsyncMock(return_value={})
        orchestrator._linear = SimpleNamespace(
            fetch_issue_states_by_ids=fetch_states
        )
        return orchestrator, fetch_states

    async def test_reconcile_stops_issue_reassigned_away_from_me(self):
        orchestrator, fetch_states = self.make_orchestrator("me")
        attempt = RunAttempt(issue_id="issue-1", issue_identifier="SYN-1")
        task = Mock()
        orchestrator.running["issue-1"] = attempt
        orchestrator.claimed.add("issue-1")
        orchestrator._slot_held.add("issue-1")
        orchestrator._tasks["issue-1"] = task

        await orchestrator._reconcile()

        fetch_states.assert_awaited_once_with(["issue-1"], assignee="me")
        task.cancel.assert_called_once_with()
        self.assertNotIn("issue-1", orchestrator.running)
        self.assertNotIn("issue-1", orchestrator.claimed)
        self.assertNotIn("issue-1", orchestrator._slot_held)

    async def test_reconcile_keeps_missing_issue_without_assignee_filter(self):
        orchestrator, fetch_states = self.make_orchestrator(None)
        attempt = RunAttempt(issue_id="issue-1", issue_identifier="SYN-1")
        task = Mock()
        orchestrator.running["issue-1"] = attempt
        orchestrator._tasks["issue-1"] = task

        await orchestrator._reconcile()

        fetch_states.assert_awaited_once_with(["issue-1"], assignee=None)
        task.cancel.assert_not_called()
        self.assertIn("issue-1", orchestrator.running)

    async def test_gate_is_evicted_when_issue_is_reassigned_away(self):
        orchestrator, fetch_states = self.make_orchestrator("me")
        orchestrator._pending_gates["issue-1"] = "review"
        orchestrator._issue_current_state["issue-1"] = "review"
        orchestrator._issue_state_runs["issue-1"] = 1
        orchestrator._last_issues["issue-1"] = Issue(
            id="issue-1",
            identifier="SYN-1",
            title="Example",
        )

        await orchestrator._evict_terminal_gates()

        fetch_states.assert_awaited_once_with(["issue-1"], assignee="me")
        self.assertNotIn("issue-1", orchestrator._pending_gates)
        self.assertNotIn("issue-1", orchestrator._issue_current_state)


if __name__ == "__main__":
    unittest.main()
