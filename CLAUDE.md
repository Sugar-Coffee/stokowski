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

The agent prompt, runtime config, and workspace setup all live in `workflow.yaml` in the operator's directory — not in this codebase.

---

## Package structure

```
stokowski/
  artifacts.py     Agent evidence: collection, git isolation, cleanup
  config.py        workflow.yaml parser + typed config dataclasses
  events.py        stream-json event parsing -> RunAttempt state
  ledger.py        Append-only run log + approval-rate summary
  linear.py        Linear GraphQL client (httpx async)
  model_catalogue.py  Known models by provider (data, for the studio dropdown)
  models.py        Domain models: Issue, RunAttempt, RetryEntry
  orchestrator.py  Main poll loop, dispatch, reconciliation, retry
  prompt.py        Three-layer prompt assembly + the agent reporting contract
  report.py        Structured run reports -> rendered Linear comments
  studio.py        Comment-preserving config editing behind the dashboard
  runner.py        Claude Code CLI integration, subprocess lifecycle
  tracking.py      State machine tracking via structured Linear comments
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

### workflow.yaml as the operator contract
The operator's `workflow.yaml` defines the runtime config and state machine. Stokowski re-parses it on every poll tick — config changes take effect without restart. Both `.yaml` and legacy `.md` (YAML front matter + Jinja2 body) formats are supported. Prompt templates are now separate `.md` files referenced by path from the config.

### State machine workflow
Each workflow defines a set of internal states that map to Linear states. States have types: `agent` (runs Claude Code), `gate` (waits for human review), or `terminal` (issue complete). Transitions between states are declared explicitly in config.

**Three-layer prompt assembly:** Every agent turn's prompt is built from three layers concatenated together:
1. **Global prompt** — shared context loaded from a `.md` file (referenced by `prompts.global_prompt`)
2. **Stage prompt** — state-specific instructions loaded from the state's `prompt` path
3. **Lifecycle injection** — auto-generated section with issue metadata, transitions, rework context, and recent comments

**Grounding before gates:** The example pipeline puts an independent
`ground-check` agent between `investigate` and the first human gate, running
`session: fresh`. The failure it targets is an investigation that reasoned
correctly from the wrong data — wrong environment, dead column, unreproducible
number. That report is fluent, internally consistent and wrong, which is
exactly why a human gate does not catch it: a reviewer reading prose cannot
distinguish a well-sourced conclusion from a well-written one.

The fresh session is load-bearing rather than cosmetic. A continued session
inherits the assumptions it is supposed to be testing and will confirm them.
`tests/test_example_prompts.py` asserts both properties, so a future edit that
routes `investigate` straight to a gate fails the suite.

**Gate protocol:** When an agent completes a state that transitions to a gate, Stokowski moves the issue to the gate's Linear state and posts a structured tracking comment. Humans approve or request rework via Linear state changes. On approval, Stokowski advances to the gate's `approve` transition target. On rework, it returns to the gate's `rework_to` state.

**Structured comment tracking:** State transitions and gate decisions are persisted as HTML comments on Linear issues (`<!-- stokowski:state {...} -->` and `<!-- stokowski:gate {...} -->`). These enable crash recovery and provide context for rework runs.

### Workspace isolation
Each issue gets its own directory under `workspace.root`. Agents run with `cwd` set to that directory. Workspaces persist across turns for the same session; they're deleted when the issue reaches a terminal state.

### Headless system prompt
Every first-turn launch appends a system prompt via `--append-system-prompt` that instructs Claude not to use interactive skills, slash commands, or plan mode. This prevents agents from stalling on interactive workflows.

---

## Component deep-dives

### config.py
Parses `workflow.yaml` (or legacy `.md` with front matter) into typed dataclasses:
- `TrackerConfig` — Linear endpoint, API key, project slug
- `PollingConfig` — interval
- `WorkspaceConfig` — root path (supports `~` and `$VAR` expansion)
- `HooksConfig` — shell scripts for lifecycle events + timeout (includes `on_stage_enter`)
- `ClaudeConfig` — command, permission mode, model, timeouts, system prompt
- `AgentConfig` — concurrency limits (global + per-state)
- `ServerConfig` — optional web dashboard port
- `LinearStatesConfig` — maps logical state names (`todo`, `active`, `review`, `gate_approved`, `rework`, `terminal`) to actual Linear state names. Issues in the `todo` state are picked up and automatically moved to `active` on dispatch.
- `PromptsConfig` — global prompt file reference
- `StateConfig` — a single state in the state machine: type, prompt path, linear_state key, runner, session mode, transitions, per-state overrides (model, max_turns, timeouts, hooks), gate-specific fields (rework_to, max_rework)

`ServiceConfig` provides helper methods: `entry_state` (first agent state), `active_linear_states()`, `gate_linear_states()`, `terminal_linear_states()`.

`merge_state_config(state, root_claude, root_hooks)` merges per-state overrides with root defaults — only specified fields are overridden. Returns `(ClaudeConfig, HooksConfig)`.

`parse_workflow_file()` detects format by file extension: `.yaml`/`.yml` files are parsed as pure YAML; `.md` files are split on `---` delimiters for front matter + body.

`validate_config()` checks state machine integrity: all transitions point to existing states, gates have `rework_to` and `approve` transition, at least one agent and one terminal state exist, warns about unreachable states.

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

### events.py
Owns the mapping from stream-json onto `RunAttempt`. Split out of `runner.py`
so the parsing is testable without launching a subprocess — the original bug
class survived precisely because nothing could exercise it in isolation.

`process_event()` folds one event in. Tool calls, thinking, text, tool errors,
rate limits and results all append to `attempt.activity`, a bounded deque
(`ACTIVITY_MAXLEN = 250`) that the dashboard renders as a timeline.

Successful tool results are deliberately NOT recorded — a real run makes
hundreds, and they carry no information. Only failures are.

`display_tool_name()` shortens `mcp__playwright__browser_take_screenshot` to
`playwright:browser_take_screenshot` so the argument stays visible.

### artifacts.py
Agent evidence (screenshots, exports) lives in `.stokowski/artifacts/` **inside**
the workspace, not beside it. Playwright MCP and the simulator MCP refuse to
write outside their working directory, so an external path silently produces
nothing.

Because it lives inside the clone it must be ignored, and the ignore goes in
`.git/info/exclude` — never the project's `.gitignore`, which belongs to the
project and would show up in every diff. `tests/test_artifacts.py` asserts
against real `git status` output.

`collect()` sorts by mtime so before/after pairs read in capture order, filters
to known evidence types, and skips empty or oversized files.

### report.py
Stokowski renders the Linear comment; the agent supplies structured JSON at
`.stokowski/report.json`. Authorship sits here because a model asked to
summarise its own work reliably produces something readable and unreliably
produces something checkable.

The rendering is deliberately unflattering. A claim with no `evidence` or
`source` is printed with a warning marker rather than dropped; an unverified
`data_source` is marked as such; a missing report posts "no structured report"
rather than silently falling back to prose. The point is that thin work should
look thin on the issue.

`classification` maps to a Linear label (`stokowski/bug-fix`,
`stokowski/improvement`, `stokowski/prototype`, …), created on the team if
absent. This is how the board gets filterable by what the work turned out to be.

The prompt side of this contract is `prompt.build_reporting_contract()`, which
is injected into every agent prompt.

### ledger.py
Append-only JSONL at `<workflow-dir>/.stokowski/ledger.jsonl`, recording each
stage, each gate decision, and each terminal outcome.

Stokowski keeps no database, which is fine for scheduling and useless for the
question that matters once you are running dozens of tickets a week: is this
working, and for what? A Linear issue holds one run's report; it cannot tell
you that `bug-fix` work lands 95% of the time while `improvement` lands 50%,
or whether the agent's own `high` confidence predicts anything.

The human verdict is taken from gate decisions already in the workflow —
approved means accepted, rework means sent back. No separate rating step: the
judgement was always being made, it just was not written down.

Attribution uses the FIRST stage that declared a classification (the
investigation that framed the work). A later stage's self-assessment is not
independent of the work it just did.

`stokowski --stats` prints the summary. Rates below 10 decisions are shown with
their sample size rather than as a bare percentage, because a 1-for-1 reading
as 100% is how a ledger starts lying to you.

### studio.py
Backs `/studio` on the dashboard: the pipeline at a glance, plus editing for
the obvious knobs. Modelled on the content-pipeline studio — the config file
stays the source of truth and this is a view onto it that can write back.

Two properties do the work:

**Comments survive.** The comments in a workflow file are its documentation.
PyYAML keeps 7 of 193 on the shipped example and collapses 310 lines to 131, so
round-tripping goes through `ruamel.yaml` with `indent(mapping=2, sequence=4,
offset=2)` — which reproduces the hand-written file byte for byte. A single
field edit changes exactly one line.

**An invalid config is never written.** Every edit is rendered, written to a
temp file *inside the workflow directory* (so relative prompt paths resolve as
they will at runtime), parsed and validated. Only then is it committed, via
`os.replace`. The orchestrator re-parses config on every poll tick, so a bad or
torn write is a live failure.

Edits are confined to a whitelist (`ROOT_FIELDS`, `STATE_FIELDS`). Structural
changes — adding states, rewiring transitions — stay in the file where a diff
shows what happened. `tracker.api_key` is deliberately absent.

Note the route ordering constraint in `web.py`: `/api/v1/{issue_identifier}`
matches any single segment, so every literal `/api/v1/...` route must be
declared before it.

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

**`check_for_updates()`** hits the GitHub releases API (`/repos/Sugar-Coffee/stokowski/releases/latest`) via httpx, compares the latest tag against the installed `__version__`, and sets `_update_message` if a newer version exists. Best-effort — all exceptions are silently swallowed.

**`_force_kill_children()`** uses `pgrep -f "claude.*-p.*--output-format.*stream-json"` as a last-resort cleanup on `KeyboardInterrupt`.

**`_load_dotenv()`** reads `.env` from cwd on startup — supports `KEY=value` format, ignores comments and blank lines. The project-local `.env` takes precedence over the shell environment (uses direct assignment, overrides existing env vars).

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
workflow.yaml parsed → states + config loaded
    → Linear poll → Issue fetched → state resolved from tracking comments
    → _dispatch() called
        → RunAttempt created in self.running
        → _run_worker() task spawned
            → ensure_workspace() → after_create hook (git clone, npm install, etc.)
            → assemble_prompt() → 3 layers: global + stage + lifecycle
            → run_agent_turn() called in loop (up to max_turns)
                → build_claude_args() → claude -p subprocess
                → NDJSON streamed: tool_use events, assistant messages, result
                → session_id captured for next turn
            → _on_worker_exit() called
                → state transition on success → tracking comment posted
                → tokens/timing aggregated
                → retry or continuation scheduled
```

The agent itself handles: moving Linear state, posting comments, creating branches, opening PRs via `gh pr create`, linking PR to issue. Stokowski doesn't do any of that — it's the scheduler, not the agent.

---

## Stream-json event format

Claude Code emits NDJSON on stdout under `--output-format stream-json --verbose`.
Parsing lives in `events.py`; `tests/fixtures/real_turn.ndjson` is an unedited
capture, and `tests/test_events.py` asserts against it. **Verify any change here
against a real capture rather than against this document.**

```json
{"type":"system","subtype":"init","session_id":"uuid","model":"claude-sonnet-4-6","tools":[…]}
{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"…"}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{…}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,…}]}}
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"five_hour","resetsAt":1788181200}}
{"type":"result","subtype":"success","session_id":"uuid","usage":{…},"modelUsage":{…},"total_cost_usd":0.079,"num_turns":2,"stop_reason":"end_turn","permission_denials":[]}
```

Three things about this shape bite repeatedly:

- **There is no top-level `tool_use` event.** Tool calls are content blocks
  inside `assistant` messages; their outcomes are `tool_result` blocks inside
  `user` messages. Parsing for a top-level event yields a dashboard that shows
  nothing but "running" — which is exactly what shipped before v0.6.
- **`usage` has no `total_tokens` field**, and `cache_creation_input_tokens` /
  `cache_read_input_tokens` normally dwarf `input_tokens` by two orders of
  magnitude. A measured trivial turn: 4 input, 150 output, 10,479 cache-write,
  45,255 cache-read. Summing only input+output reports 154 of 55,888.
- **`usage` is per invocation, not per session.** A worker running many turns
  via `--resume` must accumulate; assigning reports only the final turn.

`total_cost_usd` is authoritative — prefer it over computing cost from tokens.

Exit code 0 = success, non-zero = failure (stderr captured). Note that
`is_error: true` on a `result` event (e.g. `error_max_turns`) still exits 0, so
the exit code alone is not sufficient.

---

## Running it

`package.json` carries script aliases for people who reach for `pnpm` first;
they wrap `scripts/stokowski.sh`, which finds the CLI (activated venv → local
`.venv` → PATH) and the workflow file, so nothing depends on remembering where
the virtualenv lives. Run them from the directory holding your `workflow.yaml`
— usually the operator directory, not this repo.

```bash
pnpm start      # run the orchestrator; opens the dashboard
pnpm studio     # same process, opens /studio instead
pnpm check      # --dry-run
pnpm stats      # ledger summary
pnpm test       # pytest
```

The dashboard and the studio are one process — `/studio` is a route on it, not
a second server. `pnpm studio` differs from `pnpm start` only in which page it
opens, reading `server.port` out of the config to build the URL.

The local `.venv` is checked before PATH deliberately: a stale global install
silently running old code against new config is a genuinely confusing failure,
and it happened during development.

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

```bash
pip install pytest && python -m pytest tests/ -q
```

`tests/` covers the parts that fail silently: stream-json parsing (against a
real captured fixture), artifact git-isolation (against real `git status`), and
report rendering. Everything else — dispatch, gates, reconciliation — is still
best verified by running against a real Linear project with a test ticket.

---

## Contributing

### Adding a new tracker (not Linear)
1. Add a client in a new file (e.g., `github_issues.py`) implementing the same three methods as `LinearClient`
2. Add the new tracker kind to `config.py` parsing
3. Update `orchestrator.py` to instantiate the right client based on `cfg.tracker.kind`
4. Update `validate_config()` to handle the new kind

### Running against a project
The workflow path is a positional argument (`stokowski /path/to/workflow.yaml`),
so an operator directory needs only `workflow.yaml`, `prompts/` and `.env` —
**not** a checkout of this repo. Keeping a full clone as the operator directory
pins that deployment to whatever version it was cloned at, and `python -m
stokowski` run from inside it will import the local `stokowski/` package in
preference to anything installed, silently running old code against new config.

### Adding config fields
1. Add the field to the relevant dataclass in `config.py`
2. Parse it in `parse_workflow_file()`
3. Use it wherever needed
4. Update `WORKFLOW.example.md` and the README config reference

### Changing the web dashboard
`web.py` is self-contained. The HTML/CSS/JS is inline in the `HTML` constant. The dashboard is intentionally dependency-free on the frontend — no build step, no npm.

### Common pitfalls
- **Never trust a documented event shape over a captured one.** The parser bug
  fixed in v0.6 existed because this file described a `{"type":"tool_use"}`
  event that the CLI has never emitted. Capture a real stream and read it.
- **Cache tokens are the token count.** Any usage arithmetic that ignores
  `cache_read_input_tokens` is wrong by roughly two orders of magnitude.
- **Agent evidence must go inside the workspace.** Tools that produce it will
  not write outside their cwd. Isolate with `.git/info/exclude`, not
  `.gitignore`.
- **Headless bans interactivity, not tooling.** Slash commands, skills and
  subagents all work under `claude -p` and are usually the best work available.
  Only plan mode, brainstorming and confirmation prompts must be excluded.
- **`/api/v1/{issue_identifier}` is a catch-all.** Any literal route under
  `/api/v1` must be declared before it or it will 404 as an unknown issue.
- **Never round-trip a workflow file through PyYAML.** It destroys the comments
  that document it. Use `studio._yaml()`.
- **`max_turns` does nothing in state machine mode.** Each dispatch is exactly
  one `claude -p` invocation — the state machine controls continuation — and
  the CLI has no `--max-turns` flag, so the value reaches neither the loop nor
  the agent. It applies to legacy multi-turn workflows only. The real runaway
  guard is `max_budget_usd` (`--max-budget-usd`, requires `-p`).
- **`--resume` needs a session id captured from `system/init`.** Reading it
  only from `result` loses the session on any turn that stalls or times out.
- **`tty.setraw` vs `tty.setcbreak`**: Don't switch back to `setraw`. It disables `OPOST` output processing and causes Rich log lines to render diagonally (no carriage return on newlines).
- **`Issue(title=...)` is required**: Minimal Issue constructors (in `linear.py` `fetch_issues_by_states` and the `orchestrator.py` state-check default) must pass `title=""` — it's a required positional field.
- **`--verbose` with stream-json**: Claude Code requires `--verbose` when using `--output-format stream-json`. Without it you get an error.
- **Linear project slug**: The `project_slug` is the hex `slugId` from the project URL, not the human-readable name. These look like `abc123def456`.
- **Uvicorn signal handlers**: Must be monkey-patched (`server.install_signal_handlers = lambda: None`) before calling `serve()`, otherwise uvicorn hijacks SIGINT.
- **workflow.yaml is pure YAML**: No markdown front matter. The legacy `.md` format with `---` delimiters is still supported but `.yaml` is the canonical format.
- **Prompt files use Jinja2 with silent undefined**: Missing variables become empty strings rather than raising errors. This is intentional — not all variables are available in every context.
