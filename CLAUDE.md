# Stokowski

Claude Code adaptation of [OpenAI's Symphony](https://github.com/openai/symphony). Orchestrates coding agents via issue trackers (Linear and GitHub Issues).

This file is the single source of truth for contributors. It covers architecture, design decisions, key behaviours, and how to work on the codebase.

---

## What it does

Stokowski is a long-running Python daemon that:
1. Manages N workflows from a root `stokowski.yaml` config (multi-workflow manager)
2. Polls Linear or GitHub Issues for issues in configured active states
3. Creates an isolated git-cloned workspace per issue (or runs workspace-free)
4. Launches Claude Code, Codex, or Gemini CLI in that workspace
5. Manages multi-turn sessions via `--resume <session_id>`
6. Retries failures with exponential backoff
7. Reconciles running agents against tracker state changes
8. Persists per-issue state to disk for crash recovery
9. Records completed runs to `history.json`
10. Exposes a live web dashboard (light/dark mode) and terminal UI

The agent prompt, runtime config, and workspace setup all live in `workflow.yaml` in the operator's directory — not in this codebase.

---

## Package structure

```
stokowski/
  manager.py         Multi-workflow manager (holds N orchestrators, shared Linear client)
  config.py          workflow.yaml + stokowski.yaml parser, typed config dataclasses
  tracker.py         TrackerClient protocol (interface for all tracker backends)
  linear.py          Linear GraphQL client (httpx async, shared rate limiter)
  github_issues.py   GitHub Issues REST API client (httpx async)
  state.py           Per-issue persistent state (PersistedIssueState), crash recovery
  history.py         Run history recording to history.json
  models.py          Domain models: Issue, RunAttempt, RetryEntry
  orchestrator.py    Main poll loop, dispatch, reconciliation, retry, orphan cleanup
  prompt.py          Three-layer prompt assembly for state machine workflows
  runner.py          Multi-runner CLI integration (Claude Code, Codex, Gemini), stream-json parser
  tracking.py        State machine tracking via structured comments
  workspace.py       Per-issue workspace lifecycle and hooks
  web.py             Optional FastAPI dashboard + webhook endpoints (light/dark mode, history)
  main.py            CLI entry point, keyboard handler
  __main__.py        Enables python -m stokowski
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

### Persistent state without a database
Runtime state lives in memory, but per-issue state is persisted to disk via `state.py` (`PersistedIssueState`) for crash recovery. The orchestrator recovers from restart by loading persisted state, re-polling the tracker, and re-discovering active issues. Completed runs are recorded to `history.json` by `history.py`. Workspace directories on disk also act as durable state. No external database is required.

### Multi-workflow manager
The `manager.py` module holds N orchestrators, each driven by its own `workflow.yaml`. A root `stokowski.yaml` config declares the workflows. The manager provides independent start/stop for each workflow and shares a single `LinearClient` instance across all workflows to stay within rate limits.

### Shared Linear rate limiter
All workflows share one `LinearClient` instance with a semaphore(1) and a minimum 1-second delay between requests. This prevents concurrent workflows from hitting Linear's rate limits.

### workflow.yaml as the operator contract
The operator's `workflow.yaml` defines the runtime config and state machine. Stokowski re-parses it on every poll tick — config changes take effect without restart. Both `.yaml` and legacy `.md` (YAML front matter + Jinja2 body) formats are supported. Prompt templates are now separate `.md` files referenced by path from the config.

### State machine workflow
Each workflow defines a set of internal states that map to Linear states. States have types: `agent` (runs Claude Code), `gate` (waits for human review), or `terminal` (issue complete). Transitions between states are declared explicitly in config.

**Three-layer prompt assembly:** Every agent turn's prompt is built from three layers concatenated together:
1. **Global prompt** — shared context loaded from a `.md` file (referenced by `prompts.global_prompt`)
2. **Stage prompt** — state-specific instructions loaded from the state's `prompt` path
3. **Lifecycle injection** — auto-generated section with issue metadata, transitions, rework context, and recent comments

**Gate protocol:** When an agent completes a state that transitions to a gate, Stokowski moves the issue to the gate's Linear state and posts a structured tracking comment. Humans approve or request rework via Linear state changes. On approval, Stokowski advances to the gate's `approve` transition target. On rework, it returns to the gate's `rework_to` state.

**Structured comment tracking:** State transitions and gate decisions are persisted as HTML comments on Linear issues (`<!-- stokowski:state {...} -->` and `<!-- stokowski:gate {...} -->`). These enable crash recovery and provide context for rework runs.

### Workspace isolation
Each issue gets its own workspace. Two modes are supported:
- **Clone mode** (`workspace.mode: clone`, default) — each issue gets a fresh `git clone` under `workspace.root`
- **Worktree mode** (`workspace.mode: worktree`) — each issue gets a git worktree under `{repo_path}/.worktrees/{issue-number}/`, branching from `origin/main`. Requires `workspace.repo_path` to point to the git repo. Lighter weight than cloning — no redundant `.git` history.

Agents run with `cwd` set to the workspace directory. Workspaces persist across turns for the same session; they're deleted (worktree removed + branch deleted) when the issue reaches a terminal state.

---

## Component deep-dives

### config.py
Parses `workflow.yaml`, `stokowski.yaml` (root config for multi-workflow), and legacy `.md` (with front matter) into typed dataclasses. `RootConfig` holds the top-level `stokowski.yaml` structure: list of workflow paths, shared server config, shared `linear_states`.
- `TrackerConfig` — tracker kind (`linear` or `github`), connection details (endpoint/API key for Linear; owner/repo/token for GitHub)
- `PollingConfig` — interval
- `WorkspaceConfig` — root path (supports `~` and `$VAR` expansion)
- `HooksConfig` — shell scripts for lifecycle events + timeout (includes `on_stage_enter`)
- `ClaudeConfig` — command, permission mode, model, timeouts, system prompt
- `AgentConfig` — concurrency limits (global, per-state, and per-project per-state)
- `ServerConfig` — optional web dashboard port
- `WebhookConfig` — optional webhook listener: `secret` for HMAC-SHA256 signature verification
- `LinearStatesConfig` — maps logical state names (`todo`, `active`, `review`, `gate_approved`, `rework`, `blocked`, `terminal`) to actual Linear state names. Issues in the `todo` state are picked up and automatically moved to `active` on dispatch. Issues moved to `blocked` are released from the orchestrator.
- `GitHubStatesConfig` — same logical mapping but for GitHub Issues (uses labels as state markers, with optional `close_on_terminal`)
- `PromptsConfig` — global prompt file reference
- `StateConfig` — a single state in the state machine: type, prompt path, linear_state key, runner, session mode, transitions, per-state overrides (model, max_turns, timeouts, hooks), gate-specific fields (rework_to, max_rework)
- `RoutingRule` — maps Linear labels to entry states for label-based routing
- `ScheduleConfig` — optional cron-based issue creation via external shell command with `{date}`/`{datetime}` placeholders

Per-workflow fields: `filter_labels` (only pick up issues with these labels), `exclude_labels` (skip issues with these labels), `pickup_states` (override which states to pick up from), `tracker_enabled` (disable tracker polling for schedule-only workflows), `workspace_enabled` (skip workspace creation), `source` (`github-prs` for PR-based dispatch without a tracker). Each workflow can also override the shared `linear_states` from the root config.

`ServiceConfig` provides helper methods: `entry_state` (first agent state), `entry_state_for_issue(labels)` (label-routed entry state), `active_linear_states()`, `gate_linear_states()`, `terminal_linear_states()`.

`merge_state_config(state, root_claude, root_hooks)` merges per-state overrides with root defaults — only specified fields are overridden. Returns `(ClaudeConfig, HooksConfig)`.

`parse_workflow_file()` detects format by file extension: `.yaml`/`.yml` files are parsed as pure YAML; `.md` files are split on `---` delimiters for front matter + body.

`validate_config()` checks state machine integrity: all transitions point to existing states, gates have `rework_to` and `approve` transition, at least one agent and one terminal state exist, warns about unreachable states.

`ServiceConfig.resolved_api_key()` resolves the key in priority order:
1. Literal value in YAML
2. `$VAR` reference resolved from env
3. `LINEAR_API_KEY` env var as fallback

### tracker.py
Defines `TrackerClient` — a `Protocol` class that all tracker backends must implement. Methods: `close()`, `fetch_candidate_issues()`, `fetch_issue_states_by_ids()`, `fetch_issues_by_states()`, `post_comment()`, `fetch_comments()`, `update_issue_state()`. The orchestrator uses duck typing — no explicit subclassing needed.

### manager.py
Multi-workflow manager. Parses the root `stokowski.yaml` (`RootConfig`) and creates one `Orchestrator` per workflow entry. Provides `start()` / `stop()` lifecycle for all workflows. Shares a single `LinearClient` instance across all orchestrators to enforce rate limiting. Supports crash recovery — manager state (which workflows are running, their last-known state) is persisted and restored on restart.

### state.py
Per-issue persistent state. `PersistedIssueState` is a dataclass serialized to a JSON file alongside the workflow YAML. Stores issue ID, current state, session ID, turn count, token usage, and timestamps. On startup, the orchestrator loads persisted state to recover in-flight issues without re-polling from scratch. State is updated after each turn and cleared when an issue reaches a terminal state.

### history.py
Run history recording. When an agent worker exits (success, failure, or block), the completed run is appended to `history.json` — a JSON Lines file next to the workflow YAML. Each entry records: issue identifier, state, outcome, token usage, duration, and timestamps. The web dashboard reads this file to display a run history table.

### linear.py
Async GraphQL client over httpx implementing `TrackerClient`. In multi-workflow setups, a single `LinearClient` instance is shared across all orchestrators via the manager. Rate limiting is enforced with an `asyncio.Semaphore(1)` and a minimum 1-second delay between requests to avoid hitting Linear's API limits.

Supports two filtering modes: **project-scoped** (`project_slug`) and **team-scoped** (`team_key`). Each mode has its own GraphQL query set — team queries use `team: { key: { eq: $teamKey } }` filter.

### github_issues.py
GitHub Issues REST API client implementing `TrackerClient`. Uses labels prefixed with `stokowski:` to represent workflow states (GitHub only has open/closed natively). Key design: `update_issue_state()` atomically swaps `stokowski:*` labels while preserving user labels. `fetch_candidate_issues()` queries per state label since GitHub API filters by single label. Auto-creates missing labels on first use.

Key methods:
- `fetch_candidate_issues(project_slug, states, team_key)` — paginated, fetches all issues in active states with full detail (labels, blockers, branch name). Uses team query when `team_key` is set.
- `fetch_issue_states_by_ids()` — lightweight reconciliation query, returns `{id: state_name}`. Not scoped (uses issue IDs directly).
- `fetch_issues_by_states(project_slug, states, team_key)` — used on startup cleanup and gate detection. Uses team query when `team_key` is set.
- `update_issue_state(issue_id, state_name)` — moves an issue to a new state. Used for active, blocked, and terminal transitions.

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

**Orphan agent cleanup:** On startup, `_kill_orphan_agents()` finds and kills stale `claude -p` processes left behind by prior crashes, preventing token bleed.

**Dispatch queue:** Candidate issues are queued in a dispatch queue that survives failed API ticks — candidates are not lost if a single poll fails.

**Dispatch logic:**
1. Issues sorted by priority (lower = higher), then created_at, then identifier
2. `_is_eligible()` checks: valid fields, active state, not already running/claimed, blockers resolved
3. Per-state concurrency limits checked against `max_concurrent_agents_by_state` (global) and `max_concurrent_by_project` (per-project, uses `issue.project_slug`)
4. `_dispatch()` creates a `RunAttempt`, adds to `self.running`, spawns `_run_worker` task

**PR-based dispatch:** When `source: github-prs` is configured, the orchestrator processes pull requests directly via `gh pr list` without a tracker.

**Reconciliation:** on each tick, fetches current states for all running issue IDs. If an issue moved to terminal state → cancel worker + clean workspace. If moved out of active states → cancel worker, release claim.

**Blocked handling:** When a runner detects `STOKOWSKI:BLOCKED` in the agent's result text, `attempt.status` is set to `"blocked"`. On worker exit, `_move_to_blocked()` posts a comment with the reason, moves the issue to the Blocked Linear state, and releases all tracking. The workspace is preserved on block (not deleted).

**Label-based routing:** `_resolve_current_state()` calls `cfg.entry_state_for_issue(labels)` for new issues (no tracking comments). If routing rules match the issue's labels, the issue enters the matched state instead of the default entry state.

**Retry logic:**
- `blocked` → `_move_to_blocked()` — no retry
- `succeeded` → schedule continuation retry in 1s (checks if more work needed)
- `failed/timed_out/stalled` → exponential backoff: `min(10000 * 2^(attempt-1), max_retry_backoff_ms)`
- `canceled` → release claim immediately

**Shutdown:** `stop()` sets `_stop_event`, kills all child PIDs via `os.killpg`, cancels async tasks.

### runner.py
`run_agent_turn()` builds CLI args, launches subprocess, streams NDJSON output.

**PID tracking:** `on_pid` callback registers/unregisters child PIDs with the orchestrator for clean shutdown.

**Stall detection:** background `stall_monitor()` task checks time since last output. Kills process if `stall_timeout_ms` exceeded.

**Turn timeout:** `asyncio.wait()` with `turn_timeout_ms` as overall deadline.

**Event processing** (`_process_event` for Claude, `_process_gemini_event` for Gemini):
- `result` event → extracts `session_id`, token usage, result text
- `assistant`/`message` event → extracts last message for display
- `tool_use` event → updates last message with tool name

**Three runners supported:**
- `claude` (default) — Claude Code CLI with `--output-format stream-json --verbose`
- `codex` — OpenAI Codex CLI with `--quiet`, no session resumption
- `gemini` — Gemini CLI with `--output-format stream-json`, session resumption via `--resume`. Session ID comes from `init` event (not `result`). Token stats in `stats` field (not `usage`).

### workspace.py
Two workspace modes:
- **Clone mode** — `_ensure_clone_workspace()` creates a directory under `workspace.root`, runs `after_create` hook (typically `git clone`). Workspace key is the sanitized identifier.
- **Worktree mode** — `_ensure_worktree()` runs `git worktree add -b {branch} .worktrees/{issue-number} origin/main` from `workspace.repo_path`. Falls back to attaching existing branch if `-b` fails. `_remove_worktree()` removes the worktree and deletes the branch.

`ensure_workspace()` and `remove_workspace()` dispatch to the correct mode based on `workspace_cfg.mode`. Both accept an optional `workspace_cfg` parameter; when `None`, they use clone mode. When `workspace_enabled: false` is set in the workflow config, workspace creation is skipped entirely.

Blocked issues preserve their workspace (not deleted on block) so humans can inspect the state.

`run_hook()` executes shell scripts via `asyncio.create_subprocess_shell` with timeout.

### web.py
Optional FastAPI app returned by `create_app(orch)`. Routes:
- `GET /` — HTML dashboard (IBM Plex Mono font, light/dark mode with system preference detection and manual toggle, responsive layout, ARIA accessible). Multi-workflow setups show workflow tabs. Includes a run history section displaying completed runs from `history.json`.
- `GET /api/v1/state` — full JSON snapshot from `orch.get_state_snapshot()`
- `GET /api/v1/{issue_identifier}` — single issue state
- `POST /api/v1/refresh` — triggers `orch._tick()` immediately
- `POST /api/v1/webhook/linear` — Linear webhook endpoint; verifies HMAC-SHA256 signature (if `webhook.secret` configured), filters to issue state changes and creations, triggers a coalesced `webhook_tick()`
- `POST /api/v1/webhook/github` — GitHub webhook endpoint; verifies `X-Hub-Signature-256`. Handles three event types: `issues` (label/state changes → tick), `pull_request_review` (approved/changes_requested → gate transitions via `pr_triggers`), `pull_request` (merged → gate transitions)

Dashboard JS polls `/api/v1/state` every 3s and updates the DOM without page reload.

Uvicorn is started as an `asyncio.create_task` with `install_signal_handlers` monkey-patched to a no-op to prevent it hijacking SIGINT/SIGTERM. On shutdown, `server.should_exit = True` is set and the task is awaited with a 2s timeout.

### main.py
CLI entry point (`cli()`) and keyboard handler.

**`KeyboardHandler`** runs in a daemon background thread using `tty.setcbreak()` (not `setraw` — `setraw` disables `OPOST` output processing which causes diagonal log output). Uses `select.select()` with 100ms timeout for non-blocking key reads. Restores terminal state in `finally`.

**`_make_footer()`** builds the Rich `Text` status line shown at bottom of terminal via `Live`.

**`check_for_updates()`** hits the GitHub releases API (`/repos/erikpr1994/stokowski/releases/latest`) via httpx, compares the latest tag against the installed `__version__`, and sets `_update_message` if a newer version exists. Best-effort — all exceptions are silently swallowed.

**`_force_kill_children()`** uses `pgrep -f "claude.*-p.*--output-format.*stream-json"` as a last-resort cleanup on `KeyboardInterrupt`.

**`_load_dotenv()`** reads `.env` and `.stokowski/.env` from cwd on startup — supports `KEY=value` format, ignores comments and blank lines. Uses `setdefault` so existing env vars are not overridden.

**Auto-detection:** When no workflow path is specified, `cli()` checks `.stokowski/stokowski.yaml` first (root config), then falls back to `workflow.yaml`/`workflow.yml`/`WORKFLOW.md`. The port is read from `server.port` in the root config when `--port` is not specified. Running `stokowski` with no arguments in a project with `.stokowski/stokowski.yaml` starts the multi-workflow daemon with the dashboard.

### prompt.py
Three-layer prompt assembly for state machine workflows. Main entry point is `assemble_prompt()`.

**`load_prompt_file(path, workflow_dir)`** resolves a prompt file path (absolute or relative to workflow dir) and returns its contents.

**`render_template(template_str, context)`** renders a Jinja2 template with `_SilentUndefined` — missing variables render as empty strings instead of raising errors.

**`build_template_context(issue, state_name, run, attempt, last_run_at)`** builds the flat dict used for Jinja2 rendering. Includes: `issue_id`, `issue_identifier`, `issue_title`, `issue_description`, `issue_url`, `issue_priority`, `issue_state`, `issue_branch`, `issue_labels`, `state_name`, `run`, `attempt`, `last_run_at`.

**`build_lifecycle_section()`** generates the auto-injected lifecycle section appended to every prompt. Includes issue metadata, rework context with review comments, recent activity, available transitions, and completion instructions. Clearly demarcated with HTML comments.

**`assemble_prompt()`** orchestrates the three layers: loads and renders global prompt, loads and renders stage prompt, generates lifecycle section, joins with double newlines.

### tracking.py
State machine tracking via structured Linear comments:
- `make_state_comment(state, run)` — builds state entry comment with hidden JSON (`<!-- stokowski:state {...} -->`) + human-readable text
- `make_gate_comment(state, status, prompt, rework_to, run)` — builds gate status comment (waiting/approved/rework/escalated)
- `parse_latest_tracking(comments)` — scans comments (oldest-first) to find latest state or gate tracking entry for crash recovery
- `get_last_tracking_timestamp(comments)` — finds the timestamp of the latest tracking comment
- `get_comments_since(comments, since_timestamp)` — filters to non-tracking comments after a given timestamp (used to gather review feedback for rework runs)

---

## Data flow: issue dispatch to PR

```
stokowski.yaml parsed → RootConfig → Manager created with N orchestrators
    → shared LinearClient instantiated (rate-limited)
    → persisted state loaded from disk (state.py) for crash recovery
    → orphan agent processes killed on startup

Per workflow:
    workflow.yaml parsed → states + config loaded
        → Tracker poll → Issue fetched → state resolved from tracking comments + persisted state
        → candidates queued in dispatch queue (survives failed ticks)
        → _dispatch() called
            → RunAttempt created in self.running
            → persisted state updated on disk
            → _run_worker() task spawned
                → ensure_workspace() → after_create hook (git clone, npm install, etc.)
                → assemble_prompt() → 3 layers: global + stage + lifecycle
                → run_agent_turn() called in loop (up to max_turns)
                    → build_claude_args() → claude/codex/gemini subprocess
                    → NDJSON streamed: tool_use events, assistant messages, result
                    → session_id captured for next turn
                    → persisted state updated after each turn
                → _on_worker_exit() called
                    → state transition on success → tracking comment posted
                    → tokens/timing aggregated
                    → run recorded to history.json (history.py)
                    → retry or continuation scheduled
```

The agent itself handles: moving tracker state, posting comments, creating branches, opening PRs via `gh pr create`, linking PR to issue. Stokowski doesn't do any of that — it's the scheduler, not the agent.

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
pip install -e ".[web,schedule]"

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

### Adding a new tracker (beyond Linear and GitHub Issues)
1. Add a client in a new file implementing the `TrackerClient` protocol from `tracker.py`
2. Add the new tracker kind to `config.py` parsing
3. Update `orchestrator.py` to instantiate the right client based on `cfg.tracker.kind`
4. Update `validate_config()` to handle the new kind

### Scheduled issue creation
Workflows can auto-create tracker issues on a cron schedule via the `schedule` section:

```yaml
schedule:
  cron: "0 9 * * *"                     # 9 AM UTC daily
  create_command: |
    gh issue create --title "docs: daily sync — {date}" --label docs --body "Auto-created by Stokowski schedule."
```

Requires `pip install stokowski[schedule]` (adds `croniter`). On each poll tick, the orchestrator checks if the cron has fired since the last check and runs `create_command` as a shell subprocess. `{date}` and `{datetime}` placeholders are replaced with the fire time. The command runs with the same env vars as agent subprocesses (includes tracker credentials).

Config: `ScheduleConfig` in `config.py`. Logic: `_check_schedule()` in `orchestrator.py`.

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
- **workflow.yaml is pure YAML**: No markdown front matter. The legacy `.md` format with `---` delimiters is still supported but `.yaml` is the canonical format.
- **Prompt files use Jinja2 with silent undefined**: Missing variables become empty strings rather than raising errors. This is intentional — not all variables are available in every context.
- **`STOKOWSKI:BLOCKED` marker**: Agents signal blocked status with the literal text `STOKOWSKI:BLOCKED` in their result output — not HTML comments (`<!-- stokowski:blocked -->`). The marker format changed in v0.5.0.
- **Shared Linear client rate limiting**: In multi-workflow setups, all orchestrators share a single `LinearClient` with semaphore(1) + 1s minimum delay. Do not instantiate separate clients per workflow — this bypasses rate limiting and causes 429 errors.
