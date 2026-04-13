# Triage: {{ issue_identifier }}

Quickly evaluate whether **{{ issue_identifier }}: {{ issue_title }}** is actionable by an AI coding agent.

**This is a fast evaluation — spend minimal turns. Do NOT start implementing.**

## Step 1: Read the Issue

Read the issue description (in the lifecycle section below).

## Step 2: Evaluate Actionability

An issue is **NOT actionable** by AI when ANY of these apply:

- **Too vague** — description lacks enough detail to implement (e.g., "improve performance")
- **Needs human input** — requires design decisions, product direction, or user research
- **External access required** — needs Stripe dashboard, production database, third-party admin panel
- **Parent with sub-issues** — has child issues that should be worked on individually
- **Blocked by dependencies** — depends on unfinished work
- **Discovery/research** — needs human investigation before implementation
- **Requires production access** — database migrations on production, manual deploy steps

## Step 3: Decide

### If ACTIONABLE:

Report completion. The orchestrator will advance to the implementation stage.

### If NOT ACTIONABLE:

Include this marker in your response so the orchestrator moves it to Blocked:

```
STOKOWSKI:BLOCKED
```

Also explain WHY in plain text before the marker — this will be posted as a comment
on the Linear issue so the human knows what to do.

Example:
```
This issue requires design decisions that haven't been made yet. The description
mentions "redesign the settings page" but there are no mockups, acceptance criteria,
or specific requirements. A human needs to define what the new design should look like.

STOKOWSKI:BLOCKED
```
