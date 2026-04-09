# Fix PR Reviews & Merge: {{ issue_identifier }}

You are handling PR reviews and merging for **{{ issue_identifier }}: {{ issue_title }}**.

This is a **fresh session**. Find the PR and work through reviews until merged.

## Step 1: Find the PR

```bash
BRANCH=$(git branch --show-current)
PR_NUMBER=$(gh pr view --json number -q .number 2>/dev/null)
OWNER=$(gh repo view --json owner -q .owner.login)
REPO_NAME=$(gh repo view --json name -q .name)
echo "PR #$PR_NUMBER for branch $BRANCH"
```

## Step 2: Watch CI

**MANDATORY** — always wait for CI before proceeding:

```bash
gh pr checks $PR_NUMBER --watch --fail-fast --interval 30
```

Use `timeout: 600000` (10 min).

- If checks **pass** → Step 3
- If checks **fail** → read the failure, fix it, commit, push, go back to Step 2

## Step 3: Check Review Status

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
              nodes {
                id
                body
                path
                line
                author { login }
              }
            }
          }
        }
      }
    }
  }
' -f owner="$OWNER" -f repo="$REPO_NAME" -F pr=$PR_NUMBER
```

Also check for CodeRabbit review body (outside-diff-range comments):

```bash
gh api repos/$OWNER/$REPO_NAME/pulls/$PR_NUMBER/reviews \
  --jq '[.[] | select(.user.login == "coderabbitai")] | sort_by(.submitted_at) | last | .body // ""'
```

## Step 4: Fix Unresolved Threads

For each unresolved thread:
1. Read the comment and the code it refers to
2. Fix the issue in code
3. Reply to the thread explaining the fix
4. Commit: `git commit -m "fix: address review feedback [{{ issue_identifier }}]"`
5. Push: `git push` (timeout: 300000)

After pushing, go back to **Step 2** (watch CI again).

## Step 5: Resolve Threads & Merge

When `APPROVED` + 0 unresolved threads:

Resolve concluded threads:

```bash
gh api graphql -f query='
  mutation {
    resolveReviewThread(input: {threadId: "THREAD_ID"}) { thread { isResolved } }
  }'
```

Then merge — **do NOT confirm with user, merge automatically**:

```bash
gh pr merge $PR_NUMBER --squash --delete-branch
```

Verify:

```bash
gh pr view $PR_NUMBER --json state -q .state
# Should output: "MERGED"
```

## Step 6: Cleanup

```bash
# Return to main repo and clean up worktree
REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE_DIR=$(git rev-parse --git-common-dir 2>/dev/null)
BRANCH=$(git branch --show-current)

if [ "$WORKTREE_DIR" != "$(git rev-parse --git-dir)" ]; then
  MAIN_REPO=$(cd "$WORKTREE_DIR" && git rev-parse --show-toplevel 2>/dev/null || dirname "$WORKTREE_DIR")
  cd "$MAIN_REPO"
  git worktree remove "$REPO_ROOT" --force 2>/dev/null || true
  git branch -D "$BRANCH" 2>/dev/null || true
fi
```

Report completion. The issue will be moved to Done by the orchestrator.

## Iteration Limit

Max 10 iterations through the fix-push-review cycle. If still not mergeable after 10,
report the current status and stop.
