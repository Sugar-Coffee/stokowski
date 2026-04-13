"""Tests for runner event processing."""

from stokowski.models import RunAttempt
from stokowski.runner import _process_event, _process_gemini_event


class TestLastMessageNotTruncated:
    def test_full_result_text_preserved(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        long_text = "A" * 500
        event = {
            "type": "result",
            "result": long_text,
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert len(attempt.last_message) == 500

    def test_full_assistant_text_preserved(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        long_text = "B" * 500
        event = {
            "type": "assistant",
            "message": {"content": long_text},
        }
        _process_event(event, attempt, None, "DEV-1")
        assert len(attempt.last_message) == 500

    def test_full_gemini_message_preserved(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        long_text = "C" * 500
        event = {
            "type": "message",
            "role": "assistant",
            "content": long_text,
        }
        _process_gemini_event(event, attempt, None, "DEV-1")
        assert len(attempt.last_message) == 500


class TestProcessEventNeedsReview:
    def test_needs_review_detected(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "result",
            "result": "Analysis complete. STOKOWSKI:NEEDS_REVIEW — please verify assumptions.",
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert attempt.needs_review is True

    def test_no_marker_no_review(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "result",
            "result": "All done, no questions.",
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert attempt.needs_review is False

    def test_blocked_and_needs_review_both_detected(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "result",
            "result": "STOKOWSKI:BLOCKED STOKOWSKI:NEEDS_REVIEW",
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert attempt.status == "blocked"
        assert attempt.needs_review is True

    def test_rework_detected_claude(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "result",
            "result": "Issues found in review. STOKOWSKI:REWORK",
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert attempt.status == "rework"

    def test_blocked_takes_priority_over_rework(self):
        """BLOCKED appears first in the elif chain, so it wins."""
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "result",
            "result": "STOKOWSKI:BLOCKED STOKOWSKI:REWORK",
            "session_id": "sess-1",
        }
        _process_event(event, attempt, None, "DEV-1")
        assert attempt.status == "blocked"


class TestProcessGeminiEventNeedsReview:
    def test_needs_review_detected(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "message",
            "role": "assistant",
            "content": "Done. STOKOWSKI:NEEDS_REVIEW",
        }
        _process_gemini_event(event, attempt, None, "DEV-1")
        assert attempt.needs_review is True

    def test_no_marker_no_review(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "message",
            "role": "assistant",
            "content": "All done.",
        }
        _process_gemini_event(event, attempt, None, "DEV-1")
        assert attempt.needs_review is False

    def test_rework_detected_gemini(self):
        attempt = RunAttempt(issue_id="1", issue_identifier="DEV-1")
        event = {
            "type": "message",
            "role": "assistant",
            "content": "Code has issues. STOKOWSKI:REWORK",
        }
        _process_gemini_event(event, attempt, None, "DEV-1")
        assert attempt.status == "rework"
