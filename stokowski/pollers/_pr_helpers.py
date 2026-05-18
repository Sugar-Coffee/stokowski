#!/usr/bin/env python3
"""
Shared helpers for poll_ci_status.py and poll_pr_conflicts.py.

PR list caching
---------------
``get_symphony_prs()`` (used by poll_pr_conflicts.py) caches the ``gh pr list``
response in /tmp with a CACHE_TTL_S-second TTL.

``fetch_symphony_pr_full()`` (used by poll_ci_status.py) issues a single
``gh api graphql`` call per tick that returns *everything* the poller needs:
open + closed PRs, each with the HEAD commit's check-runs, the commit date,
and recent comments. Replaces the per-ticket ``gh pr checks`` +
``gh pr view --json comments`` + ``gh api git/commits/<sha>`` cascade that
exhausted the 5,000/hr GraphQL budget when more than a handful of tickets
sat in Awaiting CI (caused the SYN-1039 / SYN-924 / SYN-919 / SYN-948 /
SYN-955 escapes on 2026-05-11).

TTL tradeoff (F13)
------------------
Early versions used a 30s TTL. At 30s, a freshly-rebased PR can appear
CONFLICTING to the conflict poller for the full 30-second window — long
enough for a poller tick to wrongly bounce the ticket back to Rework.
At CACHE_TTL_S=10 that window shrinks to at most 10 seconds. Because the
conflict poller runs every 5 minutes, any stale-CONFLICTING read clears on
the very next tick (~10s after cache expiry) rather than persisting across
multiple ticks.

Resilience contract
-------------------
All public fetchers in this module are *transient-failure-safe*: a
``gh`` non-zero exit (rate limit, network blip, auth glitch) is caught and
surfaced as a ``None`` return (rich fetcher) or empty list (list fetchers),
with a warning to stderr. Callers must NOT raise on missing data — they
should skip the tick and try again next time. This mirrors the pattern in
poll-runners-online.py (8ca4e1e3) for ``urllib.error.URLError``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

# How long (seconds) to serve a cached PR list before re-fetching.
# See "TTL tradeoff (F13)" in the module docstring for rationale.
CACHE_TTL_S: int = 10

# Required-status-checks change only on admin branch-protection edits — a
# generous TTL avoids redundant REST calls while keeping the gate current.
REQUIRED_CHECKS_CACHE_TTL_S: int = 300  # 5 minutes


def _cache_path(repo: str) -> str:
    slug = hashlib.sha1(repo.encode()).hexdigest()[:12]
    return os.path.join("/tmp", f"_pr_helpers_symphony_prs_{slug}.json")


def get_symphony_prs(repo: str, *, ttl: int = CACHE_TTL_S) -> list[dict]:
    """Return open symphony PRs for *repo*, using a /tmp cache.

    Fields returned: number, title, body, url, headRefName, mergeable, headRefOid.
    Callers may ignore fields they do not need.
    Cache is keyed by repo so that multiple repos on the same host get
    separate cache files.

    On transient ``gh`` failure (rate limit, network), returns an empty
    list and logs a warning. Callers should treat that as "no PRs to
    process this tick" and retry on the next invocation.
    """
    path = _cache_path(repo)
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached["ts"] < ttl:
            return cached["prs"]
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass

    prs = _fetch_symphony_prs(repo)
    if not prs:
        # Don't overwrite a healthy cache with an empty result from a
        # failed fetch. If we have a cache file, leave it; otherwise skip.
        return prs
    try:
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "prs": prs}, f)
    except OSError:
        pass  # cache write failure is non-fatal; pollers proceed with fresh data
    return prs


def _fetch_symphony_prs(repo: str) -> list[dict]:
    try:
        out = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", repo,
                "--label", "symphony",
                "--state", "open",
                "--json", "number,title,body,url,headRefName,mergeable,headRefOid",
                "--limit", "200",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        _warn_gh_failure("gh pr list (open symphony)", e)
        return []
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        print(f"warning: gh pr list returned invalid JSON: {e}", file=sys.stderr)
        return []


def _closed_cache_path(repo: str) -> str:
    slug = hashlib.sha1(repo.encode()).hexdigest()[:12]
    return os.path.join("/tmp", f"_pr_helpers_closed_symphony_prs_{slug}.json")


# Closed PRs change rarely (a merged PR stays merged), so a longer cache TTL
# is fine. The cost of staleness here is small: a ticket whose closed-PR
# transition is delayed by ~5 min is acceptable, and the cache invalidates
# anyway on every poll-ci-status tick (every 2 min).
CLOSED_CACHE_TTL_S: int = 120


def get_closed_symphony_prs(repo: str, *, ttl: int = CLOSED_CACHE_TTL_S) -> list[dict]:
    """Return recently closed/merged symphony PRs for *repo*, /tmp-cached.

    Includes both ``state == "MERGED"`` and ``state == "CLOSED"`` (unmerged).
    Used to resolve ghost-Awaiting-CI tickets whose PR was closed or merged
    after the agent stopped polling. Returns ``[]`` on transient gh failure.
    """
    path = _closed_cache_path(repo)
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached["ts"] < ttl:
            return cached["prs"]
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass

    prs = _fetch_closed_symphony_prs(repo)
    if not prs:
        return prs
    try:
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "prs": prs}, f)
    except OSError:
        pass
    return prs


def _fetch_closed_symphony_prs(repo: str) -> list[dict]:
    try:
        out = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", repo,
                "--label", "symphony",
                "--state", "closed",
                "--json", "number,title,body,url,headRefName,state,closedAt,mergedAt",
                "--limit", "100",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        _warn_gh_failure("gh pr list (closed symphony)", e)
        return []
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        print(f"warning: gh pr list (closed) returned invalid JSON: {e}", file=sys.stderr)
        return []


def _req_checks_cache_path(repo: str, branch: str) -> str:
    slug = hashlib.sha1(f"{repo}:{branch}".encode()).hexdigest()[:12]
    return os.path.join("/tmp", f"_pr_helpers_req_checks_{slug}.json")


def get_required_status_checks(repo: str, branch: str) -> set[str] | None:
    """Return required status check context names for *branch* in *repo*.

    Uses GitHub's branch protection API:
      GET /repos/{owner}/{repo}/branches/{branch}/protection/required_status_checks

    Returns a non-empty ``set[str]`` of context names when branch protection is
    configured with at least one required check. Returns ``None`` when:
      - the branch has no protection rule (404)
      - protection exists but has no required checks (empty contexts)
      - the API call fails (transient error, auth)

    Callers treat ``None`` as "unknown" and fall back to blocking on all
    failing checks (current behavior). This means the fix is opt-in: repos
    without branch protection are unaffected.

    Results are cached for REQUIRED_CHECKS_CACHE_TTL_S seconds since
    branch protection changes are rare (admin-only operations).
    """
    path = _req_checks_cache_path(repo, branch)
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached["ts"] < REQUIRED_CHECKS_CACHE_TTL_S:
            data = cached["data"]
            return set(data) if data is not None else None
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass

    contexts = _fetch_required_status_checks(repo, branch)
    try:
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "data": list(contexts) if contexts is not None else None}, f)
    except OSError:
        pass
    return contexts


def _fetch_required_status_checks(repo: str, branch: str) -> set[str] | None:
    """Fetch required status check contexts from GitHub's branch protection API.

    Returns a non-empty set of context names, or None on 404 / empty / failure.
    """
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/branches/{branch}/protection/required_status_checks"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").lower()
        if e.returncode == 1 and ("404" in stderr or "not found" in stderr or "branch is not protected" in stderr):
            # No branch protection configured — not an error.
            return None
        _warn_gh_failure(f"gh api branches/{branch}/protection/required_status_checks", e)
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

    contexts: set[str] = set()
    # Newer GitHub API format: "checks" array with per-check "context" field.
    for check in data.get("checks") or []:
        ctx = check.get("context")
        if ctx:
            contexts.add(ctx)
    # Legacy format: "contexts" is a flat list of strings.
    for ctx in data.get("contexts") or []:
        if isinstance(ctx, str):
            contexts.add(ctx)
    return contexts if contexts else None


# ============================================================================
# Consolidated GraphQL fetch — replaces per-ticket gh pr checks / gh pr view /
# gh api git/commits in poll_ci_status.py with a single GraphQL query per tick.
# ============================================================================

# A single GraphQL query that returns:
#  - Open symphony PRs with their HEAD commit's check-runs, commit date, and
#    recent comments (everything poll_ci_status needs per ticket).
#  - Closed/merged symphony PRs (for ghost-Awaiting-CI resolution).
#
# Counts: 50 open PRs × (1 commit × 50 checks + 50 comments) + 50 closed PRs
#   ≈ 5,100 nodes per query. Well below GitHub's 500,000-node hard limit.
#
# Cost: 1 GraphQL point per call. Replaces the previous ~(2N+2) calls per
# tick (where N = number of Awaiting-CI tickets).
SYMPHONY_PRS_QUERY = """
query SymphonyPRs($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    openPRs: pullRequests(
      labels: ["symphony"]
      states: [OPEN]
      orderBy: { field: UPDATED_AT, direction: DESC }
      first: 100
    ) {
      pageInfo { hasNextPage }
      nodes {
        number
        title
        body
        url
        headRefName
        headRefOid
        mergeable
        state
        labels(first: 20) { nodes { name } }
        commits(last: 1) {
          nodes {
            commit {
              oid
              committedDate
              statusCheckRollup {
                state
                contexts(first: 50) {
                  totalCount
                  nodes {
                    __typename
                    ... on CheckRun {
                      name
                      conclusion
                      status
                      detailsUrl
                    }
                    ... on StatusContext {
                      context
                      state
                      targetUrl
                    }
                  }
                }
              }
            }
          }
        }
        comments(last: 50) {
          nodes {
            body
            createdAt
            author {
              login
            }
          }
        }
      }
    }
    closedPRs: pullRequests(
      labels: ["symphony"]
      states: [CLOSED, MERGED]
      orderBy: { field: UPDATED_AT, direction: DESC }
      first: 100
    ) {
      nodes {
        number
        title
        body
        url
        headRefName
        state
        closedAt
        mergedAt
      }
    }
  }
}
""".strip()


def fetch_symphony_pr_full(repo: str) -> dict | None:
    """Single GraphQL fetch for everything poll_ci_status needs per tick.

    Returns a dict on success::

        {
          "open":   {pr_number: pr_data_dict, ...},   # ordered most-recently-updated first
          "closed": [pr_data_dict, ...],
          "has_next_page": bool,    # True if >50 open symphony PRs
        }

    Each open ``pr_data_dict`` carries the same fields as ``get_symphony_prs``
    PLUS:

      - ``headCommittedDate``: ISO-8601 string (replaces the per-PR
        ``gh api git/commits/<sha>`` REST call).
      - ``checks``: list of ``{name, bucket, state, link}`` shaped to match
        ``gh pr checks --json`` output, so ``check_ci_status`` can consume
        it unchanged.
      - ``comments``: list of ``{body, createdAt}`` (last 50, oldest-first).

    Returns ``None`` on transient ``gh`` / GraphQL failure. Callers MUST
    treat ``None`` as "skip this tick" and not transition any tickets —
    we can't tell open from closed when the read failed.
    """
    owner, _, name = repo.partition("/")
    if not name:
        raise ValueError(f"repo must be 'owner/name', got: {repo!r}")
    try:
        out = subprocess.run(
            [
                "gh", "api", "graphql",
                "-F", f"owner={owner}",
                "-F", f"name={name}",
                "-f", f"query={SYMPHONY_PRS_QUERY}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        _warn_gh_failure("gh api graphql (symphony PRs)", e)
        return None
    try:
        body = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        print(f"warning: gh api graphql returned invalid JSON: {e}", file=sys.stderr)
        return None
    if "errors" in body:
        # GraphQL itself returned errors (e.g., rate-limit, schema change).
        # Surface and bail rather than transitioning tickets on partial data.
        print(
            f"warning: gh api graphql returned errors; skipping tick: {body['errors']}",
            file=sys.stderr,
        )
        return None
    repo_data = (body.get("data") or {}).get("repository") or {}
    open_block = repo_data.get("openPRs") or {}
    closed_block = repo_data.get("closedPRs") or {}
    open_nodes = open_block.get("nodes") or []
    closed_nodes = closed_block.get("nodes") or []
    has_next_page = (open_block.get("pageInfo") or {}).get("hasNextPage", False)
    if has_next_page:
        print(
            "warning: >50 open symphony PRs; oldest are excluded from this tick. "
            "Consider raising the GraphQL page size.",
            file=sys.stderr,
        )
    return {
        "open": {n["number"]: _shape_open_pr(n) for n in open_nodes},
        "closed": closed_nodes,
        "has_next_page": has_next_page,
    }


def _shape_open_pr(node: dict) -> dict:
    """Flatten a GraphQL PullRequest node into the dict shape poll_ci_status expects."""
    commits = (node.get("commits") or {}).get("nodes") or []
    commit = commits[0]["commit"] if commits else {}
    rollup = commit.get("statusCheckRollup") or {}
    rollup_contexts = (rollup.get("contexts") or {}).get("nodes") or []
    checks: list[dict] = []
    for ctx in rollup_contexts:
        normalized = _normalize_check_node(ctx)
        if normalized is not None:
            checks.append(normalized)
    comments = ((node.get("comments") or {}).get("nodes")) or []
    label_nodes = ((node.get("labels") or {}).get("nodes")) or []
    labels = [l.get("name", "") for l in label_nodes if l.get("name")]
    return {
        "number": node["number"],
        "title": node.get("title") or "",
        "body": node.get("body") or "",
        "url": node.get("url") or "",
        "headRefName": node.get("headRefName") or "",
        "headRefOid": node.get("headRefOid") or "",
        "mergeable": node.get("mergeable") or "",
        "state": node.get("state") or "OPEN",
        "labels": labels,
        "headCommittedDate": commit.get("committedDate"),
        "checks": checks,
        "comments": comments,
    }


def _normalize_check_node(ctx: dict) -> dict | None:
    """Normalize a CheckRun/StatusContext GraphQL node to gh-pr-checks shape.

    The existing ``check_ci_status`` function in poll_ci_status.py reads
    ``{name, bucket, state, link}`` directly from ``gh pr checks --json``.
    Mapping the GraphQL response into the same shape lets that logic stay
    untouched.

    Bucket vocabulary (matches gh CLI):
      - ``pass``     → check completed successfully (SUCCESS, NEUTRAL)
      - ``fail``     → check failed (FAILURE, TIMED_OUT, ACTION_REQUIRED, STALE,
                                     STARTUP_FAILURE, status FAILURE/ERROR)
      - ``cancel``   → check cancelled (used by concurrency-supersede)
      - ``pending``  → check still running (IN_PROGRESS, QUEUED, PENDING, etc.)
      - ``skipping`` → check skipped (SKIPPED) — treated as pass-equivalent by
                       check_ci_status (not in failures/cancelled/pending)
    """
    typename = ctx.get("__typename")
    if typename == "CheckRun":
        name = ctx.get("name") or "?"
        status = (ctx.get("status") or "").upper()
        conclusion = (ctx.get("conclusion") or "").upper()
        link = ctx.get("detailsUrl") or ""
        if status != "COMPLETED":
            # IN_PROGRESS / QUEUED / PENDING / WAITING / REQUESTED
            return {"name": name, "bucket": "pending", "state": "IN_PROGRESS", "link": link}
        if conclusion in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE"):
            return {"name": name, "bucket": "fail", "state": "FAILURE", "link": link}
        if conclusion == "CANCELLED":
            return {"name": name, "bucket": "cancel", "state": "CANCELLED", "link": link}
        if conclusion == "SKIPPED":
            return {"name": name, "bucket": "skipping", "state": "SKIPPED", "link": link}
        if conclusion in ("SUCCESS", "NEUTRAL"):
            return {"name": name, "bucket": "pass", "state": "SUCCESS", "link": link}
        # Unknown conclusion — treat as pending so we wait rather than fire false Rework
        return {"name": name, "bucket": "pending", "state": "PENDING", "link": link}
    if typename == "StatusContext":
        name = ctx.get("context") or "?"
        state_raw = (ctx.get("state") or "").upper()
        link = ctx.get("targetUrl") or ""
        if state_raw in ("FAILURE", "ERROR"):
            return {"name": name, "bucket": "fail", "state": state_raw, "link": link}
        if state_raw in ("PENDING", "EXPECTED"):
            return {"name": name, "bucket": "pending", "state": state_raw, "link": link}
        if state_raw == "SUCCESS":
            return {"name": name, "bucket": "pass", "state": state_raw, "link": link}
        return {"name": name, "bucket": "pending", "state": state_raw, "link": link}
    return None


def _warn_gh_failure(label: str, exc: subprocess.CalledProcessError) -> None:
    """Print a one-line stderr warning for a transient gh failure.

    Format includes return code + a snippet of stderr so journalctl shows
    the specific failure mode (rate limit, network, auth, etc.) without
    spewing a full stack trace.
    """
    stderr_snippet = (exc.stderr or "").strip().replace("\n", " ")[:300]
    print(
        f"warning: {label} failed (rc={exc.returncode}); skipping this tick. {stderr_snippet}",
        file=sys.stderr,
    )
