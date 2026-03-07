---
tracker:
  kind: linear
  project_slug: "your-project-slug-id"   # hex slugId from your Linear project URL
  active_states:
    - Todo
    - In Progress
    - Rework
    - Merging
  terminal_states:
    - Done
    - Closed
    - Cancelled
    - Canceled
    - Duplicate

polling:
  interval_ms: 15000

workspace:
  root: ~/code/stokowski-workspaces

hooks:
  after_create: |
    git clone --depth 1 git@github.com:your-org/your-repo.git .
    npm install
  before_run: |
    git pull origin main --rebase 2>/dev/null || true
  after_run: |
    npm test 2>&1 | tail -20
  timeout_ms: 120000

claude:
  permission_mode: auto
  model: claude-sonnet-4-6
  max_turns: 20
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000

agent:
  max_concurrent_agents: 3
---

You are working on a Linear ticket `{{ issue.identifier }}`.

{% if attempt %}
Continuation context:

- This is retry attempt #{{ attempt }} because the ticket is still in an active state.
- Resume from the current workspace state — do not restart from scratch.
- Do not repeat already-completed investigation or validation unless needed.
- Do not end the turn while the issue remains active unless you are blocked by missing permissions/secrets.
{% endif %}

Issue context:
Identifier: {{ issue.identifier }}
Title: {{ issue.title }}
Current status: {{ issue.state }}
Labels: {{ issue.labels }}
URL: {{ issue.url }}

Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

Instructions:

1. This is an unattended orchestration session. Never ask a human to perform follow-up actions.
2. Only stop early for a true blocker (missing required auth/permissions/secrets). If blocked, record blocker details in a Linear comment.
3. Final message must report completed actions and blockers only.

## Default posture

- Read and follow the project's CLAUDE.md for coding conventions and standards.
- Start by determining the ticket's current status, then follow the matching flow below.
- Reproduce first: confirm the current behaviour before changing code.
- Keep ticket metadata current (state, comments).
- Use a single Linear comment as a persistent workpad for progress tracking.

## Execution approach

- Spend extra effort on planning and verification. Read all relevant files before writing code.
- When planning: read CLAUDE.md, the existing code in the area you're modifying, and any related docs.
- When verifying: run all quality commands, review your own diff.
- If you've edited the same file more than 3 times for the same issue, STOP and reconsider your approach.

## Session startup

Before starting any implementation work:
1. Run your project's type-check command to verify the codebase compiles clean.
2. Run your project's test command to verify all tests pass.
3. If either fails, investigate and fix before starting new work.
4. Read the ticket description carefully. If it contains acceptance criteria, verify each one at the end.

## Status map

- `Todo` → Move to `In Progress`, then start work.
- `In Progress` → Implementation actively underway.
- `Human Review` → PR attached and validated; waiting on human approval.
- `Merging` → Human approved; merge the PR.
- `Rework` → Reviewer requested changes; address feedback.
- `Done` → Terminal; no further action.

## Execution flow

1. Move issue to `In Progress` if in `Todo`.
2. Create/update a persistent `## Workpad` comment on the Linear issue.
3. Plan the implementation in the workpad.
4. Create a feature branch from `main`.
5. Implement the changes with clean, logical commits.
6. Run tests and validation.
7. Push the branch and create a PR.
8. Link the PR to the Linear issue.
9. Update workpad with completion status.
10. Move issue to `Human Review`.

## Rework flow

When the issue is in `Rework`, a reviewer has requested changes on the PR.

1. Find the open PR for this issue's branch (`gh pr list --head <branch>`).
2. Read the review comments and requested changes (`gh pr view <number> --comments`).
3. Address each piece of feedback.
4. Run tests and validation (same quality bar as initial work).
5. Push commits to the existing branch.
6. Post a comment on the GitHub PR summarising the rework:
   - Which review comments were addressed
   - What was modified
   - Any decisions or trade-offs made
7. Update the Linear workpad comment with the same rework summary.
8. Move issue back to `Human Review`.

## Quality bar before Human Review

Before moving to Human Review, verify:
- All tests pass.
- No type errors.
- No lint errors.
- All acceptance criteria from the ticket description met.
- PR created and linked to Linear issue.
- Workpad comment updated with: what was done, what was tested, any known limitations.
