<div align="center">

# Stokowski

**Autonomous Claude Code agents, orchestrated by Linear issues.**

A Python implementation of the [OpenAI Symphony](https://github.com/openai/symphony) workflow specification — adapted for [Claude Code](https://claude.ai/claude-code) and [Linear](https://linear.app).

[![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-22c55e)](LICENSE)
[![Claude Code](https://img.shields.io/badge/powered%20by-Claude%20Code-D97757?logo=anthropic&logoColor=white)](https://claude.ai/claude-code)
[![Linear](https://img.shields.io/badge/Linear-integrated-5E6AD2?logo=linear&logoColor=white)](https://linear.app)
[![Symphony Spec](https://img.shields.io/badge/spec-Symphony-black?logo=openai&logoColor=white)](https://github.com/openai/symphony)

*Named after Leopold Stokowski — the conductor who brought orchestral music to the masses.*

</div>

---

## What it actually does

You write a ticket in Linear. You move it to **Todo**. That's it — Stokowski handles everything else:

```
You move ticket to Todo
        │
        ▼
Stokowski picks it up, clones your repo into an isolated workspace
        │
        ▼
Claude Code reads your codebase, CLAUDE.md, and ticket description
        │
        ▼
Agent implements the feature — writes code, runs tests, fixes type errors
        │
        ▼
Agent opens a Pull Request on GitHub with a full description
        │
        ▼
Agent moves the ticket to Human Review and posts a workpad comment on the issue
        │
        ▼
You review the PR — approve it or request changes
        │
   ┌────┴─────┐
approved    changes requested
   │              │
   ▼              ▼
You move       You move ticket to Rework
to Merging          │
   │           Agent picks it up, addresses feedback, updates PR
   │                │
   ▼                ▼
Agent merges   Back to Human Review
the PR
   │
   ▼
Done ✓
```

Each agent runs in its own isolated git clone — multiple tickets can be worked in parallel without conflicts. Token usage, turn count, and last activity are tracked live in the terminal and web dashboard.

---

## How is this different from Emdash?

[Emdash](https://www.emdash.sh/) is a great product that solves a similar problem — it integrates with Linear and spins up Claude Code agents for your tickets. If you're evaluating both, here's an honest comparison.

**The core difference: separation of agent context from interactive context.**

When you work interactively with Claude Code in your repo, you rely on `CLAUDE.md` and your project's rule files to guide Claude's behaviour. The problem with putting your autonomous agent instructions in `CLAUDE.md` is that they bleed into your regular Claude Code sessions — your day-to-day interactive work now carries all the "you are running headlessly, never ask a human, follow this state machine" instructions that only make sense for an unattended agent.

Stokowski solves this with `WORKFLOW.md`. Your autonomous agent prompt — how to handle Linear states, what quality gates to run, how to structure PRs, what to do when blocked — lives entirely in `WORKFLOW.md` and is only injected into headless agent sessions. Your `CLAUDE.md` stays clean for interactive use.

```
Interactive session:    Claude reads CLAUDE.md        ← your normal instructions
Stokowski agent:        Claude reads CLAUDE.md         ← same conventions
                              +  WORKFLOW.md prompt    ← agent-only instructions
```

This separation lets you build a genuinely autonomous pipeline without compromising your day-to-day developer experience.

**Other differences:**

| | Stokowski | Emdash |
|---|---|---|
| Agent instructions | Separate `WORKFLOW.md` — doesn't affect interactive sessions | Applied via project rules, shared with interactive context |
| Prompt template | Full Jinja2 template with complete issue context | Managed by Emdash |
| Quality gate hooks | `before_run` / `after_run` shell scripts per turn | Not available |
| MCP servers | Any `.mcp.json` in your repo — Figma, iOS Simulator, Playwright, etc. | Emdash-managed integrations |
| Per-state concurrency | Configurable per Linear state | Not available |
| Cost | Your existing Claude subscription | Additional Emdash subscription |
| Open source | Yes — fork it, modify it, self-host it | Closed source SaaS |
| Maintenance | You maintain it | Emdash maintains it |

**When to choose Emdash:** You want a polished managed product, don't want to run infrastructure, and your workflow fits their model.

**When to choose Stokowski:** You want full control over the agent prompt and workflow, need the interactive/autonomous context separation, have specialised MCP tooling (Figma, iOS, etc.), or want to run quality gates on every turn.

---

## What is it?

[Symphony](https://github.com/openai/symphony) is OpenAI's open specification for autonomous coding agent orchestration: poll a tracker for issues, create isolated workspaces, run agents, manage multi-turn sessions, retry failures, and reconcile state. It ships with a Codex/Elixir reference implementation.

**Stokowski implements the same spec for Claude Code.** Point it at your Linear project and git repo, and agents autonomously pick up issues, write code, run tests, open PRs, and move tickets through your workflow — all while you do other things.

```
Linear issue → isolated git clone → claude -p → PR + Human Review → merge
```

### How it maps to Symphony

| Symphony | Stokowski |
|----------|-----------|
| `codex app-server` JSON-RPC | `claude -p --output-format stream-json` |
| `thread/start` → thread_id | First turn → `session_id` |
| `turn/start` on thread | `claude -p --resume <session_id>` |
| `approval_policy: never` | `--dangerously-skip-permissions` |
| `thread_sandbox` tools | `--allowedTools` list |
| Elixir/OTP supervision | Python asyncio task pool |

---

## Features

- **Linear-driven dispatch** — polls for issues in configured states, dispatches agents with bounded concurrency
- **Session continuity** — multi-turn Claude Code sessions via `--resume`; agents pick up where they left off
- **Isolated workspaces** — per-issue git clones so parallel agents never conflict
- **Lifecycle hooks** — `after_create`, `before_run`, `after_run`, `before_remove` shell scripts for setup, quality gates, and cleanup
- **Retry with backoff** — failed turns retry automatically with exponential backoff
- **State reconciliation** — running agents are stopped if their Linear issue moves to a terminal state mid-run
- **Web dashboard** — live view of agent status, token usage, and last activity at `localhost:<port>`
- **MCP-aware** — agents inherit `.mcp.json` from the workspace (Figma, Linear, iOS Simulator, Playwright, etc.)
- **Jinja2 prompt templates** — full issue context available in the agent prompt
- **Persistent terminal UI** — live status bar, single-key controls (`q` quit · `s` status · `r` refresh · `h` help)

---

## What Stokowski adds beyond Symphony

Beyond porting to Claude Code + Python, Stokowski ships several improvements over the reference implementation:

<details>
<summary><strong>Terminal experience</strong></summary>

- **Persistent command bar** — a live footer pinned at the bottom of the terminal showing agent count, token usage, and keyboard shortcuts; stays visible as logs scroll above it
- **Single-key controls** — `q` graceful shutdown · `s` status table · `r` force poll · `h` help. No Ctrl+C wrestling.
- **Graceful shutdown** — `q` kills all Claude Code subprocesses by process group before exiting, so you don't bleed tokens on orphaned agents

</details>

<details>
<summary><strong>Web dashboard</strong></summary>

- Live dashboard built with FastAPI + vanilla JS (no page reloads)
- Agent cards: turn count, token usage, last activity message, blinking live status pill
- Aggregate metrics: total tokens used, uptime, running/queued counts
- Auto-refreshes every 3 seconds

</details>

<details>
<summary><strong>Reliability</strong></summary>

- **Stall detection** — kills agents that produce no output for a configurable period, rather than waiting for the full turn timeout
- **Process group tracking** — child PIDs registered on spawn and killed via `os.killpg`, catching grandchild processes too
- **Interruptible poll sleep** — shutdown wakes the poll loop immediately; doesn't wait for the current interval to expire
- **Headless system prompt** — agents receive an appended system prompt disabling interactive skills, plan mode, and slash commands

</details>

<details>
<summary><strong>Configuration</strong></summary>

- **`.env` auto-load** — `LINEAR_API_KEY` loaded from `.env` on startup, no `export` needed
- **`$VAR` references** — any config value can reference an env var with `$VAR_NAME` syntax
- **Hot-reload** — `WORKFLOW.md` is re-parsed on every poll tick; config changes take effect without restart
- **Per-state concurrency limits** — cap concurrency per Linear state independently of the global limit

</details>

---

## Setup guide

> **Follow these steps in order.** Each one is required before Stokowski will work.

### 1. Install prerequisites

<details>
<summary><strong>Python 3.11+</strong></summary>

```bash
python3 --version  # must be 3.11 or higher
```

If not installed: [python.org/downloads](https://www.python.org/downloads/) or `brew install python` on macOS.

</details>

<details>
<summary><strong>Claude Code</strong></summary>

```bash
npm install -g @anthropic-ai/claude-code

# Verify and authenticate
claude --version
claude  # follow the login prompts if not already authenticated
```

</details>

<details>
<summary><strong>GitHub CLI — required for agents to open PRs</strong></summary>

```bash
# macOS
brew install gh

# Other platforms: https://cli.github.com

# Authenticate
gh auth login
# Choose: GitHub.com → HTTPS → Login with a web browser

# Verify
gh auth status
```

</details>

<details>
<summary><strong>SSH access to your repository</strong></summary>

Agents clone your repo over SSH. Verify it's working:

```bash
ssh -T git@github.com
# Should print: Hi username! You've successfully authenticated.
```

Not set up? [GitHub SSH key guide →](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)

</details>

---

### 2. Install Stokowski

```bash
git clone https://github.com/yourname/stokowski
cd stokowski

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[web]"     # installs core + web dashboard

stokowski --help             # verify it's working
```

---

### 3. Get your Linear API key

1. Open Linear → click your avatar (bottom-left) → **Settings**
2. Go to **Security & access** → **Personal API keys**
3. Click **Create key**, name it `stokowski`, and copy the value

---

### 4. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and paste your key:

```env
LINEAR_API_KEY=lin_api_your_key_here
```

`.env` is gitignored and auto-loaded on startup — it will never be committed.

---

### 5. Set up Linear workflow states

Stokowski uses a specific set of states to manage the agent ↔ human handoff. Linear includes basic states by default; you'll need to add a few custom ones.

**Recommended states:**

| State | Set by | Meaning |
|-------|--------|---------|
| `Todo` | Human | Ready for an agent to pick up |
| `In Progress` | Agent | Actively working |
| `Human Review` | Agent | PR opened, waiting for approval |
| `Rework` | Human | Changes requested — agent picks up again |
| `Merging` | Human | PR approved — agent will merge |
| `Done` | Auto | Complete (via GitHub integration) |
| `Cancelled` | Human | Abandoned |

**To add the custom states:**

1. Linear → **Settings** → **Teams** → your team → **Workflow**
2. Under **In Progress**, add:
   - `Human Review` · colour `#4ea7fc` (blue)
   - `Rework` · colour `#eb5757` (red)
   - `Merging` · colour `#e2b714` (amber)

> **Note:** State names are case-sensitive and must exactly match the `active_states` list in your `WORKFLOW.md`.

**The full lifecycle:**

```
Todo
 └─▶ [Agent] In Progress  →  Human Review
                                   │
                    ┌──────────────┴──────────────┐
               approved                    changes requested
                    │                             │
                    ▼                             ▼
                Merging                        Rework
                    │                             │
             [Agent] merges              [Agent] fixes, back to
                    │                        Human Review
                    ▼
                  Done
```

---

### 6. Configure your workflow

```bash
cp WORKFLOW.example.md WORKFLOW.md
```

Open `WORKFLOW.md` and update these fields:

**`tracker.project_slug`** — the hex ID at the end of your Linear project URL:

```
https://linear.app/your-team/project/my-project-abc123def456
                                                  ^^^^^^^^^^^^
                                              this part, not the name
```

**`hooks.after_create`** — how to clone your repo into a fresh workspace:

```yaml
hooks:
  after_create: |
    git clone --depth 1 git@github.com:your-org/your-repo.git .
```

**`tracker.active_states`** — must exactly match your Linear state names (case-sensitive).

**`agent.max_concurrent_agents`** — start with `1` or `2` while getting familiar.

`WORKFLOW.md` is gitignored — your config stays local.

---

### 7. Validate

```bash
source .venv/bin/activate   # if not already active
stokowski --dry-run
```

This connects to Linear, validates your config, and lists candidate issues — **without dispatching any agents**.

**Common errors:**

| Error | Fix |
|-------|-----|
| `Missing tracker API key` | Check `LINEAR_API_KEY` is in `.env` |
| `Missing tracker.project_slug` | Set `project_slug` in `WORKFLOW.md` |
| `Failed to fetch candidates` | Check your API key has access to the project |
| No issues listed | Check `active_states` matches your Linear state names exactly |

---

### 8. Run

```bash
# Terminal only
stokowski

# With web dashboard
stokowski --port 4200
```

Open `http://localhost:4200` for the live dashboard.

**Keyboard shortcuts:**

| Key | Action |
|-----|--------|
| `q` | Graceful shutdown — kills all agents, exits cleanly |
| `s` | Status table — running agents, token usage |
| `r` | Force an immediate Linear poll |
| `h` | Help |

---

## Configuration reference

<details>
<summary><strong>Full WORKFLOW.md schema</strong></summary>

```yaml
---
tracker:
  kind: linear                          # only "linear" supported
  project_slug: "abc123def456"          # hex slugId from your Linear project URL
  api_key: "$LINEAR_API_KEY"            # env var reference, or omit (uses LINEAR_API_KEY)
  active_states:                        # issues in these states are dispatched to agents
    - Todo
    - In Progress
  terminal_states:                      # issues in these states stop any running agent
    - Done
    - Cancelled
    - Canceled
    - Closed
    - Duplicate

polling:
  interval_ms: 15000                    # how often to poll Linear (default: 30000)

workspace:
  root: ~/code/stokowski-workspaces     # where per-issue directories are created

hooks:
  after_create: |                       # runs once when a new workspace is created
    git clone --depth 1 git@github.com:org/repo.git .
    npm install
  before_run: |                         # runs before each agent turn
    git pull origin main --rebase 2>/dev/null || true
  after_run: |                          # runs after each agent turn (quality gate)
    npm test 2>&1 | tail -20
  before_remove: |                      # runs before workspace is deleted
    echo "cleaning up"
  timeout_ms: 120000                    # hook timeout in ms (default: 60000)

claude:
  permission_mode: auto                 # "auto" = --dangerously-skip-permissions
                                        # "allowedTools" = scoped tool list below
  allowed_tools:                        # used only when permission_mode = allowedTools
    - Bash
    - Read
    - Edit
    - Write
    - Glob
    - Grep
  model: claude-sonnet-4-6             # optional model override
  max_turns: 20                         # max turns before giving up
  turn_timeout_ms: 3600000             # per-turn wall-clock timeout (default: 1h)
  stall_timeout_ms: 300000             # kill agent if silent for this long (default: 5m)
  append_system_prompt: |              # extra text appended to every agent's system prompt
    Always write tests for new code.

agent:
  max_concurrent_agents: 3             # global concurrency cap (default: 5)
  max_retry_backoff_ms: 300000         # max retry delay (default: 5m)
  max_concurrent_agents_by_state:      # optional per-state concurrency limits
    in progress: 2
    rework: 1
---

Your Jinja2 prompt template goes here.
Available: {{ issue.identifier }}, {{ issue.title }}, {{ issue.description }},
           {{ issue.state }}, {{ issue.labels }}, {{ issue.url }},
           {{ issue.priority }}, {{ issue.branch_name }}, {{ attempt }}
```

</details>

---

## Prompt template variables

The body of `WORKFLOW.md` is a [Jinja2](https://jinja.palletsprojects.com/) template. Every agent receives it rendered with:

| Variable | Description |
|----------|-------------|
| `{{ issue.identifier }}` | e.g. `ENG-42` |
| `{{ issue.title }}` | Issue title |
| `{{ issue.description }}` | Full issue description |
| `{{ issue.state }}` | Current Linear state |
| `{{ issue.priority }}` | `0` none · `1` urgent · `2` high · `3` medium · `4` low |
| `{{ issue.labels }}` | List of label names (lowercase) |
| `{{ issue.url }}` | Linear issue URL |
| `{{ issue.branch_name }}` | Suggested git branch name |
| `{{ issue.blocked_by }}` | List of `{id, identifier, state}` blockers |
| `{{ attempt }}` | Retry attempt number (`None` on first run) |

---

## MCP servers

Agents run with `cwd` set to the workspace (the cloned repo), so `.mcp.json` in the repo root is automatically picked up.

Example `.mcp.json` with Figma, Linear, Playwright, and iOS Simulator:

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "http://127.0.0.1:3845/mcp"
    },
    "linear": {
      "command": "npx",
      "args": ["-y", "@linear/mcp-server"],
      "env": { "LINEAR_API_KEY": "${LINEAR_API_KEY}" }
    },
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    },
    "ios-simulator": {
      "command": "npx",
      "args": ["-y", "@joshuarileydev/simulator-mcp"]
    }
  }
}
```

Playwright and iOS Simulator don't need MCP — agents can run `npx playwright test` and `xcrun simctl` directly via shell. MCP makes it more ergonomic.

---

## Writing good tickets for agents

The quality of an agent's output is directly proportional to the quality of the ticket it receives. A vague ticket produces vague work. A well-specified ticket with clear acceptance criteria produces work you can ship.

**A good ticket includes:**

- **Summary** — what is being built and why, in plain language
- **Scope** — what's in and explicitly what's out
- **Implementation notes** — key files, patterns to follow, technical constraints
- **Acceptance criteria** — a machine-readable JSON block the agent uses to self-verify before marking the ticket ready for review

### Acceptance criteria JSON

Agents are instructed to read the `criteria` block from the ticket description and verify each item before moving to Human Review. Use this format:

```json
{
  "criteria": [
    { "description": "The settings screen renders correctly on iOS and Android", "verified": false },
    { "description": "Tapping Save writes changes to the user profile API", "verified": false },
    { "description": "All existing tests pass", "verified": false },
    { "description": "No TypeScript errors", "verified": false }
  ]
}
```

Each criterion should be independently verifiable — one thing, not compound statements. Be specific: "renders correctly at 375px viewport" beats "looks good on mobile".

### Using Claude Code to write your tickets

The best way to write a well-structured ticket is to let Claude Code help you. The `examples/create-ticket.md` file in this repo is a Claude Code slash command that walks you through the process interactively — asking the right questions, researching the codebase, and generating a complete description with acceptance criteria.

**To use it, copy it into your project:**

```bash
mkdir -p .claude/commands
cp /path/to/stokowski/examples/create-ticket.md .claude/commands/create-ticket.md
```

Then in Claude Code, run:

```
/create-ticket
```

Claude will ask for your Linear ticket identifier, interview you about what needs to be built, research relevant code, draft the acceptance criteria with you, and post the finished description directly to Linear via MCP — ready for an agent to pick up.

---

## Getting the most out of Stokowski

Autonomous agents work best when the codebase they operate in is highly self-describing. The more an agent can read about conventions, known pitfalls, and expectations — the less it has to guess, and the better the output.

**Treat your `CLAUDE.md` and supporting rule files as a first-class engineering artefact.** A well-maintained instruction suite is a force-multiplier: agents follow conventions, avoid known mistakes, and produce work that needs less correction.

This is formalised in OpenAI's [Harness Engineering](https://openai.com/index/harness-engineering/) concept — building a rigid, self-healing, self-documenting instruction harness around your codebase so agents can operate autonomously with a low error rate.

**In practice this means:**

- A thorough `CLAUDE.md` covering architecture, conventions, and agent anti-patterns
- Rule files (e.g. `.claude/rules/agent-pitfalls.md`) for codebase-specific failure modes
- Acceptance criteria in ticket descriptions so agents can self-verify before moving to Human Review
- Quality gate hooks (`before_run`, `after_run`) that catch regressions each turn
- A `docs/build-log.md` agents are instructed to maintain — keeping the codebase self-documenting over time

---

## Architecture

```
WORKFLOW.md
  ├── YAML front matter  →  ServiceConfig
  └── Jinja2 body        →  prompt template
          │
          ▼
    Orchestrator  ──────────────────────▶  Linear GraphQL API
    (asyncio loop)                         fetch candidates
          │                                reconcile state
          │  dispatch (bounded concurrency)
          ▼
    Workspace Manager
    ├── after_create hook  →  git clone, npm install, etc.
    ├── before_run hook    →  git pull, typecheck, etc.
    └── after_run hook     →  tests, lint, etc.
          │
          ▼
    Agent Runner
    ├── claude -p --output-format stream-json
    ├── --resume <session_id>  (multi-turn continuity)
    ├── stall detection + turn timeout
    └── PID tracking for clean shutdown
          │
          ▼
    Claude Code (headless)
    reads code · writes code · runs tests · opens PRs
```

| File | Purpose |
|------|---------|
| `stokowski/config.py` | `WORKFLOW.md` parser, typed config dataclasses |
| `stokowski/linear.py` | Linear GraphQL client (httpx async) |
| `stokowski/models.py` | Domain models: `Issue`, `RunAttempt`, `RetryEntry` |
| `stokowski/orchestrator.py` | Poll loop, dispatch, reconciliation, retry |
| `stokowski/runner.py` | Claude Code CLI integration, stream-json parser |
| `stokowski/workspace.py` | Per-issue workspace lifecycle and hooks |
| `stokowski/web.py` | Optional FastAPI dashboard |
| `stokowski/main.py` | CLI entry point, keyboard handler |

---

## Security

- **`permission_mode: auto`** passes `--dangerously-skip-permissions` to Claude Code. Agents can execute arbitrary commands in the workspace. Only use in trusted environments or Docker containers.
- **`permission_mode: allowedTools`** scopes Claude to a specific tool list — safer for production.
- API keys are loaded from `.env` and never hardcoded. `.env` is gitignored.
- Each agent only has access to its own isolated workspace directory.

---

## License

[Apache 2.0](LICENSE)

---

## Credits

- [OpenAI Symphony](https://github.com/openai/symphony) — the spec and architecture Stokowski implements
- [Anthropic Claude Code](https://claude.ai/claude-code) — the agent runtime
