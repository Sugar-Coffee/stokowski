# Review & Push: {{ issue_identifier }}

Review the implementation of **{{ issue_identifier }}: {{ issue_title }}** before pushing.

This is a **fresh session** — review the changes objectively.

## Step 1: Understand What Was Done

```bash
git fetch origin main 2>/dev/null
git log --oneline origin/main..HEAD
git diff origin/main --stat
git diff origin/main
```

Read the issue description (in lifecycle section) to understand requirements.

## Step 2: Review

Check for:
- **Requirements** — do changes satisfy the acceptance criteria?
- **Code quality** — clean code, no dead code, proper error handling
- **Tests** — changed code has coverage
- **Security** — no hardcoded secrets, proper auth checks

## Step 3: Fix Issues

Fix any issues found. Commit:

```bash
git add <specific files>
git commit -m "refactor: address pre-push review feedback [{{ issue_identifier }}]"
```

## Step 4: Push & Create PR

```bash
git push -u origin $(git branch --show-current)
```

Use `timeout: 300000` for the push (pre-push hooks may run tests).

Then create the PR:

```bash
if gh pr view --json number >/dev/null 2>&1; then
  echo "PR already exists"
else
  gh pr create \
    --title "$(git log --format=%s -1)" \
    --body "## Summary

$(git log --oneline origin/main..HEAD | sed 's/^/- /')

## Linear Issue

Closes {{ issue_identifier }}
{{ issue_url }}"
fi
```

Report completion.
