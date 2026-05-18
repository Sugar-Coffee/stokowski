"""Tests for poll_ci_status.py — focused on _retrigger_ci and reviewer_verdict.

These tests were added as part of SYN-1303 to prevent regressions in the
two areas that caused Mode-1 CI re-trigger 422s and false-positive reworks.
"""
from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

# Set required env var before importing the module (it exits early if missing).
os.environ.setdefault("LINEAR_API_KEY", "test-key-for-unit-tests")
os.environ.setdefault("REPO", "TestOrg/test-repo")

import stokowski.pollers.poll_ci_status as pcs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed(stdout: str) -> subprocess.CompletedProcess:
    """Return a mock CompletedProcess with the given stdout."""
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.returncode = 0
    r.stderr = ""
    return r


FAKE_PR = {
    "number": 99,
    "headRefName": "feature/test-branch",
    "headRefOid": "aabbccdd1122334455667788",
    "url": "https://github.com/TestOrg/test-repo/pull/99",
}

FAKE_TREE_SHA = "tree1234567890abcdef"
FAKE_NEW_SHA = "newsha1234567890abcdef"

COMMIT_RESPONSE = json.dumps({"tree": {"sha": FAKE_TREE_SHA}})
NEW_COMMIT_RESPONSE = json.dumps({"sha": FAKE_NEW_SHA})
PATCH_RESPONSE = json.dumps({"ref": "refs/heads/feature/test-branch", "object": {"sha": FAKE_NEW_SHA}})


# ---------------------------------------------------------------------------
# _retrigger_ci — success path
# ---------------------------------------------------------------------------

class TestRetriggerCiSuccess:
    def test_returns_new_sha_on_success(self):
        """_retrigger_ci returns the new commit SHA when all gh api calls succeed."""
        with patch.object(subprocess, "run", side_effect=[
            _completed(COMMIT_RESPONSE),
            _completed(NEW_COMMIT_RESPONSE),
            _completed(PATCH_RESPONSE),
        ]):
            result = pcs._retrigger_ci(FAKE_PR)

        assert result == FAKE_NEW_SHA

    def test_patch_call_omits_force_field(self):
        """The PATCH /git/refs call must not include a `force` parameter.

        GitHub's endpoint defaults force to false (safe fast-forward). Passing
        force=<any-string> returns 422 Unprocessable Entity because the API
        expects a boolean, not a string. Omitting it entirely avoids the
        flag-type ambiguity between `-f` (raw string) and `-F` (typed bool).
        """
        with patch.object(subprocess, "run", side_effect=[
            _completed(COMMIT_RESPONSE),
            _completed(NEW_COMMIT_RESPONSE),
            _completed(PATCH_RESPONSE),
        ]) as mock_run:
            pcs._retrigger_ci(FAKE_PR)

        # The third call is the PATCH /git/refs
        patch_call_args = mock_run.call_args_list[2][0][0]  # first positional arg = cmd list
        cmd_str = " ".join(str(a) for a in patch_call_args)
        assert "force" not in cmd_str, (
            f"PATCH call must not include 'force' field; got: {cmd_str}"
        )

    def test_patch_call_includes_sha(self):
        """The PATCH call sends the new commit SHA via -f sha=<value>."""
        with patch.object(subprocess, "run", side_effect=[
            _completed(COMMIT_RESPONSE),
            _completed(NEW_COMMIT_RESPONSE),
            _completed(PATCH_RESPONSE),
        ]) as mock_run:
            pcs._retrigger_ci(FAKE_PR)

        patch_call_args = mock_run.call_args_list[2][0][0]
        cmd_str = " ".join(str(a) for a in patch_call_args)
        assert FAKE_NEW_SHA in cmd_str

    def test_uses_parent_sha_as_commit_parent(self):
        """The POST /git/commits call references the PR's headRefOid as parent."""
        with patch.object(subprocess, "run", side_effect=[
            _completed(COMMIT_RESPONSE),
            _completed(NEW_COMMIT_RESPONSE),
            _completed(PATCH_RESPONSE),
        ]) as mock_run:
            pcs._retrigger_ci(FAKE_PR)

        create_call_args = mock_run.call_args_list[1][0][0]
        cmd_str = " ".join(str(a) for a in create_call_args)
        assert FAKE_PR["headRefOid"] in cmd_str


# ---------------------------------------------------------------------------
# _retrigger_ci — failure paths
# ---------------------------------------------------------------------------

class TestRetriggerCiFailure:
    def test_returns_none_on_called_process_error(self):
        """Returns None when gh api exits with non-zero (e.g. 422 from GitHub)."""
        with patch.object(subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "gh", stderr="422 Unprocessable Entity")):
            result = pcs._retrigger_ci(FAKE_PR)

        assert result is None

    def test_returns_none_on_timeout(self):
        """Returns None when the gh api call times out."""
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            result = pcs._retrigger_ci(FAKE_PR)

        assert result is None

    def test_returns_none_on_missing_branch(self):
        """Returns None early when the PR dict has no headRefName."""
        pr_no_branch = {**FAKE_PR, "headRefName": ""}
        with patch.object(subprocess, "run") as mock_run:
            result = pcs._retrigger_ci(pr_no_branch)

        assert result is None
        mock_run.assert_not_called()

    def test_returns_none_on_missing_parent_sha(self):
        """Returns None early when the PR dict has no headRefOid."""
        pr_no_sha = {**FAKE_PR, "headRefOid": ""}
        with patch.object(subprocess, "run") as mock_run:
            result = pcs._retrigger_ci(pr_no_sha)

        assert result is None
        mock_run.assert_not_called()

    def test_prints_diagnostic_on_api_error(self, capsys):
        """Prints a stdout diagnostic (not just stderr) on gh api failure.

        Distinct stdout output makes the failure distinguishable in the systemd
        journal from a runner that is merely slow (which produces no output).
        """
        with patch.object(subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "gh", stderr="422 Unprocessable")):
            pcs._retrigger_ci(FAKE_PR)

        captured = capsys.readouterr()
        assert "[retrigger] FAIL" in captured.out
        assert "gh api rc=1" in captured.out


# ---------------------------------------------------------------------------
# reviewer_verdict — author filtering
# ---------------------------------------------------------------------------

class TestReviewerVerdictAuthorFilter:
    """reviewer_verdict must only accept REVIEW_VERDICT lines from accounts
    whose login contains 'claude' (case-insensitive)."""

    def _comment(self, body: str, login: str, created_at: str = "2026-05-18T10:00:00Z") -> dict:
        return {"body": body, "createdAt": created_at, "author": {"login": login}}

    def test_accepts_verdict_from_claude_login(self):
        comments = [self._comment("REVIEW_VERDICT: APPROVE", "claude[bot]")]
        verdict, _ = pcs.reviewer_verdict(comments)
        assert verdict == "approve"

    def test_accepts_verdict_from_claude_code_action_login(self):
        comments = [self._comment("REVIEW_VERDICT: REQUEST_CHANGES", "claude-code-action")]
        verdict, _ = pcs.reviewer_verdict(comments)
        assert verdict == "request_changes"

    def test_ignores_verdict_from_linear_bot(self):
        """The Linear bot's issue-description comment quoting a prior
        REVIEW_VERDICT: REQUEST_CHANGES must NOT be treated as a verdict."""
        body = (
            "<!-- linear-linkback -->\n"
            "The Claude reviewer on **PR #841** posted `REVIEW_VERDICT: REQUEST_CHANGES`"
        )
        comments = [self._comment(body, "linear[bot]")]
        verdict, body_out = pcs.reviewer_verdict(comments)
        assert verdict is None
        assert body_out is None

    def test_ignores_verdict_from_github_actions_bot(self):
        comments = [self._comment("REVIEW_VERDICT: APPROVE", "github-actions[bot]")]
        verdict, _ = pcs.reviewer_verdict(comments)
        assert verdict is None

    def test_ignores_verdict_from_unknown_user(self):
        comments = [self._comment("REVIEW_VERDICT: APPROVE", "random-user")]
        verdict, _ = pcs.reviewer_verdict(comments)
        assert verdict is None

    def test_most_recent_claude_verdict_wins(self):
        """When multiple claude comments exist, the newest one wins."""
        comments = [
            self._comment("REVIEW_VERDICT: REQUEST_CHANGES", "claude[bot]", "2026-05-18T09:00:00Z"),
            self._comment("REVIEW_VERDICT: APPROVE", "claude[bot]", "2026-05-18T11:00:00Z"),
        ]
        verdict, _ = pcs.reviewer_verdict(comments)
        assert verdict == "approve"

    def test_stale_verdict_ignored(self):
        """A verdict comment older than the commit timestamp is ignored."""
        comments = [
            self._comment("REVIEW_VERDICT: REQUEST_CHANGES", "claude[bot]", "2026-05-18T08:00:00Z"),
        ]
        verdict, _ = pcs.reviewer_verdict(comments, after="2026-05-18T09:00:00Z")
        assert verdict is None

    def test_fresh_verdict_not_ignored(self):
        """A verdict comment newer than the commit timestamp is accepted."""
        comments = [
            self._comment("REVIEW_VERDICT: APPROVE", "claude[bot]", "2026-05-18T10:00:00Z"),
        ]
        verdict, _ = pcs.reviewer_verdict(comments, after="2026-05-18T09:00:00Z")
        assert verdict == "approve"

    def test_no_comments_returns_none(self):
        verdict, body = pcs.reviewer_verdict([])
        assert verdict is None
        assert body is None

    def test_none_comments_returns_none(self):
        verdict, body = pcs.reviewer_verdict(None)
        assert verdict is None
        assert body is None

    def test_linear_bot_comment_before_push_not_mistaken_for_verdict(self):
        """Realistic scenario: linear bot posts at PR creation (before commit),
        its body quotes REVIEW_VERDICT from a different PR. The stale check
        alone is NOT sufficient because the bot comment may be posted AFTER
        the commit (linear bot replies to PR opened, which is after the push).
        The author filter is the primary guard."""
        body = (
            "## Source\nThe Claude reviewer on PR #841 posted "
            "`REVIEW_VERDICT: REQUEST_CHANGES` with IMPORTANT + MINOR findings."
        )
        # Comment posted AFTER the commit — stale check would not filter this
        comments = [self._comment(body, "linear[bot]", "2026-05-18T05:12:43Z")]
        verdict, _ = pcs.reviewer_verdict(comments, after="2026-05-18T05:10:49Z")
        assert verdict is None
