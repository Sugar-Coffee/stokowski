"""Tests for runner event processing."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stokowski.models import RunAttempt
from stokowski.runner import (
    _capture_stderr,
    _extract_gemini_retry_delay_ms,
    _format_gemini_process_error,
    _process_event,
    _process_gemini_event,
    _summarize_stderr,
)


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


class TestCaptureStderr:
    @pytest.mark.asyncio
    async def test_short_stderr_returned_as_is(self):
        proc = MagicMock()
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(return_value=b"short error")
        result = await _capture_stderr(proc, "DEV-1")
        assert result == "short error"

    @pytest.mark.asyncio
    async def test_long_stderr_written_to_file(self, tmp_path):
        long_err = "X" * 1000
        proc = MagicMock()
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(return_value=long_err.encode())
        result = await _capture_stderr(proc, "DEV-1", workspace_path=tmp_path)
        assert result.startswith("..." + "X" * 500)
        assert "full output:" in result
        # Verify file was written
        log_dir = tmp_path / ".stokowski" / "logs"
        log_files = list(log_dir.glob("DEV-1-*.stderr"))
        assert len(log_files) == 1
        assert log_files[0].read_text() == long_err

    @pytest.mark.asyncio
    async def test_no_stderr_returns_empty(self):
        proc = MagicMock()
        proc.stderr = None
        result = await _capture_stderr(proc, "DEV-1")
        assert result == ""


class TestGeminiCliErrorFormatting:
    def test_extract_gemini_retry_delay_ms(self):
        stderr_output = "retryDelayMs: 68033648.177763,\nreason: 'QUOTA_EXHAUSTED'\n"
        assert _extract_gemini_retry_delay_ms(stderr_output) == 68033648

    def test_format_gemini_process_error_uses_api_details(self, tmp_path):
        stderr_output = "\n".join(
            [
                "YOLO mode is enabled. All tool calls will be automatically approved.",
                "Skill conflict detected: " + ("humanizer ..." * 80),
                "cause: {",
                "  code: 429,",
                "  message: 'You have exhausted your capacity on this model. Your quota will reset after 18h53m53s.',",
                "}",
                "retryDelayMs: 68033648.177763,",
                "reason: 'QUOTA_EXHAUSTED'",
            ]
        )
        stderr_summary = _summarize_stderr(stderr_output, "DEV-1", tmp_path)

        result = _format_gemini_process_error(1, stderr_output, stderr_summary)

        assert result.startswith(
            "Gemini API error QUOTA_EXHAUSTED (429): "
            "You have exhausted your capacity on this model."
        )
        assert "Retry after 68033648ms." in result
        assert "full output:" in result
        assert "YOLO mode is enabled" not in result
