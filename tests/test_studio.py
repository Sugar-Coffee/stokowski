"""Tests for config editing.

Two properties carry the weight:

1. Comments survive an edit. They are the documentation in these files, and a
   naive load-and-dump destroys them.
2. An invalid config is never written. The orchestrator re-parses config on
   every poll tick, so a bad write is a live failure, not a deferred one.

Everything else is a convenience. These two are the reason it is safe to point
a UI at a config file at all.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stokowski.studio import Studio, StudioError

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def workflow(tmp_path):
    """A real copy of the shipped example, prompts and all."""
    shutil.copy(REPO / "workflow.example.yaml", tmp_path / "workflow.yaml")
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")
    return tmp_path / "workflow.yaml"


@pytest.fixture
def studio(workflow):
    return Studio(workflow)


# ── Comments are the documentation ───────────────────────────────────────────


def test_an_edit_preserves_every_comment(studio, workflow):
    before = workflow.read_text()
    assert before.count("#") > 100  # the example is heavily commented

    studio.apply([{"scope": "root", "field": "polling.interval_ms", "value": 30000}])

    after = workflow.read_text()
    assert after.count("#") == before.count("#")
    assert len(after.splitlines()) == len(before.splitlines())
    assert "interval_ms: 30000" in after


def test_an_edit_changes_only_the_edited_line(studio, workflow):
    before = workflow.read_text().splitlines()
    studio.apply([{"scope": "root", "field": "agent.max_concurrent_agents", "value": 2}])
    after = workflow.read_text().splitlines()

    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(changed) == 1
    assert "max_concurrent_agents" in changed[0][1]


def test_a_no_op_edit_leaves_the_file_byte_identical(studio, workflow):
    before = workflow.read_text()
    current = studio.describe()["root"]["polling.interval_ms"]
    studio.apply([{"scope": "root", "field": "polling.interval_ms", "value": current}])
    assert workflow.read_text() == before


# ── An invalid config is never written ───────────────────────────────────────


def test_a_state_pointed_at_a_missing_prompt_is_rejected(studio, workflow):
    before = workflow.read_text()
    with pytest.raises(StudioError):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "prompt", "value": "prompts/does-not-exist.md"}])
    assert workflow.read_text() == before  # nothing written


def test_a_gate_rework_target_that_does_not_exist_is_rejected(studio, workflow):
    before = workflow.read_text()
    with pytest.raises(StudioError):
        studio.apply([{"scope": "state", "state": "research-review",
                       "field": "rework_to", "value": "nowhere"}])
    assert workflow.read_text() == before


def test_raw_write_of_broken_yaml_is_rejected(studio, workflow):
    before = workflow.read_text()
    with pytest.raises(StudioError, match="would not parse"):
        studio.write_raw("states: [this is not\n  valid: yaml:::")
    assert workflow.read_text() == before


def test_raw_write_of_valid_yaml_that_is_an_invalid_workflow_is_rejected(studio, workflow):
    before = workflow.read_text()
    with pytest.raises(StudioError, match="would be invalid"):
        studio.write_raw("tracker:\n  kind: linear\nstates: {}\n")
    assert workflow.read_text() == before


def test_a_good_raw_write_is_accepted(studio, workflow):
    text = workflow.read_text().replace("interval_ms: 15000", "interval_ms: 45000")
    studio.write_raw(text)
    assert "interval_ms: 45000" in workflow.read_text()


def test_no_temp_files_are_left_behind(studio, workflow):
    with pytest.raises(StudioError):
        studio.write_raw("nonsense: [")
    studio.apply([{"scope": "root", "field": "polling.interval_ms", "value": 20000}])
    strays = [p.name for p in workflow.parent.iterdir() if p.name.startswith(".")]
    assert strays == []


# ── The whitelist ────────────────────────────────────────────────────────────


def test_unlisted_fields_are_refused(studio):
    with pytest.raises(StudioError, match="not editable"):
        studio.apply([{"scope": "root", "field": "tracker.api_key", "value": "lin_api_x"}])
    with pytest.raises(StudioError, match="not editable"):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "type", "value": "terminal"}])


def test_an_unknown_state_is_refused(studio):
    with pytest.raises(StudioError, match="No such state"):
        studio.apply([{"scope": "state", "state": "nope", "field": "max_turns", "value": 5}])


@pytest.mark.parametrize("value", ["abc", "", None, -1])
def test_bad_numbers_are_refused_or_cleared(studio, workflow, value):
    before = workflow.read_text()
    if value in ("", None):
        # Clearing a state field falls back to the inherited default.
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "max_budget_usd", "value": value}])
        assert studio.describe()
    else:
        with pytest.raises(StudioError):
            studio.apply([{"scope": "state", "state": "implement",
                           "field": "max_budget_usd", "value": value}])
        assert workflow.read_text() == before


def test_max_turns_is_not_offered_as_a_state_field(studio):
    """It does nothing in state machine mode, so the UI must not imply it does.

    Each dispatch is exactly one `claude -p` invocation — the state machine
    controls continuation — and the CLI has no --max-turns flag, so the value
    reaches neither the loop nor the agent. Offering it as a runaway guard
    would be offering a limit that does not limit anything.
    """
    d = studio.describe()
    assert "max_turns" not in d["state_fields"]
    assert "claude.max_turns" not in d["root_fields"]
    assert "max_budget_usd" in d["state_fields"]  # the guard that does work


def test_model_fields_advertise_the_catalogue(studio):
    d = studio.describe()
    assert d["state_fields"]["model"]["type"] == "model"
    labels = [g["label"] for g in d["model_catalogue"]]
    assert any("Claude" in l for l in labels)
    assert any("Codex" in l for l in labels)


def test_a_model_in_use_but_not_in_the_catalogue_is_still_offered(studio, workflow):
    """An operator on a model newer than this release must not lose it."""
    studio.apply([{"scope": "state", "state": "implement",
                   "field": "model", "value": "claude-opus-9-future"}])
    groups = studio.describe()["model_catalogue"]
    assert groups[0]["label"] == "In this workflow"
    assert "claude-opus-9-future" in groups[0]["models"]


def test_effort_rejects_levels_the_cli_does_not_accept(studio):
    with pytest.raises(StudioError, match="must be one of"):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "effort", "value": "extreme"}])
    studio.apply([{"scope": "state", "state": "implement",
                   "field": "effort", "value": "xhigh"}])


def test_enum_fields_reject_anything_off_the_list(studio):
    with pytest.raises(StudioError, match="must be one of"):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "session", "value": "sideways"}])
    with pytest.raises(StudioError, match="must be one of"):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "runner", "value": "gpt"}])


def test_empty_update_list_is_refused(studio):
    with pytest.raises(StudioError, match="No changes"):
        studio.apply([])


# ── Prompts ──────────────────────────────────────────────────────────────────


def test_prompts_are_listed_and_readable(studio):
    prompts = studio.list_prompts()
    assert any(p["path"].endswith("global.example.md") for p in prompts)
    body = studio.read_prompt("prompts/global.example.md")
    assert "Global Agent Instructions" in body


def test_prompt_round_trip(studio, workflow):
    studio.write_prompt("prompts/merge.example.md", "# Replaced\n")
    assert (workflow.parent / "prompts" / "merge.example.md").read_text() == "# Replaced\n"


@pytest.mark.parametrize("path", [
    "../../../etc/passwd",
    "../outside.md",
    "prompts/../../escape.md",
])
def test_prompt_paths_cannot_escape_the_workflow_directory(studio, path):
    with pytest.raises(StudioError):
        studio.read_prompt(path)
    with pytest.raises(StudioError):
        studio.write_prompt(path, "pwned")


def test_the_workflow_file_cannot_be_written_through_the_prompt_route(studio):
    """Otherwise the prompt editor becomes an unvalidated config editor."""
    with pytest.raises(StudioError, match="Only .md"):
        studio.write_prompt("workflow.yaml", "states: {}")


# ── Describe ─────────────────────────────────────────────────────────────────


def test_describe_reports_the_pipeline(studio):
    d = studio.describe()
    names = [s["name"] for s in d["states"]]
    assert d["entry_state"] == "investigate"
    assert "ground-check" in names

    gc = next(s for s in d["states"] if s["name"] == "ground-check")
    assert gc["session"] == "fresh"
    assert gc["transitions"]["complete"] == "research-review"
    assert gc["concurrency"] == 2  # read from the by-state map

    gate = next(s for s in d["states"] if s["type"] == "gate")
    assert gate["rework_to"]


def test_describe_advertises_what_is_editable(studio):
    d = studio.describe()
    assert d["state_fields"]["session"]["choices"] == ["inherit", "fresh"]
    assert d["root_fields"]["polling.interval_ms"]["type"] == "int"
    assert "tracker.api_key" not in d["root_fields"]
