"""Main orchestration loop - polls Linear, dispatches agents, manages state."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

from .config import (
    ClaudeConfig,
    HooksConfig,
    ServiceConfig,
    StateConfig,
    WorkflowDefinition,
    merge_state_config,
    parse_workflow_file,
    validate_config,
)
from .models import Issue, RetryEntry, RunAttempt
from .tracker import TrackerClient
from .prompt import assemble_prompt, build_lifecycle_section
from .runner import run_agent_turn, run_turn
from .tracking import make_gate_comment, make_state_comment, parse_latest_tracking
from .workspace import ensure_workspace, remove_workspace, WorkspaceResult

logger = logging.getLogger("stokowski")


class Orchestrator:
    def __init__(self, workflow_path: str | Path):
        self.workflow_path = Path(workflow_path)
        self.workflow: WorkflowDefinition | None = None

        # Runtime state
        self.running: dict[str, RunAttempt] = {}  # issue_id -> RunAttempt
        self.claimed: set[str] = set()
        self.retry_attempts: dict[str, RetryEntry] = {}
        self.completed: set[str] = set()

        # Aggregate metrics
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_tokens: int = 0
        self.total_seconds_running: float = 0

        # Internal
        self._tracker: TrackerClient | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._retry_timers: dict[str, asyncio.TimerHandle] = {}
        self._child_pids: set[int] = set()  # Track claude subprocess PIDs
        self._last_session_ids: dict[str, str] = {}  # issue_id -> last known session_id
        self._jinja = Environment(undefined=StrictUndefined)
        self._running = False
        self._last_issues: dict[str, Issue] = {}
        self._last_completed_at: dict[str, datetime] = {}  # issue_id -> last worker completion time

        # State machine tracking
        self._issue_current_state: dict[str, str] = {}   # issue_id -> internal state name
        self._issue_state_runs: dict[str, int] = {}       # issue_id -> run number for current state
        self._pending_gates: dict[str, str] = {}           # issue_id -> gate state name

        # Schedule tracking
        self._last_schedule_fire: datetime | None = None

        # Webhook coalescing
        self._webhook_tick_pending: bool = False

    @property
    def cfg(self) -> ServiceConfig:
        assert self.workflow is not None
        return self.workflow.config

    def _load_workflow(self) -> list[str]:
        """Load/reload workflow file. Returns validation errors."""
        try:
            self.workflow = parse_workflow_file(self.workflow_path)
        except Exception as e:
            return [f"Workflow load error: {e}"]
        return validate_config(self.cfg)

    def _ensure_tracker_client(self) -> TrackerClient:
        if self._tracker is None:
            if self.cfg.tracker.kind == "github":
                from .github_issues import GitHubIssuesClient
                self._tracker = GitHubIssuesClient(
                    owner=self.cfg.tracker.github_owner,
                    repo=self.cfg.tracker.github_repo,
                    token=self.cfg.resolved_api_key(),
                )
            else:
                from .linear import LinearClient
                self._tracker = LinearClient(
                    endpoint=self.cfg.tracker.endpoint,
                    api_key=self.cfg.resolved_api_key(),
                )
        return self._tracker

    async def start(self):
        """Start the orchestration loop."""
        errors = self._load_workflow()
        if errors:
            for e in errors:
                logger.error(f"Config error: {e}")
            raise RuntimeError(f"Startup validation failed: {errors}")

        logger.info(
            f"Starting Stokowski "
            f"project={self.cfg.tracker.project_slug} "
            f"max_agents={self.cfg.agent.max_concurrent_agents} "
            f"poll_ms={self.cfg.polling.interval_ms}"
        )

        self._running = True
        self._stop_event = asyncio.Event()

        # Startup terminal cleanup
        await self._startup_cleanup()

        # Main poll loop
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}")

            # Interruptible sleep
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.cfg.polling.interval_ms / 1000,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # Normal poll interval elapsed

    async def stop(self):
        """Stop the orchestration loop and kill all running agents."""
        self._running = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()

        # Kill all child claude processes first
        for pid in list(self._child_pids):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        self._child_pids.clear()

        # Cancel async tasks
        for issue_id, task in list(self._tasks.items()):
            task.cancel()
        # Give them a moment to finish
        if self._tasks:
            await asyncio.sleep(0.5)
        self._tasks.clear()

        if self._tracker:
            await self._tracker.close()

    async def _startup_cleanup(self):
        """Remove workspaces for issues already in terminal states."""
        try:
            client = self._ensure_tracker_client()
            terminal = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                self.cfg.terminal_linear_states(),
                team_key=self.cfg.tracker.team_key,
            )
            ws_root = self.cfg.workspace.resolved_root()
            for issue in terminal:
                await remove_workspace(ws_root, issue.identifier, self.cfg.hooks, workspace_cfg=self.cfg.workspace)
            if terminal:
                logger.info(f"Cleaned {len(terminal)} terminal workspaces")
        except Exception as e:
            logger.warning(f"Startup cleanup failed (continuing): {e}")

    async def _resolve_current_state(self, issue: Issue) -> tuple[str, int]:
        """Resolve current state machine state for an issue.
        Returns (state_name, run).
        """
        # Check cache first
        if issue.id in self._issue_current_state:
            state_name = self._issue_current_state[issue.id]
            run = self._issue_state_runs.get(issue.id, 1)
            return state_name, run

        # Fetch comments from Linear and parse latest tracking
        client = self._ensure_tracker_client()
        comments = await client.fetch_comments(issue.id)
        tracking = parse_latest_tracking(comments)

        entry = self.cfg.entry_state_for_issue(issue.labels)
        if entry is None:
            raise RuntimeError("No entry state defined in config")

        # No tracking → entry state (may be label-routed), run 1
        if tracking is None:
            self._issue_current_state[issue.id] = entry
            self._issue_state_runs[issue.id] = 1
            return entry, 1

        if tracking["type"] == "state":
            state_name = tracking.get("state", entry)
            run = tracking.get("run", 1)
            if state_name in self.cfg.states:
                self._issue_current_state[issue.id] = state_name
                self._issue_state_runs[issue.id] = run
                return state_name, run
            # Unknown state → fallback to entry
            self._issue_current_state[issue.id] = entry
            self._issue_state_runs[issue.id] = 1
            return entry, 1

        if tracking["type"] == "gate":
            gate_state = tracking.get("state", "")
            status = tracking.get("status", "")
            run = tracking.get("run", 1)

            if status == "waiting":
                if gate_state in self.cfg.states:
                    self._issue_current_state[issue.id] = gate_state
                    self._issue_state_runs[issue.id] = run
                    self._pending_gates[issue.id] = gate_state
                    return gate_state, run

            elif status == "approved":
                gate_cfg = self.cfg.states.get(gate_state)
                if gate_cfg and "approve" in gate_cfg.transitions:
                    target = gate_cfg.transitions["approve"]
                    self._issue_current_state[issue.id] = target
                    self._issue_state_runs[issue.id] = run
                    return target, run

            elif status == "rework":
                gate_cfg = self.cfg.states.get(gate_state)
                rework_to = tracking.get("rework_to", "")
                if not rework_to and gate_cfg:
                    rework_to = gate_cfg.rework_to or ""
                if rework_to and rework_to in self.cfg.states:
                    self._issue_current_state[issue.id] = rework_to
                    self._issue_state_runs[issue.id] = run
                    return rework_to, run

        # Fallback to entry state
        self._issue_current_state[issue.id] = entry
        self._issue_state_runs[issue.id] = 1
        return entry, 1

    async def _safe_enter_gate(self, issue: Issue, state_name: str):
        """Wrapper around _enter_gate that logs errors."""
        try:
            await self._enter_gate(issue, state_name)
        except Exception as e:
            logger.error(
                f"Enter gate failed issue={issue.identifier} "
                f"gate={state_name}: {e}",
                exc_info=True,
            )

    async def _enter_gate(self, issue: Issue, state_name: str):
        """Move issue to gate state and post tracking comment."""
        state_cfg = self.cfg.states.get(state_name)
        prompt = state_cfg.prompt if state_cfg else ""
        run = self._issue_state_runs.get(issue.id, 1)

        client = self._ensure_tracker_client()

        comment = make_gate_comment(
            state=state_name,
            status="waiting",
            prompt=prompt or "",
            run=run,
        )
        await client.post_comment(issue.id, comment)

        review_state = self.cfg.linear_states.review
        moved = await client.update_issue_state(issue.id, review_state)
        if not moved:
            logger.error(
                f"Failed to move {issue.identifier} to review state '{review_state}' "
                f"— issue will remain claimed to prevent re-dispatch loop"
            )
            # Keep claimed so the issue doesn't get re-dispatched while
            # still in the active Linear state. Track the gate so
            # _handle_gate_responses can pick it up if the state is
            # changed manually.
            self._pending_gates[issue.id] = state_name
            self._issue_current_state[issue.id] = state_name
            self.running.pop(issue.id, None)
            self._tasks.pop(issue.id, None)
            # Schedule a retry to attempt the state move again
            self._schedule_retry(issue, attempt_num=0, delay_ms=10_000)
            return

        self._pending_gates[issue.id] = state_name
        self._issue_current_state[issue.id] = state_name
        # Release from running/claimed so it doesn't block slots
        self.running.pop(issue.id, None)
        self._tasks.pop(issue.id, None)
        self.claimed.discard(issue.id)

        logger.info(
            f"Gate entered issue={issue.identifier} gate={state_name} "
            f"run={run}"
        )

    async def _move_to_blocked(self, issue: Issue, attempt: RunAttempt):
        """Move an issue to Blocked state when the agent signals it can't proceed."""
        try:
            client = self._ensure_tracker_client()
            blocked_state = self.cfg.linear_states.blocked

            # Post a comment with the agent's last message (contains the reason)
            reason = attempt.last_message or "Agent could not complete this issue"
            comment = (
                f"**Stokowski: Issue blocked**\n\n"
                f"{reason}\n\n"
                f"<!-- stokowski:blocked {{\"state\":\"{attempt.state_name}\","
                f"\"reason\":\"{reason[:100]}\"}} -->"
            )
            await client.post_comment(issue.id, comment)

            # Move to Blocked state
            moved = await client.update_issue_state(issue.id, blocked_state)
            if moved:
                logger.info(f"Moved {issue.identifier} to Blocked: {reason[:100]}")
            else:
                logger.warning(f"Failed to move {issue.identifier} to Blocked state")

            # Clean up workspace
            try:
                ws_root = self.cfg.workspace.resolved_root()
                await remove_workspace(
                    ws_root, issue.identifier, self.cfg.hooks,
                    workspace_cfg=self.cfg.workspace,
                )
            except Exception as e:
                logger.warning(f"Failed to remove workspace for blocked {issue.identifier}: {e}")

        except Exception as e:
            logger.error(f"Failed to block {issue.identifier}: {e}")

        # Release tracking
        self._issue_current_state.pop(issue.id, None)
        self._issue_state_runs.pop(issue.id, None)
        self._pending_gates.pop(issue.id, None)
        self._last_session_ids.pop(issue.id, None)
        self.claimed.discard(issue.id)

    async def _safe_transition(self, issue: Issue, transition_name: str):
        """Wrapper around _transition that logs errors instead of silently swallowing them."""
        try:
            await self._transition(issue, transition_name)
        except Exception as e:
            logger.error(
                f"Transition failed issue={issue.identifier} "
                f"transition={transition_name}: {e}",
                exc_info=True,
            )
            # Release claimed so the issue can be retried on next tick
            self.claimed.discard(issue.id)

    async def _transition(self, issue: Issue, transition_name: str):
        """Follow a transition from the current state.

        Handles target types:
        - terminal → move to Done, clean workspace, release tracking
        - gate → enter gate
        - agent → post state comment, ensure active Linear state, schedule retry
        """
        current_state_name = self._issue_current_state.get(issue.id)
        if not current_state_name:
            logger.warning(f"No current state for {issue.identifier}, cannot transition")
            return

        current_cfg = self.cfg.states.get(current_state_name)
        if not current_cfg:
            logger.warning(f"Unknown state '{current_state_name}' for {issue.identifier}")
            return

        target_name = current_cfg.transitions.get(transition_name)
        if not target_name:
            logger.warning(
                f"No '{transition_name}' transition from state '{current_state_name}' "
                f"for {issue.identifier}"
            )
            return

        target_cfg = self.cfg.states.get(target_name)
        if not target_cfg:
            logger.warning(f"Transition target '{target_name}' not found in config")
            return

        run = self._issue_state_runs.get(issue.id, 1)

        if target_cfg.type == "terminal":
            # Move issue to terminal state
            terminal_state = self.cfg.terminal_linear_states()[0] if self.cfg.terminal_linear_states() else "Done"
            try:
                client = self._ensure_tracker_client()
                moved = await client.update_issue_state(issue.id, terminal_state)
                if moved:
                    logger.info(f"Moved {issue.identifier} to terminal state '{terminal_state}'")
                else:
                    logger.warning(f"Failed to move {issue.identifier} to terminal state '{terminal_state}'")
            except Exception as e:
                logger.warning(f"Failed to move {issue.identifier} to terminal: {e}")
            # Clean up workspace
            try:
                ws_root = self.cfg.workspace.resolved_root()
                await remove_workspace(ws_root, issue.identifier, self.cfg.hooks, workspace_cfg=self.cfg.workspace)
            except Exception as e:
                logger.warning(f"Failed to remove workspace for {issue.identifier}: {e}")
            # Clean up tracking state
            self._issue_current_state.pop(issue.id, None)
            self._issue_state_runs.pop(issue.id, None)
            self._pending_gates.pop(issue.id, None)
            self._last_session_ids.pop(issue.id, None)
            self.claimed.discard(issue.id)
            self.completed.add(issue.id)

        elif target_cfg.type == "gate":
            self._issue_current_state[issue.id] = target_name
            await self._enter_gate(issue, target_name)

        else:
            # Agent state — post state comment, ensure active Linear state, schedule retry
            self._issue_current_state[issue.id] = target_name
            client = self._ensure_tracker_client()
            comment = make_state_comment(
                state=target_name,
                run=run,
            )
            await client.post_comment(issue.id, comment)

            # Ensure issue is in active Linear state
            active_state = self.cfg.linear_states.active
            moved = await client.update_issue_state(issue.id, active_state)
            if not moved:
                logger.warning(f"Failed to move {issue.identifier} to active state '{active_state}'")

            self._schedule_retry(issue, attempt_num=0, delay_ms=1000)

    async def _handle_gate_responses(self):
        """Check for gate-approved and rework issues, handle transitions."""
        # Early return if no gate states in config
        has_gates = any(sc.type == "gate" for sc in self.cfg.states.values())
        if not has_gates:
            return

        client = self._ensure_tracker_client()

        # Fetch gate-approved issues
        try:
            approved_issues = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                [self.cfg.linear_states.gate_approved],
                team_key=self.cfg.tracker.team_key,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch gate-approved issues: {e}")
            approved_issues = []

        for issue in approved_issues:
            if issue.id in self.running or issue.id in self.claimed:
                continue

            gate_state = self._pending_gates.pop(issue.id, None)
            if not gate_state:
                comments = await client.fetch_comments(issue.id)
                tracking = parse_latest_tracking(comments)
                if tracking and tracking.get("type") == "gate" and tracking.get("status") == "waiting":
                    gate_state = tracking.get("state", "")

            if gate_state:
                run = self._issue_state_runs.get(issue.id, 1)
                comment = make_gate_comment(
                    state=gate_state, status="approved", run=run,
                )
                await client.post_comment(issue.id, comment)

                # Follow approve transition
                self._issue_current_state[issue.id] = gate_state
                gate_cfg = self.cfg.states.get(gate_state)
                if gate_cfg and "approve" in gate_cfg.transitions:
                    target = gate_cfg.transitions["approve"]
                    self._issue_current_state[issue.id] = target

                active_state = self.cfg.linear_states.active
                moved = await client.update_issue_state(issue.id, active_state)
                if moved:
                    issue.state = active_state
                else:
                    logger.warning(f"Failed to move {issue.identifier} to active after gate approval")
                self._last_issues[issue.id] = issue
                logger.info(f"Gate approved issue={issue.identifier} gate={gate_state}")

        # Fetch rework issues
        try:
            rework_issues = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                [self.cfg.linear_states.rework],
                team_key=self.cfg.tracker.team_key,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch rework issues: {e}")
            rework_issues = []

        for issue in rework_issues:
            if issue.id in self.running or issue.id in self.claimed:
                continue

            gate_state = self._pending_gates.pop(issue.id, None)
            if not gate_state:
                comments = await client.fetch_comments(issue.id)
                tracking = parse_latest_tracking(comments)
                if tracking and tracking.get("type") == "gate" and tracking.get("status") == "waiting":
                    gate_state = tracking.get("state", "")

            if gate_state:
                gate_cfg = self.cfg.states.get(gate_state)
                rework_to = gate_cfg.rework_to if gate_cfg else ""
                if not rework_to:
                    logger.warning(f"Gate {gate_state} has no rework_to target, skipping")
                    continue

                # Check max_rework
                run = self._issue_state_runs.get(issue.id, 1)
                max_rework = gate_cfg.max_rework if gate_cfg else None
                if max_rework is not None and run >= max_rework:
                    # Exceeded max rework — post escalated comment, don't transition
                    comment = make_gate_comment(
                        state=gate_state, status="escalated", run=run,
                    )
                    await client.post_comment(issue.id, comment)
                    logger.warning(
                        f"Max rework exceeded issue={issue.identifier} "
                        f"gate={gate_state} run={run} max={max_rework}"
                    )
                    continue

                new_run = run + 1
                self._issue_state_runs[issue.id] = new_run

                comment = make_gate_comment(
                    state=gate_state, status="rework",
                    rework_to=rework_to, run=new_run,
                )
                await client.post_comment(issue.id, comment)

                self._issue_current_state[issue.id] = rework_to

                active_state = self.cfg.linear_states.active
                moved = await client.update_issue_state(issue.id, active_state)
                if moved:
                    issue.state = active_state
                else:
                    logger.warning(f"Failed to move {issue.identifier} to active after rework")
                self._last_issues[issue.id] = issue
                logger.info(
                    f"Rework issue={issue.identifier} gate={gate_state} "
                    f"rework_to={rework_to} run={new_run}"
                )

    async def webhook_tick(self):
        """Coalesced tick triggered by webhook. Deduplicates rapid calls."""
        if self._webhook_tick_pending:
            return
        self._webhook_tick_pending = True
        await asyncio.sleep(0.5)  # coalesce rapid-fire webhooks
        self._webhook_tick_pending = False
        try:
            await self._tick()
        except Exception as e:
            logger.error(f"Webhook-triggered tick error: {e}")

    async def _check_schedule(self):
        """Check if a scheduled issue should be created via create_command."""
        schedule = self.cfg.schedule
        if not schedule or not schedule.cron or not schedule.create_command:
            return

        try:
            from croniter import croniter
        except ImportError:
            logger.warning("Schedule requires 'croniter' package: pip install stokowski[schedule]")
            return

        now = datetime.now(timezone.utc)

        cron = croniter(schedule.cron, now.replace(tzinfo=None))
        prev_fire_naive = cron.get_prev(datetime)
        prev_fire = prev_fire_naive.replace(tzinfo=timezone.utc)

        if self._last_schedule_fire and self._last_schedule_fire >= prev_fire:
            return

        # Substitute placeholders in create_command
        fire_date = prev_fire.strftime("%Y-%m-%d")
        fire_datetime = prev_fire.strftime("%Y-%m-%d %H:%M")
        command = schedule.create_command.replace("{date}", fire_date).replace("{datetime}", fire_datetime)

        logger.info(f"Schedule fired, running create_command")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.cfg.agent_env(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                logger.info(f"Schedule create_command succeeded: {stdout.decode().strip()[:200]}")
            else:
                logger.error(
                    f"Schedule create_command failed (exit {proc.returncode}): "
                    f"{stderr.decode().strip()[:200]}"
                )
        except asyncio.TimeoutError:
            logger.error("Schedule create_command timed out after 30s")
        except Exception as e:
            logger.error(f"Schedule create_command error: {e}")

        self._last_schedule_fire = prev_fire

    async def _tick(self):
        """Single poll tick: reconcile, validate, fetch, dispatch."""
        # Reload workflow (supports hot-reload)
        errors = self._load_workflow()

        # Check scheduled issue creation
        try:
            await self._check_schedule()
        except Exception as e:
            logger.warning(f"Schedule check failed: {e}")

        # Part 1: Reconcile running issues
        await self._reconcile()

        # Handle gate responses
        await self._handle_gate_responses()

        # Part 2: Validate config
        if errors:
            logger.warning(f"Config invalid, skipping dispatch: {errors}")
            return

        # Part 3: Fetch candidates
        try:
            client = self._ensure_tracker_client()
            candidates = await client.fetch_candidate_issues(
                self.cfg.tracker.project_slug,
                self.cfg.active_linear_states(),
                team_key=self.cfg.tracker.team_key,
            )
        except Exception as e:
            logger.error(f"Failed to fetch candidates: {e}")
            return

        # Cache issues for retry lookup
        for issue in candidates:
            self._last_issues[issue.id] = issue

        # Part 4: Sort by priority
        candidates.sort(
            key=lambda i: (
                i.priority if i.priority is not None else 999,
                i.created_at or datetime.min.replace(tzinfo=timezone.utc),
                i.identifier,
            )
        )

        # Resolve state for new issues before dispatch
        for issue in candidates:
            if issue.id not in self._issue_current_state and issue.id not in self.running:
                try:
                    await self._resolve_current_state(issue)
                except Exception as e:
                    logger.warning(f"Failed to resolve state for {issue.identifier}: {e}")

        # Part 5: Dispatch
        available_slots = max(
            self.cfg.agent.max_concurrent_agents - len(self.running), 0
        )

        for issue in candidates:
            if available_slots <= 0:
                break
            if not self._is_eligible(issue):
                continue

            # Per-state concurrency check (global)
            issue_machine_state = self._issue_current_state.get(issue.id, "")
            state_key_for_limit = issue_machine_state or issue.state.strip().lower()
            state_limit = self.cfg.agent.max_concurrent_agents_by_state.get(state_key_for_limit)
            if state_limit is not None:
                state_count = sum(
                    1
                    for r in self.running.values()
                    if (self._issue_current_state.get(r.issue_id, "") or
                        self._last_issues.get(r.issue_id, Issue(id="", identifier="", title="")).state.strip().lower())
                    == state_key_for_limit
                )
                if state_count >= state_limit:
                    continue

            # Per-project concurrency check
            project_limits = self.cfg.agent.max_concurrent_by_project
            if project_limits and issue.project_slug:
                proj_limit = project_limits.get(state_key_for_limit)
                if proj_limit is not None:
                    proj_count = sum(
                        1
                        for r in self.running.values()
                        if (self._last_issues.get(r.issue_id, Issue(id="", identifier="", title="")).project_slug
                            == issue.project_slug)
                        and (self._issue_current_state.get(r.issue_id, "") or
                             self._last_issues.get(r.issue_id, Issue(id="", identifier="", title="")).state.strip().lower())
                        == state_key_for_limit
                    )
                    if proj_count >= proj_limit:
                        continue

            self._dispatch(issue)
            available_slots -= 1

    def _is_eligible(self, issue: Issue) -> bool:
        """Check if an issue is eligible for dispatch."""
        if not issue.id or not issue.identifier or not issue.title or not issue.state:
            return False

        state_lower = issue.state.strip().lower()
        active_lower = [s.strip().lower() for s in self.cfg.active_linear_states()]
        terminal_lower = [s.strip().lower() for s in self.cfg.terminal_linear_states()]

        if state_lower not in active_lower:
            return False
        if state_lower in terminal_lower:
            return False
        if issue.id in self.running:
            return False
        if issue.id in self.claimed:
            return False

        # Blocker check for Todo
        if state_lower == "todo":
            for blocker in issue.blocked_by:
                if blocker.state and blocker.state.strip().lower() not in terminal_lower:
                    return False

        return True

    def _dispatch(self, issue: Issue, attempt_num: int | None = None):
        """Dispatch a worker for an issue."""
        self.claimed.add(issue.id)

        state_name = self._issue_current_state.get(issue.id)
        if not state_name:
            state_name = self.cfg.entry_state

        # If at a gate, enter it instead of dispatching a worker
        state_cfg = self.cfg.states.get(state_name) if state_name else None
        if state_cfg and state_cfg.type == "gate":
            asyncio.create_task(self._safe_enter_gate(issue, state_name))
            return

        attempt = RunAttempt(
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt_num,
            state_name=state_name,
        )

        # Session handling
        use_fresh_session = False
        if state_cfg and state_cfg.session == "fresh":
            use_fresh_session = True

        if not use_fresh_session:
            if issue.id in self.running:
                old = self.running[issue.id]
                if old.session_id:
                    attempt.session_id = old.session_id
            elif issue.id in self._last_session_ids:
                attempt.session_id = self._last_session_ids[issue.id]

        self.running[issue.id] = attempt
        task = asyncio.create_task(self._run_worker(issue, attempt))
        self._tasks[issue.id] = task

        runner = state_cfg.runner if state_cfg else "claude"
        logger.info(
            f"Dispatched issue={issue.identifier} "
            f"state={issue.state} "
            f"machine_state={state_name or 'entry'} "
            f"runner={runner} "
            f"session={'fresh' if use_fresh_session else 'inherit'} "
            f"attempt={attempt_num}"
        )

    async def _run_worker(self, issue: Issue, attempt: RunAttempt):
        """Worker coroutine: prepare workspace, run agent turns."""
        try:
            # Resolve state if not set
            if not attempt.state_name:
                state_name, run = await self._resolve_current_state(issue)
                attempt.state_name = state_name
                state_cfg = self.cfg.states.get(state_name)
                if state_cfg and state_cfg.type == "gate":
                    # Issue should be at a gate, not running
                    await self._enter_gate(issue, state_name)
                    return

            state_name = attempt.state_name
            state_cfg = self.cfg.states.get(state_name) if state_name else None

            claude_cfg = self.cfg.claude
            hooks_cfg = self.cfg.hooks
            runner_type = "claude"

            if state_cfg:
                claude_cfg, hooks_cfg = merge_state_config(
                    state_cfg, self.cfg.claude, self.cfg.hooks
                )
                runner_type = state_cfg.runner

            ws_root = self.cfg.workspace.resolved_root()
            ws = await ensure_workspace(
                ws_root, issue.identifier, self.cfg.hooks,
                workspace_cfg=self.cfg.workspace,
                branch_name=issue.branch_name,
            )
            attempt.workspace_path = str(ws.path)

            # Move issue from Todo to In Progress if needed
            todo_state = self.cfg.linear_states.todo
            if todo_state and issue.state.strip().lower() == todo_state.strip().lower():
                try:
                    client = self._ensure_tracker_client()
                    active_state = self.cfg.linear_states.active
                    moved = await client.update_issue_state(issue.id, active_state)
                    if moved:
                        issue.state = active_state
                        logger.info(
                            f"Moved {issue.identifier} from '{todo_state}' to '{active_state}'"
                        )
                    else:
                        logger.warning(
                            f"Failed to move {issue.identifier} from '{todo_state}' to '{active_state}' "
                            f"— Linear API returned failure"
                        )
                except Exception as e:
                    logger.warning(f"Failed to move {issue.identifier} to active: {e}")

            # Post state tracking comment (only for first dispatch of a state)
            if state_name:
                run = self._issue_state_runs.get(issue.id, 1)
                if run == 1 and (attempt.attempt is None or attempt.attempt == 0):
                    client = self._ensure_tracker_client()
                    comment = make_state_comment(
                        state=state_name,
                        run=run,
                    )
                    await client.post_comment(issue.id, comment)

            # Run on_stage_enter hook if defined
            if state_cfg and state_cfg.hooks and state_cfg.hooks.on_stage_enter:
                from .workspace import run_hook
                ok = await run_hook(
                    state_cfg.hooks.on_stage_enter,
                    ws.path,
                    (state_cfg.hooks.timeout_ms if state_cfg.hooks else self.cfg.hooks.timeout_ms),
                    f"on_stage_enter:{state_name}",
                )
                if not ok:
                    attempt.status = "failed"
                    attempt.error = f"on_stage_enter hook failed for state {state_name}"
                    self._on_worker_exit(issue, attempt)
                    return

            prompt = await self._render_prompt_async(issue, attempt.attempt, state_name)

            # Build env vars for the agent subprocess from workflow.yaml config
            agent_env = self.cfg.agent_env()

            # State machine mode: single turn per dispatch. The state
            # machine handles continuation via _transition after each
            # turn completes — multi-turn loops would bypass gate
            # transitions and cause the agent to blow past stage
            # boundaries.
            if state_name and state_cfg:
                attempt = await run_turn(
                    runner_type=runner_type,
                    claude_cfg=claude_cfg,
                    hooks_cfg=hooks_cfg,
                    prompt=prompt,
                    workspace_path=ws.path,
                    issue=issue,
                    attempt=attempt,
                    on_event=self._on_agent_event,
                    on_pid=self._on_child_pid,
                    env=agent_env,
                )
            else:
                # Legacy mode: multi-turn loop
                max_turns = claude_cfg.max_turns
                for turn in range(max_turns):
                    if turn > 0:
                        current_state = issue.state
                        try:
                            client = self._ensure_tracker_client()
                            states = await client.fetch_issue_states_by_ids([issue.id])
                            current_state = states.get(issue.id, issue.state)
                            state_lower = current_state.strip().lower()
                            active_lower = [
                                s.strip().lower() for s in self.cfg.active_linear_states()
                            ]
                            if state_lower not in active_lower:
                                logger.info(
                                    f"Issue {issue.identifier} no longer active "
                                    f"(state={current_state}), stopping"
                                )
                                break
                        except Exception as e:
                            logger.warning(f"State check failed, continuing: {e}")

                        prompt = (
                            f"Continue working on {issue.identifier}. "
                            f"The issue is still in '{current_state}' state. "
                            f"Check your progress and continue the task."
                        )

                    attempt = await run_turn(
                        runner_type=runner_type,
                        claude_cfg=claude_cfg,
                        hooks_cfg=hooks_cfg,
                        prompt=prompt,
                        workspace_path=ws.path,
                        issue=issue,
                        attempt=attempt,
                        on_event=self._on_agent_event,
                        on_pid=self._on_child_pid,
                        env=agent_env,
                    )

                    if attempt.status != "succeeded":
                        break

            self._on_worker_exit(issue, attempt)

        except asyncio.CancelledError:
            logger.info(f"Worker cancelled issue={issue.identifier}")
            attempt.status = "canceled"
            self._on_worker_exit(issue, attempt)
        except Exception as e:
            logger.error(f"Worker error issue={issue.identifier}: {e}")
            attempt.status = "failed"
            attempt.error = str(e)
            self._on_worker_exit(issue, attempt)

    async def _render_prompt_async(
        self, issue: Issue, attempt_num: int | None, state_name: str | None = None
    ) -> str:
        """Render prompt using state machine prompt assembly (async — fetches comments)."""
        if state_name and state_name in self.cfg.states:
            state_cfg = self.cfg.states[state_name]
            run = self._issue_state_runs.get(issue.id, 1)
            last_completed = self._last_completed_at.get(issue.id)
            last_run_at = last_completed.isoformat() if last_completed else None

            # Fetch comments for lifecycle context
            comments: list[dict] | None = None
            try:
                client = self._ensure_tracker_client()
                comments = await client.fetch_comments(issue.id)
            except Exception as e:
                logger.warning(f"Failed to fetch comments for prompt: {e}")

            return assemble_prompt(
                cfg=self.cfg,
                workflow_dir=str(self.workflow_path.parent),
                issue=issue,
                state_name=state_name,
                state_cfg=state_cfg,
                run=run,
                is_rework=False,
                attempt=attempt_num or 1,
                last_run_at=last_run_at,
                comments=comments,
            )

        # Legacy fallback
        return self._render_prompt(issue, attempt_num, state_name)

    def _render_prompt(
        self, issue: Issue, attempt_num: int | None, state_name: str | None = None
    ) -> str:
        """Render the prompt template with issue context (legacy/sync fallback)."""
        assert self.workflow is not None

        # State machine mode: call assemble_prompt without comments
        if state_name and state_name in self.cfg.states:
            state_cfg = self.cfg.states[state_name]
            run = self._issue_state_runs.get(issue.id, 1)
            last_completed = self._last_completed_at.get(issue.id)
            last_run_at = last_completed.isoformat() if last_completed else None

            return assemble_prompt(
                cfg=self.cfg,
                workflow_dir=str(self.workflow_path.parent),
                issue=issue,
                state_name=state_name,
                state_cfg=state_cfg,
                run=run,
                is_rework=False,
                attempt=attempt_num or 1,
                last_run_at=last_run_at,
                comments=None,
            )

        # Legacy mode: use workflow prompt_template with Jinja2
        template_str = self.workflow.prompt_template

        if not template_str:
            return f"You are working on an issue from Linear: {issue.identifier} - {issue.title}"

        last_completed = self._last_completed_at.get(issue.id)
        last_run_at = last_completed.isoformat() if last_completed else ""

        try:
            template = self._jinja.from_string(template_str)
            return template.render(
                issue={
                    "id": issue.id,
                    "identifier": issue.identifier,
                    "title": issue.title,
                    "description": issue.description or "",
                    "priority": issue.priority,
                    "state": issue.state,
                    "branch_name": issue.branch_name,
                    "url": issue.url,
                    "labels": issue.labels,
                    "blocked_by": [
                        {"id": b.id, "identifier": b.identifier, "state": b.state}
                        for b in issue.blocked_by
                    ],
                    "created_at": str(issue.created_at) if issue.created_at else "",
                    "updated_at": str(issue.updated_at) if issue.updated_at else "",
                },
                attempt=attempt_num,
                last_run_at=last_run_at,
                stage=state_name,
            )
        except TemplateSyntaxError as e:
            raise RuntimeError(f"Template syntax error: {e}")

    def _on_child_pid(self, pid: int, is_register: bool):
        """Track child claude process PIDs for cleanup on shutdown."""
        if is_register:
            self._child_pids.add(pid)
        else:
            self._child_pids.discard(pid)

    def _on_agent_event(self, identifier: str, event_type: str, event: dict):
        """Callback for agent events."""
        logger.debug(f"Agent event issue={identifier} type={event_type}")

    def _on_worker_exit(self, issue: Issue, attempt: RunAttempt):
        """Handle worker completion."""
        self.total_input_tokens += attempt.input_tokens
        self.total_output_tokens += attempt.output_tokens
        self.total_tokens += attempt.total_tokens
        if attempt.started_at:
            elapsed = (datetime.now(timezone.utc) - attempt.started_at).total_seconds()
            self.total_seconds_running += elapsed

        if attempt.session_id:
            self._last_session_ids[issue.id] = attempt.session_id

        completed_at = datetime.now(timezone.utc)
        attempt.completed_at = completed_at
        if attempt.status != "canceled":
            self._last_completed_at[issue.id] = completed_at

        self.running.pop(issue.id, None)
        self._tasks.pop(issue.id, None)

        if attempt.status == "blocked":
            # Agent signaled it can't handle this issue — move to Blocked
            asyncio.create_task(self._move_to_blocked(issue, attempt))
        elif attempt.status == "succeeded":
            if attempt.state_name and attempt.state_name in self.cfg.states:
                # State machine mode: transition via "complete"
                asyncio.create_task(self._safe_transition(issue, "complete"))
            else:
                # Legacy mode
                self._schedule_retry(issue, attempt_num=1, delay_ms=1000)
        elif attempt.status in ("failed", "timed_out", "stalled"):
            current_attempt = (attempt.attempt or 0) + 1
            delay = min(
                10_000 * (2 ** (current_attempt - 1)),
                self.cfg.agent.max_retry_backoff_ms,
            )
            self._schedule_retry(
                issue,
                attempt_num=current_attempt,
                delay_ms=delay,
                error=attempt.error,
            )
        else:
            self.claimed.discard(issue.id)

    def _schedule_retry(
        self,
        issue: Issue,
        attempt_num: int,
        delay_ms: int,
        error: str | None = None,
    ):
        """Schedule a retry for an issue."""
        # Cancel existing retry
        if issue.id in self._retry_timers:
            self._retry_timers[issue.id].cancel()

        entry = RetryEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            attempt=attempt_num,
            due_at_ms=time.monotonic() * 1000 + delay_ms,
            error=error,
        )
        self.retry_attempts[issue.id] = entry

        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            delay_ms / 1000,
            lambda: loop.create_task(self._handle_retry(issue.id)),
        )
        self._retry_timers[issue.id] = handle

        logger.info(
            f"Retry scheduled issue={issue.identifier} "
            f"attempt={attempt_num} delay={delay_ms}ms "
            f"error={error or 'continuation'}"
        )

    async def _handle_retry(self, issue_id: str):
        """Handle a retry timer firing."""
        entry = self.retry_attempts.pop(issue_id, None)
        self._retry_timers.pop(issue_id, None)

        if entry is None:
            return

        # Fetch fresh candidates to check eligibility
        try:
            client = self._ensure_tracker_client()
            candidates = await client.fetch_candidate_issues(
                self.cfg.tracker.project_slug,
                self.cfg.active_linear_states(),
                team_key=self.cfg.tracker.team_key,
            )
        except Exception as e:
            logger.warning(f"Retry candidate fetch failed: {e}")
            self.claimed.discard(issue_id)
            return

        issue = None
        for c in candidates:
            if c.id == issue_id:
                issue = c
                break

        if issue is None:
            # No longer active
            self.claimed.discard(issue_id)
            logger.info(f"Retry: issue {entry.identifier} no longer active, releasing")
            return

        # Check slots
        available = max(
            self.cfg.agent.max_concurrent_agents - len(self.running), 0
        )
        if available <= 0:
            # Re-queue
            self._schedule_retry(
                issue,
                attempt_num=entry.attempt,
                delay_ms=10_000,
                error="no available orchestrator slots",
            )
            return

        self._dispatch(issue, attempt_num=entry.attempt)

    async def _reconcile(self):
        """Reconcile running issues against current Linear state."""
        if not self.running:
            return

        running_ids = list(self.running.keys())

        try:
            client = self._ensure_tracker_client()
            states = await client.fetch_issue_states_by_ids(running_ids)
        except Exception as e:
            logger.warning(f"Reconciliation state fetch failed: {e}")
            return

        terminal_lower = [
            s.strip().lower() for s in self.cfg.terminal_linear_states()
        ]
        active_lower = [
            s.strip().lower() for s in self.cfg.active_linear_states()
        ]
        review_lower = self.cfg.linear_states.review.strip().lower()

        for issue_id in running_ids:
            current_state = states.get(issue_id)
            if current_state is None:
                continue

            state_lower = current_state.strip().lower()

            if state_lower in terminal_lower:
                # Terminal - stop worker and clean workspace
                logger.info(
                    f"Reconciliation: {issue_id} is terminal ({current_state}), stopping"
                )
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()

                attempt = self.running.get(issue_id)
                if attempt:
                    ws_root = self.cfg.workspace.resolved_root()
                    await remove_workspace(
                        ws_root, attempt.issue_identifier, self.cfg.hooks,
                        workspace_cfg=self.cfg.workspace,
                    )

                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)
                self.claimed.discard(issue_id)

            elif state_lower == review_lower:
                # In review/gate state — stop worker but keep gate tracking
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()
                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)

            elif state_lower not in active_lower:
                # Neither active nor terminal nor review - stop without cleanup
                logger.info(
                    f"Reconciliation: {issue_id} not active ({current_state}), stopping"
                )
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()
                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)
                self.claimed.discard(issue_id)

    def get_state_snapshot(self) -> dict[str, Any]:
        """Get current runtime state for observability."""
        now = datetime.now(timezone.utc)
        active_seconds = sum(
            (now - r.started_at).total_seconds()
            for r in self.running.values()
            if r.started_at
        )

        return {
            "generated_at": now.isoformat(),
            "counts": {
                "running": len(self.running),
                "retrying": len(self.retry_attempts),
                "gates": len(self._pending_gates),
            },
            "running": [
                {
                    "issue_id": r.issue_id,
                    "issue_identifier": r.issue_identifier,
                    "session_id": r.session_id,
                    "turn_count": r.turn_count,
                    "status": r.status,
                    "last_event": r.last_event,
                    "last_message": r.last_message,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "last_event_at": (
                        r.last_event_at.isoformat() if r.last_event_at else None
                    ),
                    "tokens": {
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "total_tokens": r.total_tokens,
                    },
                    "state_name": r.state_name,
                }
                for r in self.running.values()
            ],
            "retrying": [
                {
                    "issue_id": e.issue_id,
                    "issue_identifier": e.identifier,
                    "attempt": e.attempt,
                    "error": e.error,
                }
                for e in self.retry_attempts.values()
            ],
            "gates": [
                {
                    "issue_id": issue_id,
                    "issue_identifier": self._last_issues.get(issue_id, Issue(id="", identifier=issue_id, title="")).identifier,
                    "gate_state": gate_state,
                    "run": self._issue_state_runs.get(issue_id, 1),
                }
                for issue_id, gate_state in self._pending_gates.items()
            ],
            "totals": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_tokens,
                "seconds_running": round(
                    self.total_seconds_running + active_seconds, 1
                ),
            },
        }
