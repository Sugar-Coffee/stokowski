# Example Workflows

These are ready-to-use workflow templates. Copy one to your project and customize.

## autonomous/

Fully autonomous implementation pipeline — no human gates:

```
implement → review & push → fix PR reviews → merge
```

Best for: bugs, improvements, tech debt, well-scoped tasks.

## feature-definition/

AI collaboration pipeline — multiple agents define a feature:

```
PM Agent → Engineer Agent → Architect Agent → sub-issues created
```

Best for: new features that need definition before implementation.

## Setup

1. Copy the example to your project:
   ```bash
   cp -r examples/autonomous/ your-project/.stokowski/
   ```

2. Edit `workflow.yaml`:
   - Set `workspace.repo_path` to your project path
   - Adjust `linear_states` to match your Linear workflow
   - Customize `claude.append_system_prompt` for your project
   - Adjust concurrency in `agent.max_concurrent_agents`

3. Set env vars:
   ```bash
   export LINEAR_API_KEY=lin_api_...
   export LINEAR_PROJECT_SLUG=abc123def456
   ```

4. Run:
   ```bash
   cd your-project
   stokowski .stokowski/workflow.yaml
   ```

## Customizing Prompts

Prompt templates in `prompts/` use Jinja2 variables:

| Variable | Description |
|----------|-------------|
| `{{ issue_identifier }}` | e.g., `DEV-123` |
| `{{ issue_title }}` | Issue title |
| `{{ issue_description }}` | Full description |
| `{{ issue_url }}` | Linear URL |
| `{{ issue_branch }}` | Suggested branch name |
| `{{ issue_labels }}` | Comma-separated labels |
| `{{ issue_priority }}` | Priority (1=Urgent, 4=Low) |
| `{{ state_name }}` | Current state machine state |
| `{{ run }}` | Run number (increments on rework) |
