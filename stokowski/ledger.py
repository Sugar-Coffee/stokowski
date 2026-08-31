"""Append-only record of what agents did and how it was judged.

Stokowski keeps no database — state lives in memory and is rebuilt from Linear
on restart. That is fine for scheduling and useless for the question that
actually matters once you are running dozens of tickets a week: is this
working, and for what?

Nothing else answers that. The Linear issue holds one run's report; it cannot
tell you that `bug-fix` work lands 90% of the time while `improvement` work
lands half the time, or whether the agent's own `high` confidence means
anything. Those are the numbers that decide how much review a class of ticket
needs, and they only exist if something writes them down as they happen.

The human verdict is taken from the gate decisions already in the workflow: an
approved gate is work a person accepted, a rework is work they sent back. No
extra rating step, no separate feedback UI — the judgement is already being
made, it was simply never recorded.

One JSON object per line, appended, never rewritten. A corrupt or partial line
is skipped on read rather than taking the summary down with it.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("stokowski.ledger")

DEFAULT_LEDGER_PATH = Path(".stokowski") / "ledger.jsonl"


class Ledger:
    """Append-only JSONL run log."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def for_workflow(cls, workflow_path: Path, configured: str | None = None) -> "Ledger":
        """Resolve the ledger location relative to the workflow file."""
        base = workflow_path.parent
        rel = Path(configured) if configured else DEFAULT_LEDGER_PATH
        return cls(rel if rel.is_absolute() else base / rel)

    # ── Writing ──────────────────────────────────────────────────────────

    def _append(self, event: dict[str, Any]) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        except OSError as e:
            # Losing a ledger line must never cost the run it describes.
            logger.warning(f"Could not append to ledger at {self.path}: {e}")

    def record_stage(
        self,
        *,
        project: str,
        issue_id: str,
        issue: str,
        title: str,
        state: str,
        run: int,
        status: str,
        report: dict[str, Any] | None,
        tokens: int,
        cost_usd: float,
        tool_calls: int,
        tool_errors: int,
        artifacts: int,
        model: str | None,
        duration_s: float | None,
    ) -> None:
        claims = report.get("claims") if isinstance(report, dict) else None
        claims = claims if isinstance(claims, list) else []

        # An unsourced claim is the signal that a stage produced assertion
        # rather than evidence. Counting them per run makes that trend visible.
        unsourced = sum(
            1 for c in claims
            if isinstance(c, dict) and not (
                str(c.get("evidence") or "").strip() and str(c.get("source") or "").strip()
            )
        )

        self._append({
            "event": "stage",
            "project": project,
            "issue_id": issue_id,
            "issue": issue,
            "title": title,
            "state": state,
            "run": run,
            "status": status,
            "classification": _field(report, "classification"),
            "confidence": _field(report, "confidence"),
            "headline": _field(report, "headline"),
            "has_report": report is not None,
            "claims": len(claims),
            "unsourced_claims": unsourced,
            "tokens": tokens,
            "cost_usd": round(cost_usd, 4),
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "artifacts": artifacts,
            "model": model,
            "duration_s": round(duration_s, 1) if duration_s is not None else None,
        })

    def record_gate(
        self, *, project: str, issue_id: str, issue: str, gate: str,
        verdict: str, run: int,
    ) -> None:
        """A human accepted or rejected the work. This is the ground truth."""
        self._append({
            "event": "gate", "project": project, "issue_id": issue_id,
            "issue": issue, "gate": gate, "verdict": verdict, "run": run,
        })

    def record_terminal(
        self, *, project: str, issue_id: str, issue: str, state: str,
    ) -> None:
        self._append({
            "event": "terminal", "project": project, "issue_id": issue_id,
            "issue": issue, "state": state,
        })

    # ── Reading ──────────────────────────────────────────────────────────

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        try:
            with self.path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn line must not hide the rest
        except OSError as e:
            logger.warning(f"Could not read ledger at {self.path}: {e}")

    def summarise(self) -> dict[str, Any]:
        """Correlate what the agent claimed against what humans accepted.

        Attribution rule: an issue's classification and confidence come from
        the FIRST stage that declared them — the investigation that framed the
        work — and gate verdicts are counted against that. A later stage's
        self-assessment is not independent of the work it just did.
        """
        first_claim: dict[str, dict[str, str | None]] = {}
        stages: list[dict[str, Any]] = []
        gates: list[dict[str, Any]] = []
        terminals: list[dict[str, Any]] = []

        for entry in self.entries():
            kind = entry.get("event")
            if kind == "stage":
                stages.append(entry)
                issue_id = entry.get("issue_id")
                if issue_id and issue_id not in first_claim and entry.get("classification"):
                    first_claim[issue_id] = {
                        "classification": entry.get("classification"),
                        "confidence": entry.get("confidence"),
                    }
            elif kind == "gate":
                gates.append(entry)
            elif kind == "terminal":
                terminals.append(entry)

        by_class: dict[str, dict[str, int]] = defaultdict(lambda: {"approved": 0, "rework": 0})
        by_confidence: dict[str, dict[str, int]] = defaultdict(lambda: {"approved": 0, "rework": 0})

        for gate in gates:
            verdict = gate.get("verdict")
            if verdict not in ("approved", "rework"):
                continue
            claim = first_claim.get(gate.get("issue_id"), {})
            by_class[claim.get("classification") or "unclassified"][verdict] += 1
            by_confidence[claim.get("confidence") or "unstated"][verdict] += 1

        def rate(bucket: dict[str, int]) -> dict[str, Any]:
            total = bucket["approved"] + bucket["rework"]
            return {
                **bucket,
                "total": total,
                "approval_rate": round(bucket["approved"] / total, 3) if total else None,
            }

        costed = [s for s in stages if s.get("cost_usd")]
        return {
            "stages": len(stages),
            "issues": len({s.get("issue_id") for s in stages if s.get("issue_id")}),
            "gate_decisions": len([g for g in gates if g.get("verdict") in ("approved", "rework")]),
            "total_cost_usd": round(sum(s.get("cost_usd") or 0 for s in stages), 2),
            "total_tokens": sum(s.get("tokens") or 0 for s in stages),
            "cost_per_stage": round(sum(s.get("cost_usd") or 0 for s in costed) / len(costed), 3) if costed else None,
            "stages_without_report": sum(1 for s in stages if not s.get("has_report")),
            "unsourced_claims": sum(s.get("unsourced_claims") or 0 for s in stages),
            "by_classification": {k: rate(v) for k, v in sorted(by_class.items())},
            "by_confidence": {k: rate(v) for k, v in sorted(by_confidence.items())},
            "terminal": dict(_count(t.get("state") for t in terminals)),
        }


def _field(report: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(report, dict):
        return None
    value = report.get(key)
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _count(values) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        if v:
            out[str(v)] += 1
    return out
