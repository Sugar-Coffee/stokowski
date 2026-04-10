"""Tests for workspace utilities (sanitize_key, _extract_issue_number)."""

from stokowski.workspace import _extract_issue_number, sanitize_key


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
