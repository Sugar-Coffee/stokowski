"""Tests for tracking comment parsing."""

from stokowski.tracking import (
    get_comments_since,
    get_last_tracking_timestamp,
    make_gate_comment,
    make_state_comment,
    parse_latest_tracking,
)


class TestMakeStateComment:
    def test_contains_machine_readable_part(self):
        comment = make_state_comment("implement", run=2)
        assert "<!-- stokowski:state" in comment
        assert '"state": "implement"' in comment
        assert '"run": 2' in comment

    def test_contains_human_readable_part(self):
        comment = make_state_comment("implement")
        assert "Entering state: **implement**" in comment


class TestMakeGateComment:
    def test_waiting_status(self):
        comment = make_gate_comment("review", "waiting", prompt="Check the PR")
        assert "<!-- stokowski:gate" in comment
        assert '"status": "waiting"' in comment
        assert "Check the PR" in comment

    def test_approved_status(self):
        comment = make_gate_comment("review", "approved")
        assert "approved" in comment

    def test_rework_status(self):
        comment = make_gate_comment("review", "rework", rework_to="implement", run=3)
        assert '"rework_to": "implement"' in comment
        assert "Returning to: **implement**" in comment
        assert "(run 3)" in comment

    def test_escalated_status(self):
        comment = make_gate_comment("review", "escalated")
        assert "Escalating" in comment


class TestParseLatestTracking:
    def test_no_tracking_comments(self):
        comments = [{"body": "just a regular comment"}]
        assert parse_latest_tracking(comments) is None

    def test_empty_comments(self):
        assert parse_latest_tracking([]) is None

    def test_single_state_comment(self):
        body = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->\n\nHuman text'
        result = parse_latest_tracking([{"body": body}])
        assert result is not None
        assert result["type"] == "state"
        assert result["state"] == "implement"
        assert result["run"] == 1

    def test_gate_overrides_state(self):
        state_body = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->'
        gate_body = '<!-- stokowski:gate {"state": "review", "status": "waiting", "run": 1, "timestamp": "2026-01-01T01:00:00+00:00"} -->'
        result = parse_latest_tracking([
            {"body": state_body},
            {"body": gate_body},
        ])
        assert result["type"] == "gate"
        assert result["state"] == "review"

    def test_latest_wins(self):
        body1 = '<!-- stokowski:state {"state": "implement", "run": 1, "timestamp": "2026-01-01T00:00:00+00:00"} -->'
        body2 = '<!-- stokowski:state {"state": "review-push", "run": 2, "timestamp": "2026-01-01T02:00:00+00:00"} -->'
        result = parse_latest_tracking([
            {"body": body1},
            {"body": body2},
        ])
        assert result["state"] == "review-push"
        assert result["run"] == 2

    def test_malformed_json_skipped(self):
        bad = '<!-- stokowski:state {not valid json} -->'
        good = '<!-- stokowski:state {"state": "work", "run": 1, "timestamp": "2026-01-01"} -->'
        result = parse_latest_tracking([{"body": bad}, {"body": good}])
        assert result["state"] == "work"


class TestGetLastTrackingTimestamp:
    def test_returns_latest(self):
        body1 = '<!-- stokowski:state {"state": "a", "run": 1, "timestamp": "2026-01-01T00:00:00"} -->'
        body2 = '<!-- stokowski:state {"state": "b", "run": 1, "timestamp": "2026-01-02T00:00:00"} -->'
        ts = get_last_tracking_timestamp([{"body": body1}, {"body": body2}])
        assert ts == "2026-01-02T00:00:00"

    def test_no_tracking_returns_none(self):
        assert get_last_tracking_timestamp([{"body": "hello"}]) is None


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
