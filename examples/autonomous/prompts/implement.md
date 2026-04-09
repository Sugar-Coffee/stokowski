# Implement: {{ issue_identifier }}

Implement **{{ issue_identifier }}: {{ issue_title }}**.

## Step 1: Understand

Read the issue description (in the lifecycle section below). Extract acceptance
criteria, scope, and any constraints.

If this issue is NOT actionable by AI (needs human investigation, external service
access, or is too vague), post a comment explaining why and stop.

## Step 2: Explore

Search the codebase for related code. Understand existing patterns before writing.
Check for related test files and documentation.

## Step 3: Implement

1. Follow existing codebase conventions
2. Add/update tests for changed code
3. Keep changes focused

## Step 4: Commit

```bash
git add <specific files>
git commit -m "{type}({scope}): {description} [{{ issue_identifier }}]"
```

Let pre-commit hooks handle linting and formatting. If they fail, fix and retry.

## Step 5: Verify

Confirm the changes address the acceptance criteria. Report completion.
