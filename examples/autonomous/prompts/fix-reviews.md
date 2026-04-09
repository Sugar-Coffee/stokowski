# Fix PR Reviews & Merge: {{ issue_identifier }}

Handle PR reviews and merge for **{{ issue_identifier }}: {{ issue_title }}**.

This is a **fresh session**. Find the PR and iterate until merged.

## Step 1: Find the PR

```bash
PR_NUMBER=$(gh pr view --json number -q .number 2>/dev/null)
OWNER=$(gh repo view --json owner -q .owner.login)
REPO_NAME=$(gh repo view --json name -q .name)
```

## Step 2: Watch CI

```bash
gh pr checks $PR_NUMBER --watch --fail-fast --interval 30
```

Use `timeout: 600000` (10 min).

- Checks **pass** → Step 3
- Checks **fail** → fix, commit, push, repeat Step 2

## Step 3: Check Reviews

```bash
gh api graphql -f query='
  query($owner: String!, $repo: String!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        reviewDecision
        reviewThreads(first: 100) {
          nodes {
            id
            isResolved
            comments(first: 50) {
              nodes { id body path line author { login } }
            }
          }
        }
      }
    }
  }
' -f owner="$OWNER" -f repo="$REPO_NAME" -F pr=$PR_NUMBER
```

## Step 4: Fix Unresolved Threads

For each unresolved thread:
1. Read the comment and referenced code
2. Fix the issue
3. Reply explaining the fix
4. Commit and push

Go back to **Step 2** after pushing.

## Step 5: Merge

When `APPROVED` + 0 unresolved threads — merge automatically:

```bash
gh pr merge $PR_NUMBER --squash --delete-branch
```

Verify: `gh pr view $PR_NUMBER --json state -q .state` → `"MERGED"`

## Limits

Max 10 iterations. If still not mergeable, report status and stop.
