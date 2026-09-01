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
    from stokowski.config import global_prompt_paths

    globals_ = {n: tuple(global_prompt_paths(wf.global_prompt))
                for n, wf in shipped.workflows.items()
                if n in ("bug-fix", "feature", "exploration")}
    assert len(set(globals_.values())) == 3, globals_


def test_specialised_globals_stack_on_the_shared_one(shipped):
    """A specialised global supplements the base one, it does not replace it.

    `global-bug-fix.md` used to say "everything in global.md applies" in prose.
    Nothing loaded that file, so every bug-fix run went out missing the shared
    grounding, evidence and branching rules it claimed to inherit.
    """
    from stokowski.config import global_prompt_paths

    for name in ("bug-fix", "exploration"):
        paths = global_prompt_paths(shipped.workflows[name].global_prompt)
        assert len(paths) == 2, f"{name} does not stack its global prompts: {paths}"
        # An operator file may shadow the shipped example, so match the stem.
        assert Path(paths[0]).name.startswith("global.") , (
            f"{name} must load the shared global first, got {paths}"
        )


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


# ── Through the orchestrator ─────────────────────────────────────────────────
#
# These exercise the path a running tick actually takes. Every test above works
# on the parsed config directly, which is why two bugs got through: the
# orchestrator reads a per-project ServiceConfig *view*, and that view was
# dropping `workflows` and `routing` entirely — so routing never fired and
# `all_states()` did not exist on the type the orchestrator holds.


@pytest.fixture
def orchestrator(tmp_path):
    """An Orchestrator over a real multi-workflow config, loaded as a tick would."""
    from stokowski.orchestrator import Orchestrator

    shutil.copy(REPO / "workflow.example.yaml", tmp_path / "workflow.yaml")
    shutil.copytree(REPO / "workflows", tmp_path / "workflows")
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")

    orch = Orchestrator(workflow_path=tmp_path / "workflow.yaml")
    orch._load_workflow()
    return orch


def _issue(labels, ident="ENG-1", id_="i1"):
    from stokowski.models import Issue
    return Issue(id=id_, identifier=ident, title="t", labels=labels)


def test_the_orchestrator_sees_the_workflows(orchestrator):
    """Regression: the per-project view dropped workflows and routing."""
    assert set(orchestrator.cfg.workflows) >= {"bug-fix", "feature", "exploration"}
    assert orchestrator.cfg.routing.default == "feature"


def test_all_states_exists_on_what_the_orchestrator_holds(orchestrator):
    """Regression: `'ServiceConfig' object has no attribute 'all_states'`.

    The gate check calls this on every tick, so a missing method is not a
    latent bug — it breaks the poll loop immediately.
    """
    states = orchestrator.cfg.all_states()
    assert "reproduce" in states      # bug-fix only
    assert "investigate" in states    # feature and exploration
    assert any(s.type == "gate" for s in states.values())


@pytest.mark.parametrize("labels,expected", [
    (["bug"], "bug-fix"),
    (["spike"], "exploration"),
    ([], "feature"),
])
def test_the_orchestrator_routes_an_issue(orchestrator, labels, expected):
    assert orchestrator._workflow_name_for(_issue(labels)) == expected


def test_the_orchestrator_uses_the_routed_state_machine(orchestrator):
    # Distinct ids: pins are per-issue, so reusing one would resolve the second
    # lookup from the first issue's pin rather than from its own labels.
    assert "reproduce" in orchestrator._states_for(_issue(["bug"], id_="bug-1"))
    assert "reproduce" not in orchestrator._states_for(_issue([], id_="plain-1"))


def test_relabelling_does_not_move_a_running_issue(orchestrator):
    """The pin is the point: a mid-run label edit must not switch pipelines."""
    issue = _issue(["bug"])
    assert orchestrator._workflow_name_for(issue) == "bug-fix"

    issue.labels = ["spike"]
    assert orchestrator._workflow_name_for(issue) == "bug-fix"


def test_a_restart_recovers_the_pin_from_linear(orchestrator):
    """In-memory pins are lost on restart; the tracking comment carries them."""
    issue = _issue(["spike"])
    orchestrator._adopt_workflow_from_tracking(issue, {"type": "state", "workflow": "bug-fix"})
    assert orchestrator._workflow_name_for(issue) == "bug-fix"


def test_a_recorded_workflow_that_no_longer_exists_is_ignored(orchestrator):
    """A deleted workflow must fall back to routing, not pin to nothing."""
    issue = _issue(["bug"])
    orchestrator._adopt_workflow_from_tracking(issue, {"type": "state", "workflow": "deleted"})
    assert orchestrator._workflow_name_for(issue) == "bug-fix"
