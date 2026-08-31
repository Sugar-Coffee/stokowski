# Diagnose

You are finding the root cause of **{{ issue.identifier }}**: {{ issue.title }}

**URL:** {{ issue.url }}

The reproduction stage has already made this bug happen on demand. Read its
report on the Linear issue before you start — it has the trigger, the
environment, and the boundary conditions.

## Objective

Trace the reported behaviour to the specific code that causes it. Output is a
diagnosis, not a fix.

## Process

1. Read the reproduction report. If you cannot reproduce it yourself from what
   it gives you, say so — that is a finding about the reproduction, and it
   matters more than proceeding on an unverified premise.
2. Work backwards from the symptom. Follow the actual execution path rather
   than the one the code appears to describe; comments and names go stale.
3. Find the specific line where behaviour diverges from intent, and be able to
   say *why* it diverges — a line number alone is a location, not a cause.
4. Check the boundary the reproduction stage found. Your explanation has to
   account for why the bug appears in one case and not the other. If it does
   not, your explanation is incomplete.
5. Look for siblings. The same mistake is rarely made once — grep for the
   pattern and report what else you find, whether or not it is in scope.
6. Propose the fix in prose. Say what should change and why, and name anything
   it risks breaking.

## Reporting

Write `.stokowski/report.json`.

- `classification`: `bug-fix`
- `claims`: the root cause traced to `file:line`, and the mechanism — each
  sourced to code you read, not inferred
- `data_sources`: which branch and commit you read
- `risks`: what the proposed fix could break
- `open_questions`: anything your explanation does not account for
- `verdict`: `complete` when you have a cause that explains the boundary
  condition, `blocked` when you do not
- `next`: the proposed fix in a sentence or two, and why it addresses the
  cause rather than the symptom. Rendered at the top of the comment.
- `next_steps`: ordered actions for the implementation stage — the file to
  change, the test to write first, anything to check before starting

If you found sibling occurrences, list them as claims even though they are out
of scope. Someone should know.

## Do NOT

- Write the fix. Describe it.
- Stop at a plausible cause. A plausible cause that does not explain the
  boundary condition is the wrong cause, and shipping a fix for it wastes the
  entire pipeline behind you.
