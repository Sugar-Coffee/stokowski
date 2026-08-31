# Merge Stage

You are merging the approved PR for **{{ issue.identifier }}**: {{ issue.title }}

**URL:** {{ issue.url }}

## Objective

Merge the PR and move the issue to its terminal state.  This is a short,
mechanical stage — no new code changes.

## Process

1. Find the open PR for this issue:
   ```
   gh pr list --head <branch-name>
   ```
2. Verify the PR is approved and CI is passing:
   ```
   gh pr view <number> --json reviewDecision,statusCheckRollup
   ```
3. If CI is failing, investigate briefly.  If it is a flaky test or transient
   failure, re-run the checks.  If it is a real failure, post a comment on the
   Linear issue and stop.
4. Merge the PR using squash merge:
   ```
   gh pr merge <number> --squash --delete-branch
   ```
5. Write `.stokowski/report.json` confirming the merge: the PR number, the
   merge commit, and the CI result. Set `verdict` to `complete`, or `blocked`
   with what stopped you. Stokowski posts it.
6. Move the Linear issue to `Done`.

## Rework run

If this is a rework run (merge was attempted before but failed):

1. Check why the previous merge attempt failed (CI failure, merge conflict, etc.).
2. If there is a merge conflict:
   - Rebase the branch onto `main` and resolve conflicts.
   - Push the updated branch.
   - Wait for CI to pass, then merge.
3. If CI failed:
   - Read the failure logs.
   - If it is a test failure caused by the PR's changes, post details to
     Linear and stop (this needs to go back to implementation).
   - If it is a flaky or infrastructure issue, re-run and retry the merge.
4. Write `.stokowski/report.json` describing what happened and what fixed it.

## Do NOT

- Make code changes beyond conflict resolution.
- Open new PRs.
- Skip CI checks.
