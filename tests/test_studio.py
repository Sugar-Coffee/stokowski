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
    """A real copy of the shipped example — config, workflows and prompts.

    All three are needed: the config's routing rules name workflows, and those
    workflows name prompts. Copying only the config is rejected by validation,
    which is the correct behaviour and worth preserving.
    """
    shutil.copy(REPO / "workflow.example.yaml", tmp_path / "workflow.yaml")
    shutil.copytree(REPO / "workflows", tmp_path / "workflows")
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
    target = {"scope": "state", "state": "research-review", "field": "max_rework"}
    if value in ("", None):
        # Clearing a state field falls back to the inherited default.
        studio.apply([{**target, "value": value}], workflow="feature")
        assert studio.describe("feature")
    else:
        with pytest.raises(StudioError):
            studio.apply([{**target, "value": value}], workflow="feature")
        assert workflow.read_text() == before


def test_inert_controls_are_not_offered_as_fields(studio):
    """The UI must not imply a control that has no effect.

    `max_turns` does nothing in state machine mode: each dispatch is one
    `claude -p` invocation, and the CLI has no --max-turns flag. `max_budget_usd`
    capped API spend, which does not exist on a subscription — the meter it read
    was never running. A run is bounded by turn and stall timeouts instead.
    """
    d = studio.describe()
    for dead in ("max_turns", "max_budget_usd"):
        assert dead not in d["state_fields"]
    assert "claude.max_turns" not in d["root_fields"]
    assert "claude.max_budget_usd" not in d["root_fields"]


def test_model_fields_advertise_the_catalogue(studio):
    d = studio.describe()
    assert d["state_fields"]["model"]["type"] == "model"
    labels = [g["label"] for g in d["model_catalogue"]]
    assert any("Claude" in l for l in labels)
    assert any("Codex" in l for l in labels)


def test_a_model_in_use_but_not_in_the_catalogue_is_still_offered(studio, workflow):
    """An operator on a model newer than this release must not lose it."""
    studio.apply([{"scope": "state", "state": "implement",
                   "field": "model", "value": "claude-opus-9-future"}],
                 workflow="feature")
    groups = studio.describe("feature")["model_catalogue"]
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


# ── Multi-workflow editing ───────────────────────────────────────────────────


def test_editing_one_workflow_leaves_the_others_untouched(studio, workflow):
    """Workflow files are separate documents; an edit must not bleed across."""
    others = {
        name: (workflow.parent / "workflows" / f"{name}.example.yaml").read_text()
        for name in ("bug-fix", "exploration")
    }
    studio.apply([{"scope": "state", "state": "implement",
                   "field": "effort", "value": "low"}], workflow="feature")

    for name, before in others.items():
        after = (workflow.parent / "workflows" / f"{name}.example.yaml").read_text()
        assert after == before, f"{name} changed while editing feature"
    assert studio.describe("feature")["states"]


def test_a_workflow_edit_preserves_that_file_s_comments(studio, workflow):
    path = workflow.parent / "workflows" / "feature.example.yaml"
    before = path.read_text()
    studio.apply([{"scope": "state", "state": "implement",
                   "field": "effort", "value": "low"}], workflow="feature")
    after = path.read_text()
    assert after.count("#") == before.count("#")
    assert len(after.splitlines()) == len(before.splitlines())


def test_an_edit_that_would_break_a_workflow_is_rejected_and_rolled_back(studio, workflow):
    """The candidate is written in place to validate, so it must be restored."""
    path = workflow.parent / "workflows" / "feature.example.yaml"
    before = path.read_text()
    with pytest.raises(StudioError):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "prompt", "value": "prompts/nope.md"}],
                     workflow="feature")
    assert path.read_text() == before


def test_unknown_workflows_are_refused(studio):
    with pytest.raises(StudioError, match="No such workflow"):
        studio.apply([{"scope": "state", "state": "implement",
                       "field": "effort", "value": "low"}], workflow="nope")
    with pytest.raises(StudioError, match="No such workflow"):
        studio.set_default_workflow("nope")


def test_describe_lists_workflows_and_the_routing_table(studio):
    d = studio.describe()
    # `default` is the example's inline states block, which is a workflow too.
    assert set(d["workflows"]) == {"bug-fix", "exploration", "feature", "default"}
    assert d["selected_workflow"] == d["routing"]["default"] == "feature"
    assert {"label": "bug", "workflow": "bug-fix"} in d["routing"]["rules"]


def test_describe_can_show_a_named_workflow(studio):
    names = [s["name"] for s in studio.describe("bug-fix")["states"]]
    assert names[0] == "reproduce"


def test_setting_the_default_workflow(studio, workflow):
    studio.set_default_workflow("bug-fix")
    assert studio.describe()["routing"]["default"] == "bug-fix"
    # Still a valid config afterwards.
    assert "routing" in workflow.read_text()


# ── The inline workflow ──────────────────────────────────────────────────────
#
# An inline `states:` block is a real workflow named `default` — routing can
# target it, and for an operator migrating from a single pipeline it usually IS
# the default. But it lives in the main config rather than in `workflows/`, so
# every path lookup has to account for it.
#
# Regression: the studio listed only `workflows/*.yaml`, so a config whose
# routing default was the inline block raised "No such workflow: default" and
# the page failed to render at all. The shipped example did not catch it because
# its default points at a workflow *file*.


@pytest.fixture
def inline_default(workflow):
    """A config whose routing default is its inline states block."""
    text = workflow.read_text().replace("default: feature", "default: default")
    workflow.write_text(text)
    return Studio(workflow)


def test_the_inline_workflow_is_listed(inline_default):
    assert "default" in inline_default._list_workflows()


def test_a_config_defaulting_to_its_inline_block_renders(inline_default):
    d = inline_default.describe()
    assert d["selected_workflow"] == "default"
    assert d["states"], "the inline pipeline should be shown, not an empty list"


def test_every_listed_workflow_can_be_shown(inline_default):
    """Whatever the studio lists, it must be able to render."""
    for name in inline_default._list_workflows():
        assert inline_default.describe(name)["states"], name


def test_editing_the_inline_workflow_writes_the_main_config(inline_default, workflow):
    before = workflow.read_text()
    inline_default.apply([{"scope": "state", "state": "implement",
                           "field": "effort", "value": "low"}], workflow="default")

    after = workflow.read_text()
    assert after != before
    assert after.count("#") == before.count("#")  # comments survive
    assert inline_default.describe("default")


def test_a_bad_edit_to_the_inline_workflow_is_still_rejected(inline_default, workflow):
    """It is validated as a whole config, not as a standalone workflow file."""
    before = workflow.read_text()
    with pytest.raises(StudioError):
        inline_default.apply([{"scope": "state", "state": "implement",
                               "field": "prompt", "value": "prompts/nope.md"}],
                             workflow="default")
    assert workflow.read_text() == before


def test_a_config_with_no_inline_states_does_not_invent_one(workflow):
    """Only list `default` when there is actually an inline block."""
    text = workflow.read_text()
    start = text.index("\nstates:")
    workflow.write_text(text[:start] + "\n")
    assert "default" not in Studio(workflow)._list_workflows()
