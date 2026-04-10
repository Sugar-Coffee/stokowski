"""Tracker protocol — defines the interface all tracker backends must implement."""

from __future__ import annotations

from typing import Protocol

from .models import Issue


class TrackerClient(Protocol):
    """Duck-typed interface for issue tracker backends.

    All methods are async. Backends must implement every method.
    """

    async def close(self) -> None: ...

    async def fetch_candidate_issues(
        self,
        project_slug: str,
        active_states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch all issues in active states (full detail)."""
        ...

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, str]:
        """Return {issue_id: state_name} for reconciliation."""
        ...

    async def fetch_issues_by_states(
        self,
        project_slug: str,
        states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch minimal issues in specific states (startup cleanup, gate detection)."""
        ...

    async def post_comment(self, issue_id: str, body: str) -> bool:
        """Post a comment on an issue. Returns True on success."""
        ...

    async def fetch_comments(self, issue_id: str) -> list[dict]:
        """Fetch all comments on an issue. Returns list of {id, body, createdAt}."""
        ...

    async def update_issue_state(self, issue_id: str, state_name: str) -> bool:
        """Move an issue to a new state by name. Returns True on success."""
        ...

    async def fetch_issue_description(self, issue_id: str) -> str:
        """Fetch the issue description/body text."""
        ...

    async def update_issue_description(self, issue_id: str, description: str) -> bool:
        """Update the issue description/body. Returns True on success."""
        ...
