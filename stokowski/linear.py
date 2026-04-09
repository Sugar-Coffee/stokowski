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

TEAM_ID_QUERY = """
query($teamKey: String!) {
  teams(filter: { key: { eq: $teamKey } }) {
    nodes { id }
  }
}
"""

ISSUE_CREATE_MUTATION = """
mutation($teamId: String!, $title: String!, $description: String, $priority: Int, $stateId: String, $labelIds: [String!]) {
  issueCreate(input: { teamId: $teamId, title: $title, description: $description, priority: $priority, stateId: $stateId, labelIds: $labelIds }) {
    success
    issue { id identifier title }
  }
}
"""

SEARCH_ISSUES_BY_TITLE_QUERY_TEAM = """
query($teamKey: String!, $title: String!, $states: [String!]!) {
  issues(
    filter: {
      team: { key: { eq: $teamKey } }
      title: { eq: $title }
      state: { name: { in: $states } }
    }
    first: 1
  ) {
    nodes { id identifier title }
  }
}
"""

SEARCH_ISSUES_BY_TITLE_QUERY_PROJECT = """
query($projectSlug: String!, $title: String!, $states: [String!]!) {
  issues(
    filter: {
      project: { slugId: { eq: $projectSlug } }
      title: { eq: $title }
      state: { name: { in: $states } }
    }
    first: 1
  ) {
    nodes { id identifier title }
  }
}
"""

TEAM_LABELS_QUERY = """
query($teamKey: String!) {
  teams(filter: { key: { eq: $teamKey } }) {
    nodes {
      labels { nodes { id name } }
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

    async def search_issue_by_title(
        self,
        title: str,
        non_terminal_states: list[str],
        project_slug: str = "",
        team_key: str = "",
    ) -> Issue | None:
        """Search for an existing issue by exact title in non-terminal states."""
        try:
            if team_key:
                data = await self._graphql(
                    SEARCH_ISSUES_BY_TITLE_QUERY_TEAM,
                    {"teamKey": team_key, "title": title, "states": non_terminal_states},
                )
            else:
                data = await self._graphql(
                    SEARCH_ISSUES_BY_TITLE_QUERY_PROJECT,
                    {"projectSlug": project_slug, "title": title, "states": non_terminal_states},
                )
            nodes = data.get("issues", {}).get("nodes", [])
            if nodes:
                node = nodes[0]
                return Issue(
                    id=node["id"],
                    identifier=node.get("identifier", ""),
                    title=node.get("title", ""),
                )
            return None
        except Exception as e:
            logger.error(f"Failed to search issues by title: {e}")
            return None

    async def get_team_id(self, team_key: str) -> str | None:
        """Get team ID from team key."""
        try:
            data = await self._graphql(TEAM_ID_QUERY, {"teamKey": team_key})
            nodes = data.get("teams", {}).get("nodes", [])
            return nodes[0]["id"] if nodes else None
        except Exception as e:
            logger.error(f"Failed to get team ID for {team_key}: {e}")
            return None

    async def get_team_label_ids(
        self, team_key: str, label_names: list[str]
    ) -> list[str]:
        """Resolve label names to IDs for a team."""
        if not label_names:
            return []
        try:
            data = await self._graphql(TEAM_LABELS_QUERY, {"teamKey": team_key})
            teams = data.get("teams", {}).get("nodes", [])
            if not teams:
                return []
            labels = teams[0].get("labels", {}).get("nodes", [])
            name_to_id = {l["name"].lower(): l["id"] for l in labels if l.get("name") and l.get("id")}
            return [name_to_id[n.lower()] for n in label_names if n.lower() in name_to_id]
        except Exception as e:
            logger.error(f"Failed to get label IDs: {e}")
            return []

    async def get_state_id(self, team_key: str, state_name: str) -> str | None:
        """Get state ID by name for a team. Requires fetching via a team issue."""
        try:
            # Fetch team ID first, then get states via team
            team_id = await self.get_team_id(team_key)
            if not team_id:
                return None
            # Use a different query to get team states
            data = await self._graphql(
                """query($teamId: String!) {
                    team(id: $teamId) {
                        states { nodes { id name } }
                    }
                }""",
                {"teamId": team_id},
            )
            states = data.get("team", {}).get("states", {}).get("nodes", [])
            for s in states:
                if s.get("name", "").strip().lower() == state_name.strip().lower():
                    return s["id"]
            return None
        except Exception as e:
            logger.error(f"Failed to get state ID for '{state_name}': {e}")
            return None

    async def create_issue(
        self,
        team_key: str,
        title: str,
        description: str = "",
        priority: int = 3,
        state_name: str | None = None,
        label_names: list[str] | None = None,
    ) -> Issue | None:
        """Create a new Linear issue. Returns the created Issue or None."""
        try:
            team_id = await self.get_team_id(team_key)
            if not team_id:
                logger.error(f"Team '{team_key}' not found")
                return None

            variables: dict = {
                "teamId": team_id,
                "title": title,
                "description": description or None,
                "priority": priority,
            }

            if state_name:
                state_id = await self.get_state_id(team_key, state_name)
                if state_id:
                    variables["stateId"] = state_id

            if label_names:
                label_ids = await self.get_team_label_ids(team_key, label_names)
                if label_ids:
                    variables["labelIds"] = label_ids

            data = await self._graphql(ISSUE_CREATE_MUTATION, variables)
            result = data.get("issueCreate", {})
            if not result.get("success"):
                logger.error(f"Linear rejected issue creation: {data}")
                return None

            node = result.get("issue", {})
            issue = Issue(
                id=node["id"],
                identifier=node.get("identifier", ""),
                title=node.get("title", ""),
            )
            logger.info(f"Created scheduled issue: {issue.identifier} — {issue.title}")
            return issue
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
            return None

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
