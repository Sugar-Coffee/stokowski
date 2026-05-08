# Investigation Stage

You are investigating issue **{{ issue.identifier }}**: {{ issue.title }}

**Current status:** {{ issue.state }}
**Labels:** {{ issue.labels }}
**URL:** {{ issue.url }}

## Issue description

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

## Objective

Understand the problem thoroughly before any code is written.  Your output is
an investigation summary posted as a Linear comment — not code changes.

## First run

1. Read the issue description and any existing Linear comments.
2. Identify the relevant source files — read them, understand the architecture.
3. If the issue is a bug: reproduce it first (run the failing test or repro steps).
4. If the issue is a feature: map out which files/modules need changes.
5. Write a structured investigation summary:
   - **Root cause** or **Requirements** (depending on issue type)
   - **Affected files** with brief explanation of needed changes
   - **Risks or open questions**
   - **Proposed approach** (high-level, 3-5 bullet points)
6. Post the summary as a Linear comment titled `## Investigation`:
   ```
   gh issue comment {{ issue.identifier }} --body "$(cat <<'COMMENT'
   ## Investigation

   <root cause / requirements>

   ### Affected files
   - ...

   ### Proposed approach
   - ...

   <!-- END STOKOWSKI LIFECYCLE -->
   COMMENT
   )"
   ```
7. Update the workpad with investigation status.

## Rework run

If this is a rework run (the workspace already has investigation content):

1. Read the review feedback from Linear comments.
2. Read your prior investigation summary.
3. Address the specific feedback — expand analysis, correct mistakes, or
   investigate additional areas as requested.
4. Update the `## Investigation` comment (append rework note):
   ```
   gh issue comment {{ issue.identifier }} --body "$(cat <<'COMMENT'
   ## Investigation

   [Prior findings]

   ### Rework (Run N, YYYY-MM-DD)

   [Updated findings]

   <!-- END STOKOWSKI LIFECYCLE -->
   COMMENT
   )"
   ```
5. Append a rework section to the workpad.

## Do NOT

- Write implementation code.
- Create branches or PRs.
- Modify source files (reading is fine).
