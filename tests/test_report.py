"""Tests for structured run reports.

Stokowski renders these, not the agent, so the rendering has to be reliably
unflattering: unsupported claims must be visible as unsupported rather than
quietly dropped, because a clean-looking summary of unverified work is the
failure mode this whole layer exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from stokowski import report


def render(data, **kw):
    kw.setdefault("state", "investigate")
    kw.setdefault("run", 1)
    return report.render(data, **kw)


# ── Loading ──────────────────────────────────────────────────────────────────


def test_loads_the_report_file(tmp_path):
    (tmp_path / ".stokowski").mkdir()
    (tmp_path / ".stokowski" / "report.json").write_text(
        json.dumps({"summary": "found it", "classification": "bug-fix"})
    )
    assert report.load(tmp_path)["summary"] == "found it"


def test_falls_back_to_a_fenced_block_in_the_result(tmp_path):
    text = 'Here is my report:\n```json\n{"summary": "from the message"}\n```\nDone.'
    assert report.load(tmp_path, text)["summary"] == "from the message"


def test_unrelated_json_in_the_summary_is_not_mistaken_for_a_report(tmp_path):
    text = 'The config was:\n```json\n{"port": 3000, "host": "localhost"}\n```'
    assert report.load(tmp_path, text) is None


def test_the_file_wins_over_the_message(tmp_path):
    (tmp_path / ".stokowski").mkdir()
    (tmp_path / ".stokowski" / "report.json").write_text('{"summary": "file"}')
    assert report.load(tmp_path, '```json\n{"summary": "message"}\n```')["summary"] == "file"


def test_malformed_report_file_does_not_raise(tmp_path):
    (tmp_path / ".stokowski").mkdir()
    (tmp_path / ".stokowski" / "report.json").write_text("{not json")
    assert report.load(tmp_path) is None


def test_missing_report_is_none(tmp_path):
    assert report.load(tmp_path) is None


def test_discard_removes_the_file(tmp_path):
    (tmp_path / ".stokowski").mkdir()
    path = tmp_path / ".stokowski" / "report.json"
    path.write_text("{}")
    report.discard(tmp_path)
    assert not path.exists()
    report.discard(tmp_path)  # idempotent


# ── The point of the whole exercise ──────────────────────────────────────────


def test_a_claim_without_evidence_is_flagged_not_hidden():
    out = render({"claims": [{"claim": "Users see a blank tile", "confidence": "low"}]})
    assert "Users see a blank tile" in out
    assert "no evidence given" in out
    assert "unsourced" in out


def test_an_unverified_data_source_is_flagged():
    out = render({"data_sources": [{"name": "staging db", "how_verified": ""}]})
    assert "not verified" in out


def test_a_supported_claim_carries_no_warning():
    out = render({"claims": [{
        "claim": "0.4% of enrollments are orphaned",
        "evidence": "318 of 79,412 rows",
        "source": "scripts/audit/orphan.sql:1-24",
        "confidence": "high",
    }]})
    assert "no evidence given" not in out
    assert "scripts/audit/orphan.sql:1-24" in out


def test_a_missing_report_says_so_rather_than_pretending():
    out = render(None, fallback_text="All done, looks good!")
    assert "no structured report" in out
    assert "unverified" in out
    assert "All done, looks good!" in out


def test_pipes_in_content_cannot_break_the_table():
    out = render({"claims": [{
        "claim": "a | b | c", "evidence": "x|y", "source": "f.ts", "confidence": "high",
    }]})
    table_rows = [ln for ln in out.splitlines() if ln.startswith("| a ")]
    assert len(table_rows) == 1
    row = table_rows[0]
    # Content pipes are escaped, so only the four cell delimiters are live.
    assert row.count("|") - row.count("\\|") == 5
    assert "a \\| b \\| c" in row


def test_newlines_in_content_cannot_break_the_table():
    out = render({"claims": [{
        "claim": "line one\nline two", "evidence": "e", "source": "s", "confidence": "high",
    }]})
    assert "| line one line two |" in out


# ── Rendering ────────────────────────────────────────────────────────────────


def test_images_embed_and_other_files_link():
    out = render(
        {"artifacts": [{"file": "shot.png", "caption": "The bug"},
                       {"file": "trace.json", "caption": "Trace"}]},
        uploaded={"shot.png": "https://u/1", "trace.json": "https://u/2"},
    )
    assert "![The bug](https://u/1)" in out
    assert "[Trace](https://u/2)" in out
    assert "![Trace]" not in out


def test_artifacts_without_a_caption_fall_back_to_the_filename():
    out = render({}, uploaded={"shot.png": "https://u/1"})
    assert "![shot.png](https://u/1)" in out


def test_verification_results_are_marked():
    out = render({"verification": [
        {"check": "pnpm test", "result": "pass", "detail": "412 passed"},
        {"check": "pnpm docs:check", "result": "fail", "detail": "stale"},
    ]})
    assert "✅ pass" in out and "❌ fail" in out


def test_footer_carries_the_real_cost():
    out = render({}, usage={"total_tokens": 2_377_260, "cost_usd": 13.54, "tool_calls": 26})
    assert "2,377,260 tokens" in out
    assert "$13.54" in out


def test_empty_sections_are_omitted():
    out = render({"summary": "just prose", "claims": [], "risks": [], "verification": []})
    for heading in ("### Findings", "### Risks", "### Verification", "### Evidence"):
        assert heading not in out
    assert "just prose" in out


def test_render_tolerates_garbage_field_types():
    out = render({
        "claims": "not a list", "verification": None, "artifacts": 42,
        "assumptions": [None, "", "real one"], "data_sources": ["bare string"],
        "summary": None, "classification": 7,
    })
    assert "real one" in out


# ── Classification -> label ──────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("bug-fix", "stokowski/bug-fix"),
    ("bug_fix", "stokowski/bug-fix"),
    ("IMPROVEMENT", "stokowski/improvement"),
    ("prototype", "stokowski/prototype"),
])
def test_classification_maps_to_a_label(value, expected):
    assert report.classification_label({"classification": value})[0] == expected


@pytest.mark.parametrize("data", [None, {}, {"classification": "nonsense"}])
def test_no_label_for_unknown_classification(data):
    assert report.classification_label(data) is None


# ── The recommendation block ─────────────────────────────────────────────────
#
# A gate reviewer is deciding one thing: approve, send back, or unblock. That
# decision has to be readable without scrolling into the evidence — the tables
# are for when the conclusion is surprising, not for reaching it.


def _first_lines(out: str, n: int = 12) -> str:
    return "\n".join(out.splitlines()[:n])


def test_the_recommendation_comes_before_any_evidence():
    out = render({
        "verdict": "needs-rework",
        "next": "The figure came from staging.",
        "next_steps": ["Re-run against production"],
        "summary": "Prose that should not come first.",
        "claims": [{"claim": "c", "evidence": "e", "source": "s"}],
        "data_sources": [{"name": "db", "how_verified": "checked"}],
    })
    rec = out.index("Needs rework")
    for later in ("Prose that should not come first", "### Findings", "### Data sources"):
        assert rec < out.index(later), f"{later} appears above the recommendation"


def test_the_verdict_is_rendered_as_a_heading_with_a_marker():
    out = render({"verdict": "approve", "next": "Ship it."})
    assert "> ## ✅ Approve" in out


@pytest.mark.parametrize("verdict,expected", [
    ("stands-up", "✅ Stands up"),
    ("request-changes", "🔄 Request changes"),
    ("cannot-verify", "⛔ Cannot verify"),
    ("not-reproducible", "⛔ Not reproducible"),
    ("needs_rework", "🔄 Needs rework"),   # underscores normalise
    ("REWORK", "🔄 Needs rework"),         # case normalises
])
def test_verdict_vocabulary(verdict, expected):
    assert expected in render({"verdict": verdict, "next": "x"})


def test_an_unknown_verdict_is_shown_rather_than_dropped():
    """Better a verdict we did not anticipate than a silently blank heading."""
    out = render({"verdict": "escalate-to-human", "next": "x"})
    assert "Escalate To Human" in out


def test_next_steps_render_as_an_ordered_list():
    out = render({"verdict": "rework", "next_steps": ["First thing", "Second thing"]})
    assert "> 1. First thing" in out
    assert "> 2. Second thing" in out


def test_the_block_carries_a_trust_signal():
    """How much of the verdict is actually sourced bears on accepting it."""
    out = render({
        "verdict": "approve", "next": "Looks right.",
        "classification": "bug-fix", "confidence": "high",
        "claims": [
            {"claim": "a", "evidence": "e", "source": "s"},
            {"claim": "b"},                       # unsourced
            {"claim": "c", "evidence": "e"},      # no source
        ],
    })
    assert "⚠️ 2 of 3 claims unsourced" in _first_lines(out)
    assert "confidence ●●● high" in _first_lines(out)


@pytest.mark.parametrize("n,expected", [(1, "1 claim, all sourced"), (2, "2 claims, all sourced")])
def test_the_trust_signal_pluralises(n, expected):
    out = render({"verdict": "approve", "next": "x",
                  "claims": [{"claim": "a", "evidence": "e", "source": "s"}] * n})
    assert expected in out


def test_a_fully_sourced_report_says_so():
    out = render({
        "verdict": "approve", "next": "x",
        "claims": [{"claim": "a", "evidence": "e", "source": "s"}],
    })
    assert "1 claim, all sourced" in out
    assert "unsourced" not in _first_lines(out)


def test_the_recommendation_is_not_repeated_at_the_bottom():
    """It used to live at the bottom; duplicating it is noise."""
    out = render({"verdict": "approve", "next": "Ship it."})
    assert out.count("Ship it.") == 1
    assert "**Next:**" not in out


def test_a_report_with_no_recommendation_still_renders():
    """Not every stage has a verdict; the block simply does not appear."""
    out = render({"classification": "chore", "confidence": "high", "summary": "Did a thing."})
    assert "Did a thing." in out
    assert "> ##" not in out
    # The tag line is the fallback when there is no block to carry them.
    assert "`chore`" in out


def test_classification_and_confidence_are_not_shown_twice():
    out = render({"verdict": "approve", "next": "x",
                  "classification": "bug-fix", "confidence": "high"})
    assert out.count("`bug-fix`") == 1


def test_multiline_recommendation_stays_inside_the_quote():
    """A bare newline would break out of the blockquote mid-panel."""
    out = render({"verdict": "rework", "next": "First line.\nSecond line."})
    assert "> Second line." in out


# ── Machine attribution ──────────────────────────────────────────────────────


def test_a_report_is_marked_as_machine_output():
    """Reports post under the operator's API key, so Linear attributes them to a
    human. Without a marker an agent reads its own previous output as
    instruction from the person it is working for."""
    from stokowski.report import REPORT_MARKER
    assert render({"verdict": "approve", "next": "x"}).startswith(REPORT_MARKER)
    assert render(None, fallback_text="x").startswith(REPORT_MARKER)


def test_reports_do_not_reach_the_agent_as_human_comments():
    from stokowski.tracking import get_comments_since

    kept = get_comments_since([
        {"body": render({"verdict": "approve", "next": "Ship it."}),
         "createdAt": "2026-08-31T10:00:00Z"},
        {"body": "Actually, check production.", "createdAt": "2026-08-31T11:00:00Z"},
    ], None)
    assert [c["body"] for c in kept] == ["Actually, check production."]
