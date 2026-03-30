"""Tests for runner validation logic."""

import pytest
from datetime import datetime, timezone

from stokowski.runner import validate_agent_output, _finalize_attempt
from stokowski.models import RunAttempt


class TestValidateAgentOutput:
    """Tests for validate_agent_output function."""

    def test_empty_output_returns_error(self):
        """Empty output should fail validation."""
        is_valid, error = validate_agent_output("")
        assert not is_valid
        assert "no output" in error.lower()

    def test_none_output_returns_error(self):
        """None output should fail validation."""
        is_valid, error = validate_agent_output(None)
        assert not is_valid
        assert "no output" in error.lower()

    def test_missing_start_tag(self):
        """Missing opening tag should fail validation."""
        output = "Some response without report tags"
        is_valid, error = validate_agent_output(output)
        assert not is_valid
        assert "MISSING REQUIRED REPORT" in error
        assert "<stokowski:report>" in error

    def test_missing_end_tag(self):
        """Missing closing tag should fail validation."""
        output = "<stokowski:report>Content without closing tag"
        is_valid, error = validate_agent_output(output)
        assert not is_valid
        assert "MISSING CLOSING TAG" in error
        assert "</stokowski:report>" in error

    def test_reversed_tags(self):
        """Closing tag before opening tag should fail validation."""
        output = "</stokowski:report>Content<stokowski:report>"
        is_valid, error = validate_agent_output(output)
        assert not is_valid
        assert "INVALID TAG ORDER" in error

    def test_valid_report(self):
        """Properly formatted report should pass validation."""
        output = """Some response text

<stokowski:report>
## Summary
- Item 1
- Item 2

## Technical Details
Details here

## Files Changed
- `file.py` - description
</stokowski:report>"""
        is_valid, error = validate_agent_output(output)
        assert is_valid
        assert error is None

    def test_report_with_extra_content_after(self):
        """Report with content after closing tag should pass."""
        output = """<stokowski:report>
## Summary
Test
</stokowski:report>

Extra content after"""
        is_valid, error = validate_agent_output(output)
        assert is_valid
        assert error is None

    def test_multiple_reports(self):
        """Multiple report blocks should pass (we check presence, not count)."""
        output = """<stokowski:report>
## Summary
First
</stokowski:report>

<stokowski:report>
## Summary
Second
</stokowski:report>"""
        is_valid, error = validate_agent_output(output)
        assert is_valid
        assert error is None

    def test_empty_report_content(self):
        """Empty report content between tags should pass (structure valid)."""
        output = "<stokowski:report></stokowski:report>"
        is_valid, error = validate_agent_output(output)
        assert is_valid  # We validate structure, not content
        assert error is None


class TestFinalizeAttempt:
    """Tests for _finalize_attempt helper function."""

    @pytest.fixture
    def attempt(self):
        """Create a fresh RunAttempt for testing."""
        return RunAttempt(
            issue_id="test-123",
            issue_identifier="TEST-123",
            status="streaming",
            full_output="<stokowski:report>Content</stokowski:report>",
        )

    def test_successful_exit_with_valid_report(self, attempt):
        """Exit code 0 with valid report should succeed."""
        _finalize_attempt(attempt, returncode=0, stderr_output="", issue_identifier="TEST-123")

        assert attempt.status == "succeeded"
        assert attempt.error is None

    def test_successful_exit_with_invalid_report(self, attempt):
        """Exit code 0 with invalid report should fail."""
        attempt.full_output = "Missing report tags"

        _finalize_attempt(attempt, returncode=0, stderr_output="", issue_identifier="TEST-123")

        assert attempt.status == "failed"
        assert "Report validation failed" in attempt.error
        assert "MISSING REQUIRED REPORT" in attempt.error

    def test_failed_exit_code(self, attempt):
        """Non-zero exit code should fail regardless of report."""
        _finalize_attempt(attempt, returncode=1, stderr_output="Process error", issue_identifier="TEST-123")

        assert attempt.status == "failed"
        assert "Exit code 1" in attempt.error
        assert "Process error" in attempt.error

    def test_already_set_status_not_overwritten(self, attempt):
        """Status already set (e.g., by timeout handler) should not be overwritten."""
        attempt.status = "timed_out"
        attempt.error = "Turn exceeded timeout"

        _finalize_attempt(attempt, returncode=0, stderr_output="", issue_identifier="TEST-123")

        # Status should remain as-is since it's not "streaming"
        assert attempt.status == "timed_out"
        assert attempt.error == "Turn exceeded timeout"

    def test_failed_exit_with_empty_stderr(self, attempt):
        """Failed exit with empty stderr should still work."""
        _finalize_attempt(attempt, returncode=127, stderr_output="", issue_identifier="TEST-123")

        assert attempt.status == "failed"
        assert "Exit code 127" in attempt.error

    def test_negative_exit_code(self, attempt):
        """Negative exit code (e.g., killed by signal) should fail."""
        _finalize_attempt(attempt, returncode=-9, stderr_output="Killed", issue_identifier="TEST-123")

        assert attempt.status == "failed"
        assert "Exit code -9" in attempt.error


class TestIntegrationFlow:
    """Integration tests for validation flow."""

    def test_end_to_end_success_flow(self):
        """Test complete successful validation flow."""
        attempt = RunAttempt(
            issue_id="test-456",
            issue_identifier="TEST-456",
            status="streaming",
            full_output="""Work completed

<stokowski:report>
## Summary
Task done

## Files Changed
- `file.py` - modified
</stokowski:report>""",
        )

        # First validate the output
        is_valid, error = validate_agent_output(attempt.full_output)
        assert is_valid

        # Then finalize
        _finalize_attempt(attempt, returncode=0, stderr_output="", issue_identifier="TEST-456")

        assert attempt.status == "succeeded"
        assert attempt.error is None

    def test_end_to_end_failure_flow(self):
        """Test complete failure flow with missing report."""
        attempt = RunAttempt(
            issue_id="test-789",
            issue_identifier="TEST-789",
            status="streaming",
            full_output="Work completed but forgot report",
        )

        # First validate the output
        is_valid, error = validate_agent_output(attempt.full_output)
        assert not is_valid

        # Then finalize - should detect and fail
        _finalize_attempt(attempt, returncode=0, stderr_output="", issue_identifier="TEST-789")

        assert attempt.status == "failed"
        assert "Report validation failed" in attempt.error
