# Changelog

## [0.5.0](https://github.com/erikpr1994/stokowski/compare/v0.4.0...v0.5.0) (2026-04-10)

### Features

* multi-workflow manager — run N workflows from a shared `stokowski.yaml` root config with independent start/stop controls (manager.py) ([99b12bf](https://github.com/erikpr1994/stokowski/commit/99b12bf))
* GitHub Issues tracker backend — label-based state management with automatic label creation and atomic swaps (github_issues.py) ([68d928b](https://github.com/erikpr1994/stokowski/commit/68d928b))
* PR-based dispatch — `source: github-prs` processes pull requests directly without a tracker ([68d928b](https://github.com/erikpr1994/stokowski/commit/68d928b))
* crash recovery — persist and restore per-issue state (stage, session ID, workspace path) on restart ([2974b97](https://github.com/erikpr1994/stokowski/commit/2974b97))
* run history — completed agent runs recorded to `history.json` and displayed in dashboard ([644d4f4](https://github.com/erikpr1994/stokowski/commit/644d4f4)), ([ddaec4d](https://github.com/erikpr1994/stokowski/commit/ddaec4d))
* shared Linear rate limiter — all workflows share one API client with semaphore throttling and exponential cooldown ([99b12bf](https://github.com/erikpr1994/stokowski/commit/99b12bf)), ([ce18abe](https://github.com/erikpr1994/stokowski/commit/ce18abe))
* dispatch queue — candidate issues survive failed API ticks instead of being dropped ([1baec5f](https://github.com/erikpr1994/stokowski/commit/1baec5f))
* per-workflow filtering — `filter_labels`, `exclude_labels`, `pickup_states`, per-workflow `linear_states` ([4b49141](https://github.com/erikpr1994/stokowski/commit/4b49141)), ([2ae492b](https://github.com/erikpr1994/stokowski/commit/2ae492b)), ([789714c](https://github.com/erikpr1994/stokowski/commit/789714c))
* schedule-only workflows — `tracker_enabled: false` with cron schedule, no tracker dependency ([4b49141](https://github.com/erikpr1994/stokowski/commit/4b49141))
* workspace-free workflows — `workspace_enabled: false` skips worktree/clone creation ([6c9ea43](https://github.com/erikpr1994/stokowski/commit/6c9ea43))
* light/dark mode with system preference detection, responsive layout, ARIA accessibility ([d859b85](https://github.com/erikpr1994/stokowski/commit/d859b85)), ([8b048fa](https://github.com/erikpr1994/stokowski/commit/8b048fa)), ([ff351c3](https://github.com/erikpr1994/stokowski/commit/ff351c3))
* webhook init — `stokowski init` configures secrets for both Linear and GitHub with HMAC verification ([9d412f1](https://github.com/erikpr1994/stokowski/commit/9d412f1))
* Gemini CLI runner with session resumption and stream-json parsing (runner.py)
* orphan agent cleanup — kill stale `claude -p` processes on startup ([839bde8](https://github.com/erikpr1994/stokowski/commit/839bde8))
* terminal states support custom `linear_state` and `"none"` ([3d71c34](https://github.com/erikpr1994/stokowski/commit/3d71c34))
* workflow start time shown in dashboard tabs ([f2c576d](https://github.com/erikpr1994/stokowski/commit/f2c576d))
* manager state recovery — previously running workflows auto-restart ([ddaec4d](https://github.com/erikpr1994/stokowski/commit/ddaec4d))

### Bug Fixes

* preserve workspace when issue is blocked — unpushed WIP survives ([bc90fcf](https://github.com/erikpr1994/stokowski/commit/bc90fcf))
* CancelledError during shutdown on Python 3.14 — `task.cancelled()` guard ([839bde8](https://github.com/erikpr1994/stokowski/commit/839bde8))
* worktree reuse when branch is already checked out ([a511b5e](https://github.com/erikpr1994/stokowski/commit/a511b5e)), ([5d00b3c](https://github.com/erikpr1994/stokowski/commit/5d00b3c))
* reconciliation includes pickup_states as valid active states ([844ee78](https://github.com/erikpr1994/stokowski/commit/844ee78))
* PR-based issues skip all Linear API calls ([caf82c1](https://github.com/erikpr1994/stokowski/commit/caf82c1)), ([62e8d99](https://github.com/erikpr1994/stokowski/commit/62e8d99))
* stopped workflows cannot be ticked by webhooks or poll loops ([bd92bee](https://github.com/erikpr1994/stokowski/commit/bd92bee))
* Linear API retry on 400/429 with exponential backoff ([f677fac](https://github.com/erikpr1994/stokowski/commit/f677fac))
* serialize worktree creation with asyncio.Lock ([d8be170](https://github.com/erikpr1994/stokowski/commit/d8be170))
* prevent re-dispatch of completed issues ([d8be170](https://github.com/erikpr1994/stokowski/commit/d8be170))
* eliminate unnecessary Linear API calls from orchestrator ([55ac029](https://github.com/erikpr1994/stokowski/commit/55ac029))
* WorkspaceResult field names ([419519a](https://github.com/erikpr1994/stokowski/commit/419519a)), ([135b867](https://github.com/erikpr1994/stokowski/commit/135b867))
* webhook secret storage in .env and stokowski.yaml ([889a07f](https://github.com/erikpr1994/stokowski/commit/889a07f)), ([71401d4](https://github.com/erikpr1994/stokowski/commit/71401d4))
* light mode hardcoded colors and schedule validation ([0146255](https://github.com/erikpr1994/stokowski/commit/0146255))
* file logging + don't cancel internally-tracked workers ([9f0c5d6](https://github.com/erikpr1994/stokowski/commit/9f0c5d6))
* stop retry spam when no orchestrator slots available ([1b2cf4e](https://github.com/erikpr1994/stokowski/commit/1b2cf4e))
* remove all HTML comments from Linear issues ([72eb638](https://github.com/erikpr1994/stokowski/commit/72eb638))

## [0.4.0](https://github.com/erikpr1994/stokowski/compare/v0.3.0...v0.4.0) (2026-03-23)

### Features

* pass workflow.yaml Linear credentials (`api_key`, `project_slug`, `endpoint`) to agent subprocesses as env vars — agents now use the same Linear credentials as Stokowski without relying on shell environment ([770206c](https://github.com/erikpr1994/stokowski/commit/770206c))

### Documentation

* workflow.yaml is now the single source of truth for Linear credentials — removed `.env.example` and updated README setup guide ([a9ed097](https://github.com/erikpr1994/stokowski/commit/a9ed097))
* update README intro to position Stokowski as building beyond Symphony ([a9ed097](https://github.com/erikpr1994/stokowski/commit/a9ed097))

## [0.3.0](https://github.com/erikpr1994/stokowski/compare/v0.2.2...v0.3.0) (2026-03-15)

### Features

* add todo state — pick up issues from Todo and move to In Progress automatically ([94b9d02](https://github.com/erikpr1994/stokowski/commit/94b9d02))

### Bug Fixes

* single turn per dispatch in state machine mode — agents no longer blow past stage boundaries ([ee8f0f6](https://github.com/erikpr1994/stokowski/commit/ee8f0f6))
* prevent re-dispatch loop when gate state transition fails — keep issue claimed and retry ([60f391f](https://github.com/erikpr1994/stokowski/commit/60f391f))
* include lifecycle context in multi-turn continuation prompts ([ca82942](https://github.com/erikpr1994/stokowski/commit/ca82942))
* increase subprocess stdout buffer to 10MB to handle large NDJSON lines ([a346125](https://github.com/erikpr1994/stokowski/commit/a346125))
* check return value of `update_issue_state` at all call sites ([6347584](https://github.com/erikpr1994/stokowski/commit/6347584))
* Linear 400 on state update — use `team.states` instead of `workflowStates` filter ([77a0bad](https://github.com/erikpr1994/stokowski/commit/77a0bad))
* make `_SilentUndefined` inherit from `jinja2.Undefined` ([1b6ddb3](https://github.com/erikpr1994/stokowski/commit/1b6ddb3))
* read `__version__` from package metadata instead of hardcoded string ([ae74016](https://github.com/erikpr1994/stokowski/commit/ae74016))

## [0.2.2](https://github.com/erikpr1994/stokowski/compare/v0.2.1...v0.2.2) (2026-03-15)

### Features

* add todo state — pick up issues from Todo and move to In Progress automatically ([94b9d02](https://github.com/erikpr1994/stokowski/commit/94b9d02))

### Bug Fixes

* read `__version__` from package metadata instead of hardcoded string — update checker now shows correct version ([ae74016](https://github.com/erikpr1994/stokowski/commit/ae74016))

## [0.2.1](https://github.com/erikpr1994/stokowski/compare/v0.2.0...v0.2.1) (2026-03-15)

### Bug Fixes

* exclude `prompts/` from setuptools package discovery — fresh installs failed with "Multiple top-level packages" error ([de001b4](https://github.com/erikpr1994/stokowski/commit/de001b4))
* `project.license` deprecation warning — switched to SPDX string format ([de001b4](https://github.com/erikpr1994/stokowski/commit/de001b4))

### Documentation

* rewrite Emdash comparison for accuracy — now an open-source desktop app with 22+ agent CLIs ([15d15d4](https://github.com/erikpr1994/stokowski/commit/15d15d4))
* expand "What Stokowski adds beyond Symphony" with state machine, multi-runner, and prompt assembly sections ([15d15d4](https://github.com/erikpr1994/stokowski/commit/15d15d4))
* clarify workflow diagram is a configurable example, not a fixed pipeline ([f9879b6](https://github.com/erikpr1994/stokowski/commit/f9879b6))

## [0.2.0](https://github.com/erikpr1994/stokowski/compare/v0.1.0...v0.2.0) (2026-03-13)

### Features

* configurable state machine workflows replacing fixed staged pipeline (`config.py`, `orchestrator.py`) ([c0109d9](https://github.com/erikpr1994/stokowski/commit/c0109d9))
* three-layer prompt assembly — global prompt + stage prompt + lifecycle injection (`prompt.py`) ([a2d61fd](https://github.com/erikpr1994/stokowski/commit/a2d61fd))
* multi-runner support — Claude Code and Codex configurable per-state (`runner.py`) ([8ff0e74](https://github.com/erikpr1994/stokowski/commit/8ff0e74))
* gate protocol with "Gate Approved" / "Rework" Linear states and `max_rework` escalation (`orchestrator.py`) ([b100531](https://github.com/erikpr1994/stokowski/commit/b100531))
* structured state tracking via HTML comments on Linear issues (`tracking.py`) ([1a684c4](https://github.com/erikpr1994/stokowski/commit/1a684c4))
* Linear comment creation, comment fetching, and issue state mutation methods (`linear.py`) ([e475351](https://github.com/erikpr1994/stokowski/commit/e475351))
* `on_stage_enter` lifecycle hook (`config.py`) ([c5852c4](https://github.com/erikpr1994/stokowski/commit/c5852c4))
* Codex runner stall detection and timeout handling (`runner.py`) ([db58f04](https://github.com/erikpr1994/stokowski/commit/db58f04))
* pipeline completion moves issues to terminal state and cleans workspace (`orchestrator.py`) ([d4a239c](https://github.com/erikpr1994/stokowski/commit/d4a239c))
* pending gates and runner type shown in web dashboard (`web.py`) ([283b145](https://github.com/erikpr1994/stokowski/commit/283b145)), ([5064a5b](https://github.com/erikpr1994/stokowski/commit/5064a5b))
* pipeline stage config dataclasses and validation (`config.py`) ([8b769d8](https://github.com/erikpr1994/stokowski/commit/8b769d8)), ([a4dd34d](https://github.com/erikpr1994/stokowski/commit/a4dd34d))
* example `workflow.yaml` and `prompts/*.example.md` files ([da63359](https://github.com/erikpr1994/stokowski/commit/da63359)), ([da7d8bb](https://github.com/erikpr1994/stokowski/commit/da7d8bb))

### Bug Fixes

* gate claiming, duplicate comments, crash recovery, codex timeout ([8f2ac3f](https://github.com/erikpr1994/stokowski/commit/8f2ac3f))
* transition key mismatch — example config used `success`, orchestrator expected `complete` ([b18da0a](https://github.com/erikpr1994/stokowski/commit/b18da0a))
* use `<br/>` for line breaks in Mermaid node labels ([754711f](https://github.com/erikpr1994/stokowski/commit/754711f))

### Code Refactoring

* `WORKFLOW.md` (YAML front matter + prompt body) replaced by `workflow.yaml` + `prompts/` directory ([c0109d9](https://github.com/erikpr1994/stokowski/commit/c0109d9))
* `TrackerConfig.active_states` / `terminal_states` replaced by `LinearStatesConfig` mapping ([c0109d9](https://github.com/erikpr1994/stokowski/commit/c0109d9))
* `RunAttempt.stage` renamed to `state_name`, `runner_type` field removed ([f0ccd48](https://github.com/erikpr1994/stokowski/commit/f0ccd48))
* web dashboard updated for state machine field names ([09a7fa8](https://github.com/erikpr1994/stokowski/commit/09a7fa8))
* CLI auto-detects `workflow.yaml` → `workflow.yml` → `WORKFLOW.md` ([0a8df54](https://github.com/erikpr1994/stokowski/commit/0a8df54))

### Documentation

* README rewritten for state machine model, multi-runner support, config reference ([d6c7ad3](https://github.com/erikpr1994/stokowski/commit/d6c7ad3)), ([b18da0a](https://github.com/erikpr1994/stokowski/commit/b18da0a))
* CLAUDE.md updated for state machine workflow model ([4775637](https://github.com/erikpr1994/stokowski/commit/4775637))

### Miscellaneous Chores

* add `workflow.yaml`, `workflow.yml`, and `prompts/*.md` to `.gitignore` ([59cb69e](https://github.com/erikpr1994/stokowski/commit/59cb69e))

## [0.1.0](https://github.com/erikpr1994/stokowski/releases/tag/v0.1.0) (2026-03-08)

### Features

* Async orchestration loop polling Linear for issues in configurable states
* Per-issue isolated git workspace lifecycle with `after_create`, `before_run`, `after_run`, `before_remove` hooks
* Claude Code CLI integration with `--output-format stream-json` streaming and multi-turn `--resume` sessions
* Exponential backoff retry and stall detection
* State reconciliation — running agents cancelled when Linear issue moves to terminal state
* Optional FastAPI web dashboard with live agent status
* Rich terminal UI with persistent status bar and single-key controls
* Jinja2 prompt templates with full issue context
* `.env` auto-load and `$VAR` env references in config
* Hot-reload of `WORKFLOW.md` on every poll tick
* Per-state concurrency limits
* `--dry-run` mode for config validation without dispatching agents
* Startup update check with footer indicator
* `last_run_at` template variable injected into agent prompts for rework timestamp filtering
* Append-only Linear comment strategy (planning + completion comment per run)
