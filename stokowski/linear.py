"""Linear API client for issue tracking."""

from __future__ import annotations

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
        user { name displayName }
        botActor { name }
        externalUser { name }
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

FILE_UPLOAD_MUTATION = """
mutation($contentType: String!, $filename: String!, $size: Int!) {
  fileUpload(contentType: $contentType, filename: $filename, size: $size) {
    success
    uploadFile {
      uploadUrl
      assetUrl
      headers { key value }
    }
  }
}
"""

TEAM_LABELS_QUERY = """
query($issueId: String!) {
  issue(id: $issueId) {
    team {
      id
      labels(first: 250) { nodes { id name } }
    }
    labels { nodes { id name } }
  }
}
"""

LABEL_CREATE_MUTATION = """
mutation($teamId: String!, $name: String!, $color: String) {
  issueLabelCreate(input: { teamId: $teamId, name: $name, color: $color }) {
    success
    issueLabel { id name }
  }
}
"""

ISSUE_ADD_LABEL_MUTATION = """
mutation($issueId: String!, $labelId: String!) {
  issueAddLabel(id: $issueId, labelId: $labelId) {
    success
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
        """Fetch all comments on a Linear issue, OLDEST FIRST.

        `orderBy: createdAt` sorts *descending* in Linear's API — newest first.
        Every consumer here (`parse_latest_tracking`, `get_last_tracking_timestamp`,
        the prompt's review-comment section) is written against oldest-first and
        keeps the last match it sees, so the raw order silently yields the
        *first* tracking entry an issue ever had instead of its current one.
        That made gate approve/rework a no-op after any restart. Sort here so
        the contract holds for every caller.
        """
        try:
            data = await self._graphql(COMMENTS_QUERY, {"issueId": issue_id})
            issue = data.get("issue", {})
            nodes = issue.get("comments", {}).get("nodes", [])
            return sorted(nodes, key=lambda c: c.get("createdAt") or "")
        except Exception as e:
            logger.error(f"Failed to fetch comments for {issue_id}: {e}")
            return []

    async def upload_file(
        self, filename: str, content_type: str, data: bytes
    ) -> str | None:
        """Upload a file to Linear's asset store. Returns the asset URL.

        Two steps: ask Linear for a pre-signed destination, then PUT the bytes
        there with the headers it hands back. The returned `assetUrl` is what
        goes in markdown; `uploadUrl` is single-use and must not be shared.
        """
        try:
            data_out = await self._graphql(
                FILE_UPLOAD_MUTATION,
                {
                    "contentType": content_type,
                    "filename": filename,
                    "size": len(data),
                },
            )
        except Exception as e:
            logger.error(f"fileUpload request failed for {filename}: {e}")
            return None

        payload = (data_out or {}).get("fileUpload") or {}
        if not payload.get("success"):
            logger.error(f"Linear declined upload slot for {filename}")
            return None

        upload_file = payload.get("uploadFile") or {}
        upload_url = upload_file.get("uploadUrl")
        asset_url = upload_file.get("assetUrl")
        if not upload_url or not asset_url:
            logger.error(f"Linear returned no upload URL for {filename}")
            return None

        headers = {"Content-Type": content_type}
        for header in upload_file.get("headers") or []:
            key, value = header.get("key"), header.get("value")
            if key and value is not None:
                headers[key] = value

        try:
            # Deliberately not self._client: that carries the Linear auth header,
            # which the storage backend rejects.
            async with httpx.AsyncClient(timeout=self.timeout * 4) as uploader:
                resp = await uploader.put(upload_url, content=data, headers=headers)
                resp.raise_for_status()
        except Exception as e:
            logger.error(f"Upload of {filename} to asset store failed: {e}")
            return None

        logger.info(f"Uploaded artifact {filename} ({len(data):,} bytes)")
        return asset_url

    async def fetch_team_labels(self, issue_id: str) -> tuple[str | None, dict[str, str], set[str]]:
        """Return (team_id, {lowercased label name: id}, {ids already on issue})."""
        try:
            data = await self._graphql(TEAM_LABELS_QUERY, {"issueId": issue_id})
        except Exception as e:
            logger.error(f"Failed to fetch labels for {issue_id}: {e}")
            return None, {}, set()

        issue = (data or {}).get("issue") or {}
        team = issue.get("team") or {}
        labels = {
            node["name"].strip().lower(): node["id"]
            for node in (team.get("labels") or {}).get("nodes", [])
            if node.get("name") and node.get("id")
        }
        existing = {
            node["id"]
            for node in (issue.get("labels") or {}).get("nodes", [])
            if node.get("id")
        }
        return team.get("id"), labels, existing

    async def create_label(
        self, team_id: str, name: str, color: str | None = None
    ) -> str | None:
        """Create a team label. Returns its id, or None if creation failed."""
        try:
            data = await self._graphql(
                LABEL_CREATE_MUTATION,
                {"teamId": team_id, "name": name, "color": color},
            )
        except Exception as e:
            logger.error(f"Failed to create label '{name}': {e}")
            return None
        payload = (data or {}).get("issueLabelCreate") or {}
        if not payload.get("success"):
            return None
        return (payload.get("issueLabel") or {}).get("id")

    async def add_label(self, issue_id: str, label_id: str) -> bool:
        """Attach an existing label to an issue."""
        try:
            data = await self._graphql(
                ISSUE_ADD_LABEL_MUTATION,
                {"issueId": issue_id, "labelId": label_id},
            )
            return (data or {}).get("issueAddLabel", {}).get("success", False)
        except Exception as e:
            logger.error(f"Failed to add label to {issue_id}: {e}")
            return False

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
