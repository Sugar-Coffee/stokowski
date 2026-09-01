"""Comment ordering — the bug that made every gate a dead end after a restart.

Linear's `orderBy: createdAt` returns comments NEWEST first. Every consumer of
`parse_latest_tracking` keeps the last match it sees, so on the raw order it
resolved an issue to the FIRST tracking entry it ever had. A ticket parked at
`merge-review` read back as `investigate`, `type` was `state` rather than
`gate`, and the approve/rework handler skipped it in silence.
"""
from __future__ import annotations

import json

from stokowski.tracking import (
    get_last_tracking_timestamp,
    make_gate_comment,
    make_state_comment,
    parse_latest_tracking,
)


def _c(body: str, created: str) -> dict:
    return {"id": created, "body": body, "createdAt": created}


def _history() -> list[dict]:
    """A ticket that ran investigate → ground-check and parked at a gate."""
    return [
        _c(make_state_comment(state="investigate", run=1), "2026-09-01T10:14:50Z"),
        _c(make_state_comment(state="ground-check", run=1), "2026-09-01T10:41:56Z"),
        _c(make_gate_comment(state="research-review", status="waiting", run=1),
           "2026-09-01T10:55:15Z"),
    ]


def test_latest_tracking_is_the_gate_however_the_list_is_ordered():
    oldest_first = _history()
    newest_first = list(reversed(oldest_first))

    for label, comments in (("oldest-first", oldest_first),
                            ("newest-first", newest_first)):
        latest = parse_latest_tracking(comments)
        assert latest is not None, label
        assert latest["type"] == "gate", f"{label}: resolved to {latest}"
        assert latest["state"] == "research-review", f"{label}: resolved to {latest}"
        assert latest["status"] == "waiting", f"{label}: resolved to {latest}"


def test_last_tracking_timestamp_is_the_newest_however_ordered():
    # The helper reads the timestamp embedded in the tracking JSON, which the
    # make_* helpers stamp at call time — so compare against that, not createdAt.
    oldest_first = _history()
    embedded = [
        json.loads(c["body"].split("stokowski:")[1].split(" ", 1)[1].rsplit("-->", 1)[0].strip())["timestamp"]
        for c in oldest_first
    ]
    newest = max(embedded)

    for comments in (oldest_first, list(reversed(oldest_first))):
        assert get_last_tracking_timestamp(comments) == newest


def test_a_gate_ticket_never_resolves_back_to_its_first_stage():
    """The user-visible failure: rework at a gate did nothing at all."""
    latest = parse_latest_tracking(list(reversed(_history())))
    assert latest["state"] != "investigate", (
        "resolved to the first stage the issue ever ran — the gate handler "
        "requires type == 'gate' and would skip this ticket in silence"
    )
