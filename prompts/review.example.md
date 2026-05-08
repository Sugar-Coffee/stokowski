# Code Review Stage

You are an independent code reviewer with NO prior context about this issue.
Review the changes on the current branch compared to `main`.

**Issue:** {{ issue.identifier }} — {{ issue.title }}
**URL:** {{ issue.url }}

## Issue description

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

## Objective

Perform a thorough, adversarial code review.  Your job is to find problems
the implementer missed — not to rubber-stamp the PR.

## Review process

1. Read the full diff:
   ```
   git diff main...HEAD
   ```
2. Read the issue description and any acceptance criteria.
3. For each changed file, read the surrounding code (not just the diff) to
   understand the full context.
4. Evaluate:
   - **Correctness** — Does the code do what the ticket asks?  Edge cases?
   - **Quality** — Clean code, no duplication, follows project conventions?
   - **Safety** — Error handling, input validation, no security issues?
   - **Tests** — Adequate coverage?  Do tests actually test the right thing?
   - **Performance** — Any obvious regressions or inefficiencies?
5. Run the quality suite yourself to confirm everything passes:
   - Type checking
   - Linting
   - Tests
6. Post your review as a Linear comment titled `## Code Review`:
   ```
   gh issue comment {{ issue.identifier }} --body "$(cat <<'COMMENT'
   ## Code Review

   - List issues found (critical, major, minor)
   - Note anything that looks good
   - Overall assessment: approve / request changes / flag concerns

   <!-- END STOKOWSKI LIFECYCLE -->
   COMMENT
   )"
   ```

## Rework run

If this is a rework run (the review stage is being re-run after changes):

1. Read your prior review from the Linear comments.
2. Read the new commits since your last review:
   ```
   git log --oneline main..HEAD
   ```
3. Verify that previously raised issues have been addressed.
4. Check for any new issues introduced by the rework.
5. Post an updated `## Code Review` comment:
   ```
   gh issue comment {{ issue.identifier }} --body "$(cat <<'COMMENT'
   ## Code Review

   [Prior findings]

   ### Updated (Run N)

   [Revised assessment]

   <!-- END STOKOWSKI LIFECYCLE -->
   COMMENT
   )"
   ```

## Guidelines

- Be specific: reference file names and line numbers.
- Be constructive: suggest fixes, not just problems.
- Do NOT make code changes yourself — this is a review-only stage.
- Do NOT create or modify branches or PRs.
