# Changelog

All notable changes to Stokowski are documented here.

---

## [Unreleased]

---

## [1.1.0] - 2026-08-31

Runs are now measurable, and the workflow is visible without opening a file.

### Added

- feat: run ledger — an append-only record of every stage and every gate
  decision, plus `stokowski --stats` for approval rate by classification and by
  the agent's own stated confidence (7c1f1b7)
- feat: workflow studio at `/studio` — the pipeline at a glance and editing for
  the obvious knobs, with comment-preserving YAML round-trips and validation
  before any write (7c1f1b7)
- feat: the agent's stated confidence applied as a Linear label alongside the
  work type, so a board can be filtered by it (7c1f1b7)
- feat: `pnpm start` / `studio` / `check` / `stats` / `test` aliases wrapping a
  launcher that finds the CLI and the workflow file itself (7c1f1b7)
- feat: prompts now require before/after measurement for any measurable claim,
  carry deployment preview URLs, name paired screenshots so they render as a
  pair, and check open PRs before touching shared code (7c1f1b7)

### Fixed

- fix: prompt files referenced by a state are now checked for existence. A
  typo'd path previously passed startup and every dry run, then failed when the
  agent launched (7c1f1b7)
- fix: literal `/api/v1` routes are declared before the
  `/api/v1/{issue_identifier}` catch-all, which was swallowing them
  (7c1f1b7)
- fix: the orchestrator no longer reads `self.cfg` during construction, which
  raised AssertionError on startup because the workflow is not loaded until the
  first tick (7c1f1b7)
- fix: releases merged without squashing are now detected. v1.0.0 was merged
  normally, the release Action found no match, reported success and shipped
  nothing — its tag had to be created by hand (5f27cfb)

---

## [1.0.0] - 2026-08-31

First release where Stokowski can account for what its agents did, prove it,
and show its working.

### Added

- feat: parse the real stream-json event shape — tool calls, thinking, tool
  errors, rate-limit windows and per-run cost, split into `events.py` and
  tested against an unedited capture (4e5231d)
- feat: expandable per-agent activity timelines in the dashboard, plus real
  cost, cache read/write split and the five-hour rate-limit window in both the
  dashboard and the CLI footer (4e5231d)
- feat: agent evidence — screenshots and exports written to
  `.stokowski/artifacts/`, uploaded to the Linear issue and cleared locally,
  isolated from the project repo via `.git/info/exclude` (4e5231d)
- feat: Stokowski renders Linear comments from a structured agent report;
  unsourced claims are published with a warning marker rather than dropped
  (4e5231d)
- feat: run classification applied as a Linear label — `stokowski/bug-fix`,
  `stokowski/improvement`, `stokowski/prototype` and others (4e5231d)
- feat: `ground-check` stage between investigation and the first human gate,
  running a fresh session to reproduce data sources and re-derive headline
  numbers (4e5231d)
- ci: run the test suite on pushes and pull requests across Python 3.11 and
  3.13, and dry-run the shipped example workflow (4e5231d)

### Fixed

- fix: token accounting ignored cache tokens and read a `total_tokens` field
  that does not exist, understating usage by roughly two orders of magnitude;
  usage was also overwritten per turn rather than accumulated, so multi-turn
  runs reported only their final turn. Cost was not reported at all
  (4e5231d)
- fix: tool activity never reached the dashboard — the parser looked for a
  top-level `tool_use` event the CLI does not emit (4e5231d)
- fix: the session id is now taken from `system/init`, so a stalled or
  timed-out turn stays resumable instead of restarting from scratch
  (4e5231d)
- fix: slash commands and skills are no longer banned in headless runs; the
  constraint is interactivity, not tooling (4e5231d)
- fix: example workspace hooks — `npm install` fails outright on a pnpm or
  yarn workspace, `--depth 1` breaks the review stage's diff, `before_run`
  exited 128 on a dirty working tree and failed every turn, and the hook
  timeout was too short for a cold install (4e5231d)
- fix: example prompts told agents to post Linear comments Stokowski now posts
  itself, and to run a code review skill the system prompt forbade
  (4e5231d)

### Changed

- **Breaking:** agents no longer author their own Linear summary comments.
  Custom prompts instructing them to do so will produce duplicates — remove
  those instructions, as the reporting contract is injected automatically
  (4e5231d)

---

## [0.5.0] - 2026-06-23

### Added

- feat: auto-start the web dashboard when `server.port` is set in config — no `--port` flag required; adds `server.host` config and a `--host` CLI flag (8f50d3b)
- feat: structured logging that tags log records with the issue they relate to via a `linked_to` field (66366fc)
- feat: show last-activity timestamps for running agents in both the dashboard and CLI status table (7611621)
- feat: live log panel in the dashboard — server-sent-events log stream with per-issue filtering, auto-scroll, and clear (008e121)

### Fixed

- fix: guard `server.host` resolution against an unloaded config so invalid configs fail with the clean startup error instead of an AttributeError (#38)
- fix: reconcile gates against Linear truth on each tick and at startup (#24)
- fix: route gate-approve through `_transition` for proper target-type dispatch (#22)
- fix: read blocker from `IssueRelation.issue`, not `relatedIssue` (#21)

### Changed

- fix: responsive font sizes in the dashboard using rem units and a width breakpoint (4fba2c7)

---

## [0.4.0] - 2026-03-23

### Added

- feat: pass workflow.yaml Linear credentials (`api_key`, `project_slug`, `endpoint`) to agent subprocesses as env vars — agents now use the same Linear credentials as Stokowski without relying on shell environment (770206c)

### Changed

- docs: workflow.yaml is now the single source of truth for Linear credentials — removed `.env.example` and updated README setup guide (a9ed097)
- docs: update README intro to position Stokowski as building beyond Symphony (a9ed097)

---

## [0.3.0] - 2026-03-15

### Added

- feat: add todo state — pick up issues from Todo and move to In Progress automatically (94b9d02)

### Fixed

- fix: single turn per dispatch in state machine mode — agents no longer blow past stage boundaries (ee8f0f6)
- fix: prevent re-dispatch loop when gate state transition fails — keep issue claimed and retry (60f391f)
- fix: include lifecycle context in multi-turn continuation prompts (ca82942)
- fix: increase subprocess stdout buffer to 10MB to handle large NDJSON lines (a346125)
- fix: check return value of `update_issue_state` at all call sites (6347584)
- fix: Linear 400 on state update — use `team.states` instead of `workflowStates` filter (77a0bad)
- fix: make `_SilentUndefined` inherit from `jinja2.Undefined` (1b6ddb3)
- fix: read `__version__` from package metadata instead of hardcoded string (ae74016)

---

## [0.2.2] - 2026-03-15

### Added

- feat: add todo state — pick up issues from Todo and move to In Progress automatically (94b9d02)

### Fixed

- fix: read `__version__` from package metadata instead of hardcoded string — update checker now shows correct version (ae74016)

---

## [0.2.1] - 2026-03-15

### Fixed

- fix: exclude `prompts/` from setuptools package discovery — fresh installs failed with "Multiple top-level packages" error (de001b4)
- fix: `project.license` deprecation warning — switched to SPDX string format (de001b4)

### Changed

- docs: rewrite Emdash comparison for accuracy — now an open-source desktop app with 22+ agent CLIs (15d15d4)
- docs: expand "What Stokowski adds beyond Symphony" with state machine, multi-runner, and prompt assembly sections (15d15d4)
- docs: clarify workflow diagram is a configurable example, not a fixed pipeline (f9879b6)

---

## [0.2.0] - 2026-03-13

### Added

- feat: configurable state machine workflows replacing fixed staged pipeline (`config.py`, `orchestrator.py`) (c0109d9)
- feat: three-layer prompt assembly — global prompt + stage prompt + lifecycle injection (`prompt.py`) (a2d61fd)
- feat: multi-runner support — Claude Code and Codex configurable per-state (`runner.py`) (8ff0e74)
- feat: gate protocol with "Gate Approved" / "Rework" Linear states and `max_rework` escalation (`orchestrator.py`) (b100531)
- feat: structured state tracking via HTML comments on Linear issues (`tracking.py`) (1a684c4)
- feat: Linear comment creation, comment fetching, and issue state mutation methods (`linear.py`) (e475351)
- feat: `on_stage_enter` lifecycle hook (`config.py`) (c5852c4)
- feat: Codex runner stall detection and timeout handling (`runner.py`) (db58f04)
- feat: pipeline completion moves issues to terminal state and cleans workspace (`orchestrator.py`) (d4a239c)
- feat: pending gates and runner type shown in web dashboard (`web.py`) (283b145, 5064a5b)
- feat: pipeline stage config dataclasses and validation (`config.py`) (8b769d8, a4dd34d)
- docs: example `workflow.yaml` and `prompts/*.example.md` files (da63359, da7d8bb)

### Fixed

- fix: gate claiming, duplicate comments, crash recovery, codex timeout (8f2ac3f)
- fix: transition key mismatch — example config used `success`, orchestrator expected `complete` (b18da0a)
- fix: use `<br/>` for line breaks in Mermaid node labels (754711f)

### Changed

- refactor: `WORKFLOW.md` (YAML front matter + prompt body) replaced by `workflow.yaml` + `prompts/` directory (c0109d9)
- refactor: `TrackerConfig.active_states` / `terminal_states` replaced by `LinearStatesConfig` mapping (c0109d9)
- refactor: `RunAttempt.stage` renamed to `state_name`, `runner_type` field removed (f0ccd48)
- refactor: web dashboard updated for state machine field names (09a7fa8)
- refactor: CLI auto-detects `workflow.yaml` → `workflow.yml` → `WORKFLOW.md` (0a8df54)
- docs: README rewritten for state machine model, multi-runner support, config reference (d6c7ad3, b18da0a)
- docs: CLAUDE.md updated for state machine workflow model (4775637)

### Chores

- chore: add `workflow.yaml`, `workflow.yml`, and `prompts/*.md` to `.gitignore` (59cb69e)

---

## [0.1.0] - 2026-03-08

### Added

- Async orchestration loop polling Linear for issues in configurable states
- Per-issue isolated git workspace lifecycle with `after_create`, `before_run`, `after_run`, `before_remove` hooks
- Claude Code CLI integration with `--output-format stream-json` streaming and multi-turn `--resume` sessions
- Exponential backoff retry and stall detection
- State reconciliation — running agents cancelled when Linear issue moves to terminal state
- Optional FastAPI web dashboard with live agent status
- Rich terminal UI with persistent status bar and single-key controls
- Jinja2 prompt templates with full issue context
- `.env` auto-load and `$VAR` env references in config
- Hot-reload of `WORKFLOW.md` on every poll tick
- Per-state concurrency limits
- `--dry-run` mode for config validation without dispatching agents
- Startup update check with footer indicator
- `last_run_at` template variable injected into agent prompts for rework timestamp filtering
- Append-only Linear comment strategy (planning + completion comment per run)

---

[Unreleased]: https://github.com/Sugar-Coffee/stokowski/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v1.1.0
[1.0.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v1.0.0
[0.5.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.5.0
[0.4.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.4.0
[0.3.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.3.0
[0.2.2]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.2.2
[0.2.1]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.2.1
[0.2.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.2.0
[0.1.0]: https://github.com/Sugar-Coffee/stokowski/releases/tag/v0.1.0
