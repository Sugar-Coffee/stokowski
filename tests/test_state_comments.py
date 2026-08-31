"""Tests for the entering-a-state comments Stokowski posts to Linear.

The thread is the operator's whole view of a run. Every duplicated comment is
noise between them and the report they actually need to read, and COG-368
managed three identical "Entering state: implement" comments in 1.7 seconds.

Two call sites post these — the transition into a state, and the worker that
picks that state up about a second later — and neither knew about the other.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


class FakeLinear:
    def __init__(self):
        self.comments: list[tuple[str, str]] = []

    async def post_comment(self, issue_id, body):
        self.comments.append((issue_id, body))
        return True


@pytest.fixture
def orchestrator(tmp_path):
    from stokowski.orchestrator import Orchestrator

    shutil.copy(REPO / "workflow.example.yaml", tmp_path / "workflow.yaml")
    shutil.copytree(REPO / "workflows", tmp_path / "workflows")
    shutil.copytree(REPO / "prompts", tmp_path / "prompts")

    orch = Orchestrator(workflow_path=tmp_path / "workflow.yaml")
    orch._load_workflow()
    orch.linear = FakeLinear()
    orch._ensure_linear_client = lambda: orch.linear
    return orch


def run(coro):
    return asyncio.run(coro)


def _issue(id_="i1", ident="ENG-1", labels=("bug",)):
    from stokowski.models import Issue
    return Issue(id=id_, identifier=ident, title="t", labels=list(labels))


def test_a_state_is_announced_once_however_many_times_it_is_entered(orchestrator):
    """The regression. Both call sites firing for the same state, plus a
    continuation re-entering the worker path, produced three comments."""
    issue = _issue()
    for _ in range(3):
        run(orchestrator._announce_state(issue, "implement", 1))
    assert len(orchestrator.linear.comments) == 1
    assert "implement" in orchestrator.linear.comments[0][1]


def test_a_rework_run_is_announced_again(orchestrator):
    """Going back around is real movement — the thread should show it."""
    issue = _issue()
    run(orchestrator._announce_state(issue, "implement", 1))
    run(orchestrator._announce_state(issue, "implement", 2))
    assert len(orchestrator.linear.comments) == 2
    assert "run 2" in orchestrator.linear.comments[1][1]


def test_each_state_is_announced_in_its_own_right(orchestrator):
    issue = _issue()
    for state in ("reproduce", "diagnose", "implement"):
        run(orchestrator._announce_state(issue, state, 1))
    assert len(orchestrator.linear.comments) == 3


def test_issues_do_not_suppress_each_other(orchestrator):
    """Keyed per issue, or the second ticket into a state goes unannounced."""
    run(orchestrator._announce_state(_issue("i1", "ENG-1"), "implement", 1))
    run(orchestrator._announce_state(_issue("i2", "ENG-2"), "implement", 1))
    assert len(orchestrator.linear.comments) == 2


def test_the_announcement_carries_the_workflow(orchestrator):
    """A restart recovers the pinned workflow from these comments, so the
    surviving one has to be the one that names it."""
    run(orchestrator._announce_state(_issue(labels=["bug"]), "reproduce", 1))
    body = orchestrator.linear.comments[0][1]
    assert "bug-fix" in body


def test_finishing_an_issue_forgets_it(orchestrator):
    """Without this the set grows for the life of the process, and an issue
    reopened later would never announce again."""
    issue = _issue()
    run(orchestrator._announce_state(issue, "implement", 1))
    orchestrator._announced_states = {
        k for k in orchestrator._announced_states if k[0] != issue.id
    }
    run(orchestrator._announce_state(issue, "implement", 1))
    assert len(orchestrator.linear.comments) == 2
