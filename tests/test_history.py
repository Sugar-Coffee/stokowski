"""Tests for run history persistence."""

import json
from pathlib import Path

from stokowski.history import (
    MAX_HISTORY,
    RunRecord,
    append_run,
    history_file_path,
    load_history,
)


class TestRunRecord:
    def test_create_minimal(self):
        r = RunRecord(
            issue_id="id-1",
            identifier="DEV-1",
            title="Test",
            workflow="feature",
            status="succeeded",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:01:00Z",
            duration_seconds=60.0,
            tokens=1000,
        )
        assert r.issue_id == "id-1"
        assert r.stages == []
        assert r.last_message == ""
        assert r.error is None

    def test_create_full(self):
        r = RunRecord(
            issue_id="id-2",
            identifier="DEV-2",
            title="Full record",
            workflow="docs",
            status="failed",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:05:00Z",
            duration_seconds=300.0,
            tokens=5000,
            stages=["implement", "review"],
            last_message="Oops",
            error="Timeout",
        )
        assert r.stages == ["implement", "review"]
        assert r.error == "Timeout"


class TestHistoryFilePath:
    def test_returns_correct_path(self, tmp_path):
        p = history_file_path(tmp_path)
        assert p == tmp_path / "history.json"
        assert p.parent == tmp_path


class TestLoadHistory:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_history(tmp_path / "nonexistent.json") == []

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "history.json"
        p.write_text("{{{invalid")
        assert load_history(p) == []

    def test_non_list_returns_empty(self, tmp_path):
        p = tmp_path / "history.json"
        p.write_text('{"key": "value"}')
        assert load_history(p) == []

    def test_valid_list(self, tmp_path):
        p = tmp_path / "history.json"
        p.write_text('[{"issue_id": "1"}]')
        result = load_history(p)
        assert len(result) == 1
        assert result[0]["issue_id"] == "1"


class TestAppendRun:
    def _make_record(self, identifier: str = "DEV-1") -> RunRecord:
        return RunRecord(
            issue_id=f"id-{identifier}",
            identifier=identifier,
            title="Test",
            workflow="feature",
            status="succeeded",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:01:00Z",
            duration_seconds=60.0,
            tokens=100,
        )

    def test_append_creates_file(self, tmp_path):
        p = tmp_path / "history.json"
        append_run(p, self._make_record())
        assert p.exists()
        data = json.loads(p.read_text())
        assert len(data) == 1
        assert data[0]["identifier"] == "DEV-1"

    def test_append_adds_to_existing(self, tmp_path):
        p = tmp_path / "history.json"
        append_run(p, self._make_record("DEV-1"))
        append_run(p, self._make_record("DEV-2"))
        data = json.loads(p.read_text())
        assert len(data) == 2

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "history.json"
        append_run(p, self._make_record("DEV-10"))
        loaded = load_history(p)
        assert len(loaded) == 1
        assert loaded[0]["identifier"] == "DEV-10"
        assert loaded[0]["tokens"] == 100

    def test_max_entries_trimmed(self, tmp_path):
        p = tmp_path / "history.json"
        for i in range(MAX_HISTORY + 50):
            append_run(p, self._make_record(f"DEV-{i}"))
        data = json.loads(p.read_text())
        assert len(data) == MAX_HISTORY
        # The oldest entries should have been trimmed
        assert data[0]["identifier"] == "DEV-50"

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "history.json"
        append_run(p, self._make_record())
        assert p.exists()
