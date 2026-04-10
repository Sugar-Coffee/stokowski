"""Tests for workspace utilities (sanitize_key, _extract_issue_number, _remove_worktree)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from stokowski.workspace import _extract_issue_number, _remove_worktree, sanitize_key


class TestSanitizeKey:
    def test_linear_identifier(self):
        assert sanitize_key("DEV-123") == "DEV-123"

    def test_github_issue_number(self):
        assert sanitize_key("#3366") == "_3366"

    def test_preserves_dots_and_underscores(self):
        assert sanitize_key("my_project.v2") == "my_project.v2"

    def test_spaces_replaced(self):
        assert sanitize_key("has spaces") == "has_spaces"

    def test_special_chars_replaced(self):
        assert sanitize_key("feat/add@login!") == "feat_add_login_"

    def test_already_safe(self):
        assert sanitize_key("SAFE-123") == "SAFE-123"

    def test_empty_string(self):
        assert sanitize_key("") == ""

    def test_unicode_replaced(self):
        result = sanitize_key("issue-\u00e9\u00e8")
        assert "_" in result  # non-ASCII chars get replaced

    def test_slash_replaced(self):
        assert sanitize_key("stokowski/DEV-123") == "stokowski_DEV-123"


class TestExtractIssueNumber:
    def test_linear_format(self):
        assert _extract_issue_number("DEV-123") == "123"

    def test_github_format(self):
        assert _extract_issue_number("#3366") == "3366"

    def test_plain_number(self):
        assert _extract_issue_number("42") == "42"

    def test_prefix_letters(self):
        assert _extract_issue_number("PROJ-007") == "007"

    def test_no_numbers_falls_back(self):
        # Falls back to sanitize_key
        result = _extract_issue_number("no-nums")
        assert result == "no-nums"

    def test_multiple_numbers_takes_first(self):
        assert _extract_issue_number("DEV-12-patch-3") == "12"

    def test_complex_identifier(self):
        assert _extract_issue_number("feature/DEV-456") == "456"


class TestRemoveWorktreeProtectedBranches:
    @pytest.mark.asyncio
    async def test_main_branch_not_deleted(self, tmp_path):
        """Ensure git branch -D is never called for main."""
        with patch("stokowski.workspace.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await _remove_worktree(tmp_path, tmp_path / "wt", "main")

            # Should have called worktree remove but NOT branch -D
            calls = [c[0] for c in mock_exec.call_args_list]
            branch_delete_calls = [
                c for c in calls if "branch" in c and "-D" in c
            ]
            assert len(branch_delete_calls) == 0

    @pytest.mark.asyncio
    async def test_master_branch_not_deleted(self, tmp_path):
        with patch("stokowski.workspace.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await _remove_worktree(tmp_path, tmp_path / "wt", "master")

            calls = [c[0] for c in mock_exec.call_args_list]
            branch_delete_calls = [
                c for c in calls if "branch" in c and "-D" in c
            ]
            assert len(branch_delete_calls) == 0

    @pytest.mark.asyncio
    async def test_feature_branch_deleted(self, tmp_path):
        with patch("stokowski.workspace.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await _remove_worktree(tmp_path, tmp_path / "wt", "fix/my-branch")

            calls = [c[0] for c in mock_exec.call_args_list]
            branch_delete_calls = [
                c for c in calls if "branch" in c and "-D" in c
            ]
            assert len(branch_delete_calls) == 1
