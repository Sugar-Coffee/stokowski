"""Tests for the shipped example workflow and prompts.

These are not decoration. The examples are what operators copy and, in our own
case, run directly — so a stale instruction in an example is a stale
instruction in production. Two whole classes of bug shipped from exactly that:
prompts telling agents to "run a code review skill" while the system prompt
banned skills, and prompts telling agents to post Linear comments after
Stokowski took over that job.

Anything asserted here is a promise the examples make to the agent. If a
promise changes, change it in one place and let these fail loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokowski.config import parse_workflow_file, validate_config
from stokowski.models import Issue
from stokowski.prompt import assemble_prompt, load_prompt_file, render_template

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / "workflow.example.yaml"
PROMPTS = REPO / "prompts"

# Global prompts frame a workflow; stage prompts drive one step of it. Only
# the latter own the report contract, so they are what that test applies to.
GLOBAL_PROMPTS = sorted(PROMPTS.glob("global*.example.md"))
STAGE_PROMPTS = sorted(
    p for p in PROMPTS.glob("*.example.md") if not p.name.startswith("global")
)
ALL_PROMPTS = sorted(PROMPTS.glob("*.example.md"))


@pytest.fixture(scope="module")
def cfg():
    # parse_workflow_file returns a WorkflowDefinition wrapping the config.
    return parse_workflow_file(str(WORKFLOW)).config


@pytest.fixture
def issue():
    return Issue(
        id="issue-uuid",
        identifier="ENG-123",
        title="average() crashes on an empty list",
        description="Calling average([]) raises ZeroDivisionError.",
        state="In Progress",
        url="https://linear.app/team/issue/ENG-123",
        labels=["bug"],
        branch_name="eng-123-average-empty",
    )


# ── The example workflow must actually be valid ──────────────────────────────


def test_example_workflow_parses_and_validates(cfg):
    errors = validate_config(cfg)
    assert errors == [] or all("warn" in str(e).lower() for e in errors), errors


def test_every_state_prompt_file_exists(cfg):
    for name, state in cfg.states.items():
        if state.type != "agent":
            continue
        assert state.prompt, f"agent state '{name}' has no prompt"
        assert (WORKFLOW.parent / state.prompt).is_file(), (
            f"state '{name}' points at a missing prompt: {state.prompt}"
        )


def test_global_prompt_file_exists(cfg):
    assert cfg.prompts.global_prompt
    assert (WORKFLOW.parent / cfg.prompts.global_prompt).is_file()


def test_workflow_reaches_a_terminal_state(cfg):
    """A pipeline that cannot finish parks issues forever."""
    assert any(s.type == "terminal" for s in cfg.states.values())
    assert cfg.entry_state is not None


# ── Every prompt must render ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=lambda p: p.name)
def test_prompt_is_valid_jinja(path, issue):
    """A template error takes the whole stage down at dispatch time."""
    body = load_prompt_file(path.name, PROMPTS)
    rendered = render_template(body, {"issue": {
        "identifier": issue.identifier, "title": issue.title,
        "description": issue.description, "state": issue.state,
        "url": issue.url, "labels": ", ".join(issue.labels),
    }})
    assert rendered.strip()
    assert "{{" not in rendered, "unrendered placeholder left in output"


@pytest.mark.parametrize("path", ALL_PROMPTS, ids=lambda p: p.name)
def test_prompt_has_no_stale_instructions(path):
    """Instructions that contradict the runtime, in the words they shipped in."""
    text = path.read_text().lower()

    forbidden = {
        "workpad": "the Linear workpad was replaced by .stokowski/report.json",
        "post the summary as a linear comment":
            "Stokowski posts the comment now — this would duplicate it",
        "post your review as a linear comment":
            "Stokowski posts the comment now — this would duplicate it",
        "never use interactive commands, slash commands":
            "slash commands work headlessly and are no longer banned",
    }
    for phrase, why in forbidden.items():
        assert phrase not in text, f"{path.name}: {why}"


@pytest.mark.parametrize("path", STAGE_PROMPTS, ids=lambda p: p.name)
def test_every_stage_prompt_requires_a_report(path):
    """Stokowski has nothing to render if the stage never asks for one."""
    text = path.read_text()
    assert ".stokowski/report.json" in text, (
        f"{path.name} never tells the agent to write a report"
    )


@pytest.mark.parametrize("path", STAGE_PROMPTS, ids=lambda p: p.name)
def test_every_stage_prompt_asks_for_the_gate_summary(path):
    """The four fields that render above the fold.

    A stage that fills `next_steps` but not `key_points` produces a comment
    whose top block says what to do without saying why — which is the thing a
    reviewer at a gate most needs and the reason this block exists at all.
    """
    text = path.read_text()
    if "`next_steps`" not in text:
        pytest.skip("stage does not enumerate report fields")
    assert "`key_points`" in text, (
        f"{path.name} asks for next_steps but never for key_points, so its "
        f"recommendation will land without its reasons"
    )


def test_global_prompt_covers_grounding_and_evidence():
    """The two failure modes this workflow exists to defend against."""
    text = (PROMPTS / "global.example.md").read_text().lower()
    assert "$stokowski_artifacts" in text, "agents are not told where evidence goes"
    for concept in ("data source", "assumption"):
        assert concept in text, f"global prompt does not address {concept!r}"
    assert "agent-pitfalls" in text, "global prompt does not point at known-mistake lists"


def test_review_prompt_audits_the_implementer_not_just_the_code():
    """The meeting's core finding: a confident report over bad data is the risk."""
    text = (PROMPTS / "review.example.md").read_text().lower()
    assert "agent-pitfalls" in text
    assert "data source" in text


# ── Full assembly, as the orchestrator does it ───────────────────────────────


def _agent_states() -> list[str]:
    """Every agent state in the example workflow.

    Derived from the config rather than hardcoded so a newly added stage is
    covered automatically instead of silently untested.
    """
    config = parse_workflow_file(str(WORKFLOW)).config
    return [n for n, s in config.states.items() if s.type == "agent"]


@pytest.mark.parametrize("state_name", _agent_states())
def test_full_prompt_assembly(cfg, issue, state_name):
    state = cfg.states[state_name]
    prompt = assemble_prompt(
        cfg=cfg,
        workflow_dir=str(WORKFLOW.parent),
        issue=issue,
        state_name=state_name,
        state_cfg=state,
        run=1,
    )
    # Three layers present: global, stage, lifecycle.
    assert "Global Agent Instructions" in prompt
    assert issue.identifier in prompt
    assert "STOKOWSKI LIFECYCLE" in prompt
    # The reporting contract is injected for every stage.
    assert ".stokowski/report.json" in prompt
    assert "$STOKOWSKI_ARTIFACTS" in prompt
    assert '"classification"' in prompt


def test_rework_run_surfaces_the_review_feedback(cfg, issue):
    prompt = assemble_prompt(
        cfg=cfg,
        workflow_dir=str(WORKFLOW.parent),
        issue=issue,
        state_name="implement",
        state_cfg=cfg.states["implement"],
        run=2,
        is_rework=True,
        comments=[{"body": "You queried staging, not production.",
                   "createdAt": "2026-08-31T10:00:00Z"}],
    )
    assert "rework" in prompt.lower()
    assert "You queried staging, not production." in prompt


def test_assembled_prompt_stays_a_sane_size(cfg, issue):
    """Every token here is spent before the agent reads a line of code."""
    prompt = assemble_prompt(
        cfg=cfg, workflow_dir=str(WORKFLOW.parent), issue=issue,
        state_name="implement", state_cfg=cfg.states["implement"], run=1,
    )
    assert len(prompt) < 20_000, f"prompt is {len(prompt)} chars — trim it"


def test_the_pipeline_verifies_before_it_asks_a_human_to_approve(cfg):
    """An investigation must be fact-checked before it reaches a human gate.

    A human reading prose cannot tell a well-sourced conclusion from a
    well-written one. Something has to check the facts first, in a fresh
    session so it cannot inherit the assumption it is testing.
    """
    entry = cfg.entry_state
    nxt = cfg.states[entry].transitions.get("complete")
    assert nxt, f"entry state '{entry}' has no completion transition"

    checker = cfg.states[nxt]
    assert checker.type == "agent", (
        f"'{entry}' hands straight to '{nxt}' ({checker.type}) — "
        "nothing verifies the investigation's facts before a human sees them"
    )
    assert checker.session == "fresh", (
        f"'{nxt}' must run in a fresh session; a continued session inherits "
        "the assumptions it is supposed to be testing"
    )


def test_the_grounding_prompt_actually_checks_grounding():
    text = (PROMPTS / "ground-check.example.md").read_text().lower()
    # `session: fresh` is asserted against the config, not the prose.
    for concept in ("data_sources", "reproduce", "no prior context", "staging"):
        assert concept in text, f"grounding prompt does not address {concept!r}"
    # A verifier that only ever reports problems is not trustworthy.
    assert "confirm" in text


# ── Workspace hooks ──────────────────────────────────────────────────────────
#
# Every one of these was a real defect in the shipped example, and each would
# have stopped the pipeline before an agent produced a single line of work.


def test_clone_hook_keeps_enough_history_to_diff(cfg):
    """`--depth 1` breaks the review stage.

    The review prompt runs `git diff main...HEAD`, which needs a merge base a
    shallow clone does not have.
    """
    after_create = cfg.hooks.after_create or ""
    assert "--depth 1" not in after_create and "--depth=1" not in after_create


def test_before_run_hook_cannot_fail_a_turn(cfg):
    """A non-zero exit here fails the turn before the agent launches.

    The original hook ended `git rebase origin/main || git rebase --abort`,
    which exits 128 on a dirty tree — the normal state mid-task — so every
    retry hit the same wall and the issue looped on backoff forever.
    """
    before_run = (cfg.hooks.before_run or "").strip()
    assert before_run.endswith("exit 0"), (
        "before_run must end `exit 0`; it runs on dirty working trees and a "
        "non-zero exit fails the turn before the agent starts"
    )
    if "rebase" in before_run:
        assert "git diff --quiet" in before_run, (
            "a rebase in before_run must be guarded by a clean-tree check"
        )


def test_workspace_setup_has_a_realistic_timeout(cfg):
    """A cold clone plus dependency install is minutes of work, not seconds."""
    assert cfg.hooks.timeout_ms >= 600_000, (
        f"hooks.timeout_ms is {cfg.hooks.timeout_ms}ms — a cold monorepo "
        "install will exceed it and every workspace creation will fail"
    )


# ── Workflows ────────────────────────────────────────────────────────────────


def _workflow_configs():
    from stokowski.config import _load_workflow_dir
    return sorted(_load_workflow_dir(REPO).items())


@pytest.mark.parametrize("name,wf", _workflow_configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_shipped_workflow_is_runnable(cfg, name, wf):
    """Each pipeline must stand on its own: prompts present, states coherent."""
    from stokowski.config import _validate_states

    errors: list[str] = []
    _validate_states(wf.states, cfg.projects[0], f"workflow '{name}'", errors,
                     global_prompt=wf.global_prompt)
    assert errors == [], errors


@pytest.mark.parametrize("name,wf", _workflow_configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_workflow_names_a_global_prompt_that_exists(name, wf):
    assert wf.global_prompt, f"workflow '{name}' has no global prompt to frame it"
    assert (REPO / wf.global_prompt).is_file()


@pytest.mark.parametrize("path", GLOBAL_PROMPTS, ids=lambda p: p.name)
def test_global_prompts_do_not_branch_on_ticket_type(path):
    """The whole point of per-workflow globals is that branching moves here —
    into which file gets loaded — rather than living inside the prose."""
    text = path.read_text().lower()
    for phrase in ("if this is a bug", "if the issue is a bug",
                   "if this is a feature", "for bug tickets"):
        assert phrase not in text, f"{path.name} still branches on ticket type"


# ── Comment attribution ──────────────────────────────────────────────────────
#
# Reported from a real run: the agent addressed the operator by a colleague's
# name. Two causes — comments were injected with no author at all, and the repo
# documentation names people, which the agent bound to its reader.


def test_comments_are_attributed_to_their_author():
    from stokowski.prompt import format_comment

    out = "\n".join(format_comment({
        "body": "Check production, not staging.",
        "createdAt": "2026-08-31T10:00:00Z",
        "user": {"displayName": "Josh Pine", "name": "josh"},
    }))
    assert "**Josh Pine**" in out
    assert "Check production, not staging." in out


def test_a_bot_comment_is_marked_as_one():
    """Stokowski's own comments must not read as a human instruction."""
    from stokowski.prompt import format_comment
    assert "(bot)" in "\n".join(format_comment({"body": "x", "botActor": {"name": "Stokowski"}}))


def test_an_unattributed_comment_says_so_rather_than_going_bare():
    """A bare quote invites the agent to guess who said it."""
    from stokowski.prompt import format_comment
    assert "unknown author" in "\n".join(format_comment({"body": "x"}))


def test_a_multiline_comment_stays_inside_its_quote():
    from stokowski.prompt import format_comment
    out = "\n".join(format_comment({"body": "One.\nTwo.", "user": {"name": "A"}}))
    assert "> Two." in out


def test_comment_authors_reach_the_assembled_prompt(cfg, issue):
    """The whole point is that the agent sees who said what."""
    prompt = assemble_prompt(
        cfg=cfg, workflow_dir=str(WORKFLOW.parent), issue=issue,
        state_name="implement", state_cfg=cfg.states["implement"], run=2,
        is_rework=True,
        comments=[{"body": "You queried staging.", "createdAt": "2026-08-31T10:00:00Z",
                   "user": {"displayName": "Amadeus"}}],
    )
    assert "**Amadeus**" in prompt
    assert "You queried staging." in prompt


def test_the_global_prompt_forbids_guessing_the_reader():
    """Names in repo docs are colleagues, not the person reading the report."""
    # Collapse whitespace: these are prose assertions and a line wrap should
    # not decide whether the concept is present.
    text = " ".join((PROMPTS / "global.example.md").read_text().lower().split())
    assert "who you are talking to" in text
    for concept in ("do not know who will read", "colleagues mentioned in documentation"):
        assert concept in text, f"global prompt does not cover {concept!r}"
