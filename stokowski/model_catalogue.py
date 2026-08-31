"""Known agent models, grouped by provider.

Deliberately data rather than logic: model lineups change faster than this
project releases, so keeping the catalogue in one editable list means a new
model is a one-line edit rather than a code change.

The list is a convenience for the studio's dropdown, never a constraint. Any
value already in a workflow is preserved and offered alongside these, and the
studio accepts free text — an unlisted model is passed through to the CLI
untouched. A stale entry here can never stop an operator running a new model.
"""

from __future__ import annotations

# provider label -> ordered model ids, most capable first within each family.
MODELS: dict[str, list[str]] = {
    "Claude — current": [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-haiku-4-5",
    ],
    "Claude — previous": [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
    ],
    # Used by states with `runner: codex`. The Codex CLI takes `-m/--model`;
    # these are the ids it documents. Unlisted values still work.
    "Codex (OpenAI)": [
        "gpt-5-codex",
        "gpt-5",
        "o3",
    ],
}

# Reasoning effort, passed to the Claude CLI as --effort. Higher costs more and
# takes longer; the CLI defaults to high when unset.
EFFORT_LEVELS: list[str] = ["low", "medium", "high", "xhigh", "max"]


def catalogue(*, in_use: list[str] | None = None) -> list[dict[str, object]]:
    """Model groups for the studio, with anything already in use kept first.

    An operator running a model newer than this file must not see their choice
    silently vanish from the dropdown, so values found in the live config are
    surfaced in their own group rather than dropped.
    """
    known = {m for models in MODELS.values() for m in models}
    unknown = sorted({m for m in (in_use or []) if m and m not in known})

    groups: list[dict[str, object]] = []
    if unknown:
        groups.append({"label": "In this workflow", "models": unknown})
    groups.extend({"label": label, "models": models} for label, models in MODELS.items())
    return groups
