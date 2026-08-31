"""Structured run reports.

The agent emits a machine-readable report; Stokowski renders the Linear
comment. Authorship sits here rather than in the prompt because a report the
model writes freely is a report it can quietly skip the awkward parts of — and
the awkward parts (what data did you actually look at, what did you assume) are
the ones that catch a confidently wrong run.

The rendering is deliberately unflattering to thin work. A claim with no
evidence is not omitted, it is printed with a warning marker next to it. An
absent report does not fall back silently — it says the report was missing.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("stokowski.report")

# Stamped on every rendered report. Reports are posted with the operator's API
# key, so Linear attributes them to whoever owns it — meaning an agent reading
# the thread sees its own previous output under a human's name and treats it as
# instruction. The marker lets `get_comments_since` recognise them as machine
# output; it shares the `stokowski:` prefix that filter already matches.
REPORT_MARKER = "<!-- stokowski:report -->"

# Where the agent is told to write its report, relative to the workspace root.
REPORT_PATH = Path(".stokowski") / "report.json"

# Fenced-block fallback, in case the agent puts the report in its final message
# instead of writing the file.
_FENCE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL
)

# Classification -> (Linear label name, colour). These become issue labels so a
# board can be filtered by what the agent decided the work actually was.
CLASSIFICATIONS: dict[str, tuple[str, str]] = {
    "bug-fix":       ("stokowski/bug-fix", "#d95f52"),
    "improvement":   ("stokowski/improvement", "#5b9cf6"),
    "prototype":     ("stokowski/prototype", "#a855f7"),
    "investigation": ("stokowski/investigation", "#e8b84b"),
    "chore":         ("stokowski/chore", "#8b8b80"),
    "docs":          ("stokowski/docs", "#4cba6e"),
}

CONFIDENCE_MARK = {"high": "●●●", "medium": "●●○", "low": "●○○"}

# A stage's own verdict on its work, rendered as the first thing a human sees.
# Deliberately a small shared vocabulary rather than per-stage wording: the
# person at a gate is answering the same question every time — ship it, send it
# back, or it is stuck — and should not have to learn a new dialect per stage.
VERDICTS: dict[str, tuple[str, str]] = {
    "approve":         ("✅", "Approve"),
    "stands-up":       ("✅", "Stands up"),
    "complete":        ("✅", "Complete"),
    "reproduced":      ("✅", "Reproduced"),
    "rework":          ("🔄", "Needs rework"),
    "needs-rework":    ("🔄", "Needs rework"),
    "request-changes": ("🔄", "Request changes"),
    "blocked":         ("⛔", "Blocked"),
    "cannot-verify":   ("⛔", "Cannot verify"),
    "not-reproducible":("⛔", "Not reproducible"),
}

# The agent's own confidence, applied as a label so a board can be filtered by
# it. Only worth anything once you can compare it against how the work was
# actually judged — see `stokowski --stats`.
CONFIDENCE_LABELS: dict[str, tuple[str, str]] = {
    "high":   ("stokowski/confidence-high", "#4cba6e"),
    "medium": ("stokowski/confidence-medium", "#e8b84b"),
    "low":    ("stokowski/confidence-low", "#d95f52"),
}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load(workspace_path: Path, result_text: str = "") -> dict[str, Any] | None:
    """Find the agent's report: the file first, then a fenced block."""
    path = workspace_path / REPORT_PATH
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
            logger.warning(f"Report at {path} is not an object")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read report at {path}: {e}")

    for match in _FENCE.finditer(result_text or ""):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        # Only treat it as a report if it looks like one, so an unrelated JSON
        # snippet in the summary is not mistaken for the contract.
        if isinstance(data, dict) and ("summary" in data or "claims" in data):
            return data

    return None


def discard(workspace_path: Path) -> None:
    """Remove a consumed report so the next stage cannot re-post it."""
    path = workspace_path / REPORT_PATH
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def classification_label(report: dict[str, Any] | None) -> tuple[str, str] | None:
    """Map a report's classification onto a Linear label name and colour."""
    if not report:
        return None
    raw = _text(report.get("classification")).lower().replace("_", "-")
    return CLASSIFICATIONS.get(raw)


def confidence_label(report: dict[str, Any] | None) -> tuple[str, str] | None:
    """Map a report's confidence onto a Linear label name and colour."""
    if not report:
        return None
    return CONFIDENCE_LABELS.get(_text(report.get("confidence")).lower())


def labels_for(report: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Every label a report earns."""
    return [l for l in (classification_label(report), confidence_label(report)) if l]


# ── Rendering ────────────────────────────────────────────────────────────────



def _render_recommendation(report: dict[str, Any]) -> list[str]:
    """The verdict, the reasoning, and what to do — before anything else.

    A gate reviewer is deciding one thing: approve, send back, or unblock. That
    decision was previously reachable only by reading past several tables to a
    `**Next:**` line at the bottom, which is exactly backwards — the conclusion
    should be readable without the evidence, and the evidence should be there
    for when the conclusion is surprising.

    Rendered as a blockquote so it reads as a distinct panel in Linear rather
    than as more prose.
    """
    verdict_raw = _text(report.get("verdict")).lower().replace("_", "-")
    icon, label = VERDICTS.get(verdict_raw, ("▶", verdict_raw.replace("-", " ").title()))
    recommendation = _text(report.get("next"))
    steps = [_text(s) for s in _as_list(report.get("next_steps")) if _text(s)]

    if not (verdict_raw or recommendation or steps):
        return []

    lines = ["> ## " + (f"{icon} {label}" if verdict_raw else "▶ Recommendation"), ">"]
    if recommendation:
        lines.append("> " + recommendation.replace("\n", "\n> "))
        lines.append(">")

    # The reasons behind the verdict, enumerated. A reviewer deciding at a gate
    # wants "rework, because of these three things" — scannable, not a
    # paragraph they have to parse the argument out of. The full reasoning
    # still sits below; this is the version you can read in ten seconds.
    points = [_text(k) for k in _as_list(report.get("key_points")) if _text(k)]
    if points:
        lines.append("> **Why**")
        lines.append(">")
        lines += [f"> - {point}" for point in points]
        lines.append(">")

    if steps:
        lines.append("> **Next steps**")
        lines.append(">")
        lines += [f"> {i}. {step}" for i, step in enumerate(steps, 1)]
        lines.append(">")

    # A trust signal beside the recommendation: how sure the agent is, and how
    # much of what it said it actually sourced. Both bear on whether to accept
    # the verdict without reading further.
    signals = []
    classification = _text(report.get("classification"))
    if classification:
        signals.append(f"`{classification}`")
    confidence = _text(report.get("confidence")).lower()
    if confidence:
        signals.append(f"confidence {CONFIDENCE_MARK.get(confidence, '')} {confidence}")

    claims = [c for c in _as_list(report.get("claims")) if isinstance(c, dict)]
    unsourced = sum(
        1 for c in claims
        if not (_text(c.get("evidence")) and _text(c.get("source")))
    )
    if claims:
        noun = "claim" if len(claims) == 1 else "claims"
        signals.append(
            f"⚠️ {unsourced} of {len(claims)} {noun} unsourced" if unsourced
            else f"{len(claims)} {noun}, all sourced"
        )
    if signals:
        lines.append("> <sub>" + " · ".join(signals) + "</sub>")

    return [ln.rstrip() for ln in lines] + [""]


def _render_claims(claims: list) -> list[str]:
    """Claims table. Evidence is mandatory in presentation, not just in spirit."""
    rows = ["| Claim | Evidence | Source | Confidence |",
            "| --- | --- | --- | --- |"]
    for item in claims:
        if not isinstance(item, dict):
            continue
        claim = _text(item.get("claim")) or "—"
        evidence = _text(item.get("evidence"))
        source = _text(item.get("source"))
        confidence = _text(item.get("confidence")).lower()

        if not evidence:
            evidence = "⚠️ **no evidence given**"
        if not source:
            source = "⚠️ **unsourced**"

        mark = CONFIDENCE_MARK.get(confidence, "—")
        label = f"{mark} {confidence}" if confidence else mark
        rows.append(
            f"| {_cell(claim)} | {_cell(evidence)} | {_cell(source)} | {label} |"
        )
    return rows if len(rows) > 2 else []


def _cell(text: str) -> str:
    """Make a string safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _render_verification(checks: list) -> list[str]:
    rows = ["| Check | Result | Detail |", "| --- | --- | --- |"]
    for item in checks:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("check")) or "—"
        result = _text(item.get("result")).lower()
        icon = {"pass": "✅ pass", "fail": "❌ fail", "skip": "⏭️ skipped"}.get(
            result, result or "—"
        )
        rows.append(f"| {_cell(name)} | {icon} | {_cell(_text(item.get('detail')))} |")
    return rows if len(rows) > 2 else []


def _render_artifacts(artifacts: list, uploaded: dict[str, str]) -> list[str]:
    """Embed uploaded images inline; link anything else."""
    lines: list[str] = []
    captions = {
        _text(a.get("file")): _text(a.get("caption"))
        for a in artifacts if isinstance(a, dict)
    }

    for filename, url in uploaded.items():
        caption = captions.get(filename) or filename
        if _looks_like_image(filename):
            lines.append(f"**{caption}**")
            lines.append("")
            lines.append(f"![{caption}]({url})")
        else:
            lines.append(f"- [{caption}]({url})")
        lines.append("")
    return lines


def _looks_like_image(name: str) -> bool:
    return name.lower().endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif")
    )


def _bullets(title: str, items: list, marker: str = "-") -> list[str]:
    values = [_text(i) if isinstance(i, str) else _text(str(i)) for i in items]
    values = [v for v in values if v]
    if not values:
        return []
    return [f"### {title}", ""] + [f"{marker} {v}" for v in values] + [""]


def render(
    report: dict[str, Any] | None,
    *,
    state: str,
    run: int,
    uploaded: dict[str, str] | None = None,
    usage: dict[str, Any] | None = None,
    fallback_text: str = "",
) -> str:
    """Render the Linear comment for a completed stage."""
    uploaded = uploaded or {}
    out: list[str] = []

    out.append(REPORT_MARKER)
    out.append("")

    if report is None:
        out.append(f"## {state} — no structured report")
        out.append("")
        out.append(
            "The agent did not produce `.stokowski/report.json`, so there is no "
            "evidence table for this run. Treat the summary below as unverified."
        )
        out.append("")
        if fallback_text:
            out.append("> " + fallback_text.strip().replace("\n", "\n> ")[:4000])
            out.append("")
        out.extend(_render_footer(state, run, usage, uploaded))
        return "\n".join(out)

    headline = _text(report.get("headline"))
    classification = _text(report.get("classification"))
    confidence = _text(report.get("confidence")).lower()

    title = f"## {state}"
    if headline:
        title += f" — {headline}"
    out.append(title)
    out.append("")

    # The recommendation carries classification and confidence, so they are not
    # repeated as a separate tag line.
    recommendation = _render_recommendation(report)
    out.extend(recommendation)
    if not recommendation:
        tags = []
        if classification:
            tags.append(f"`{classification}`")
        if confidence:
            tags.append(f"confidence {CONFIDENCE_MARK.get(confidence, '')} {confidence}")
        if tags:
            out.append(" · ".join(tags))
            out.append("")

    summary = _text(report.get("summary"))
    if summary:
        out.append(summary)
        out.append("")

    # Data sources come first and deliberately so: the most expensive failure
    # mode is a well-argued conclusion drawn from the wrong place.
    sources = [s for s in _as_list(report.get("data_sources")) if isinstance(s, dict)]
    if sources:
        out.append("### Data sources")
        out.append("")
        out.append("| Source | How it was verified |")
        out.append("| --- | --- |")
        for source in sources:
            name = _text(source.get("name")) or "—"
            how = _text(source.get("how_verified")) or "⚠️ **not verified**"
            out.append(f"| {_cell(name)} | {_cell(how)} |")
        out.append("")

    claims = _render_claims([c for c in _as_list(report.get("claims"))])
    if claims:
        out.append("### Findings")
        out.append("")
        out.extend(claims)
        out.append("")

    changes = [c for c in _as_list(report.get("changes")) if isinstance(c, dict)]
    if changes:
        out.append("### Changes")
        out.append("")
        for change in changes:
            path = _text(change.get("file"))
            what = _text(change.get("what"))
            if path and what:
                out.append(f"- `{path}` — {what}")
            elif path or what:
                out.append(f"- {path or what}")
        out.append("")

    checks = _render_verification([c for c in _as_list(report.get("verification"))])
    if checks:
        out.append("### Verification")
        out.append("")
        out.extend(checks)
        out.append("")

    if uploaded:
        out.append("### Evidence")
        out.append("")
        out.extend(
            _render_artifacts(
                [a for a in _as_list(report.get("artifacts"))], uploaded
            )
        )

    out.extend(_bullets("Assumptions made", _as_list(report.get("assumptions"))))
    out.extend(_bullets("Risks", _as_list(report.get("risks"))))
    out.extend(_bullets("Open questions", _as_list(report.get("open_questions"))))

    preview = _text(report.get("preview_url"))
    if preview:
        out.append(f"**Preview:** {preview}")
        out.append("")

    out.extend(_render_footer(state, run, usage, uploaded))
    return "\n".join(out)


def _render_footer(
    state: str, run: int, usage: dict[str, Any] | None, uploaded: dict[str, str]
) -> list[str]:
    bits = [f"state `{state}`", f"run {run}"]
    if usage:
        tokens = usage.get("total_tokens")
        cost = usage.get("cost_usd")
        tools = usage.get("tool_calls")
        if tokens:
            bits.append(f"{tokens:,} tokens")
        if cost:
            bits.append(f"${cost:,.2f}")
        if tools:
            bits.append(f"{tools} tool calls")
    if uploaded:
        bits.append(f"{len(uploaded)} artifact{'s' if len(uploaded) != 1 else ''}")
    return ["---", "", f"<sub>Stokowski · {' · '.join(bits)}</sub>"]
