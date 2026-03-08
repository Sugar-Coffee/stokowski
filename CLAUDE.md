# Stokowski

Claude Code adaptation of [OpenAI's Symphony](https://github.com/openai/symphony). Orchestrates Claude Code agents via Linear issues.

This file is the single source of truth for contributors. It covers architecture, design decisions, key behaviours, and how to work on the codebase.

---

## What it does

Stokowski is a long-running Python daemon that:
1. Polls Linear for issues in configured active states
2. Creates an isolated git-cloned workspace per issue
3. Launches Claude Code (`claude -p`) in that workspace
4. Manages multi-turn sessions via `--resume <session_id>`
5. Retries failures with exponential backoff
6. Reconciles running agents against Linear state changes
7. Exposes a live web dashboard and terminal UI

The agent prompt, runtime config, and workspace setup all live in `WORKFLOW.md` in the operator's directory — not in this codebase.

---

## Package structure

```
stokowski/
  config.py        WORKFLOW.md parser + typed config dataclasses
  linear.py        Linear GraphQL client (httpx async)
  models.py        Domain models: Issue, RunAttempt, RetryEntry
  orchestrator.py  Main poll loop, dispatch, reconciliation, retry
  runner.py        Claude Code CLI integration, stream-json parser
  workspace.py     Per-issue workspace lifecycle and hooks
  web.py           Optional FastAPI dashboard
  main.py          CLI entry point, keyboard handler
  __main__.py      Enables python -m stokowski
```

---

## Key design decisions

### Claude Code CLI instead of Codex app-server
Symphony uses Codex's JSON-RPC `app-server` protocol over stdio. Stokowski uses Claude Code's CLI:
- First turn: `claude -p "<prompt>" --output-format stream-json --verbose`
- Continuation: `claude -p "<prompt>" --resume <session_id> --output-format stream-json --verbose`

`--verbose` is required for `stream-json` to work. `session_id` is extracted from the `result` event in the NDJSON stream.

### Python + asyncio instead of Elixir/OTP
Simpler operational story — single process, no BEAM runtime, no distributed concerns. Concurrency via `asyncio.create_task`. Each agent turn is a subprocess launched with `asyncio.create_subprocess_exec`.

### No persistent database
All state lives in memory. The orchestrator recovers from restart by re-polling Linear and re-discovering active issues. Workspace directories on disk act as durable state.

### WORKFLOW.md as the operator contract
The operator's `WORKFLOW.md` contains both the runtime config (YAML front matter) and the agent prompt template (Jinja2 body). Stokowski re-parses it on every poll tick — config changes take effect without restart.

### Workspace isolation
Each issue gets its own directory under `workspace.root`. Agents run with `cwd` set to that directory. Workspaces persist across turns for the same session; they're deleted when the issue reaches a terminal state.

### Headless system prompt
Every first-turn launch appends a system prompt via `--append-system-prompt` that instructs Claude not to use interactive skills, slash commands, or plan mode. This prevents agents from stalling on interactive workflows.

---

## Component deep-dives

### config.py
Parses `WORKFLOW.md` front matter into typed dataclasses:
- `TrackerConfig` — Linear endpoint, API key, project slug, state lists
- `PollingConfig` — interval
- `WorkspaceConfig` — root path (supports `~` and `$VAR` expansion)
- `HooksConfig` — shell scripts for lifecycle events + timeout
- `ClaudeConfig` — command, permission mode, model, timeouts, system prompt
- `AgentConfig` — concurrency limits (global + per-state)
- `ServerConfig` — optional web dashboard port

`ServiceConfig.resolved_api_key()` resolves the key in priority order:
1. Literal value in YAML
2. `$VAR` reference resolved from env
3. `LINEAR_API_KEY` env var as fallback

### linear.py
Async GraphQL client over httpx. Three queries:
- `fetch_candidate_issues()` — paginated, fetches all issues in active states with full detail (labels, blockers, branch name)
- `fetch_issue_states_by_ids()` — lightweight reconciliation query, returns `{id: state_name}`
- `fetch_issues_by_states()` — used on startup cleanup, returns minimal Issue objects

Note: the reconciliation query uses `issues(filter: { id: { in: $ids } })` — not `nodes(ids:)` which doesn't exist in Linear's API.

### models.py
Three dataclasses:
- `Issue` — normalized Linear issue. `title` is required even for minimal fetches (use `title=""`).
- `RunAttempt` — per-issue runtime state: session_id, turn count, token usage, status, last message
- `RetryEntry` — retry queue entry with due time and error

### orchestrator.py
The main loop. `start()` runs until `stop()` is called:

```
while running:
    _tick()          # reconcile → fetch → dispatch
    sleep(interval)  # interruptible via asyncio.Event
```

**Dispatch logic:**
1. Issues sorted by priority (lower = higher), then created_at, then identifier
2. `_is_eligible()` checks: valid fields, active state, not already running/claimed, blockers resolved
3. Per-state concurrency limits checked against `max_concurrent_agents_by_state`
4. `_dispatch()` creates a `RunAttempt`, adds to `self.running`, spawns `_run_worker` task

**Reconciliation:** on each tick, fetches current states for all running issue IDs. If an issue moved to terminal state → cancel worker + clean workspace. If moved out of active states → cancel worker, release claim.

**Retry logic:**
- `succeeded` → schedule continuation retry in 1s (checks if more work needed)
- `failed/timed_out/stalled` → exponential backoff: `min(10000 * 2^(attempt-1), max_retry_backoff_ms)`
- `canceled` → release claim immediately

**Shutdown:** `stop()` sets `_stop_event`, kills all child PIDs via `os.killpg`, cancels async tasks.

### runner.py
`run_agent_turn()` builds CLI args, launches subprocess, streams NDJSON output.

**PID tracking:** `on_pid` callback registers/unregisters child PIDs with the orchestrator for clean shutdown.

**Stall detection:** background `stall_monitor()` task checks time since last output. Kills process if `stall_timeout_ms` exceeded.

**Turn timeout:** `asyncio.wait()` with `turn_timeout_ms` as overall deadline.

**Event processing** (`_process_event`):
- `result` event → extracts `session_id`, token usage, result text
- `assistant` event → extracts last message for display
- `tool_use` event → updates last message with tool name

### workspace.py
`ensure_workspace()` creates the directory if needed, runs `after_create` hook on first creation.
`remove_workspace()` runs `before_remove` hook, then deletes the directory.
`run_hook()` executes shell scripts via `asyncio.create_subprocess_shell` with timeout.

Workspace key is the sanitized issue identifier: only `[A-Za-z0-9._-]` characters.

### web.py
Optional FastAPI app returned by `create_app(orch)`. Routes:
- `GET /` — HTML dashboard (IBM Plex Mono font, dark theme, amber accents)
- `GET /api/v1/state` — full JSON snapshot from `orch.get_state_snapshot()`
- `GET /api/v1/{issue_identifier}` — single issue state
- `POST /api/v1/refresh` — triggers `orch._tick()` immediately

Dashboard JS polls `/api/v1/state` every 3s and updates the DOM without page reload.

Uvicorn is started as an `asyncio.create_task` with `install_signal_handlers` monkey-patched to a no-op to prevent it hijacking SIGINT/SIGTERM. On shutdown, `server.should_exit = True` is set and the task is awaited with a 2s timeout.

### main.py
CLI entry point (`cli()`) and keyboard handler.

**`KeyboardHandler`** runs in a daemon background thread using `tty.setcbreak()` (not `setraw` — `setraw` disables `OPOST` output processing which causes diagonal log output). Uses `select.select()` with 100ms timeout for non-blocking key reads. Restores terminal state in `finally`.

**`_make_footer()`** builds the Rich `Text` status line shown at bottom of terminal via `Live`.

**`_force_kill_children()`** uses `pgrep -f "claude.*-p.*--output-format.*stream-json"` as a last-resort cleanup on `KeyboardInterrupt`.

**`_load_dotenv()`** reads `.env` from cwd on startup — supports `KEY=value` format, ignores comments and blank lines. The project-local `.env` takes precedence over the shell environment (uses direct assignment, overrides existing env vars).

---

## Data flow: issue dispatch to PR

```
Linear poll → Issue fetched → _dispatch() called
    → RunAttempt created in self.running
    → _run_worker() task spawned
        → ensure_workspace() → after_create hook (git clone, npm install, etc.)
        → _render_prompt() → Jinja2 renders WORKFLOW.md body with issue vars
        → run_agent_turn() called in loop (up to max_turns)
            → build_claude_args() → claude -p subprocess
            → NDJSON streamed: tool_use events, assistant messages, result
            → session_id captured for next turn
        → _on_worker_exit() called
            → tokens/timing aggregated
            → retry or continuation scheduled
```

The agent itself handles: moving Linear state, posting comments, creating branches, opening PRs via `gh pr create`, linking PR to issue. Stokowski doesn't do any of that — it's the scheduler, not the agent.

---

## Stream-json event format

Claude Code emits NDJSON on stdout when run with `--output-format stream-json --verbose`. Key event types:

```json
{"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
{"type": "tool_use", "name": "Bash", "input": {"command": "..."}}
{"type": "result", "session_id": "uuid", "usage": {"input_tokens": 1234, "output_tokens": 456, "total_tokens": 1690}, "result": "final message text"}
```

Exit code 0 = success. Non-zero = failure (stderr captured for error message).

---

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[web]"

# Validate config without dispatching agents
stokowski --dry-run

# Run with verbose logging
stokowski -v

# Run with web dashboard
stokowski --port 4200
```

There are no automated tests beyond `--dry-run`. The system is best verified by running against a real Linear project with a test ticket.

---

## Contributing

### Adding a new tracker (not Linear)
1. Add a client in a new file (e.g., `github_issues.py`) implementing the same three methods as `LinearClient`
2. Add the new tracker kind to `config.py` parsing
3. Update `orchestrator.py` to instantiate the right client based on `cfg.tracker.kind`
4. Update `validate_config()` to handle the new kind

### Adding config fields
1. Add the field to the relevant dataclass in `config.py`
2. Parse it in `parse_workflow_file()`
3. Use it wherever needed
4. Update `WORKFLOW.example.md` and the README config reference

### Changing the web dashboard
`web.py` is self-contained. The HTML/CSS/JS is inline in the `HTML` constant. The dashboard is intentionally dependency-free on the frontend — no build step, no npm.

### Common pitfalls
- **`tty.setraw` vs `tty.setcbreak`**: Don't switch back to `setraw`. It disables `OPOST` output processing and causes Rich log lines to render diagonally (no carriage return on newlines).
- **`Issue(title=...)` is required**: Minimal Issue constructors (in `linear.py` `fetch_issues_by_states` and the `orchestrator.py` state-check default) must pass `title=""` — it's a required positional field.
- **`--verbose` with stream-json**: Claude Code requires `--verbose` when using `--output-format stream-json`. Without it you get an error.
- **Linear project slug**: The `project_slug` is the hex `slugId` from the project URL, not the human-readable name. These look like `abc123def456`.
- **Uvicorn signal handlers**: Must be monkey-patched (`server.install_signal_handlers = lambda: None`) before calling `serve()`, otherwise uvicorn hijacks SIGINT.
