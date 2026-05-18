#!/usr/bin/env python3
"""
Poll Linear tickets in `Awaiting CI` and reflect CI + reviewer status back.

For each ticket in the configured project's `Awaiting CI` state:
  1. Find the linked symphony PR (matches the ticket's identifier in title/body/branch).
  2. Read PR CI status from the consolidated GraphQL fetch.
  3. Read PR comments for the AI reviewer's `REVIEW_VERDICT: APPROVE|REQUEST_CHANGES`
     line, unless reviewer is skipped for this ticket.
  4. Decide:
       - CI green AND (reviewer APPROVE or skipped)  -> Gate Approved
       - CI red OR reviewer REQUEST_CHANGES          -> Rework (with comment)
       - CI never fired (OPEN+mergeable, zero check
         runs) past the grace window                 -> re-trigger CI via an
                                                        empty commit, capped
                                                        at 3 attempts, then
                                                        fall back to Rework
       - Anything still pending                      -> leave alone, retry next tick

Reviewer is skipped when:
  - The Linear ticket has the `skip-review` label.
  - The PR title contains `[skip-review]` (also makes the reviewer workflow itself skip).

Idempotent: safe to run on a timer. The Rework comment uses a marker to avoid
duplicate notifications.

GraphQL load (post 2026-05-11 refactor)
---------------------------------------
This tick issues exactly **one** `gh api graphql` call against GitHub —
``fetch_symphony_pr_full`` in ``_pr_helpers.py``. That call returns every
open + closed symphony PR with checks, commit date, and comments embedded.
Prior to the refactor, each Awaiting-CI ticket required ~2 separate `gh`
GraphQL calls per tick (`gh pr checks` + `gh pr view --json comments`),
plus 1 list call and 1 REST commit-date call. At ~10 tickets, that
exhausted the 5,000/hr GraphQL budget and the script crashed mid-loop,
stranding tickets (SYN-1039 / SYN-924 / SYN-919 / SYN-948 / SYN-955).

If the fetch fails (rate limit, transient gh error), the tick exits
gracefully with a warning — no tickets transitioned, the next tick retries.

Env:
  LINEAR_API_KEY              required
  GH_TOKEN or `gh auth status` already authed
  REPO                        optional, defaults to SyncedTech/synced-sport
  PROJECT_ID                  optional, Linear project to poll (defaults to CMBA Trial)
  STOKOWSKI_URL               optional, URL of the Stokowski dashboard API
  LINEAR_TEAM_ID              optional, team owning workflow labels
  LINEAR_STATE_IN_PROGRESS    optional, Linear state ID for In Progress
  LINEAR_STATE_AWAITING_CI    optional, Linear state ID for Awaiting CI
  LINEAR_STATE_GATE_APPROVED  optional, Linear state ID for Gate Approved
  LINEAR_STATE_REWORK         optional, Linear state ID for Rework
  LINEAR_STATE_DONE           optional, Linear state ID for Done
  LINEAR_STATE_CANCELED       optional, Linear state ID for Canceled

Usage:
  stokowski-poll-ci-status            # one shot (console script)
  stokowski-poll-ci-status --verbose  # log every ticket checked
  python -m stokowski.pollers.poll_ci_status  # via python -m
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pr_helpers

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY")
if not LINEAR_API_KEY:
    print("error: LINEAR_API_KEY not set", file=sys.stderr)
    sys.exit(2)

REPO = os.environ.get("REPO", "SyncedTech/synced-sport")
# Default: CMBA Trial (2026-05-31). Override with PROJECT_ID env var to point
# at a different Linear project (e.g. E0 Tech Debt = 7e5a667f-cdd0-4376-91b5-5b950bf6b0e6).
PROJECT_ID = os.environ.get("PROJECT_ID", "d3afa11e-877b-4bda-9bf0-63ed86c653d3")
STOKOWSKI_URL = os.environ.get("STOKOWSKI_URL", "http://127.0.0.1:7878")

# Workflow state IDs — override via env for different Linear teams/projects.
IN_PROGRESS_STATE_ID   = os.environ.get("LINEAR_STATE_IN_PROGRESS",   "ad121b92-ec8a-47c5-bc06-cd0b5d70e76a")
AWAITING_CI_STATE_ID   = os.environ.get("LINEAR_STATE_AWAITING_CI",   "7de43ad3-40a0-430c-bd7e-282c4f95c660")
GATE_APPROVED_STATE_ID = os.environ.get("LINEAR_STATE_GATE_APPROVED", "ae56053a-2627-42e4-8e7f-c96b8eb7b51c")
REWORK_STATE_ID        = os.environ.get("LINEAR_STATE_REWORK",        "84d1fc1f-d2b7-47c2-add2-5bbfcd930252")
DONE_STATE_ID          = os.environ.get("LINEAR_STATE_DONE",          "38a17a85-a54e-4414-ae96-372b8c169caf")
CANCELED_STATE_ID      = os.environ.get("LINEAR_STATE_CANCELED",      "bdcb15e5-a2de-4da7-8ca9-141180500fa1")

# Stale-In-Progress safety net: if a ticket has been in In Progress for longer
# than this AND has an open symphony PR AND no agent is currently running on it,
# force-transition to Awaiting CI. Catches the case where the agent pushed but
# failed to transition (and Stokowski's auto-transition also didn't fire).
STALE_IN_PROGRESS_THRESHOLD_S = 15 * 60  # 15 minutes

# Stale-Awaiting-CI safety net: if a ticket has been in Awaiting CI for longer
# than this AND check_ci_status still returns 'pending', CI never triggered or
# settled (runner down, workflow not started, concurrency-group cancellation).
# Bounce to Rework so the agent re-pushes, triggering a fresh CI run.
STALE_AWAITING_CI_THRESHOLD_S = 60 * 60  # 60 minutes

# "CI never fired" grace + retry policy (item B).
#
# Distinct from STALE_AWAITING_CI_THRESHOLD_S, which covers "checks exist but
# never settle" (a genuine runner stall — re-triggering wouldn't help). This
# covers the *other* failure mode: the PR is OPEN + mergeable but has ZERO
# real check runs in its statusCheckRollup. That means no `synchronize` event
# ever reached `ci.yml` (no push after a server-side rebase, runner was
# disk-full and never claimed the job, etc.). The code is fine; only the
# trigger was lost. Re-pushing an empty commit fires a fresh `synchronize`
# and the checks attach to the PR — far better than a false Rework that
# wastefully re-dispatches a clean agent (real case: SYN-803 / PR #811).
#
# Grace: wait at least this long after the last push before re-triggering,
# so a freshly-pushed PR whose first check simply hasn't spawned yet isn't
# molested. ~1 poll interval (2 min) is too tight under runner queue latency;
# 10 min is comfortably past CI's normal time-to-first-check while still
# reacting well before the 60-min Rework timeout would false-bounce it.
CI_NEVER_FIRED_GRACE_S = 10 * 60  # 10 minutes

# Hard cap on automatic re-triggers per PR. Each attempt drops a marker
# comment on the Linear ticket (same <!-- poll-ci-status --> mechanism the
# rest of this script uses for idempotency); the count of those markers IS
# the attempt counter. After the cap, fall through to the existing Rework
# path so a permanently-broken trigger can't loop forever.
CI_RETRIGGER_MAX_ATTEMPTS = 3

SKIP_REVIEW_LABEL = "skip-review"
AUTO_MERGE_LABEL_NAME = "auto-merge-ok"

# Stokowski's rework re-dispatch (`_handle_rework_pickup`) is triggered
# EXCLUSIVELY by the `needs-rework` Linear label — moving a ticket to the
# `Rework` state alone is a no-op for the orchestrator (it has no
# "Linear state == Rework" detection path). So every time this poller sends
# a ticket to Rework it MUST also apply this label and post the structured
# `stokowski:rework-trigger` marker the orchestrator parses for reason /
# detector. This mirrors the contract poll_pr_conflicts.py already honours.
# (Without it, CI-fail / reviewer-REQUEST_CHANGES tickets strand in Rework
# until a human or the unrelated conflicts poller incidentally labels them —
# the SYN-807 / 6h-stranding root cause.)
NEEDS_REWORK_LABEL_NAME = "needs-rework"
# Linear team that owns the workflow labels (SYN). Same constant
# poll_pr_conflicts.py uses to resolve the needs-rework label id.
TEAM_ID = os.environ.get("LINEAR_TEAM_ID", "82bdad05-fcb3-4fc8-b873-49056aa672d3")
COMMENT_MARKER = "<!-- poll-ci-status -->"
SYN_RE = re.compile(r"\bSYN-(\d+)\b", re.IGNORECASE)
VERDICT_RE = re.compile(r"REVIEW_VERDICT:\s*(APPROVE|REQUEST_CHANGES)", re.IGNORECASE)

# Patterns to parse the reviewer's issue-count summary line. Each severity is
# matched independently — the reviewer sometimes writes only critical+important
# (no minor), or wraps the line in **bold**, etc. Order doesn't matter; we just
# pick out each integer next to the severity word.
ISSUE_CRITICAL_RE  = re.compile(r"(\d+)\s+critical",  re.IGNORECASE)
ISSUE_IMPORTANT_RE = re.compile(r"(\d+)\s+important", re.IGNORECASE)
ISSUE_MINOR_RE     = re.compile(r"(\d+)\s+minor",     re.IGNORECASE)

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr)


def gql(query, variables=None):
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    if "errors" in body:
        raise RuntimeError(f"Linear API error: {body['errors']}")
    return body["data"]


def stokowski_running_identifiers():
    """Return the set of Linear identifiers currently running in Stokowski.

    Returns an empty set if the Stokowski dashboard is unreachable — in that
    case we conservatively skip the stale-In-Progress check rather than risk
    transitioning a ticket whose agent is genuinely mid-flight.
    """
    try:
        with urllib.request.urlopen(f"{STOKOWSKI_URL}/api/v1/state", timeout=5) as r:
            data = json.loads(r.read())
        return {x["issue_identifier"] for x in data.get("running", [])}
    except Exception as e:
        log(f"  ! Stokowski API unreachable ({e}); skipping stale check this tick")
        return None  # sentinel: skip stale check


def list_in_progress_issues():
    """Linear project tickets currently in 'In Progress' state, with updatedAt."""
    data = gql(
        """
        query($pid: ID!, $state: ID!) {
          issues(filter: { project: { id: { eq: $pid } }, state: { id: { eq: $state } } }, first: 100) {
            nodes { id identifier title updatedAt }
          }
        }
        """,
        {"pid": PROJECT_ID, "state": IN_PROGRESS_STATE_ID},
    )
    return data["issues"]["nodes"]


def list_awaiting_ci_issues():
    data = gql(
        """
        query($pid: ID!, $state: ID!) {
          issues(filter: { project: { id: { eq: $pid } }, state: { id: { eq: $state } } }, first: 100) {
            nodes {
              id
              identifier
              title
              updatedAt
              labels { nodes { name } }
            }
          }
        }
        """,
        {"pid": PROJECT_ID, "state": AWAITING_CI_STATE_ID},
    )
    return data["issues"]["nodes"]


def _pr_syn_ids(pr: dict) -> set[str]:
    """Extract every SYN-NNN identifier referenced by *pr* (title/body/branch)."""
    out: set[str] = set()
    for source in (pr.get("title", ""), pr.get("body") or "", pr.get("headRefName", "")):
        for m in SYN_RE.finditer(source):
            out.add(f"SYN-{m.group(1)}".upper())
    return out


def build_syn_index(open_prs: dict[int, dict]) -> dict[str, dict]:
    """Build a SYN-id → pr_data map from the consolidated open-PRs dict.

    *open_prs* is the ``"open"`` block from ``fetch_symphony_pr_full``,
    which preserves the GraphQL UPDATED_AT-DESC ordering. When two open PRs
    reference the same SYN-id, the most-recently-updated one wins (matches
    the prior behaviour where ``find_pr_for_issue`` walked the list and
    returned the first match — ``gh pr list``'s default order was effectively
    the same).
    """
    syn_to_pr: dict[str, dict] = {}
    for pr in open_prs.values():
        for syn_id in _pr_syn_ids(pr):
            if syn_id not in syn_to_pr:  # first-seen wins (most recently updated)
                syn_to_pr[syn_id] = pr
    return syn_to_pr


def find_closed_pr(identifier: str, closed_prs: list[dict]) -> dict | None:
    """Find the most-recent closed/merged symphony PR matching *identifier*.

    Used to resolve ghost-Awaiting-CI tickets whose PR was closed or merged
    after Stokowski stopped polling. Prefers MERGED over CLOSED-unmerged
    when both exist, then most-recent-first within each state.
    """
    matching = []
    for pr in closed_prs:
        if identifier.upper() in _pr_syn_ids(pr):
            matching.append(pr)
    if not matching:
        return None
    # Sort: MERGED first (key 0), then by closedAt descending.
    matching.sort(key=lambda p: (p.get("state") != "MERGED", -_iso_to_ts(p.get("closedAt") or "")))
    return matching[0]


def _iso_to_ts(s):
    """Parse Linear/GitHub ISO 8601 timestamps to a comparable float. Empty → 0."""
    if not s:
        return 0.0
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# Check names to ignore when gating CI status.
# "Analyze (" — orphaned CodeQL default-setup workflow, stuck after GHAS deactivation.
#   Always fails in 2-10s with billing errors; no real code signal.
#   See docs/ci-cd/SELF_HOSTED_RUNNERS.md ("Known cosmetic issues").
# "claude-review" — AI code-review workflow. Its JOB status (FAILURE/SUCCESS) is not
#   a meaningful CI gate; the actual verdict is communicated via the REVIEW_VERDICT line
#   in a PR comment, read separately by reviewer_verdict(). A process crash (timeout,
#   action error) shows as job FAILURE but must not fire Rework — the poller waits for
#   a verdict comment and re-dispatches if needed. (SYN-761)
IGNORED_CHECK_PREFIXES = ("Analyze (", "claude-review")


def check_ci_status(checks, required_contexts=None):
    """Return ``(state, detail)`` for the supplied normalized checks list.

    *checks* matches the shape returned by ``gh pr checks --json
    name,bucket,state,link`` — and by ``_pr_helpers._normalize_check_node``.

    *required_contexts* is an optional ``set[str]`` of check context names that
    GitHub branch protection has marked as required for the PR's base branch.
    When supplied, only checks whose names are in this set are considered for CI
    gating — failing non-required checks are silently ignored. When ``None``
    (branch protection not configured, or fetch failed), all failing checks are
    treated as blocking, which preserves the original behavior.

    State values:
      - ``'pass'``    → all gating checks succeeded (or were skipped)
      - ``'fail'``    → at least one required check failed (Rework)
      - ``'pending'`` → some gating checks still running or cancelled (wait next tick)

    *detail* is the list of checks supporting the state decision (failures
    for fail, pending+cancelled for pending, all for pass).
    """
    if checks is None:
        return "pending", []
    # Drop cosmetic orphan checks (see IGNORED_CHECK_PREFIXES above).
    checks = [c for c in checks if not (c.get("name") or "").startswith(IGNORED_CHECK_PREFIXES)]
    if not checks:
        return "pending", []

    # When required_contexts is available, restrict gating to required checks only.
    # Non-required checks are still fetched and logged but do not block the gate.
    if required_contexts is not None:
        gating = [c for c in checks if (c.get("name") or "") in required_contexts]
        if not gating:
            # Required checks haven't appeared in the rollup yet (CI not triggered
            # or check names don't match branch-protection contexts). Wait rather
            # than falsely advancing.
            log(f"    no required checks visible in rollup yet — treating as pending")
            return "pending", []
    else:
        gating = checks

    failures = [c for c in gating if c.get("bucket") == "fail" or c.get("state") in ("FAILURE", "ERROR")]
    # Cancelled checks are NOT failures — GitHub's concurrency mechanism cancels
    # older workflow runs when a new push supersedes them. Treat them as pending
    # so the poller waits for the new run rather than firing a false Rework.
    cancelled = [c for c in gating if c.get("bucket") == "cancel" or c.get("state") == "CANCELLED"]
    pending = [c for c in gating if c.get("bucket") == "pending" or c.get("state") in ("IN_PROGRESS", "QUEUED", "PENDING")]

    if failures:
        return "fail", failures
    if cancelled:
        cancelled_names = [c.get("name", "?") for c in cancelled]
        log(f"    {len(cancelled)} cancelled check(s) (concurrency supersede or manual): {cancelled_names} — treating as pending")
    if pending or cancelled:
        return "pending", pending + cancelled
    return "pass", gating


# Matches the same pattern the CI workflow uses:
#   gh api ... --jq '[.[] | select(.user.login | test("claude"; "i")) | .body]'
# Only accept REVIEW_VERDICT lines from accounts whose login contains "claude"
# (case-insensitive). This prevents false-positive reworks when the Linear bot
# posts an issue description that quotes a prior REVIEW_VERDICT: REQUEST_CHANGES
# from a different PR — the linear[bot] login does not contain "claude", so it
# is filtered out.
REVIEWER_LOGIN_RE = re.compile(r"claude", re.IGNORECASE)


def reviewer_verdict(comments, after=None):
    """Return ``(verdict, body)`` from the most recent ``REVIEW_VERDICT:`` comment.

    *comments* is the list of comment dicts from
    ``fetch_symphony_pr_full``: each has ``body``, ``createdAt``, and
    ``author { login }``.

    Only accepts verdict comments from authors whose login matches
    ``REVIEWER_LOGIN_RE`` (contains "claude", case-insensitive). This filters
    out the Linear bot's issue-description comment, which may quote prior
    ``REVIEW_VERDICT: REQUEST_CHANGES`` lines from unrelated PRs.

    Returns ``(None, None)`` when no verdict is found or when the latest
    verdict predates *after* (an ISO-8601 timestamp). A verdict older
    than *after* means the reviewer ran against a stale commit and has not
    yet seen current HEAD — the caller should keep waiting rather than
    acting on a stale opinion.
    """
    if not comments:
        return None, None
    # Walk newest-first so the most recent verdict wins (reviewer can re-review
    # after a push). The GraphQL query returns oldest-first, so reverse here.
    for c in reversed(comments):
        author = c.get("author") or {}
        login = author.get("login", "")
        if not REVIEWER_LOGIN_RE.search(login):
            continue
        body = c.get("body", "")
        m = VERDICT_RE.search(body)
        if m:
            if after:
                verdict_at = c.get("createdAt", "")
                if verdict_at and verdict_at <= after:
                    log(f"    reviewer verdict '{m.group(1)}' is stale (comment {verdict_at} <= commit {after}) -- ignoring")
                    return None, None
            return m.group(1).lower(), body
    return None, None


def parse_issue_counts(body):
    """Extract critical / important / minor counts from a reviewer comment body.

    The reviewer typically writes a summary like:
        "0 critical, 1 important, 2 minor issues found."
        "**1 critical, 1 important issues found.**"
        "0 critical, 0 important, 1 minor issue (carry-forward)."

    Returns a dict ``{'critical': int, 'important': int, 'minor': int}``.
    Severities not mentioned in the body default to 0 — e.g. an APPROVE
    comment that says "0 critical, 1 important" with no minor mention is
    treated as minor=0, which is the conservative interpretation (don't
    invent issues).
    """
    def _first_int(rx):
        m = rx.search(body or "")
        return int(m.group(1)) if m else 0
    return {
        'critical':  _first_int(ISSUE_CRITICAL_RE),
        'important': _first_int(ISSUE_IMPORTANT_RE),
        'minor':     _first_int(ISSUE_MINOR_RE),
    }


def has_marker_comment(issue_id, marker_suffix):
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) { comments(first: 100) { nodes { body } } }
        }
        """,
        {"id": issue_id},
    )
    target = f"{COMMENT_MARKER}:{marker_suffix}"
    return any(target in c["body"] for c in data["issue"]["comments"]["nodes"])


def post_comment(issue_id, body):
    gql(
        """
        mutation($id: String!, $body: String!) {
          commentCreate(input: { issueId: $id, body: $body }) { success }
        }
        """,
        {"id": issue_id, "body": body},
    )


def move_state(issue_id, state_id):
    gql(
        """
        mutation($id: String!, $state: String!) {
          issueUpdate(id: $id, input: { stateId: $state }) { success }
        }
        """,
        {"id": issue_id, "state": state_id},
    )


# --- Rework signalling (needs-rework label + trigger marker) ---------------
#
# Stokowski's `_handle_rework_pickup` only fires on the `needs-rework` label.
# The Rework *state* by itself does nothing. These helpers attach that label
# and post the structured marker the orchestrator's
# `parse_latest_rework_trigger` reads, so a poller-initiated Rework actually
# re-dispatches the agent. Label resolution is cached per process run because
# Linear's issueUpdate keys labels by id, never by name.

_NEEDS_REWORK_LABEL_ID: str | None = None
_NEEDS_REWORK_LABEL_LOOKED_UP = False


def _needs_rework_label_id():
    """Resolve (and cache) the `needs-rework` label id for TEAM_ID.

    Returns None if the label can't be found — callers degrade gracefully
    (state still moves to Rework; only the auto-pickup is lost, same as the
    pre-fix behaviour) and log a warning so the misconfig is visible.
    """
    global _NEEDS_REWORK_LABEL_ID, _NEEDS_REWORK_LABEL_LOOKED_UP
    if _NEEDS_REWORK_LABEL_LOOKED_UP:
        return _NEEDS_REWORK_LABEL_ID
    _NEEDS_REWORK_LABEL_LOOKED_UP = True
    try:
        data = gql(
            """
            query($team: String!) {
              team(id: $team) { labels { nodes { id name } } }
            }
            """,
            {"team": TEAM_ID},
        )
        for l in (data.get("team") or {}).get("labels", {}).get("nodes", []):
            if l.get("name", "").strip().lower() == NEEDS_REWORK_LABEL_NAME:
                _NEEDS_REWORK_LABEL_ID = l["id"]
                break
    except Exception as e:
        print(f"warning: could not resolve needs-rework label id: {e}",
              file=sys.stderr)
    if _NEEDS_REWORK_LABEL_ID is None:
        print("warning: needs-rework label not found on team; Rework moves "
              "will NOT auto-re-dispatch via Stokowski", file=sys.stderr)
    return _NEEDS_REWORK_LABEL_ID


def _add_needs_rework_label(issue_id):
    """Add the `needs-rework` label, preserving existing labels.

    Linear's issueUpdate replaces the whole labelIds set, so we must read
    the current ids first (mirrors poll_pr_conflicts.add_label). Idempotent:
    a ticket that already has the label is left untouched.
    """
    label_id = _needs_rework_label_id()
    if label_id is None:
        return False
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) { labels { nodes { id } } }
        }
        """,
        {"id": issue_id},
    )
    existing = [l["id"] for l in data["issue"]["labels"]["nodes"]]
    if label_id in existing:
        return True
    gql(
        """
        mutation($id: String!, $labels: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labels }) { success }
        }
        """,
        {"id": issue_id, "labels": existing + [label_id]},
    )
    return True


def _post_rework_trigger(issue_id, reason, detector, pr_number):
    """Post the structured marker Stokowski parses for rework reason/detector.

    Format byte-for-byte matches poll_pr_conflicts.post_rework_trigger so the
    orchestrator's single REWORK_TRIGGER_PATTERN regex handles both pollers.
    Idempotent per (PR, reason): the same trigger isn't re-posted while the
    ticket is still being reworked for that commit's failure.
    """
    import datetime
    marker_suffix = f"rework-trigger-{pr_number}-{reason}"
    if has_marker_comment(issue_id, marker_suffix):
        return
    payload = {
        "reason": reason,
        "detector": detector,
        "pr_number": pr_number,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    body = (
        f"{COMMENT_MARKER}:{marker_suffix}\n"
        f"<!-- stokowski:rework-trigger {json.dumps(payload)} -->\n\n"
        f"**[Stokowski]** Rework triggered: `{reason}` "
        f"(detected by `{detector}`, PR #{pr_number})."
    )
    post_comment(issue_id, body)


def signal_rework(issue_id, reason, detector, pr_number):
    """Move a ticket to Rework AND arm Stokowski's auto-pickup.

    The single entry point for every Rework transition in this script. Moving
    the Linear state alone is inert — `_handle_rework_pickup` only triggers on
    the `needs-rework` label. Order: label + trigger marker first, state move
    last, so a crash between steps still leaves the orchestrator able to pick
    the ticket up (label present) rather than stranded in Rework with no label.
    """
    _add_needs_rework_label(issue_id)
    _post_rework_trigger(issue_id, reason, detector, pr_number)
    move_state(issue_id, REWORK_STATE_ID)


def _try_auto_merge(pr, ident, summary):
    """Attempt `gh pr merge --squash` on a Gate-Approved auto-merge-ok PR.

    Develop has no branch protection on this repo (verified 2026-05-15),
    so squash-merge works as long as the PR isn't conflicting. Failures
    are logged but do not undo the Gate Approved transition — a human can
    merge manually if auto-merge falls through.
    """
    pr_num = pr.get("number")
    mergeable = pr.get("mergeable") or "UNKNOWN"

    if mergeable == "CONFLICTING":
        # poll_pr_conflicts will signal Rework on its next tick; don't merge a
        # conflicting branch. Leave at Gate Approved for now.
        log(f"    auto-merge skipped: PR #{pr_num} is CONFLICTING")
        return

    try:
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_num), "--repo", REPO,
             "--squash", "--delete-branch", "--match-head-commit",
             pr.get("headRefOid") or ""],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"warning: auto-merge timeout on PR #{pr_num} ({ident})",
              file=sys.stderr)
        return

    if result.returncode == 0:
        print(f"auto-merged: {ident} → PR #{pr_num}")
        summary["approved"].append(f"{ident} (auto-merged → PR #{pr_num})")
    else:
        # Common non-fatal causes: PR became conflicting between fetch and
        # merge; HEAD advanced (race with new agent push). Log + leave at
        # Gate Approved — human can merge or the next tick retries.
        err = (result.stderr or "").strip().replace("\n", " ")[:200]
        print(f"warning: auto-merge failed for {ident} PR #{pr_num} "
              f"(rc={result.returncode}): {err}", file=sys.stderr)


def _count_retrigger_markers(issue_id, pr_num):
    """Return how many CI re-trigger attempts have been logged for *pr_num*.

    Each successful re-trigger posts a marker comment of the form
    ``<!-- poll-ci-status -->:retrigger-ci-<pr_num>-<n>``. Counting those
    markers gives the attempt number without any external state — the Linear
    comment thread IS the durable counter, consistent with how every other
    idempotency guard in this script works (rescue-, ci-fail-, review-fail-).

    Note: the Linear query is capped at ``first: 100`` comments. On tickets
    with more than 100 comments the retrigger markers may be outside the
    window, causing the cap (``CI_RETRIGGER_MAX_ATTEMPTS``) to be silently
    bypassed. In practice this only affects very busy tickets; the 60-min
    Rework gate provides a backstop.
    """
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) { comments(first: 100) { nodes { body } } }
        }
        """,
        {"id": issue_id},
    )
    prefix = f"{COMMENT_MARKER}:retrigger-ci-{pr_num}-"
    return sum(1 for c in data["issue"]["comments"]["nodes"] if prefix in c["body"])


def _retrigger_ci(pr):
    """Re-fire `ci.yml` for *pr* by pushing an empty commit to its head branch.

    `ci.yml` triggers only on `pull_request: [opened, synchronize]`. The sole
    deterministic, gh-only way to produce a `synchronize` (so the resulting
    check runs attach to the PR's statusCheckRollup) is to advance the PR's
    head ref by one commit. We do that entirely through GitHub's Git Data API
    — no local git, no working-tree mutation — so it is safe to run from the
    systemd-driven poller regardless of what branch the host repo is on:

      1. POST git/commits  — create an empty commit (same tree as HEAD,
                              single parent = current HEAD).
      2. PATCH git/refs/heads/<branch> — fast-forward the PR branch to it.

    The empty commit changes no files (identical tree), so the PR diff is
    unaffected; only a fresh `synchronize` event is emitted. `workflow_dispatch`
    was rejected as an alternative because a manually-dispatched run executes
    against the branch ref, not in PR context, so its checks would NOT attach
    to the PR — leaving the "zero check runs" condition unchanged. A PR
    close+reopen was rejected because `ci.yml` does not list `reopened`.

    Requires only the `repo` token scope (verified: the orchestration token
    has `repo` + `workflow`). Returns the new commit SHA on success, or
    ``None`` on any gh/API failure (caller falls back to the Rework path).
    """
    branch = pr.get("headRefName") or ""
    parent = pr.get("headRefOid") or ""
    if not branch or not parent:
        log(f"    re-trigger skipped: PR #{pr.get('number')} missing branch/headRefOid")
        return None

    def _api(args):
        return subprocess.run(
            ["gh", "api", "--repo", REPO, *args],
            check=True, capture_output=True, text=True, timeout=30,
        )

    try:
        tree = json.loads(
            _api([f"repos/{REPO}/git/commits/{parent}"]).stdout
        )["tree"]["sha"]
        new_sha = json.loads(
            _api([
                f"repos/{REPO}/git/commits",
                "-f", "message=chore(ci): re-trigger CI (poller; no checks fired)\n\n"
                      "Empty commit pushed by poll_ci_status.py because the PR was "
                      "OPEN + mergeable with zero check runs — a synchronize event "
                      "never reached ci.yml. No file changes; diff is unaffected.",
                "-f", f"tree={tree}",
                "-F", "parents[]=" + parent,
            ]).stdout
        )["sha"]
        _api([
            "--method", "PATCH",
            f"repos/{REPO}/git/refs/heads/{branch}",
            "-f", f"sha={new_sha}",
            # Omit `force` — the API defaults to false (no force-push), which is
            # correct for a clean fast-forward. Leaving it out avoids the common
            # reviewer confusion between `-f` (raw string) and `-F` (typed bool).
        ])
    except subprocess.TimeoutExpired:
        # Printed to stdout so it appears in the poller's systemd journal
        # (stderr is often swallowed by the rate-limit wrapper). A timeout
        # here points at a GitHub API issue, not a stuck runner.
        print(f"[retrigger] FAIL PR #{pr.get('number')}: gh api timed out (>30s) — possible GitHub API issue")
        return None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip().replace("\n", " ")[:200]
        # Printed to stdout for the same journal visibility reason. A non-zero
        # exit from gh api usually means a 422 (branch protection, wrong token
        # scope, or the ref no longer exists) — distinct from "CI is just slow".
        print(
            f"[retrigger] FAIL PR #{pr.get('number')} "
            f"(gh api rc={e.returncode}): {err} — "
            f"check token scope / branch protection; this is not a stuck runner"
        )
        return None
    except (KeyError, ValueError) as e:
        print(
            f"warning: re-trigger got unexpected API response for "
            f"PR #{pr.get('number')}: {e}",
            file=sys.stderr,
        )
        return None
    return new_sha


def rescue_stale_in_progress(syn_to_pr):
    """Force-transition tickets stuck in 'In Progress' with an open PR but no
    running agent. Catches the case where the agent pushed but failed to call
    `linear:update_issue` AND Stokowski's auto-transition also missed.

    *syn_to_pr* is the SYN-id → pr_data map built from the consolidated fetch.

    Returns the list of ticket identifiers that were rescued.
    """
    running = stokowski_running_identifiers()
    if running is None:
        # Stokowski unreachable — skip rescue this tick.
        return []

    issues = list_in_progress_issues()
    log(f"checking {len(issues)} ticket(s) in 'In Progress' for staleness")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rescued = []

    for issue in issues:
        ident = issue["identifier"]
        if ident in running:
            log(f"  {ident}  agent currently running, skip")
            continue

        # Parse Linear's ISO-8601 updatedAt
        try:
            updated = datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))
        except Exception:
            log(f"  {ident}  bad updatedAt {issue.get('updatedAt')}, skip")
            continue
        age_s = (now - updated).total_seconds()
        if age_s < STALE_IN_PROGRESS_THRESHOLD_S:
            log(f"  {ident}  in In Progress for {int(age_s)}s, below threshold")
            continue

        pr = syn_to_pr.get(ident.upper())
        if not pr:
            # Ticket is genuinely stuck mid-implementation, no PR yet.
            # Don't rescue — the agent was supposed to open a PR before exiting.
            # Leave it alone; surface in verbose log only.
            log(f"  {ident}  stale ({int(age_s)}s) but NO PR — agent failed without pushing")
            continue

        # Stale + no agent + PR exists → agent finished but didn't transition.
        log(f"  {ident}  RESCUE: stale {int(age_s)}s, no agent running, PR #{pr['number']} exists")
        if not has_marker_comment(issue["id"], f"rescue-{pr['number']}"):
            post_comment(
                issue["id"],
                f"{COMMENT_MARKER}:rescue-{pr['number']}\n"
                f"🛟 **Auto-rescue:** ticket was in `In Progress` for {int(age_s // 60)} min "
                f"with [PR #{pr['number']}]({pr['url']}) open and no agent running. "
                f"Force-transitioning to `Awaiting CI` so the CI poller picks it up. "
                f"Implementation summary may be missing — review the PR diff directly."
            )
        move_state(issue["id"], AWAITING_CI_STATE_ID)
        rescued.append(ident)

    return rescued


def _pr_has_zero_real_checks(pr):
    """True when *pr* has NO real (non-cosmetic) check runs at all.

    This is the precise "CI never fired" signature: the PR's
    statusCheckRollup is empty (``gh pr view --json statusCheckRollup`` →
    ``[]``), OR every context present is one of the cosmetic orphans
    (``Analyze (``, ``claude-review``) that ``check_ci_status`` already
    strips. It means no `synchronize` event ever produced a CI run for
    this commit — distinct from "checks exist but are stuck IN_PROGRESS",
    which is a runner stall that re-triggering can't fix.
    """
    checks = pr.get("checks") or []
    real = [c for c in checks
            if not (c.get("name") or "").startswith(IGNORED_CHECK_PREFIXES)]
    return len(real) == 0


def rescue_stale_awaiting_ci(issues, syn_to_pr, required_contexts=None):
    """Drive Awaiting-CI tickets whose CI never settled.

    Two failure modes, handled differently (item B):

    1. **CI never fired** — PR is OPEN + not CONFLICTING but has ZERO real
       check runs. No `synchronize` reached `ci.yml` (lost push after a
       server-side rebase, runner was disk-full and never claimed the job,
       …). The code is fine; only the trigger was lost. After a short grace
       (``CI_NEVER_FIRED_GRACE_S``) we **re-trigger CI** by pushing an empty
       commit to the PR branch (see ``_retrigger_ci``), capped at
       ``CI_RETRIGGER_MAX_ATTEMPTS`` per PR (counted via marker comments).
       Re-triggered tickets stay in Awaiting CI — the next tick sees the
       new run. Only after the cap is exhausted does it fall through to the
       Rework path below.

    2. **CI fired but never settled** — checks exist yet stay `pending`
       past ``STALE_AWAITING_CI_THRESHOLD_S`` (genuine runner stall,
       concurrency-cancel storm). Re-triggering wouldn't help if the runner
       physically can't pick up jobs, so this keeps the original behaviour:
       bounce to Rework so the agent re-pushes fresh.

    Idempotency: the Rework marker includes the PR's 12-char HEAD SHA prefix,
    so a human manually moving the ticket back to Awaiting CI after a re-push
    (new SHA) won't be blocked by the old marker. Re-trigger markers are
    numbered per PR and serve as the durable attempt counter.

    Returns the list of ticket identifiers rescued (moved to Rework).
    Re-triggered tickets are NOT in that list — they stay in Awaiting CI.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rescued = []

    log(f"checking {len(issues)} ticket(s) in 'Awaiting CI' for stale CI")
    for issue in issues:
        ident = issue["identifier"]

        try:
            updated = datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))
        except Exception:
            log(f"  {ident}  bad updatedAt {issue.get('updatedAt')!r}, skip stale CI check")
            continue

        age_s = (now - updated).total_seconds()

        pr = syn_to_pr.get(ident.upper())

        # --- Mode 1: CI never fired -> re-trigger (capped), don't Rework ---
        # Evaluated on its own SHORTER grace gate, *before* the 60-min Rework
        # gate below, so a clean PR whose synchronize was lost is rescued
        # well before the false-bounce window the old code had.
        if (
            pr is not None
            and age_s >= CI_NEVER_FIRED_GRACE_S
            and (pr.get("state") or "OPEN") == "OPEN"
            and (pr.get("mergeable") or "") != "CONFLICTING"
            and _pr_has_zero_real_checks(pr)
        ):
            pr_num = pr["number"]
            attempts = _count_retrigger_markers(issue["id"], pr_num)
            if attempts < CI_RETRIGGER_MAX_ATTEMPTS:
                attempt_no = attempts + 1
                log(f"  {ident}  CI never fired on PR #{pr_num} "
                    f"(zero checks, {int(age_s // 60)} min) — "
                    f"re-trigger attempt {attempt_no}/{CI_RETRIGGER_MAX_ATTEMPTS}")
                new_sha = _retrigger_ci(pr)
                if new_sha is not None:
                    post_comment(
                        issue["id"],
                        f"{COMMENT_MARKER}:retrigger-ci-{pr_num}-{attempt_no}\n"
                        f"🔁 **CI re-triggered** "
                        f"(attempt {attempt_no}/{CI_RETRIGGER_MAX_ATTEMPTS}) on "
                        f"[PR #{pr_num}]({pr['url']}).\n\n"
                        f"The PR was OPEN + mergeable with **zero check runs** for "
                        f"~{int(age_s // 60)} min — no `synchronize` reached "
                        f"`ci.yml` (lost push after a server-side rebase, or a "
                        f"runner that couldn't claim the job). Pushed an empty "
                        f"commit `{new_sha[:12]}` to fire a fresh CI run; the diff "
                        f"is unchanged. Staying in `Awaiting CI`. If CI still "
                        f"doesn't fire after {CI_RETRIGGER_MAX_ATTEMPTS} attempts "
                        f"this ticket falls back to `Rework`.",
                    )
                # Whether the re-trigger succeeded or failed, do NOT also
                # bounce to Rework this tick — give the new run (or the next
                # retry) a chance. The 60-min Rework gate is skipped here.
                continue
            else:
                # Cap exhausted — re-trigger isn't working (broken branch,
                # workflow permanently disabled, etc.). Fall through to the
                # Rework path so a human / fresh agent takes over. Only do
                # so once the 60-min staleness gate has also elapsed, to
                # avoid Rework-bouncing prematurely if attempts piled up fast.
                if age_s < STALE_AWAITING_CI_THRESHOLD_S:
                    log(f"  {ident}  re-trigger cap hit but only {int(age_s//60)} "
                        f"min old — waiting for {STALE_AWAITING_CI_THRESHOLD_S//60} "
                        f"min Rework gate")
                    continue
                log(f"  {ident}  re-trigger cap ({CI_RETRIGGER_MAX_ATTEMPTS}) "
                    f"exhausted on PR #{pr_num} — falling back to Rework")
                # falls through to the stale-Rework block below

        # --- Mode 2: CI fired but never settled -> existing Rework path ---
        if age_s < STALE_AWAITING_CI_THRESHOLD_S:
            log(f"  {ident}  awaiting CI for {int(age_s)}s, below threshold")
            continue

        if not pr:
            log(f"  {ident}  stale awaiting CI ({int(age_s // 60)} min) but no open symphony PR")
            continue

        ci_state, _ = check_ci_status(pr.get("checks") or [], required_contexts)
        if ci_state != "pending":
            # CI has a definitive result — main loop will handle the transition.
            log(f"  {ident}  stale but ci={ci_state} — main loop will handle")
            continue

        # CI is still pending after the threshold — rescue it.
        head_sha = pr.get("headRefOid") or ""
        sha_tag = head_sha[:12] if head_sha else "unknown"
        marker_suffix = f"stale-ci-{pr['number']}-{sha_tag}"
        log(f"  {ident}  RESCUE stale awaiting CI: {int(age_s // 60)} min, PR #{pr['number']}, sha={sha_tag}")

        if not has_marker_comment(issue["id"], marker_suffix):
            post_comment(
                issue["id"],
                f"{COMMENT_MARKER}:{marker_suffix}\n"
                f"⏱ **Awaiting CI timeout** on [PR #{pr['number']}]({pr['url']}).\n\n"
                f"CI checks have been `pending` for **{int(age_s // 60)} minutes** "
                f"(commit `{sha_tag}`). Likely causes: runner outage, workflow not triggered "
                f"on this commit, or all runs cancelled by a concurrency group.\n\n"
                f"Moving to `Rework` so the agent re-pushes — a new commit triggers a fresh "
                f"CI run. If CI is just slow (long queue), move the ticket back to "
                f"`Awaiting CI` manually; the watchdog will not re-comment for the same commit.",
            )
        signal_rework(issue["id"], "ci_stuck", "poll-ci-status", pr["number"])
        rescued.append(ident)

    return rescued


def main():
    # One GraphQL call per tick — replaces the previous ~2N+2 calls.
    # On transient failure (rate limit, network), this returns None and we
    # skip the entire tick gracefully instead of crashing mid-loop.
    pr_data = _pr_helpers.fetch_symphony_pr_full(REPO)
    if pr_data is None:
        print(
            "warning: could not fetch symphony PRs this tick (gh failure); "
            "will retry on next invocation",
            file=sys.stderr,
        )
        return

    open_prs: dict[int, dict] = pr_data["open"]
    closed_prs: list[dict] = pr_data["closed"]
    syn_to_pr = build_syn_index(open_prs)
    log(f"fetched {len(open_prs)} open + {len(closed_prs)} closed symphony PRs; "
        f"{len(syn_to_pr)} unique SYN-ids")

    # Fetch branch-protection required status checks once per tick.
    # Returns None when branch protection is not configured or the fetch fails —
    # in that case check_ci_status falls back to blocking on all failures.
    required_contexts = _pr_helpers.get_required_status_checks(REPO, "develop")
    if required_contexts is not None:
        log(f"required status check contexts ({len(required_contexts)}): {sorted(required_contexts)}")
    else:
        log("required status check contexts: unavailable — gating on all failures")

    rescued = rescue_stale_in_progress(syn_to_pr)
    if rescued:
        print(f"rescued (In Progress → Awaiting CI): {', '.join(rescued)}")

    issues = list_awaiting_ci_issues()
    log(f"found {len(issues)} ticket(s) in 'Awaiting CI'")

    # Rescue stale Awaiting CI tickets before the main loop so issues already
    # moved to Rework are not processed again in the same tick.
    stale_rescued = rescue_stale_awaiting_ci(issues, syn_to_pr, required_contexts)
    if stale_rescued:
        print(f"rescued (Awaiting CI → Rework, CI never settled): {', '.join(stale_rescued)}")
    rescued_set = set(stale_rescued)

    summary = {"approved": [], "reworked": [], "pending": [], "no_pr": []}

    for issue in issues:
        ident = issue["identifier"]
        if ident in rescued_set:
            continue  # already moved to Rework above
        labels = {l["name"] for l in issue["labels"]["nodes"]}
        skip_reviewer = SKIP_REVIEW_LABEL in labels
        log(f"  {ident}  skip_reviewer={skip_reviewer}")

        pr = syn_to_pr.get(ident.upper())
        if not pr:
            # No open PR. Check whether the agent's PR was already closed or
            # merged — if so, the ticket is a ghost straggler and we should
            # transition it to the matching terminal state instead of leaving
            # it stuck in Awaiting CI forever.
            closed = find_closed_pr(ident, closed_prs)
            if closed is None:
                summary["no_pr"].append(ident)
                log(f"    ! no open or closed symphony PR found")
                continue

            pr_num = closed["number"]
            pr_url = closed.get("url", "")
            pr_state = closed.get("state", "CLOSED")
            marker = f"closed-pr-{pr_num}-{pr_state.lower()}"
            if pr_state == "MERGED":
                if not has_marker_comment(issue["id"], marker):
                    post_comment(
                        issue["id"],
                        f"{COMMENT_MARKER}:{marker}\n"
                        f"✅ **PR merged** — [PR #{pr_num}]({pr_url}) was merged but this ticket "
                        f"was still in Awaiting CI. Moving to Done.",
                    )
                move_state(issue["id"], DONE_STATE_ID)
                summary["approved"].append(f"{ident} (closed PR #{pr_num} was merged → Done)")
            else:  # CLOSED unmerged
                if not has_marker_comment(issue["id"], marker):
                    post_comment(
                        issue["id"],
                        f"{COMMENT_MARKER}:{marker}\n"
                        f"🚫 **PR closed unmerged** — [PR #{pr_num}]({pr_url}) was closed without "
                        f"merging but this ticket was still in Awaiting CI. Moving to Canceled. "
                        f"If the work should be re-attempted, move the ticket back to Todo.",
                    )
                move_state(issue["id"], CANCELED_STATE_ID)
                summary["reworked"].append(f"{ident} (closed PR #{pr_num} unmerged → Canceled)")
            continue

        # `[skip-review]` in PR title is a second way to opt out.
        if "[skip-review]" in pr.get("title", "").lower().replace(" ", ""):
            skip_reviewer = True

        checks = pr.get("checks") or []
        ci_state, ci_detail = check_ci_status(checks, required_contexts)
        log(f"    PR #{pr['number']}  ci={ci_state}")

        if ci_state == "fail":
            failed_names = [c.get("name", "?") for c in ci_detail]
            failed_links = [c.get("link") for c in ci_detail if c.get("link")]
            if not has_marker_comment(issue["id"], f"ci-fail-{pr['number']}"):
                links_md = "\n".join(f"  - [{n}]({l})" for n, l in zip(failed_names, failed_links) if l) or ""
                post_comment(
                    issue["id"],
                    f"{COMMENT_MARKER}:ci-fail-{pr['number']}\n"
                    f"❌ **CI failed** on [PR #{pr['number']}]({pr['url']}). Failed checks:\n"
                    + "\n".join(f"  - `{n}`" for n in failed_names)
                    + (f"\n\nLogs:\n{links_md}" if links_md else "")
                )
            signal_rework(issue["id"], "ci_failed", "poll-ci-status", pr["number"])
            summary["reworked"].append(f"{ident} (CI fail)")
            continue

        if ci_state == "pending":
            summary["pending"].append(f"{ident} (CI)")
            continue

        # CI passed — now check reviewer (or skip)
        if skip_reviewer:
            move_state(issue["id"], GATE_APPROVED_STATE_ID)
            summary["approved"].append(f"{ident} (CI green, reviewer skipped)")
            continue

        # Use the HEAD commit's committedDate (from the consolidated fetch) to
        # detect stale review verdicts. A verdict comment older than the latest
        # push means the reviewer has not yet seen current HEAD -- ignore it.
        head_sha = pr.get("headRefOid") or ""
        committed_at = pr.get("headCommittedDate")
        log(f"    HEAD sha={head_sha[:8] if head_sha else '?'}  committed_at={committed_at}")

        comments = pr.get("comments") or []
        verdict, verdict_body = reviewer_verdict(comments, after=committed_at)
        log(f"    reviewer verdict: {verdict}")

        if verdict == "request_changes":
            sha_tag = head_sha[:12] if head_sha else "unknown"
            review_marker = f"review-fail-{pr['number']}-{sha_tag}"
            if not has_marker_comment(issue["id"], review_marker):
                post_comment(
                    issue["id"],
                    f"{COMMENT_MARKER}:{review_marker}\n"
                    f"⚠️ **Reviewer requested changes** on [PR #{pr['number']}]({pr['url']}). "
                    f"Read the latest review comment for details and address each 🔴 critical issue.",
                )
                signal_rework(issue["id"], "review_changes_requested",
                               "poll-ci-status", pr["number"])
                summary["reworked"].append(f"{ident} (reviewer)")
            else:
                # Already dispatched Rework for this commit's review verdict.
                # Wait for the reviewer to re-review the post-fix push.
                log(f"    rework already dispatched for sha {sha_tag} -- waiting for re-review")
                summary["pending"].append(f"{ident} (reviewer re-review pending for {sha_tag})")
            continue

        if verdict == "approve":
            # Even on APPROVE, refuse to advance if the reviewer flagged unresolved
            # 🟡 important or 🔵 minor issues. The flying-colours bar is "all
            # suggestions resolved" — bounce back to Rework so the agent's next
            # dispatch addresses them under the new prompt's step-2 + step-8.
            counts = parse_issue_counts(verdict_body or "")
            unresolved = counts['important'] + counts['minor']
            log(f"    issue counts: {counts}")
            if unresolved > 0:
                sha_tag = head_sha[:12] if head_sha else "unknown"
                touchup_marker = f"approve-touchup-{pr['number']}-{sha_tag}"
                if not has_marker_comment(issue["id"], touchup_marker):
                    parts = []
                    if counts['important'] > 0:
                        parts.append(f"{counts['important']} 🟡 important")
                    if counts['minor'] > 0:
                        parts.append(f"{counts['minor']} 🔵 minor")
                    issues_summary = " + ".join(parts)
                    post_comment(
                        issue["id"],
                        f"{COMMENT_MARKER}:{touchup_marker}\n"
                        f"⚠️ **Reviewer approved with unresolved suggestions** on "
                        f"[PR #{pr['number']}]({pr['url']}): {issues_summary}.\n\n"
                        f"Bouncing to Rework to address them under the flying-colours bar — "
                        f"ship-ready means 0 important and 0 minor outstanding (or filed as "
                        f"follow-up tickets per the implement prompt).",
                    )
                    signal_rework(issue["id"], "approve_unresolved_suggestions",
                                   "poll-ci-status", pr["number"])
                    summary["reworked"].append(f"{ident} (APPROVE + {issues_summary})")
                else:
                    # Already bounced for this sha; the agent is presumably mid-rework.
                    # Don't re-bounce — wait for next push.
                    log(f"    touch-up already dispatched for sha {sha_tag} -- waiting for re-push")
                    summary["pending"].append(f"{ident} (touch-up rework pending for {sha_tag})")
                continue

            # APPROVE with 0 critical, 0 important, 0 minor — true done.
            move_state(issue["id"], GATE_APPROVED_STATE_ID)
            summary["approved"].append(f"{ident} (CI green, reviewer approved, 0/0/0)")

            # Auto-merge if the PR is opted in via the auto-merge-ok label.
            # Develop has no branch protection on this repo (verified
            # 2026-05-15), so `gh pr merge --squash` works unconditionally
            # for label-gated PRs. Merging here keeps the orchestrator + Linear
            # in sync naturally: the next tick sees a closed PR via the
            # ghost-resolution path and transitions the ticket to Done.
            pr_labels = pr.get("labels") or []
            if AUTO_MERGE_LABEL_NAME in pr_labels:
                _try_auto_merge(pr, ident, summary)
            continue

        # No verdict yet — reviewer hasn't run or is still working
        summary["pending"].append(f"{ident} (reviewer)")

    if summary["approved"]:
        print(f"approved: {', '.join(summary['approved'])}")
    if summary["reworked"]:
        print(f"reworked: {', '.join(summary['reworked'])}")
    if VERBOSE:
        if summary["pending"]:
            print(f"still pending: {', '.join(summary['pending'])}", file=sys.stderr)
        if summary["no_pr"]:
            print(f"no PR found: {', '.join(summary['no_pr'])}", file=sys.stderr)


def cli():
    """Console script entry point — wraps main() with rate-limit guard."""
    try:
        from stokowski.pollers._rate_limit_guard import run_with_rate_limit_guard
    except ImportError:
        from _rate_limit_guard import run_with_rate_limit_guard
    run_with_rate_limit_guard(main)


if __name__ == "__main__":
    cli()
