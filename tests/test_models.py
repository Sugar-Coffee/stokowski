"""Tests for core domain models."""

from datetime import datetime, timezone

from stokowski.models import BlockerRef, Issue, RetryEntry, RunAttempt


class TestIssue:
    def test_minimal_creation(self):
        issue = Issue(id="abc", identifier="DEV-1", title="Fix bug")
        assert issue.id == "abc"
        assert issue.identifier == "DEV-1"
        assert issue.title == "Fix bug"
        assert issue.description is None
        assert issue.priority is None
        assert issue.state == ""
        assert issue.branch_name is None
        assert issue.url is None
        assert issue.labels == []
        assert issue.blocked_by == []
        assert issue.created_at is None
        assert issue.project_slug is None

    def test_full_creation(self):
        now = datetime.now(timezone.utc)
        issue = Issue(
            id="abc",
            identifier="DEV-2",
            title="Add feature",
            description="Detailed description",
            priority=1,
            state="In Progress",
            branch_name="feature/dev-2",
            url="https://linear.app/issue/DEV-2",
            labels=["feature", "urgent"],
            blocked_by=[BlockerRef(id="x", identifier="DEV-1", state="Done")],
            created_at=now,
            updated_at=now,
            project_slug="proj123",
        )
        assert issue.priority == 1
        assert issue.state == "In Progress"
        assert len(issue.labels) == 2
        assert issue.blocked_by[0].identifier == "DEV-1"
        assert issue.project_slug == "proj123"

    def test_empty_title_allowed(self):
        """Minimal fetches use title='' per CLAUDE.md convention."""
        issue = Issue(id="x", identifier="DEV-99", title="")
        assert issue.title == ""

    def test_labels_default_mutable(self):
        """Each instance gets its own labels list."""
        a = Issue(id="1", identifier="A", title="A")
        b = Issue(id="2", identifier="B", title="B")
        a.labels.append("bug")
        assert b.labels == []


class TestBlockerRef:
    def test_defaults(self):
        ref = BlockerRef()
        assert ref.id is None
        assert ref.identifier is None
        assert ref.state is None

    def test_with_values(self):
        ref = BlockerRef(id="x", identifier="DEV-1", state="Done")
        assert ref.state == "Done"


class TestRunAttempt:
    def test_defaults(self):
        ra = RunAttempt(issue_id="abc", issue_identifier="DEV-1")
        assert ra.status == "pending"
        assert ra.session_id is None
        assert ra.input_tokens == 0
        assert ra.output_tokens == 0
        assert ra.total_tokens == 0
        assert ra.turn_count == 0
        assert ra.last_message == ""
        assert ra.workspace_path == ""
        assert ra.state_name is None
        assert ra.attempt is None
        assert ra.needs_review is False

    def test_with_values(self):
        ra = RunAttempt(
            issue_id="abc",
            issue_identifier="DEV-1",
            attempt=3,
            status="succeeded",
            session_id="sess-123",
            input_tokens=500,
            output_tokens=200,
            total_tokens=700,
            turn_count=5,
            state_name="implement",
        )
        assert ra.attempt == 3
        assert ra.total_tokens == 700
        assert ra.state_name == "implement"


class TestRetryEntry:
    def test_defaults(self):
        entry = RetryEntry(issue_id="abc", identifier="DEV-1")
        assert entry.attempt == 1
        assert entry.due_at_ms == 0
        assert entry.error is None

    def test_with_values(self):
        entry = RetryEntry(
            issue_id="abc",
            identifier="DEV-1",
            attempt=3,
            due_at_ms=1234567890.0,
            error="Timeout",
        )
        assert entry.attempt == 3
        assert entry.due_at_ms == 1234567890.0
        assert entry.error == "Timeout"
