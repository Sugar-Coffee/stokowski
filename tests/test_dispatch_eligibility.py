"""Tests for which issues Stokowski will pick up.

The behaviour that matters: blockers gate ENTRY to the pipeline, and they do
so against the *configured* todo state rather than a state literally named
"Todo".

The regression: the check read `state_lower == "todo"`, comparing the Linear
state's NAME against a hardcoded string. That matches only because Stokowski's
own default is named "Todo". Any team that named the state something else —
"Assigned, Not Started", "Ready", "Up Next" — silently lost the guard and
dispatched agents onto blocked issues. There was no error and no warning; the
branch simply never ran, which is why nothing caught it for so long.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stokowski.models import BlockerRef, Issue

REPO = Path(__file__).resolve().parent.parent

RENAMED_TODO = "Assigned, Not Started"


def _workflow(tmp_path: Path, todo_state: str) -> Path:
    """Copy the shipped example, renaming only the todo state."""
    src = (REPO / "workflow.example.yaml").read_text()
    assert 'todo: "Todo"' in src, "example config no longer maps todo to 'Todo'"
    (tmp_path / "workflow.yaml").write_text(
        src.replace('todo: "Todo"', f'todo: "{todo_state}"', 1)
    )
    shutil.copytree(REPO / "workflows", tmp_path / "workflows")
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")
    return tmp_path / "workflow.yaml"


def _orchestrator(tmp_path: Path, todo_state: str):
    from stokowski.orchestrator import Orchestrator

    orch = Orchestrator(workflow_path=_workflow(tmp_path, todo_state))
    errors = orch._load_workflow()
    assert errors == [], errors
    return orch


def _issue(state: str, blockers: list[str] | None = None) -> Issue:
    return Issue(
        id="i1",
        identifier="ENG-1",
        title="t",
        state=state,
        blocked_by=[
            BlockerRef(id=f"b{n}", identifier=f"ENG-{n}", state=s)
            for n, s in enumerate(blockers or [])
        ],
    )


@pytest.mark.parametrize("todo_state", ["Todo", RENAMED_TODO])
def test_an_open_blocker_holds_an_issue_out_of_the_pipeline(tmp_path, todo_state):
    """The regression, asserted for both the default name and a renamed one.

    Parametrising is the point: with the old code the "Todo" case passed and
    the renamed case did not, so a test pinned to the default would have gone
    on being green through the entire bug.
    """
    orch = _orchestrator(tmp_path, todo_state)
    issue = _issue(todo_state, blockers=["In Progress"])
    assert orch._is_eligible(issue) is False


@pytest.mark.parametrize("todo_state", ["Todo", RENAMED_TODO])
def test_an_unblocked_issue_is_eligible(tmp_path, todo_state):
    orch = _orchestrator(tmp_path, todo_state)
    assert orch._is_eligible(_issue(todo_state)) is True


@pytest.mark.parametrize("todo_state", ["Todo", RENAMED_TODO])
def test_a_resolved_blocker_does_not_hold_an_issue(tmp_path, todo_state):
    """Only blockers still open count — a Done blocker is not a blocker."""
    orch = _orchestrator(tmp_path, todo_state)
    issue = _issue(todo_state, blockers=["Done"])
    assert orch._is_eligible(issue) is True


def test_a_blocker_appearing_mid_run_does_not_stop_work_already_started(tmp_path):
    """Blockers gate entry, not work under way. An issue already in the active
    state dispatches even with an open blocker — otherwise a blocker added
    mid-pipeline would strand a live run."""
    orch = _orchestrator(tmp_path, RENAMED_TODO)
    issue = _issue("In Progress", blockers=["In Progress"])
    assert orch._is_eligible(issue) is True


def test_the_renamed_todo_state_is_polled_at_all(tmp_path):
    """Guards the assumption the tests above rest on: a renamed todo state is
    still in the active set, so the eligibility check is reached rather than
    short-circuited by the active-state filter."""
    orch = _orchestrator(tmp_path, RENAMED_TODO)
    assert RENAMED_TODO in orch.cfg.active_linear_states()
