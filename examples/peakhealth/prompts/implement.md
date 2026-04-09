# Implement: {{ issue_identifier }}

You are implementing **{{ issue_identifier }}: {{ issue_title }}**.

## Step 1: Read Project Conventions

Read `CLAUDE.md` in the repo root. Follow all conventions.

## Step 2: Understand the Issue

The issue description is provided in the lifecycle section below. Read it carefully.
Extract: acceptance criteria, scope, labels, related code.

If this issue is NOT actionable by AI (needs human investigation, external service access,
design decisions not yet made, or is too vague), then:
1. Post a comment on the Linear issue explaining why
2. Stop — the orchestrator will handle the status update

## Step 3: Explore Codebase

Search for related code using Glob and Grep. Understand existing patterns before writing.
Read the relevant files. Check for:
- Existing patterns to follow
- Related test files
- i18n keys that might need updating
- Standards in `agent-os/standards/` if they exist

## Step 4: Implement

1. Make changes following existing codebase patterns
2. Use i18n for ALL user-facing text (both `en.json` and `es.json`)
3. Add/update tests for changed code
4. Keep changes focused — if you find unrelated issues, note them but don't fix them
5. Every new or modified UI component should have a `.stories.tsx` file

## Step 5: Commit

```bash
git add <specific files>
git commit -m "{type}({scope}): {description} [{{ issue_identifier }}]"
```

The pre-commit hook handles lint, format, type-check, and related tests.
If the hook fails, fix the issue and commit again.

## Step 6: Verify

After committing successfully, verify:
- [ ] Changes address the acceptance criteria
- [ ] i18n keys are consistent across locales (if applicable)
- [ ] No `any` types introduced
- [ ] Changes are wired into the app (not dead code)

Report completion when done. The orchestrator will advance to the review stage.
