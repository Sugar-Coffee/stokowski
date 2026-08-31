# Reproduce

You are reproducing the defect reported in **{{ issue.identifier }}**: {{ issue.title }}

**URL:** {{ issue.url }}

## Issue description

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

## Objective

Make the wrong behaviour happen on demand, or establish that you cannot. Do not
diagnose, and do not fix — a later stage does that, and it depends on this one
producing something it can trust.

## Process

1. Read the report and extract the claim: what did the reporter do, what did
   they expect, what happened instead? If any of those three is missing, note
   which and work with what you have.
2. Establish where you are. Which branch, which commit, which environment. Say
   so explicitly — a bug that reproduces on a stale branch and not on `main` is
   a different finding from one that reproduces on both.
3. Reproduce it. Prefer the smallest reliable trigger: a failing test beats a
   script, a script beats a sequence of UI clicks. Whatever you use, it must be
   repeatable by someone else without you present.
4. Establish the boundary. Once it reproduces, find what makes it stop —
   different input, different user state, an earlier commit. The edge of the
   bug is usually where its cause is visible.
5. Capture evidence. Error output, a failing test's output, or a screenshot in
   `$STOKOWSKI_ARTIFACTS` for anything visual.

## If it does not reproduce

Say so, plainly, and stop. This is a real outcome, not a failure.

Do **not** go looking for something else to fix. A ticket returned as "not
reproducible on `main` at `<commit>`, here is what I ruled out" is far more
useful than a speculative change to adjacent code, which costs a reviewer their
time and leaves the original report unanswered.

Report: what you tried, which environments and commits you tried it on, what
you ruled out, and what additional information would let someone try again.

## Reporting

Write `.stokowski/report.json`.

- `classification`: `bug-fix`
- `claims`: the reproduction steps and observed behaviour, each sourced to the
  command you ran or the file you read. If it did not reproduce, the claims are
  what you ruled out and how.
- `verification`: the exact commands, with their real output
- `data_sources`: branch, commit, and environment, with how you confirmed each
- `verdict`: `reproduced` or `not-reproducible`
- `next`: one short paragraph — the trigger if you reproduced it, or what you
  ruled out and what would help if you did not. Rendered at the top of the
  Linear comment; the next stage reads it first.
- `next_steps`: on `reproduced`, what the diagnosis stage should look at first.
  On `not-reproducible`, what information or access would let someone retry.

## Do NOT

- Diagnose the cause. Note a suspicion in `next` if you have one; do not chase it.
- Change any source file. A test that demonstrates the bug is fine and welcome.
- Report a reproduction you did not actually observe.
