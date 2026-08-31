"""Tests for workflow routing.

The behaviour that matters: a ticket's labels choose its pipeline, the choice
is deterministic when several labels match, and the choice is pinned so
relabelling mid-run cannot move an issue onto a different state machine.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from stokowski.config import (
    RoutingConfig,
    RoutingRule,
    _load_workflow_dir,
    parse_workflow_file,
    validate_config,
)

REPO = Path(__file__).resolve().parent.parent


# ── Resolution ───────────────────────────────────────────────────────────────


def routing(**kw) -> RoutingConfig:
    rules = [RoutingRule(l, w) for l, w in kw.pop("rules", [])]
    return RoutingConfig(rules=rules, **kw)


def test_first_matching_rule_wins():
    """Order is the tie-break, so a multi-labelled ticket resolves the same way
    every time — and which way is readable from the config."""
    r = routing(default="feature", rules=[("bug", "bug-fix"), ("spike", "exploration")])
    assert r.resolve(["spike", "bug"]) == "bug-fix"
    assert r.resolve(["bug", "spike"]) == "bug-fix"

    reversed_ = routing(default="feature", rules=[("spike", "exploration"), ("bug", "bug-fix")])
    assert reversed_.resolve(["spike", "bug"]) == "exploration"


def test_label_matching_ignores_case_and_padding():
    r = routing(default="feature", rules=[("bug", "bug-fix")])
    for label in ("Bug", "BUG", "  bug  "):
        assert r.resolve([label]) == "bug-fix"


def test_unmatched_and_empty_labels_fall_to_the_default():
    r = routing(default="feature", rules=[("bug", "bug-fix")])
    assert r.resolve(["chore"]) == "feature"
    assert r.resolve([]) == "feature"
    assert r.resolve(None) == "feature"


def test_no_default_and_no_match_resolves_to_nothing():
    r = routing(rules=[("bug", "bug-fix")])
    assert r.resolve(["chore"]) is None


# ── Loading ──────────────────────────────────────────────────────────────────


def test_shipped_workflows_load_with_clean_names():
    workflows = _load_workflow_dir(REPO)
    assert {"bug-fix", "feature", "exploration"} <= set(workflows)
    # A packaging suffix must not leak into the routing name.
    assert not any(n.endswith(".example") for n in workflows)


def test_a_real_file_shadows_the_example_of_the_same_name(tmp_path):
    d = tmp_path / "workflows"
    d.mkdir()
    (d / "bug-fix.example.yaml").write_text("description: shipped\nstates: {}\n")
    (d / "bug-fix.yaml").write_text("description: mine\nstates: {}\n")

    workflows = _load_workflow_dir(tmp_path)
    assert list(workflows) == ["bug-fix"]
    assert workflows["bug-fix"].description == "mine"


def test_one_broken_workflow_does_not_take_down_the_others(tmp_path):
    d = tmp_path / "workflows"
    d.mkdir()
    (d / "good.yaml").write_text("states: {}\n")
    (d / "broken.yaml").write_text("states: [this is not\n  valid: yaml:::\n")

    assert "good" in _load_workflow_dir(tmp_path)


def test_a_missing_workflows_directory_is_not_an_error(tmp_path):
    assert _load_workflow_dir(tmp_path) == {}


# ── Back-compat ──────────────────────────────────────────────────────────────


@pytest.fixture
def legacy(tmp_path):
    """A single-pipeline config with an inline states block and no workflows/."""
    shutil.copy(REPO / "workflow.example.yaml", tmp_path / "workflow.yaml")
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")
    # Strip the routing block so this is a pre-routing config.
    wf = tmp_path / "workflow.yaml"
    text = wf.read_text()
    start = text.index("routing:")
    end = text.index("# ---", start)
    wf.write_text(text[:start] + text[end:])
    return wf


def test_an_inline_state_machine_becomes_the_default_workflow(legacy):
    cfg = parse_workflow_file(str(legacy)).config
    assert "default" in cfg.workflows
    assert cfg.routing.default == "default"
    assert cfg.workflows["default"].states == cfg.states
    assert validate_config(cfg) == []


def test_every_label_routes_to_default_when_there_are_no_rules(legacy):
    project = parse_workflow_file(str(legacy)).config.projects[0]
    for labels in ([], ["bug"], ["anything"]):
        assert project.workflow_for(labels).name == "default"


# ── The shipped multi-workflow config ────────────────────────────────────────


@pytest.fixture(scope="module")
def shipped():
    return parse_workflow_file(str(REPO / "workflow.example.yaml")).config


@pytest.mark.parametrize("labels,expected", [
    ([], "feature"),
    (["bug"], "bug-fix"),
    (["Spike"], "exploration"),
    (["exploration"], "exploration"),
    (["bug", "spike"], "bug-fix"),
    (["chore"], "feature"),
])
def test_shipped_routing(shipped, labels, expected):
    assert shipped.projects[0].workflow_for(labels).name == expected


def test_every_workflow_validates_on_its_own(shipped):
    assert validate_config(shipped) == []


def test_exploration_cannot_write_code(shipped):
    """The pipeline has no implement or merge stage, by design.

    An exploration that quietly becomes a code change has skipped the decision
    it existed to inform, so there is deliberately nowhere for it to do that.
    """
    states = shipped.workflows["exploration"].states
    assert "implement" not in states
    assert "merge" not in states
    assert any(s.type == "terminal" for s in states.values())


def test_bug_fix_reproduces_before_it_diagnoses(shipped):
    """Reproduction gates diagnosis — a bug nobody reproduced has no evidence."""
    states = shipped.workflows["bug-fix"].states
    entry = next(n for n, s in states.items() if s.type == "agent")
    assert entry == "reproduce"
    assert states["reproduce"].transitions["complete"] == "diagnose"


def test_workflows_share_prompts_where_the_job_is_the_same(shipped):
    """Sharing is a path, not a mechanism — two workflows naming one file."""
    review = {
        name: wf.states["code-review"].prompt
        for name, wf in shipped.workflows.items()
        if "code-review" in wf.states
    }
    assert len(review) >= 2
    assert len(set(review.values())) == 1, review


def test_each_workflow_frames_itself_with_its_own_global_prompt(shipped):
    """This is what removes `if this is a bug` branching from stage prompts."""
    globals_ = {n: wf.global_prompt for n, wf in shipped.workflows.items()
                if n in ("bug-fix", "feature", "exploration")}
    assert len(set(globals_.values())) == 3, globals_


# ── Validation ───────────────────────────────────────────────────────────────


def _write(tmp_path, routing_yaml: str, workflows: dict[str, str]):
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")
    (tmp_path / "workflows").mkdir()
    for name, body in workflows.items():
        (tmp_path / "workflows" / f"{name}.yaml").write_text(textwrap.dedent(body))
    base = (REPO / "workflow.example.yaml").read_text()
    base = base[: base.index("routing:")] + routing_yaml
    (tmp_path / "workflow.yaml").write_text(base)
    return parse_workflow_file(str(tmp_path / "workflow.yaml")).config


MINIMAL = """
    states:
      go:
        type: agent
        prompt: prompts/merge.example.md
        linear_state: active
        transitions: { complete: done }
      done:
        type: terminal
        linear_state: terminal
"""


def test_a_rule_pointing_at_a_missing_workflow_is_rejected(tmp_path):
    cfg = _write(tmp_path, "routing:\n  default: a\n  rules:\n    - label: x\n      workflow: nope\n",
                 {"a": MINIMAL})
    assert any("unknown workflow 'nope'" in e for e in validate_config(cfg))


def test_a_missing_default_workflow_is_rejected(tmp_path):
    cfg = _write(tmp_path, "routing:\n  default: nope\n", {"a": MINIMAL})
    assert any("default workflow 'nope' does not exist" in e for e in validate_config(cfg))


def test_multiple_workflows_with_no_default_is_rejected(tmp_path):
    """An unlabelled ticket would otherwise have nowhere to go."""
    cfg = _write(tmp_path, "routing:\n  rules: []\n", {"a": MINIMAL, "b": MINIMAL})
    assert any("no routing.default" in e for e in validate_config(cfg))


def test_a_broken_state_machine_is_reported_against_its_own_workflow(tmp_path):
    broken = """
    states:
      go:
        type: agent
        prompt: prompts/merge.example.md
        linear_state: active
        transitions: { complete: nowhere }
      done:
        type: terminal
        linear_state: terminal
    """
    cfg = _write(tmp_path, "routing:\n  default: a\n", {"a": broken})
    errors = validate_config(cfg)
    assert any("workflow 'a'" in e and "unknown state 'nowhere'" in e for e in errors)
