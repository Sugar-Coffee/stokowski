"""Tests for persistent state."""

import json
from pathlib import Path

from stokowski.state import (
    PersistedState,
    load_sessions,
    load_state,
    save_session,
    save_state,
    sessions_file_path,
    state_file_path,
)


class TestStateFilePath:
    def test_derives_from_workflow_name(self, tmp_path):
        workflow = tmp_path / "my-workflow.yaml"
        workflow.touch()
        path = state_file_path(workflow)
        assert path.parent == tmp_path
        assert ".stokowski_state_my-workflow.json" == path.name

    def test_different_workflows_different_paths(self, tmp_path):
        w1 = tmp_path / "a.yaml"
        w2 = tmp_path / "b.yaml"
        w1.touch()
        w2.touch()
        assert state_file_path(w1) != state_file_path(w2)


class TestSaveAndLoad:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        state = PersistedState(
            last_schedule_fire_iso="2026-04-10T09:00:00+00:00",
            total_input_tokens=1000,
            total_output_tokens=500,
            total_tokens=1500,
            total_seconds_running=120.5,
        )
        save_state(path, state)
        loaded = load_state(path)
        assert loaded.last_schedule_fire_iso == "2026-04-10T09:00:00+00:00"
        assert loaded.total_input_tokens == 1000
        assert loaded.total_output_tokens == 500
        assert loaded.total_tokens == 1500
        assert loaded.total_seconds_running == 120.5

    def test_roundtrip_with_issues(self, tmp_path):
        path = tmp_path / "state.json"
        state = PersistedState(
            issues={
                "issue-1": {
                    "issue_id": "issue-1",
                    "identifier": "DEV-1",
                    "current_state": "implement",
                    "run": 2,
                    "session_id": "sess-abc",
                    "workspace_path": "/tmp/ws/DEV-1",
                },
                "issue-2": {
                    "issue_id": "issue-2",
                    "identifier": "DEV-2",
                    "current_state": "review",
                    "run": 1,
                    "session_id": None,
                    "workspace_path": "",
                },
            },
        )
        save_state(path, state)
        loaded = load_state(path)
        assert len(loaded.issues) == 2
        assert loaded.issues["issue-1"]["current_state"] == "implement"
        assert loaded.issues["issue-1"]["session_id"] == "sess-abc"
        assert loaded.issues["issue-2"]["current_state"] == "review"

    def test_load_missing_file_returns_defaults(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        state = load_state(path)
        assert state.total_tokens == 0
        assert state.last_schedule_fire_iso is None

    def test_load_corrupt_file_returns_defaults(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json{{{")
        state = load_state(path)
        assert state.total_tokens == 0

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "state.json"
        save_state(path, PersistedState(total_tokens=42))
        loaded = load_state(path)
        assert loaded.total_tokens == 42

    def test_atomic_write_produces_valid_json(self, tmp_path):
        path = tmp_path / "state.json"
        save_state(path, PersistedState(total_tokens=99))
        data = json.loads(path.read_text())
        assert data["total_tokens"] == 99


class TestSessions:
    def test_save_and_load(self, tmp_path):
        save_session(tmp_path, "PH-123", "sid-2", ["sid-1", "sid-2"])
        sessions = load_sessions(sessions_file_path(tmp_path))
        assert "PH-123" in sessions
        assert sessions["PH-123"]["session_id"] == "sid-2"
        assert sessions["PH-123"]["session_ids"] == ["sid-1", "sid-2"]
        assert "updated_at" in sessions["PH-123"]

    def test_merges_new_sessions(self, tmp_path):
        save_session(tmp_path, "PH-123", "sid-1", ["sid-1"])
        save_session(tmp_path, "PH-123", "sid-2", ["sid-2"])
        sessions = load_sessions(sessions_file_path(tmp_path))
        assert sessions["PH-123"]["session_id"] == "sid-2"
        assert sessions["PH-123"]["session_ids"] == ["sid-1", "sid-2"]

    def test_multiple_issues(self, tmp_path):
        save_session(tmp_path, "PH-1", "a", ["a"])
        save_session(tmp_path, "PH-2", "b", ["b"])
        sessions = load_sessions(sessions_file_path(tmp_path))
        assert "PH-1" in sessions
        assert "PH-2" in sessions

    def test_no_duplicates(self, tmp_path):
        save_session(tmp_path, "PH-1", "sid-1", ["sid-1"])
        save_session(tmp_path, "PH-1", "sid-1", ["sid-1"])
        sessions = load_sessions(sessions_file_path(tmp_path))
        assert sessions["PH-1"]["session_ids"] == ["sid-1"]

    def test_empty_session_ids_skipped(self, tmp_path):
        save_session(tmp_path, "PH-1", None, [])
        path = sessions_file_path(tmp_path)
        assert not path.exists()

    def test_load_missing_file(self, tmp_path):
        sessions = load_sessions(tmp_path / "nonexistent.json")
        assert sessions == {}

    def test_load_corrupt_file(self, tmp_path):
        path = sessions_file_path(tmp_path)
        path.write_text("not json{{{")
        sessions = load_sessions(path)
        assert sessions == {}

    def test_sessions_file_path(self, tmp_path):
        path = sessions_file_path(tmp_path)
        assert path == tmp_path / "sessions.json"
