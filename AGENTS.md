You are an experienced, pragmatic software engineering AI agent. Do not over-engineer a solution when a simple one is possible. Keep edits minimal. If you want an exception to ANY rule, you MUST stop and get permission first.

# Using Superpowers

**ALWAYS invoke the `using-superpowers` skill before any response or action.**

Even if the user's request seems simple, invoke this skill first to establish proper workflow discipline:

```
Skill: using-superpowers
```

This skill determines HOW to approach every task and ensures you:
- Check for relevant skills before acting
- Follow the correct OpenSpec workflow (explore → propose → apply → archive)
- Never rationalize your way out of using skills

# Project Overview

**Stokowski** is a Python daemon that orchestrates autonomous coding agents (Claude Code, Codex) driven by Linear issues. It implements a configurable state machine workflow with human review gates, allowing issues to flow through multiple stages (investigate → implement → review → merge) with optional human approval at each gate.

## Goals

- Enable unattended agent execution for Linear issues through configurable state machines
- Provide clean separation between interactive Claude Code usage and autonomous agent workflows
- Support human-in-the-loop review gates with approve/rework transitions
- Offer real-time monitoring via terminal UI and optional web dashboard

## Technology Choices

- **Language**: Python 3.11+
- **Async Framework**: asyncio
- **HTTP Client**: httpx (for Linear GraphQL API)
- **CLI UI**: rich (terminal dashboard)
- **Templating**: jinja2 (for prompt assembly)
- **Web Dashboard**: FastAPI + uvicorn (optional)
- **External Dependencies**: Claude Code CLI or Codex CLI

# Reference

## Important Files

| File | Purpose |
|------|---------|
| `stokowski/main.py` | CLI entry point, keyboard handler, update checker |
| `stokowski/orchestrator.py` | Main poll loop, dispatch, reconciliation, retry logic |
| `stokowski/config.py` | workflow.yaml parser and typed config dataclasses |
| `stokowski/linear.py` | Linear GraphQL client (httpx async) |
| `stokowski/runner.py` | Claude Code CLI integration, NDJSON stream parser |
| `stokowski/prompt.py` | Three-layer prompt assembly (global + stage + lifecycle) |
| `stokowski/tracking.py` | State machine tracking via structured Linear comments |
| `stokowski/workspace.py` | Per-issue workspace lifecycle and hooks |
| `stokowski/web.py` | Optional FastAPI dashboard |
| `stokowski/models.py` | Domain models: Issue, RunAttempt, RetryEntry |
| `workflow.example.yaml` | Example state machine configuration |

## Directory Structure

```
stokowski/          # Main Python package
prompts/            # Example prompt templates (*.md)
examples/           # Documentation and examples
docs/               # Documentation assets
.github/workflows/  # CI/CD (release automation)
```

## Architecture

The orchestrator runs a continuous poll loop:

1. **Fetch**: Query Linear for issues in active states
2. **Reconcile**: Cancel agents for issues that moved to terminal states
3. **Dispatch**: Spawn agent subprocesses for eligible issues
4. **Retry**: Schedule continuations or exponential backoff retries

Each issue gets an isolated workspace directory. Agents run via `claude -p` subprocess with NDJSON output streaming.

# Essential Commands

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
```

## Running Stokowski

```bash
# Validate config without dispatching
stokowski --dry-run

# Run with verbose logging
stokowski -v

# Run with web dashboard
stokowski --port 4200
```

## Project Maintenance

```bash
# No test suite - validate via dry-run against real Linear project

# Install with web dependencies
pip install -e ".[web]"

# Clean Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
```

## CLI Commands (Runtime)

| Key | Action |
|-----|--------|
| `r` | Force reconciliation tick |
| `q` / `Ctrl+C` | Graceful shutdown |

# Patterns

## State Machine Configuration

Workflows are defined in `workflow.yaml` (not in this repo - operator creates it):

- **agent states**: Run Claude Code with configured prompt
- **gate states**: Pause for human review via Linear state changes
- **terminal states**: Issue complete, workspace cleaned up

## Three-Layer Prompt Assembly

Every agent turn receives:

1. **Global prompt** (shared preamble from `prompts/global.md`)
2. **Stage prompt** (state-specific from `prompts/{state}.md`)
3. **Lifecycle injection** (auto-generated: issue metadata, transitions, recent comments)

## Workspace Hooks

Configure in `workflow.yaml`:

- `after_create`: Runs once when workspace is created (e.g., `git clone`)
- `before_run`: Runs before each agent turn (e.g., `git fetch && git rebase`)
- `before_remove`: Runs before workspace cleanup

## Crash Recovery

State is recovered by parsing structured HTML comments on Linear issues:
- `<!-- stokowski-state:{...} -->` — State entry tracking
- `<!-- stokowski-gate:{...} -->` — Gate status tracking

# Anti-Patterns

## Don't Use `tty.setraw`

Use `tty.setcbreak()` instead. `setraw` disables `OPOST` output processing and causes Rich log lines to render diagonally.

## Don't Forget `--verbose` with `stream-json`

Claude Code requires `--verbose` when using `--output-format stream-json`. Without it, the command errors.

## Don't Omit `title` in Minimal Issue Constructions

`Issue(title=...)` is a required field. Even minimal Issue constructors (in `fetch_issues_by_states` and reconciliation defaults) must pass `title=""`.

## Don't Use Linear `nodes(ids:)` Query

The Linear API doesn't support this. Use `issues(filter: { id: { in: $ids } })` instead.

## Don't Monkey-Patch Uvicorn Signal Handlers After `serve()`

Patch `server.install_signal_handlers = lambda: None` before calling `serve()`, otherwise uvicorn hijacks SIGINT.

# Code Style

- Follow PEP 8
- Type hints on all public functions
- Async/await for I/O operations
- Dataclasses for configuration (see `config.py`)

# Test-Driven Development (TDD)

This project follows **strict TDD** for all code changes.

**Rule:** No production code without a failing test first.

Use the `test-driven-development` skill for complete methodology.

**Summary:** RED → GREEN → REFACTOR
1. Write a failing test first
2. Watch it fail for the right reason
3. Write minimal code to pass
4. Refactor while staying green

**The Iron Law:** Write code before the test? Delete it. Start over.

# OpenSpec Methodology

This project follows the **OpenSpec** methodology for all code changes (except light debugging).

## Workflow Overview

For complex requests or when requirements are unclear, start with exploration:

```
/openspec-explore ──► /openspec-propose ──► /openspec-apply-change ──► /openspec-archive-change
```

For straightforward changes, skip exploration:

```
/openspec-propose ──► /openspec-apply-change ──► /openspec-archive-change
```

## Available Skills

| Skill | When to Use |
|-------|-------------|
| `openspec-explore` | Think through ideas, investigate problems, clarify requirements before committing to a change |
| `openspec-propose` | Create a new change with complete artifacts (proposal, design, tasks) in one step |
| `openspec-apply-change` | Implement tasks from an existing change |
| `openspec-archive-change` | Finalize and archive a completed change |

## Prerequisite

The OpenSpec CLI must be installed to use these skills:

```bash
npm install -g @fission-ai/openspec@latest
```

## Usage

### Starting a New Change

```
/openspec-propose add-feature-x
```

This creates:
- `openspec/changes/add-feature-x/proposal.md` — what & why
- `openspec/changes/add-feature-x/design.md` — how
- `openspec/changes/add-feature-x/tasks.md` — implementation steps

### Implementing

```
/openspec-apply-change add-feature-x
```

Implements tasks from the change, updating checkboxes as you go.

### Archiving

```
/openspec-archive-change add-feature-x
```

Moves the completed change to `openspec/changes/archive/YYYY-MM-DD-add-feature-x/`.

## Rules

- **Always use OpenSpec** for feature work, refactoring, or architectural changes
- **Light debugging** may proceed without a formal change
- **Never implement directly** without going through the propose → apply workflow
- **Update artifacts** if implementation reveals design issues

## NEVER Bypass OpenSpec Skills

**This is absolute. No exceptions. No rationalizations.**

When an OpenSpec skill (`openspec-explore`, `openspec-propose`, `openspec-apply-change`, `openspec-archive-change`) applies to your task, you MUST use it. The skill's instructions override any default behavior, instinct, or "common sense" shortcut.

### What "Following the Skill" Means

1. **Read the skill completely** using the Skill tool - never assume you remember it
2. **Execute every step** exactly as the skill specifies
3. **Never improvise** - if the skill says "run X", you run X. Don't substitute with "similar" commands
4. **Never split execution** - complete the full workflow, don't partial-apply and "finish manually"

### Forbidden Actions

| Forbidden | Why | Correct Action |
|-----------|-----|----------------|
| Editing files directly instead of using `openspec-propose` | Bypasses artifact generation | Use the skill, let it create artifacts |
| Creating `proposal.md` manually | Skips skill validation & structure | Use `openspec-propose` to generate it |
| Implementing code before `openspec-apply-change` | Skips task tracking & verification | Wait for the apply phase |
| "I'll just do this one thing first" | Rationalization that leads to bypass | Stop. Use the skill. |

### The Golden Rule

**When in doubt about whether to use an OpenSpec skill: USE IT.**

Better to over-follow a process than to bypass it. If the skill turns out to be unnecessary, that's a minor inefficiency. If you bypass a necessary skill, that's a process violation that corrupts the workflow state.

# Commit and Pull Request Guidelines

## Commit Message Format

Use release commits for versioning:

```
Release vX.Y.Z
```

For regular commits, use:

```
type: description

[optional body]
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## Release Process

1. Create a release PR with title `Release vX.Y.Z`
2. Merge to `main`
3. The release workflow automatically:
   - Detects the release commit pattern
   - Creates a GitHub tag
   - Creates a GitHub release with PR body as notes

## Validation Before Committing

- Test against a real Linear project with a test ticket
- Run `stokowski --dry-run` to validate workflow.yaml
- Verify no breaking changes to state machine protocol

## Pull Request Requirements

- Describe the change and its motivation
- Note any breaking changes to config format or protocols
- Test against real Linear integration when possible
