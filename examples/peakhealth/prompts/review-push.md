# Review & Push: {{ issue_identifier }}

You are reviewing the implementation of **{{ issue_identifier }}: {{ issue_title }}** before pushing.

This is a **fresh session** — you have no prior context. Review the changes objectively.

## Step 1: Understand What Was Done

```bash
git fetch origin main 2>/dev/null
git log --oneline origin/main..HEAD
git diff origin/main --stat
```

Read the Linear issue description (provided in lifecycle section below) to understand requirements.

## Step 2: Review the Diff

```bash
git diff origin/main
```

Check for:
- **Requirements alignment** — do the changes satisfy the acceptance criteria?
- **Code quality** — clean code, no dead code, no `any` types, proper error handling
- **i18n** — all user-facing text uses translations, both en/es updated
- **Tests** — changed code has test coverage
- **Security** — no hardcoded secrets, no SQL injection, proper auth checks
- **AI slop** — remove unnecessary comments, over-engineering, verbose variable names

## Step 3: Fix Issues

Fix any issues you found. Commit fixes:

```bash
git add <specific files>
git commit -m "refactor: address pre-push review feedback [{{ issue_identifier }}]"
```

## Step 4: Push & Create PR

First, push:

```bash
git push -u origin $(git branch --show-current)
```

Use `timeout: 300000` (5 min) — pre-push hooks run the full test suite.

Then create or update the PR:

```bash
BRANCH=$(git branch --show-current)
if gh pr view --json number >/dev/null 2>&1; then
  echo "PR already exists"
else
  gh pr create \
    --title "$(git log --format=%s -1)" \
    --body "## Summary

$(git log --oneline origin/main..HEAD | sed 's/^/- /')

## Linear Issue

Closes {{ issue_identifier }}
{{ issue_url }}" \
    --base main
fi
```

Report completion. The orchestrator will advance to the PR review stage.
