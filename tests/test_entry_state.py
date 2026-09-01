"""Entry state must come from the issue's own workflow.

`ServiceConfig.entry_state` returns the first agent state of the inline
`states:` block — one pipeline among several. Routing an issue to `bug-fix`
and then starting it in `default`'s entry state (`investigate`) drops it into
a state `bug-fix` does not contain. The unknown-state fallback then returned
that same entry, so every tick produced the identical wrong answer and the
ticket could never transition. COG-349 sat there through a whole implementation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from stokowski.config import parse_workflow_file

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def project():
    return parse_workflow_file(REPO / "workflow.example.yaml").config.projects[0]


def test_every_workflow_starts_in_a_state_it_actually_has(project):
    for name, wf in project.workflows.items():
        entry = wf.entry_state
        assert entry, f"workflow '{name}' has no agent state to start in"
        assert entry in wf.states, (
            f"workflow '{name}' would start in '{entry}', which is not one of "
            f"its states: {list(wf.states)}"
        )


def test_a_workflow_entry_is_not_borrowed_from_the_inline_block(project):
    """The specific failure: bug-fix started in the default pipeline's entry."""
    project_entry = project.entry_state
    for name, wf in project.workflows.items():
        if project_entry in wf.states:
            continue
        assert wf.entry_state != project_entry, (
            f"workflow '{name}' must not inherit the project entry state "
            f"'{project_entry}' — it does not have that state"
        )


def test_workflows_that_differ_from_the_default_are_covered(project):
    """Guards the test above from silently passing on a uniform config.

    If every pipeline happened to start with the same state name, the
    assertions would hold vacuously — which is exactly why this went unnoticed.
    """
    entries = {wf.entry_state for wf in project.workflows.values()}
    assert len(entries) >= 2, (
        f"all workflows start in the same state ({entries}) — this suite cannot "
        f"detect the bug it exists for"
    )
