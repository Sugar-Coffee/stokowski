"""Tests for persistent state."""

import json
from pathlib import Path

from stokowski.state import PersistedState, load_state, save_state, state_file_path


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
