# Implementation Stage

You are implementing the solution for **{{ issue.identifier }}**: {{ issue.title }}

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

Implement the solution, create a PR, and ensure it passes all quality checks.
This stage is NOT complete until an open PR exists and you can provide its URL.

## Non-negotiable completion gate

Before you consider this stage complete, you MUST prove:

1. The current branch is pushed to origin.
2. There is an open PR for the current branch.
3. You have the PR URL and number.

Use these commands as the final check:

```
git rev-parse --abbrev-ref HEAD
gh pr list --head <branch-name> --json number,url,state
```

If no open PR is found, create one immediately. Do not proceed to completion.
If PR creation fails because of auth/permission, post `## Blocked` to Linear with
the exact error and stop.

## First run

1. Read the investigation summary from the Linear comments.
2. Read the relevant source files identified in the investigation.
3. Create a feature branch from `main`:
   ```
   git checkout -b {{ issue.identifier | lower }}-<short-description>
   ```
4. Implement the changes with clean, logical commits.
5. Run the full quality suite:
   - Type checking
   - Linting
   - All tests
6. Fix any failures before proceeding.
7. Push the branch and create a PR:
   ```
   git push -u origin HEAD
   gh pr create --title "{{ issue.identifier }}: <concise title>" --body "<description>"
   ```
8. Link the PR to the Linear issue.
9. Verify PR exists and capture PR number + URL.
10. Update the workpad with: what was done, what was tested, PR number/URL, any known limitations.

## Rework run

If this is a rework run (a branch and PR already exist):

1. Find the existing PR:
   ```
   gh pr list --head <branch-name>
   ```
2. Read review comments and requested changes:
   ```
   gh pr view <number> --comments
   ```
3. Address each piece of feedback specifically.
4. Run the full quality suite again.
5. Push new commits to the existing branch (do not force-push).
6. Verify the existing PR still matches the current branch and is open.
7. Post a comment on the GitHub PR summarising the rework:
   - Which review comments were addressed
   - What was modified
   - Any decisions or trade-offs
8. Append a rework section to the Linear workpad, including PR number/URL.

## Quality bar

Before finishing, verify:

- [ ] All tests pass
- [ ] No type errors
- [ ] No lint errors
- [ ] All acceptance criteria from the ticket description met
- [ ] PR created (or updated) and linked to Linear issue
- [ ] PR number and URL recorded in the Linear workpad
- [ ] Workpad updated with completion summary

## Final output requirement for this stage

Your final stage completion summary must include:

- `PR: #<number>`
- `URL: <full-pr-url>`
- `Branch: <branch-name>`

If you cannot provide all three, this stage is not complete.
