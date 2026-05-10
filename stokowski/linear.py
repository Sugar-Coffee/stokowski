"""Linear API client for issue tracking."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from .models import BlockerRef, Issue

logger = logging.getLogger("stokowski.linear")

CANDIDATE_QUERY = """
query($projectSlug: String!, $states: [String!]!, $after: String) {
  issues(
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $states } }
    }
    first: 50
    after: $after
    orderBy: createdAt
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      description
      priority
      url
      branchName
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations {
        nodes {
          type
          issue {
            id
            identifier
            state { name }
          }
        }
      }
    }
  }
}
"""

ISSUES_BY_IDS_QUERY = """
query($ids: [ID!]!) {
  issues(filter: { id: { in: $ids } }) {
    nodes {
      id
      identifier
      state { name }
    }
  }
}
"""

ISSUES_BY_STATES_QUERY = """
query($projectSlug: String!, $states: [String!]!, $after: String) {
  issues(
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $states } }
    }
    first: 50
    after: $after
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      state { name }
    }
  }
}
"""

COMMENT_CREATE_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
"""

COMMENTS_QUERY = """
query($issueId: String!) {
  issue(id: $issueId) {
    comments(orderBy: createdAt) {
      nodes {
        id
        body
        createdAt
      }
    }
  }
}
"""

ISSUE_UPDATE_MUTATION = """
mutation($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success
    issue { id state { name } }
  }
}
"""

ISSUE_TEAM_AND_STATES_QUERY = """
query($issueId: String!) {
  issue(id: $issueId) {
    team {
      id
      states {
        nodes {
          id
          name
        }
      }
    }
  }
}
"""


def _parse_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _normalize_issue(node: dict) -> Issue:
    labels = [
        label["name"].lower()
        for label in (node.get("labels", {}) or {}).get("nodes", [])
        if label.get("name")
    ]

    blockers = []
    for rel in (node.get("inverseRelations", {}) or {}).get("nodes", []):
        if rel.get("type") == "blocks":

            # Local patch for upstream bug: was rel["relatedIssue"] (which is the
            # current issue, not the blocker). Linear's IssueRelation.issue is
            # the source/blocker; .relatedIssue is the target/blocked.
            # Tracking: https://github.com/Sugar-Coffee/stokowski/issues/20
        
            ri = rel.get("issue", {}) or {}
            blockers.append(
                BlockerRef(
                    id=ri.get("id"),
                    identifier=ri.get("identifier"),
                    state=(ri.get("state") or {}).get("name"),
                )
            )

    priority = node.get("priority")
    if priority is not None:
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = None

    return Issue(
        id=node["id"],
        identifier=node["identifier"],
        title=node.get("title", ""),
        description=node.get("description"),
        priority=priority,
        state=(node.get("state") or {}).get("name", ""),
        branch_name=node.get("branchName"),
        url=node.get("url"),
        labels=labels,
        blocked_by=blockers,
        created_at=_parse_datetime(node.get("createdAt")),
        updated_at=_parse_datetime(node.get("updatedAt")),
    )


class LinearClient:
    def __init__(self, endpoint: str, api_key: str, timeout_ms: int = 30_000):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout_ms / 1000
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

    async def close(self):
        await self._client.aclose()

    async def _graphql(self, query: str, variables: dict) -> dict:
        resp = await self._client.post(
            self.endpoint,
            json={"query": query, "variables": variables},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
        return data.get("data", {})

    async def fetch_candidate_issues(
        self, project_slug: str, active_states: list[str]
    ) -> list[Issue]:
        """Fetch all issues in active states for the project."""
        issues: list[Issue] = []
        cursor = None

        while True:
            variables: dict = {
                "projectSlug": project_slug,
                "states": active_states,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._graphql(CANDIDATE_QUERY, variables)
            issues_data = data.get("issues", {})
            nodes = issues_data.get("nodes", [])

            for node in nodes:
                try:
                    issues.append(_normalize_issue(node))
                except (KeyError, TypeError) as e:
                    logger.warning(f"Skipping malformed issue node: {e}")

            page_info = issues_data.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
            else:
                break

        return issues

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, str]:
        """Fetch current states for given issue IDs. Returns {id: state_name}."""
        if not issue_ids:
            return {}

        data = await self._graphql(ISSUES_BY_IDS_QUERY, {"ids": issue_ids})
        result = {}
        for node in data.get("issues", {}).get("nodes", []):
            if node and node.get("id") and node.get("state"):
                result[node["id"]] = node["state"]["name"]
        return result

    async def fetch_issues_by_states(
        self, project_slug: str, states: list[str]
    ) -> list[Issue]:
        """Fetch issues in specific states (for terminal cleanup)."""
        issues: list[Issue] = []
        cursor = None

        while True:
            variables: dict = {
                "projectSlug": project_slug,
                "states": states,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._graphql(ISSUES_BY_STATES_QUERY, variables)
            issues_data = data.get("issues", {})
            for node in issues_data.get("nodes", []):
                if node and node.get("id"):
                    issues.append(
                        Issue(
                            id=node["id"],
                            identifier=node.get("identifier", ""),
                            title="",
                            state=(node.get("state") or {}).get("name", ""),
                        )
                    )

            page_info = issues_data.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
            else:
                break

        return issues

    async def post_comment(self, issue_id: str, body: str, max_retries: int = 3) -> bool:
        """Post a comment on a Linear issue. Returns True on success.

        Retries on transient failures (network, rate limit) with exponential backoff.
        """
        last_error: str | None = None
        for attempt in range(max_retries):
            try:
                data = await self._graphql(
                    COMMENT_CREATE_MUTATION,
                    {"issueId": issue_id, "body": body},
                )
                success = data.get("commentCreate", {}).get("success", False)
                if success:
                    return True
                # Non-success response (e.g. Linear validation failure) — don't retry
                logger.warning(
                    f"post_comment failed (non-success) issue={issue_id} "
                    f"attempt={attempt + 1}/{max_retries}"
                )
                return False
            except httpx.HTTPStatusError as e:
                # Retry on 429 (rate limit) and 5xx server errors
                if e.response.status_code in (429, 502, 503, 504):
                    last_error = f"HTTP {e.response.status_code}"
                    delay_ms = min(1000 * (2 ** attempt), 8000)
                    logger.warning(
                        f"post_comment retryable error issue={issue_id} "
                        f"attempt={attempt + 1}/{max_retries}: {last_error} — "
                        f"retrying in {delay_ms}ms"
                    )
                    await asyncio.sleep(delay_ms / 1000)
                    continue
                # 4xx client errors (except 429) — don't retry
                logger.error(f"post_comment non-retryable error issue={issue_id}: {e}")
                return False
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    delay_ms = min(1000 * (2 ** attempt), 8000)
                    logger.warning(
                        f"post_comment error issue={issue_id} "
                        f"attempt={attempt + 1}/{max_retries}: {last_error} — "
                        f"retrying in {delay_ms}ms"
                    )
                    await asyncio.sleep(delay_ms / 1000)
                else:
                    logger.error(
                        f"post_comment failed after {max_retries} attempts issue={issue_id}: "
                        f"{last_error}",
                        exc_info=True,
                    )
        return False

    async def fetch_comments(self, issue_id: str) -> list[dict]:
        """Fetch all comments on a Linear issue. Returns list of {id, body, createdAt}."""
        try:
            data = await self._graphql(COMMENTS_QUERY, {"issueId": issue_id})
            issue = data.get("issue", {})
            return issue.get("comments", {}).get("nodes", [])
        except Exception as e:
            logger.error(f"Failed to fetch comments for {issue_id}: {e}")
            return []

    async def update_issue_state(self, issue_id: str, state_name: str) -> bool:
        """Move an issue to a new state by name. Returns True on success."""
        try:
            # Get team and its workflow states in one query
            data = await self._graphql(
                ISSUE_TEAM_AND_STATES_QUERY, {"issueId": issue_id}
            )
            team = data.get("issue", {}).get("team", {})
            if not team:
                logger.error(f"Could not find team for issue {issue_id}")
                return False

            states = team.get("states", {}).get("nodes", [])
            state_id = None
            for s in states:
                if s.get("name", "").strip().lower() == state_name.strip().lower():
                    state_id = s["id"]
                    break

            if not state_id:
                logger.error(
                    f"State '{state_name}' not found. "
                    f"Available: {[s.get('name') for s in states]}"
                )
                return False

            # Update the issue
            result = await self._graphql(
                ISSUE_UPDATE_MUTATION,
                {"issueId": issue_id, "stateId": state_id},
            )
            success = result.get("issueUpdate", {}).get("success", False)
            if success:
                logger.info(f"Moved issue {issue_id} to state '{state_name}'")
            else:
                logger.error(f"Linear rejected state update for {issue_id}")
            return success
        except Exception as e:
            logger.error(f"Failed to update state for {issue_id}: {e}")
            return False
