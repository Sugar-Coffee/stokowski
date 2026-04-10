"""GitHub Issues tracker client."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from .models import Issue

logger = logging.getLogger("stokowski.github")


def _parse_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class GitHubIssuesClient:
    """GitHub Issues backend using REST API v3.

    State mapping: GitHub Issues only have open/closed. Stokowski uses
    labels prefixed with a configurable namespace (default "stokowski:")
    to represent workflow states. update_issue_state swaps labels atomically.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        state_label_prefix: str = "stokowski:",
        timeout_ms: int = 30_000,
    ):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.prefix = state_label_prefix
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout_ms / 1000,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # -- Helpers ----------------------------------------------------------------

    def _state_label(self, state_name: str) -> str:
        """Convert a logical state name to a GitHub label."""
        return f"{self.prefix}{state_name}"

    def _extract_state(self, labels: list[dict]) -> str:
        """Extract the Stokowski state from a list of GitHub label dicts."""
        for label in labels:
            name = label.get("name", "")
            if name.startswith(self.prefix):
                return name[len(self.prefix) :]
        return ""

    def _extract_label_names(self, labels: list[dict]) -> list[str]:
        """Extract non-state label names."""
        return [
            label["name"]
            for label in labels
            if label.get("name") and not label["name"].startswith(self.prefix)
        ]

    def _extract_priority(self, labels: list[dict]) -> int | None:
        """Extract priority from labels like 'priority:1'."""
        for label in labels:
            name = label.get("name", "")
            if name.startswith("priority:"):
                try:
                    return int(name.split(":")[1])
                except (ValueError, IndexError):
                    pass
        return None

    def _normalize_issue(self, data: dict, minimal: bool = False) -> Issue:
        """Convert a GitHub issue JSON to an Issue model."""
        labels_raw = data.get("labels", [])
        issue_number = data["number"]
        return Issue(
            id=str(issue_number),
            identifier=f"#{issue_number}",
            title=data.get("title", "") if not minimal else "",
            description=data.get("body") if not minimal else None,
            priority=self._extract_priority(labels_raw) if not minimal else None,
            state=self._extract_state(labels_raw),
            branch_name=None,
            url=data.get("html_url"),
            labels=self._extract_label_names(labels_raw) if not minimal else [],
            blocked_by=[],
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            project_slug=None,
        )

    async def _get_paginated(self, url: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages from a GitHub API endpoint."""
        results: list[dict] = []
        params = dict(params or {})
        params.setdefault("per_page", "100")

        while url:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            results.extend(resp.json())
            # Parse Link header for next page
            url = ""
            link = resp.headers.get("link", "")
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
            params = {}  # params are in the URL for subsequent pages
        return results

    async def _ensure_labels_exist(self, label_names: list[str]) -> None:
        """Create labels if they don't exist (best-effort)."""
        for name in label_names:
            try:
                resp = await self._client.get(
                    f"{self.base_url}/labels/{name}"
                )
                if resp.status_code == 404:
                    await self._client.post(
                        f"{self.base_url}/labels",
                        json={"name": name, "color": "6B5220"},
                    )
            except Exception as e:
                logger.debug(f"Label check/create for '{name}' failed: {e}")

    # -- TrackerClient interface ------------------------------------------------

    async def fetch_candidate_issues(
        self,
        project_slug: str,
        active_states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch all open issues that have one of the active state labels."""
        issues: list[Issue] = []
        seen_ids: set[int] = set()

        for state_name in active_states:
            label = self._state_label(state_name)
            try:
                items = await self._get_paginated(
                    f"{self.base_url}/issues",
                    {"state": "open", "labels": label},
                )
                for item in items:
                    if item.get("pull_request"):
                        continue  # skip PRs (GitHub returns them in /issues)
                    num = item["number"]
                    if num not in seen_ids:
                        seen_ids.add(num)
                        issues.append(self._normalize_issue(item))
            except Exception as e:
                logger.error(f"Failed to fetch issues with label '{label}': {e}")

        return issues

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, str]:
        """Fetch current states for given issue numbers."""
        if not issue_ids:
            return {}

        result: dict[str, str] = {}
        for issue_id in issue_ids:
            try:
                resp = await self._client.get(
                    f"{self.base_url}/issues/{issue_id}"
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("state") == "closed":
                    result[issue_id] = "closed"
                else:
                    result[issue_id] = self._extract_state(data.get("labels", []))
            except Exception as e:
                logger.error(f"Failed to fetch issue #{issue_id}: {e}")
        return result

    async def fetch_issues_by_states(
        self,
        project_slug: str,
        states: list[str],
        team_key: str = "",
    ) -> list[Issue]:
        """Fetch minimal issues in specific states."""
        issues: list[Issue] = []
        seen_ids: set[int] = set()

        for state_name in states:
            label = self._state_label(state_name)
            try:
                items = await self._get_paginated(
                    f"{self.base_url}/issues",
                    {"state": "all", "labels": label},
                )
                for item in items:
                    if item.get("pull_request"):
                        continue
                    num = item["number"]
                    if num not in seen_ids:
                        seen_ids.add(num)
                        issues.append(self._normalize_issue(item, minimal=True))
            except Exception as e:
                logger.error(f"Failed to fetch issues with label '{label}': {e}")

        return issues

    async def post_comment(self, issue_id: str, body: str) -> bool:
        """Post a comment on a GitHub issue."""
        try:
            resp = await self._client.post(
                f"{self.base_url}/issues/{issue_id}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to post comment on #{issue_id}: {e}")
            return False

    async def fetch_comments(self, issue_id: str) -> list[dict]:
        """Fetch all comments on a GitHub issue."""
        try:
            items = await self._get_paginated(
                f"{self.base_url}/issues/{issue_id}/comments"
            )
            return [
                {
                    "id": str(c["id"]),
                    "body": c.get("body", ""),
                    "createdAt": c.get("created_at", ""),
                }
                for c in items
            ]
        except Exception as e:
            logger.error(f"Failed to fetch comments for #{issue_id}: {e}")
            return []

    async def update_issue_state(self, issue_id: str, state_name: str) -> bool:
        """Update issue state by swapping stokowski: labels.

        Removes all existing stokowski: labels, adds the new one.
        If state_name matches a configured terminal state, also closes the issue.
        """
        try:
            # Fetch current labels
            resp = await self._client.get(f"{self.base_url}/issues/{issue_id}")
            resp.raise_for_status()
            data = resp.json()

            current_labels = [
                l["name"] for l in data.get("labels", [])
                if not l["name"].startswith(self.prefix)
            ]
            new_label = self._state_label(state_name)
            await self._ensure_labels_exist([new_label])
            current_labels.append(new_label)

            # Replace labels atomically
            resp = await self._client.put(
                f"{self.base_url}/issues/{issue_id}/labels",
                json={"labels": current_labels},
            )
            resp.raise_for_status()
            logger.info(f"Moved issue #{issue_id} to state '{state_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to update state for #{issue_id}: {e}")
            return False

    async def fetch_issue_description(self, issue_id: str) -> str:
        """Fetch the issue body."""
        try:
            resp = await self._client.get(f"{self.base_url}/issues/{issue_id}")
            resp.raise_for_status()
            return resp.json().get("body", "") or ""
        except Exception as e:
            logger.error(f"Failed to fetch description for #{issue_id}: {e}")
            return ""

    async def update_issue_description(self, issue_id: str, description: str) -> bool:
        """Update the issue body."""
        try:
            resp = await self._client.patch(
                f"{self.base_url}/issues/{issue_id}",
                json={"body": description},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to update description for #{issue_id}: {e}")
            return False
