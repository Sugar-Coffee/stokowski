"""Linear API client for issue tracking."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from .models import BlockerRef, Issue

logger = logging.getLogger("stokowski.linear")

# ---------------------------------------------------------------------------
# GraphQL queries — project-scoped (original)
# ---------------------------------------------------------------------------

CANDIDATE_QUERY_PROJECT = """
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
      project { slugId }
      inverseRelations {
        nodes {
          type
          relatedIssue {
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

ISSUES_BY_STATES_QUERY_PROJECT = """
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

# ---------------------------------------------------------------------------
# GraphQL queries — team-scoped (new)
# ---------------------------------------------------------------------------

CANDIDATE_QUERY_TEAM = """
query($teamKey: String!, $states: [String!]!, $after: String) {
  issues(
    filter: {
      team: { key: { eq: $teamKey } }
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
      project { slugId }
      inverseRelations {
        nodes {
          type
          relatedIssue {
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

ISSUES_BY_STATES_QUERY_TEAM = """
query($teamKey: String!, $states: [String!]!, $after: String) {
  issues(
    filter: {
      team: { key: { eq: $teamKey } }
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

# ---------------------------------------------------------------------------
# Shared queries (not scoped to project or team)
# ---------------------------------------------------------------------------

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
            ri = rel.get("relatedIssue", {}) or {}
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

    project_slug = (node.get("project") or {}).get("slugId")

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
        project_slug=project_slug,
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
        import asyncio

        # Global rate limiter — max 1 concurrent request, min 200ms between
        if not hasattr(self, '_rate_sem'):
            self._rate_sem = asyncio.Semaphore(1)
            self._last_request = 0.0

        async with self._rate_sem:
            import time
            elapsed = time.monotonic() - self._last_request
            if elapsed < 1.0:  # Min 1s between requests (safe with fewer total calls)
                await asyncio.sleep(1.0 - elapsed)

            for attempt in range(3):
                resp = await self._client.post(
                    self.endpoint,
                    json={"query": query, "variables": variables},
                )
                self._last_request = time.monotonic()
                if resp.status_code in (429, 400) and attempt < 2:
                    wait = (attempt + 1) * 3
                    logger.warning(f"Linear API {resp.status_code}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break

        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
        return data.get("data", {})

    async def fetch_candidate_issues(
        self,
        project_slug: str,
        active_states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch all issues in active states for the project or team."""
        issues: list[Issue] = []
        cursor = None

        # Choose query and variables based on filtering mode
        if team_key:
            query = CANDIDATE_QUERY_TEAM
            base_vars = {"teamKey": team_key, "states": active_states}
        else:
            query = CANDIDATE_QUERY_PROJECT
            base_vars = {"projectSlug": project_slug, "states": active_states}

        while True:
            variables = dict(base_vars)
            if cursor:
                variables["after"] = cursor

            data = await self._graphql(query, variables)
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
        self,
        project_slug: str,
        states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch issues in specific states (for terminal cleanup, gate detection)."""
        issues: list[Issue] = []
        cursor = None

        if team_key:
            query = ISSUES_BY_STATES_QUERY_TEAM
            base_vars = {"teamKey": team_key, "states": states}
        else:
            query = ISSUES_BY_STATES_QUERY_PROJECT
            base_vars = {"projectSlug": project_slug, "states": states}

        while True:
            variables = dict(base_vars)
            if cursor:
                variables["after"] = cursor

            data = await self._graphql(query, variables)
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

    async def post_comment(self, issue_id: str, body: str) -> bool:
        """Post a comment on a Linear issue. Returns True on success."""
        try:
            data = await self._graphql(
                COMMENT_CREATE_MUTATION,
                {"issueId": issue_id, "body": body},
            )
            return data.get("commentCreate", {}).get("success", False)
        except Exception as e:
            logger.error(f"Failed to post comment on {issue_id}: {e}")
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

    async def fetch_issue_description(self, issue_id: str) -> str:
        """Fetch the issue description."""
        try:
            data = await self._graphql(
                'query($id: String!) { issue(id: $id) { description } }',
                {"id": issue_id},
            )
            return data.get("issue", {}).get("description", "") or ""
        except Exception as e:
            logger.error(f"Failed to fetch description for {issue_id}: {e}")
            return ""

    async def update_issue_description(self, issue_id: str, description: str) -> bool:
        """Update the issue description."""
        try:
            data = await self._graphql(
                'mutation($id: String!, $desc: String!) { issueUpdate(id: $id, input: { description: $desc }) { success } }',
                {"id": issue_id, "desc": description},
            )
            return data.get("issueUpdate", {}).get("success", False)
        except Exception as e:
            logger.error(f"Failed to update description for {issue_id}: {e}")
            return False
