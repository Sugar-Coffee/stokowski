# Create Ticket

Guide the user through creating a well-structured Linear ticket with acceptance criteria and implementation context, ready for agent execution.

## Process

### Step 1: Create or fetch the ticket

Ask the user:
> Do you already have a Linear ticket for this? If so, what's the identifier? (e.g., ENG-42)
> If not, I'll create one for you once we've gathered the details.

If they provide an identifier, use the Linear MCP tool to fetch the current ticket details (title, description, labels, priority). If not, continue to Step 2 — we'll create the ticket at the end.

### Step 2: Understand the goal

If the ticket already has a description, read it and summarise your understanding back to the user. Ask if anything is missing.

If the ticket has no description or a minimal one, ask these questions one at a time (wait for each answer before asking the next):

1. **What are we building?** — Describe the feature, fix, or change in one sentence.
2. **Why?** — What problem does this solve or what value does it add?
3. **Where in the codebase?** — Which areas, packages, or apps are affected?
4. **Are there designs?** — Figma links, screenshots, or visual references?
5. **Are there dependencies?** — Does this depend on other tickets? Any API changes needed?
6. **What's out of scope?** — What is explicitly NOT included in this ticket?

### Step 3: Research and context

Based on the answers, do targeted research in the codebase:
- Read relevant existing code that will be modified
- Check for related documentation, specs, or decision records
- Look at similar completed work for patterns to follow

Summarise what you found and confirm the approach with the user.

### Step 4: Generate acceptance criteria

Based on the conversation, generate a structured acceptance criteria JSON block:

```json
{
  "criteria": [
    { "description": "Description of what must be true", "verified": false },
    { "description": "Another requirement", "verified": false }
  ]
}
```

Guidelines for good criteria:
- Each criterion is independently verifiable — one thing, not compound statements
- Include both functional requirements (what it does) and quality requirements (tests, types, architecture)
- Always include: typecheck/build passes with no errors
- Always include: all existing tests pass
- If UI changes: include design accuracy and accessibility criteria
- If new logic: include "Unit tests cover the new logic"
- Prefer "X renders correctly at mobile breakpoint" over "X looks good" — be specific

Present the criteria to the user for review. Add, remove, or modify based on feedback.

### Step 5: Generate the ticket description

Compose a structured ticket description in this format:

```markdown
## Summary
[One paragraph describing what this ticket delivers]

## Context
[Why this is needed, any relevant background]

## Scope
**In scope:**
- [list of things included]

**Out of scope:**
- [list of things explicitly excluded]

## Implementation Notes
- [Key files to modify]
- [Relevant patterns to follow]
- [Any technical considerations or gotchas]

## Acceptance Criteria
\`\`\`json
{
  "criteria": [
    { "description": "...", "verified": false }
  ]
}
\`\`\`

## References
- [Links to Figma, docs, related tickets, PRs]
```

### Step 6: Create or update the Linear ticket

Show the user the complete description and ask for approval. Once approved:

1. If the ticket already exists, use the Linear MCP tool to update its description with the generated content.
2. If no ticket exists yet, use the Linear MCP tool to create a new issue with the title, description, and any labels/priority discussed. Ask the user which team to create it in if not already clear.
3. Confirm the create/update was successful.
4. Report: "Ticket [identifier] is ready for agent execution. Move it to **Todo** when you want an agent to pick it up."

## Tips

- Keep criteria atomic — one thing per criterion
- Reference specific files and components when possible
- If the user mentions something that should be a separate ticket, note it but keep this ticket focused
- The acceptance criteria JSON block is machine-readable — Stokowski agents are instructed to verify each criterion before moving to Human Review