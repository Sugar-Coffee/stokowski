"""Three-layer prompt assembly for state machine workflows.

Assembles prompts from:
1. Global prompt — loaded from a .md file referenced in config
2. Stage prompt — loaded from the state's prompt .md file
3. Lifecycle injection — auto-generated from config + Linear data
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, Undefined

from .config import (
    LinearStatesConfig,
    ServiceConfig,
    StateConfig,
    global_prompt_paths,
)
from .models import Issue
from .tracking import get_comments_since, get_last_tracking_timestamp

log = logging.getLogger(__name__)


def load_prompt_file(path: str, workflow_dir: str | Path) -> str:
    """Load a .md prompt file relative to the workflow directory.

    Args:
        path: File path (absolute or relative to workflow_dir).
        workflow_dir: Directory containing the workflow file.

    Returns:
        The file contents as a string.

    Raises:
        FileNotFoundError: If the resolved path does not exist.
    """
    p = Path(path)
    if not p.is_absolute():
        p = Path(workflow_dir) / p
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"Prompt file not found: {p}")
    return p.read_text()


def render_template(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the given context.

    Uses a permissive undefined handler so missing variables render as
    empty strings rather than raising errors.
    """
    env = Environment(loader=BaseLoader(), undefined=_SilentUndefined)
    template = env.from_string(template_str)
    return template.render(**context)


class _SilentUndefined(Undefined):
    """Jinja2 undefined that renders as empty string instead of raising."""

    def __str__(self) -> str:
        return ""

    def __iter__(self) -> Any:
        return iter([])

    def __bool__(self) -> bool:
        return False

    def _fail_with_undefined_error(self, *args: Any, **kwargs: Any) -> Any:
        return _SilentUndefined()

    def __getattr__(self, name: str) -> _SilentUndefined:
        if name.startswith("_"):
            raise AttributeError(name)
        return _SilentUndefined()

    def __getitem__(self, name: str) -> _SilentUndefined:
        return _SilentUndefined()


def build_template_context(
    issue: Issue,
    state_name: str,
    run: int = 1,
    attempt: int = 1,
    last_run_at: str | None = None,
) -> dict[str, Any]:
    """Build the Jinja2 template context dict from issue and run metadata.

    Args:
        issue: The Linear issue being worked on.
        state_name: Internal state machine state name.
        run: Current run number for this state.
        attempt: Retry attempt within this run.
        last_run_at: ISO timestamp of the last run, if any.

    Returns:
        A flat dict suitable for Jinja2 rendering.
    """
    return {
        "issue_id": issue.id,
        "issue_identifier": issue.identifier,
        "issue_title": issue.title,
        "issue_description": issue.description or "",
        "issue_url": issue.url or "",
        "issue_priority": issue.priority,
        "issue_state": issue.state,
        "issue_branch": issue.branch_name or "",
        "issue_labels": issue.labels,
        "state_name": state_name,
        "run": run,
        "attempt": attempt,
        "last_run_at": last_run_at or "",
    }


def comment_author(comment: dict[str, Any]) -> str:
    """Who wrote a Linear comment.

    Comments were previously injected with no attribution at all, so a thread
    carrying direction from several people arrived as an undifferentiated wall
    of quotes. An agent then cannot tell the requester's instruction from a
    colleague's aside, or either from its own earlier comment — and will guess,
    typically by binding the quotes to whatever name it saw in the repo.
    """
    for key in ("user", "botActor", "externalUser"):
        actor = comment.get(key)
        if isinstance(actor, dict):
            name = actor.get("displayName") or actor.get("name")
            if name and str(name).strip():
                label = str(name).strip()
                return f"{label} (bot)" if key == "botActor" else label
    return "unknown author"


def format_comment(comment: dict[str, Any]) -> list[str]:
    """Render one comment as an attributed blockquote."""
    body = (comment.get("body") or "").strip()
    if not body:
        return []
    header = f"**{comment_author(comment)}**"
    created = (comment.get("createdAt") or "").strip()
    if created:
        header += f" · {created}"
    return [f"> {header}", ">"] + [f"> {line}" for line in body.splitlines()] + [""]


def build_lifecycle_section(
    issue: Issue,
    state_name: str,
    state_cfg: StateConfig,
    linear_states: LinearStatesConfig,
    run: int = 1,
    is_rework: bool = False,
    recent_comments: list[dict[str, Any]] | None = None,
) -> str:
    """Generate the auto-injected lifecycle section.

    This section is appended to every prompt to give the agent context about
    the current issue, state, and what actions to take when done.

    Args:
        issue: The Linear issue.
        state_name: Internal state machine state name.
        state_cfg: Configuration for the current state.
        linear_states: Linear state name mappings.
        run: Current run number.
        is_rework: Whether this is a rework run after gate rejection.
        recent_comments: Non-tracking comments since last run.

    Returns:
        A markdown string clearly demarcated as auto-generated.
    """
    lines: list[str] = []

    lines.append("---")
    lines.append("<!-- AUTO-GENERATED BY STOKOWSKI — DO NOT EDIT -->")
    lines.append("")
    lines.append("## Lifecycle Context")
    lines.append("")
    lines.append(f"- **Issue:** {issue.identifier} — {issue.title}")
    if issue.url:
        lines.append(f"- **URL:** {issue.url}")
    lines.append(f"- **State:** {state_name}")
    lines.append(f"- **Run:** {run}")
    lines.append("")

    # Rework information
    if is_rework:
        lines.append("### Rework")
        lines.append("")
        lines.append(
            "This is a **rework run**. A previous submission was reviewed "
            "and sent back for changes."
        )
        lines.append("")
        if recent_comments:
            lines.append("**Review comments:**")
            lines.append("")
            for comment in recent_comments:
                lines.extend(format_comment(comment))
        lines.append(
            "Address the feedback above before resubmitting."
        )
        lines.append("")

    # Recent activity (non-rework)
    if not is_rework and recent_comments:
        lines.append("### Recent Activity")
        lines.append("")
        for comment in recent_comments:
            lines.extend(format_comment(comment))

    # Available transitions
    if state_cfg.transitions:
        lines.append("### Transitions")
        lines.append("")
        for trigger, target in state_cfg.transitions.items():
            lines.append(f"- `{trigger}` → **{target}**")
        lines.append("")

    # Evidence + reporting contract. Stokowski, not the agent, writes the
    # Linear comment — a model asked to summarise its own work will reliably
    # produce something readable and unreliably produce something checkable.
    lines.extend(build_reporting_contract())

    lines.append("<!-- END STOKOWSKI LIFECYCLE -->")

    return "\n".join(lines)


# Written into every prompt. The report is a file rather than a section of the
# final message so it survives truncation and cannot blur into prose.
REPORT_SCHEMA = """{
  "classification": "bug-fix | improvement | prototype | investigation | chore | docs",
  "confidence": "high | medium | low",
  "headline": "one sentence, the single most important thing you found or did",
  "summary": "markdown prose; the argument, not a list of activities",
  "data_sources": [
    {"name": "what you read from, e.g. production read replica",
     "how_verified": "how you PROVED it was that source and not another"}
  ],
  "claims": [
    {"claim": "a specific factual assertion",
     "evidence": "the observation that supports it, with numbers where they exist",
     "source": "file:line, query, command, or URL a reader can check",
     "confidence": "high | medium | low"}
  ],
  "changes": [{"file": "path", "what": "what changed and why"}],
  "verification": [
    {"check": "the exact command run", "result": "pass | fail | skip", "detail": "output summary"}
  ],
  "artifacts": [{"file": "exact filename you wrote", "caption": "what it shows"}],
  "preview_url": "deployment preview URL for this branch, if one exists",
  "assumptions": ["decisions you made without being told, and why"],
  "risks": ["what could go wrong with this work"],
  "open_questions": ["what you could not resolve"],
  "verdict": "approve | stands-up | complete | reproduced | rework | request-changes | blocked | cannot-verify | not-reproducible",
  "next": "one or two sentences: the recommendation, stated plainly",
  "key_points": ["3-5 bullets: the reasons behind the verdict, or the caveats on it"],
  "next_steps": ["ordered, concrete actions someone could start on immediately"]
}"""


def build_reporting_contract() -> list[str]:
    """The evidence + report requirements appended to every agent prompt."""
    lines: list[str] = []

    lines.append("### Evidence")
    lines.append("")
    lines.append(
        "Write any screenshots, recordings or exported data into "
        "`$STOKOWSKI_ARTIFACTS` (also at `.stokowski/artifacts/` in this "
        "workspace). Stokowski uploads whatever is in there to the Linear "
        "issue and then deletes it."
    )
    lines.append("")
    lines.append(
        "Do NOT write evidence anywhere else in the repository. Files left "
        "outside that directory are not collected, are never seen by a human, "
        "and risk being committed."
    )
    lines.append("")
    lines.append(
        "If your work changes anything a person can see, capture it. A "
        "before/after pair is worth more than a paragraph describing one — "
        "name them `<thing>-before.png` and `<thing>-after.png` so they render "
        "as a pair, and shoot them at the same size and scroll position."
    )
    lines.append("")
    lines.append(
        "If your work claims an improvement to something measurable — bundle "
        "size, request count, query time, Lighthouse score — **measure it "
        "before and after and report both numbers**. An unmeasured performance "
        "claim is an opinion, and it will be reviewed as one."
    )
    lines.append("")
    lines.append(
        "If pushing the branch produces a deployment preview, put its URL in "
        "`preview_url`. It is the fastest review a human can do."
    )
    lines.append("")

    lines.append("### When Done")
    lines.append("")
    lines.append(
        "Write `.stokowski/report.json` in the workspace root. Stokowski reads "
        "it and posts the Linear comment for you — do NOT post a summary "
        "comment on the issue yourself, it will be duplicated."
    )
    lines.append("")
    lines.append("```json")
    lines.append(REPORT_SCHEMA)
    lines.append("```")
    lines.append("")
    lines.append(
        "Rules for the report, in order of how often they are broken:"
    )
    lines.append("")
    lines.append(
        "1. **Every claim needs a source a human can independently check.** "
        "A claim with an empty `evidence` or `source` is published with a "
        "warning marker beside it, so an unsupported assertion is worse than "
        "an omitted one."
    )
    lines.append(
        "2. **Name the data source and prove it.** State which database, "
        "environment, branch or file you actually read, and how you confirmed "
        "it was that one. Reading the wrong environment and reasoning "
        "perfectly from it is the most common way this work fails."
    )
    lines.append(
        "3. **Report the exact verification commands you ran and their real "
        "results.** Do not write `pass` for a check you did not run."
    )
    lines.append(
        "4. **Record what you assumed.** Anything you decided without being "
        "told belongs in `assumptions`, however obvious it felt."
    )
    lines.append(
        "5. **Lower your confidence when you are guessing.** `low` on a real "
        "finding is more useful than `high` on a shaky one."
    )
    lines.append(
        "6. **Lead with the recommendation.** `verdict`, `next`, `key_points` "
        "and `next_steps` render at the very top of the Linear comment, above "
        "everything else. Someone deciding at a gate reads only that, so those "
        "four fields have to carry the decision on their own — without them "
        "scrolling into your prose or your tables."
    )
    lines.append("")
    lines.append("   Each field does a different job, and they should not repeat each other:")
    lines.append("")
    lines.append(
        "   - `next` — one or two sentences. The recommendation itself, stated "
        "plainly. Not a summary of your work; the thing you want done."
    )
    lines.append(
        "   - `key_points` — 3 to 5 bullets, the reasons **behind** that "
        "recommendation. On a negative verdict these are the specific problems "
        "(\"the 12% figure came from staging, not production\"). On a positive "
        "one they are the reasons it is safe to proceed plus any caveats worth "
        "knowing (\"the fix is narrow, but the same null reaches two other call "
        "sites\"). Each bullet stands alone — a reader who sees only these "
        "should understand the verdict."
    )
    lines.append(
        "   - `next_steps` — ordered actions, each specific enough to start on: "
        "\"re-run the orphan join against the production replica\", not "
        "\"verify the data\"."
    )
    lines.append("")
    lines.append(
        "   Write these last, once you know what you found. They are a summary "
        "of your conclusion, not a plan you set out with."
    )
    lines.append("")

    return lines

def assemble_prompt(
    cfg: ServiceConfig,
    workflow_dir: str | Path,
    issue: Issue,
    state_name: str,
    state_cfg: StateConfig,
    run: int = 1,
    is_rework: bool = False,
    attempt: int = 1,
    last_run_at: str | None = None,
    comments: list[dict[str, Any]] | None = None,
    global_prompt: str | list[str] | None = None,
) -> str:
    """Orchestrate three-layer prompt assembly.

    Combines:
    1. Global prompt(s) (from config's prompts.global_prompt path or paths)
    2. Stage prompt (from state_cfg.prompt path)
    3. Lifecycle injection (auto-generated)

    Each layer is rendered as a Jinja2 template with the issue context.

    Args:
        cfg: The full service config.
        workflow_dir: Directory containing the workflow file.
        issue: The Linear issue.
        state_name: Internal state machine state name.
        state_cfg: Configuration for the current state.
        run: Current run number.
        is_rework: Whether this is a rework run.
        attempt: Retry attempt within this run.
        last_run_at: ISO timestamp of the last run.
        comments: All comments on the issue (for filtering).

    Returns:
        The fully assembled prompt string.
    """
    context = build_template_context(
        issue=issue,
        state_name=state_name,
        run=run,
        attempt=attempt,
        last_run_at=last_run_at,
    )

    parts: list[str] = []

    # Layer 1: Global prompt(s)
    # A workflow's own global prompt wins. This is the layer that removes
    # `if this is a bug…` branching from stage prompts: it states what kind of
    # work this is once, so a shared review prompt needs no conditional.
    #
    # A workflow may name several, loaded in order. A specialised global is a
    # supplement to the base one, and prose saying "everything in global.md
    # applies" is not — it names a file nothing loads, so those rules were
    # silently absent from the runs that cited them.
    for global_prompt_path in global_prompt_paths(
        global_prompt or cfg.prompts.global_prompt
    ):
        try:
            raw = load_prompt_file(global_prompt_path, workflow_dir)
            rendered = render_template(raw, context)
            parts.append(rendered)
        except FileNotFoundError:
            log.warning(
                "Global prompt file not found: %s", global_prompt_path
            )

    # Layer 2: Stage prompt
    if state_cfg.prompt:
        try:
            raw = load_prompt_file(state_cfg.prompt, workflow_dir)
            rendered = render_template(raw, context)
            parts.append(rendered)
        except FileNotFoundError:
            log.warning(
                "Stage prompt file not found for state '%s': %s",
                state_name,
                state_cfg.prompt,
            )

    # Layer 3: Lifecycle injection
    # Filter comments to recent non-tracking ones
    recent: list[dict[str, Any]] = []
    if comments:
        last_ts = get_last_tracking_timestamp(comments)
        recent = get_comments_since(comments, last_ts)

    lifecycle = build_lifecycle_section(
        issue=issue,
        state_name=state_name,
        state_cfg=state_cfg,
        linear_states=cfg.linear_states,
        run=run,
        is_rework=is_rework,
        recent_comments=recent,
    )
    parts.append(lifecycle)

    return "\n\n".join(parts)
