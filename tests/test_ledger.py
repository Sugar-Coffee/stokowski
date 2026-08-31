"""Tests for the run ledger.

The ledger exists to answer one question — does the agent's stated confidence
predict whether a human accepts the work — so the arithmetic behind that
answer is what these test. A summary that is subtly wrong is worse than no
summary, because decisions get made on it.
"""

from __future__ import annotations

import json

import pytest

from stokowski.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / ".stokowski" / "ledger.jsonl")


def stage(ledger, issue_id, *, classification=None, confidence=None,
          report=True, claims=None, state="investigate", **kw):
    body = None
    if report:
        body = {"classification": classification, "confidence": confidence,
                "claims": claims if claims is not None else []}
    ledger.record_stage(
        project="p", issue_id=issue_id, issue=issue_id.upper(), title="t",
        state=state, run=kw.pop("run", 1), status=kw.pop("status", "succeeded"),
        report=body, tokens=kw.pop("tokens", 1000), cost_usd=kw.pop("cost_usd", 1.0),
        tool_calls=kw.pop("tool_calls", 5), tool_errors=kw.pop("tool_errors", 0),
        artifacts=kw.pop("artifacts", 0), model="m", duration_s=kw.pop("duration_s", 10.0),
    )


def gate(ledger, issue_id, verdict, run=1):
    ledger.record_gate(project="p", issue_id=issue_id, issue=issue_id.upper(),
                       gate="research-review", verdict=verdict, run=run)


# ── Writing ──────────────────────────────────────────────────────────────────


def test_creates_its_directory_on_first_write(ledger):
    stage(ledger, "a")
    assert ledger.path.is_file()


def test_appends_rather_than_overwrites(ledger):
    for i in range(3):
        stage(ledger, f"issue-{i}")
    assert len(ledger.path.read_text().strip().splitlines()) == 3


def test_every_entry_is_timestamped(ledger):
    stage(ledger, "a")
    entry = json.loads(ledger.path.read_text().strip())
    assert entry["ts"].startswith("20")
    assert entry["event"] == "stage"


def test_an_unwritable_path_does_not_raise(tmp_path):
    """Losing a ledger line must never cost the run it describes."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    stage(Ledger(blocked / "ledger.jsonl"), "a")  # must not raise


# ── Reading ──────────────────────────────────────────────────────────────────


def test_a_torn_line_does_not_hide_the_rest(ledger):
    stage(ledger, "a")
    with ledger.path.open("a") as fh:
        fh.write('{"event": "stage", "issue_id": incomplete\n')
    stage(ledger, "b")
    assert len({e["issue_id"] for e in ledger.entries()}) == 2


def test_summary_of_an_absent_ledger_is_empty_not_an_error(tmp_path):
    s = Ledger(tmp_path / "nothing.jsonl").summarise()
    assert s["stages"] == 0 and s["gate_decisions"] == 0


# ── The numbers that matter ──────────────────────────────────────────────────


def test_approval_rate_by_confidence(ledger):
    """The question: is 'high' worth more than 'medium'?"""
    for i in range(8):
        stage(ledger, f"h{i}", classification="bug-fix", confidence="high")
        gate(ledger, f"h{i}", "approved" if i < 7 else "rework")
    for i in range(4):
        stage(ledger, f"m{i}", classification="bug-fix", confidence="medium")
        gate(ledger, f"m{i}", "approved" if i < 2 else "rework")

    by_conf = ledger.summarise()["by_confidence"]
    assert by_conf["high"] == {"approved": 7, "rework": 1, "total": 8, "approval_rate": 0.875}
    assert by_conf["medium"]["approval_rate"] == 0.5


def test_approval_rate_by_classification(ledger):
    for i in range(3):
        stage(ledger, f"b{i}", classification="bug-fix", confidence="high")
        gate(ledger, f"b{i}", "approved")
    stage(ledger, "i0", classification="improvement", confidence="high")
    gate(ledger, "i0", "rework")

    by_class = ledger.summarise()["by_classification"]
    assert by_class["bug-fix"]["approval_rate"] == 1.0
    assert by_class["improvement"]["approval_rate"] == 0.0


def test_attribution_uses_the_first_stage_that_classified(ledger):
    """A later stage's self-assessment is not independent of its own work.

    The verdict belongs to the framing the investigation set, not to whatever
    the implementation stage decided about itself afterwards.
    """
    stage(ledger, "x", classification="bug-fix", confidence="high", state="investigate")
    stage(ledger, "x", classification="improvement", confidence="low", state="implement")
    gate(ledger, "x", "approved")

    s = ledger.summarise()
    assert s["by_classification"]["bug-fix"]["approved"] == 1
    assert "improvement" not in s["by_classification"]
    assert s["by_confidence"]["high"]["approved"] == 1


def test_multiple_gates_on_one_issue_all_count(ledger):
    """Rework then approval is two data points, not one."""
    stage(ledger, "x", classification="bug-fix", confidence="high")
    gate(ledger, "x", "rework", run=1)
    gate(ledger, "x", "approved", run=2)

    bucket = ledger.summarise()["by_classification"]["bug-fix"]
    assert bucket == {"approved": 1, "rework": 1, "total": 2, "approval_rate": 0.5}


def test_work_with_no_classification_is_bucketed_not_dropped(ledger):
    stage(ledger, "x", report=False)
    gate(ledger, "x", "approved")
    s = ledger.summarise()
    assert s["by_classification"]["unclassified"]["approved"] == 1
    assert s["by_confidence"]["unstated"]["approved"] == 1


def test_unsourced_claims_are_counted(ledger):
    stage(ledger, "a", classification="bug-fix", claims=[
        {"claim": "x", "evidence": "e", "source": "f.ts:1"},   # sourced
        {"claim": "y", "evidence": "", "source": "f.ts:2"},    # no evidence
        {"claim": "z", "evidence": "e"},                       # no source
    ])
    s = ledger.summarise()
    assert s["unsourced_claims"] == 2


def test_missing_reports_are_counted(ledger):
    stage(ledger, "a", report=True, classification="bug-fix")
    stage(ledger, "b", report=False)
    assert ledger.summarise()["stages_without_report"] == 1


def test_cost_and_volume_totals(ledger):
    stage(ledger, "a", cost_usd=2.5, tokens=1_000_000)
    stage(ledger, "b", cost_usd=1.5, tokens=500_000)
    s = ledger.summarise()
    assert s["total_cost_usd"] == 4.0
    assert s["total_tokens"] == 1_500_000
    assert s["cost_per_stage"] == 2.0
    assert s["issues"] == 2


def test_an_undecided_gate_verdict_is_ignored(ledger):
    stage(ledger, "x", classification="bug-fix", confidence="high")
    gate(ledger, "x", "escalated")
    assert ledger.summarise()["gate_decisions"] == 0


def test_terminal_states_are_tallied(ledger):
    for i in range(3):
        ledger.record_terminal(project="p", issue_id=f"d{i}", issue="D", state="Done")
    ledger.record_terminal(project="p", issue_id="c0", issue="C", state="Canceled")
    assert ledger.summarise()["terminal"] == {"Done": 3, "Canceled": 1}


def test_path_resolution(tmp_path):
    wf = tmp_path / "workflow.yaml"
    assert Ledger.for_workflow(wf).path == tmp_path / ".stokowski" / "ledger.jsonl"
    assert Ledger.for_workflow(wf, "logs/l.jsonl").path == tmp_path / "logs" / "l.jsonl"
    assert Ledger.for_workflow(wf, "/abs/l.jsonl").path.as_posix() == "/abs/l.jsonl"


# ── Construction ─────────────────────────────────────────────────────────────


def test_orchestrator_construction_does_not_touch_config(tmp_path):
    """Regression: the ledger once read `self.cfg` in `__init__`.

    `cfg` asserts the workflow is loaded, which it is not until the first
    tick — so every orchestrator raised AssertionError on startup. Unit tests
    passed and `--dry-run` passed, because neither constructs an Orchestrator.
    """
    from stokowski.orchestrator import Orchestrator

    orch = Orchestrator(workflow_path=tmp_path / "workflow.yaml")
    assert orch.ledger.path == tmp_path / ".stokowski" / "ledger.jsonl"
    assert orch.workflow is None  # still unloaded, as at real startup
