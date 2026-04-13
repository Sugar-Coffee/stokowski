"""Tests for Linear client identifier validation."""

import pytest

from stokowski.linear import _is_linear_id


class TestIsLinearId:
    def test_uuid(self):
        assert _is_linear_id("550e8400-e29b-41d4-a716-446655440000") is True

    def test_team_identifier(self):
        assert _is_linear_id("DEV-123") is True
        assert _is_linear_id("PROJ-1") is True
        assert _is_linear_id("AB-99999") is True

    def test_pr_identifier_rejected(self):
        assert _is_linear_id("pr:3440") is False

    def test_hash_identifier_rejected(self):
        assert _is_linear_id("#3440") is False

    def test_plain_number_rejected(self):
        assert _is_linear_id("3440") is False

    def test_empty_rejected(self):
        assert _is_linear_id("") is False

    def test_schedule_identifier_rejected(self):
        assert _is_linear_id("schedule:daily-sync") is False

    def test_lowercase_team_rejected(self):
        # Linear team keys are uppercase
        assert _is_linear_id("dev-123") is False
