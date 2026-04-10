# Changelog

All notable changes to Stokowski are documented here.

---

## [Unreleased]

---

## [0.5.0] - 2026-04-10

### Added

- feat: multi-workflow manager — run N workflows from a shared `stokowski.yaml` root config with independent start/stop controls (manager.py) (99b12bf)
- feat: GitHub Issues tracker backend — label-based state management with automatic label creation and atomic swaps (github_issues.py) (68d928b)
- feat: PR-based dispatch — `source: github-prs` processes pull requests directly without a tracker (68d928b)
- feat: crash recovery — persist and restore per-issue state (stage, session ID, workspace path) on restart (2974b97)
- feat: run history — completed agent runs recorded to `history.json` and displayed in dashboard (644d4f4, ddaec4d)
- feat: shared Linear rate limiter — all workflows share one API client with semaphore throttling and exponential cooldown (99b12bf, ce18abe)
- feat: dispatch queue — candidate issues survive failed API ticks instead of being dropped (1baec5f)
- feat: per-workflow filtering — `filter_labels`, `exclude_labels`, `pickup_states`, per-workflow `linear_states` (4b49141, 2ae492b, 789714c)
- feat: schedule-only workflows — `tracker_enabled: false` with cron schedule, no tracker dependency (4b49141)
- feat: workspace-free workflows — `workspace_enabled: false` skips worktree/clone creation (6c9ea43)
- feat: light/dark mode with system preference detection, responsive layout, ARIA accessibility (d859b85, 8b048fa, ff351c3)
- feat: webhook init — `stokowski init` configures secrets for both Linear and GitHub with HMAC verification (9d412f1)
- feat: Gemini CLI runner with session resumption and stream-json parsing (runner.py)
- feat: orphan agent cleanup — kill stale `claude -p` processes on startup (839bde8)
- feat: terminal states support custom `linear_state` and `"none"` (3d71c34)
- feat: workflow start time shown in dashboard tabs (f2c576d)
- feat: manager state recovery — previously running workflows auto-restart (ddaec4d)

### Fixed

- fix: preserve workspace when issue is blocked — unpushed WIP survives (bc90fcf)
- fix: CancelledError during shutdown on Python 3.14 — `task.cancelled()` guard (839bde8)
- fix: worktree reuse when branch is already checked out (a511b5e, 5d00b3c)
- fix: reconciliation includes pickup_states as valid active states (844ee78)
- fix: PR-based issues skip all Linear API calls (caf82c1, 62e8d99)
- fix: stopped workflows cannot be ticked by webhooks or poll loops (bd92bee)
- fix: Linear API retry on 400/429 with exponential backoff (f677fac)
- fix: serialize worktree creation with asyncio.Lock (d8be170)
- fix: prevent re-dispatch of completed issues (d8be170)
- fix: eliminate unnecessary Linear API calls from orchestrator (55ac029)
- fix: WorkspaceResult field names (419519a, 135b867)
- fix: webhook secret storage in .env and stokowski.yaml (889a07f, 71401d4)
- fix: light mode hardcoded colors and schedule validation (0146255)
- fix: file logging + don't cancel internally-tracked workers (9f0c5d6)
- fix: stop retry spam when no orchestrator slots available (1b2cf4e)
- fix: remove all HTML comments from Linear issues (72eb638)

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

[Unreleased]: https://github.com/erikpr1994/stokowski/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.5.0
[0.4.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.4.0
[0.3.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.3.0
[0.2.2]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.2
[0.2.1]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.1
[0.2.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.0
[0.1.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.1.0
