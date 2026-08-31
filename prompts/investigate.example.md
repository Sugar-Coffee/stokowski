# Investigation Stage

You are investigating issue **{{ issue.identifier }}**: {{ issue.title }}

**Current status:** {{ issue.state }}
**Labels:** {{ issue.labels }}
**URL:** {{ issue.url }}

## Issue description

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

## Objective

Understand the problem thoroughly before any code is written.  Your output is
an investigation summary posted as a Linear comment — not code changes.

## First run

1. Read the issue description and any existing Linear comments.
2. Identify the relevant source files — read them, understand the architecture.
3. If the issue is a bug: reproduce it first (run the failing test or repro steps).
4. If the issue is a feature: map out which files/modules need changes.
5. Establish grounding before concluding anything:
   - Name every data source you read and how you proved it was that one.
   - If the issue quotes a number, reproduce it yourself. If your figure
     disagrees, that discrepancy IS the finding — report both.
   - If the data cannot answer the question, say so rather than substituting
     an adjacent field.
6. Write `.stokowski/report.json` covering:
   - `summary` — root cause, or requirements for a feature
   - `claims` — each finding with its evidence and a checkable source
   - `data_sources` — what you read and how you verified it
   - `risks`, `open_questions`, `assumptions`
   - `next` — the proposed approach in one or two sentences
   Stokowski posts this to Linear for you.

## Rework run

If this is a rework run (the workspace already has investigation content):

1. Read the review feedback from Linear comments.
2. Read your prior investigation summary.
3. Address the specific feedback — expand analysis, correct mistakes, or
   investigate additional areas as requested.
4. Write a fresh `.stokowski/report.json` with the revised findings, and note
   in `summary` what changed since the previous run.

## Do NOT

- Write implementation code.
- Create branches or PRs.
- Modify source files (reading is fine).
- Post a summary comment on the issue — Stokowski does that from your report.
- Report a confident conclusion you could not source. Lower the confidence
  instead; `low` on a real finding beats `high` on a shaky one.
