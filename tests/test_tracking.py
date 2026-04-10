"""Tests for tracking comment parsing."""

from stokowski.tracking import (
    build_tracking_block,
    get_comments_since,
    get_last_tracking_timestamp,
    make_gate_payload,
    make_state_payload,
    parse_latest_tracking,
    parse_tracking_from_description,
)


class TestMakeStatePayload:
    def test_contains_state_and_run(self):
        payload = make_state_payload("implement", run=2)
        assert payload["state"] == "implement"
        assert payload["run"] == 2

    def test_default_run(self):
        payload = make_state_payload("triage")
        assert payload["run"] == 1

    def test_has_timestamp(self):
        payload = make_state_payload("implement")
        assert "timestamp" in payload

    def test_type_is_state(self):
        payload = make_state_payload("implement")
        assert payload["type"] == "state"


class TestMakeGatePayload:
    def test_waiting_status(self):
        payload = make_gate_payload("review", "waiting")
        assert payload["state"] == "review"
        assert payload["status"] == "waiting"

    def test_approved_status(self):
        payload = make_gate_payload("review", "approved")
        assert payload["status"] == "approved"

    def test_rework_status(self):
        payload = make_gate_payload("review", "rework", rework_to="implement", run=3)
        assert payload["rework_to"] == "implement"
        assert payload["run"] == 3

    def test_type_is_gate(self):
        payload = make_gate_payload("review", "waiting")
        assert payload["type"] == "gate"


class TestBuildTrackingBlock:
    def test_state_block(self):
        payload = make_state_payload("implement", run=1)
        block = build_tracking_block(payload)
        assert "<!-- stokowski:" in block
        assert '"state": "implement"' in block

    def test_roundtrip(self):
        payload = make_state_payload("implement", run=2)
        block = build_tracking_block(payload)
        parsed = parse_tracking_from_description(block)
        assert parsed is not None
        assert parsed["state"] == "implement"
        assert parsed["run"] == 2


class TestParseLatestTracking:
    def test_no_tracking(self):
        assert parse_latest_tracking("", [{"body": "just a comment"}]) is None

    def test_empty(self):
        assert parse_latest_tracking("", []) is None
        assert parse_latest_tracking("") is None

    def test_from_description(self):
        payload = make_state_payload("implement", run=1)
        desc = build_tracking_block(payload)
        result = parse_latest_tracking(desc)
        assert result is not None
        assert result["state"] == "implement"
        assert result["run"] == 1

    def test_legacy_comment_fallback(self):
        body = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->'
        result = parse_latest_tracking("", [{"body": body}])
        assert result is not None
        assert result["type"] == "state"
        assert result["state"] == "implement"

    def test_legacy_gate_overrides_state(self):
        state_body = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->'
        gate_body = '<!-- stokowski:gate {"state": "review", "status": "waiting", "run": 1, "timestamp": "2026-01-01T01:00:00+00:00"} -->'
        result = parse_latest_tracking("", [
            {"body": state_body},
            {"body": gate_body},
        ])
        assert result["type"] == "gate"
        assert result["state"] == "review"

    def test_legacy_latest_wins(self):
        body1 = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->'
        body2 = '<!-- stokowski:state {"state": "review-push", "run": 2, "timestamp": "2026-01-01T02:00:00+00:00"} -->'
        result = parse_latest_tracking("", [{"body": body1}, {"body": body2}])
        assert result["state"] == "review-push"
        assert result["run"] == 2

    def test_legacy_malformed_json_skipped(self):
        bad = '<!-- stokowski:state {not valid json} -->'
        good = '<!-- stokowski:state {"state": "work", "run": 1, "timestamp": "2026-01-01"} -->'
        result = parse_latest_tracking("", [{"body": bad}, {"body": good}])
        assert result["state"] == "work"


class TestGetLastTrackingTimestamp:
    def test_from_description(self):
        payload = make_state_payload("implement", run=1)
        desc = build_tracking_block(payload)
        ts = get_last_tracking_timestamp(desc)
        assert ts is not None

    def test_legacy_returns_latest(self):
        body1 = '<!-- stokowski:state {"state": "a", "run": 1, "timestamp": "2026-01-01T00:00:00"} -->'
        body2 = '<!-- stokowski:state {"state": "b", "run": 1, "timestamp": "2026-01-02T00:00:00"} -->'
        ts = get_last_tracking_timestamp("", [{"body": body1}, {"body": body2}])
        assert ts == "2026-01-02T00:00:00"

    def test_no_tracking_returns_none(self):
        assert get_last_tracking_timestamp("", [{"body": "hello"}]) is None


class TestGetCommentsSince:
    def test_filters_tracking_comments(self):
        comments = [
            {"body": "<!-- stokowski:state {} -->", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "Real feedback", "createdAt": "2026-01-02T00:00:00Z"},
        ]
        result = get_comments_since(comments, None)
        assert len(result) == 1
        assert result[0]["body"] == "Real feedback"

    def test_filters_by_timestamp(self):
        comments = [
            {"body": "Old comment", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "New comment", "createdAt": "2026-01-03T00:00:00Z"},
        ]
        result = get_comments_since(comments, "2026-01-02T00:00:00+00:00")
        assert len(result) == 1
        assert result[0]["body"] == "New comment"

    def test_no_since_returns_all_non_tracking(self):
        comments = [
            {"body": "Comment 1", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "Comment 2", "createdAt": "2026-01-02T00:00:00Z"},
        ]
        result = get_comments_since(comments, None)
        assert len(result) == 2
