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
    ProjectConfig,
    ServiceConfig,
    StateConfig,
    WorkflowDefinition,
    _resolve_linear_state_name,
    merge_state_config,
    parse_workflow_file,
    validate_config,
)
from .linear import LinearClient
from .models import Issue, RetryEntry, RunAttempt
from .pool import ConcurrencyPool
from .prompt import assemble_prompt, build_lifecycle_section
from .runner import run_agent_turn, run_turn
from .tracking import make_gate_comment, make_state_comment, parse_latest_tracking
from . import artifacts as artifacts_mod
from . import report as report_mod
from .events import summarise_tool_input
from .ledger import Ledger
from .workspace import ensure_workspace, remove_workspace

logger = logging.getLogger("stokowski")


class Orchestrator:
    def __init__(
        self,
        workflow_path: str | Path,
        project_name: str | None = None,
        pool: ConcurrencyPool | None = None,
    ):
        """Run one project's dispatch loop.

        `project_name` selects which project block in the workflow file
        this orchestrator owns. If None, defaults to the first project
        (legacy single-project setups always have exactly one).

        `pool` is a shared ConcurrencyPool. When provided, slot decisions
        funnel through it so the global cap and per-project caps are
        honoured across all orchestrators. When None, falls back to the
        legacy behaviour of `agent.max_concurrent_agents - len(running)`.
        """
        self.workflow_path = Path(workflow_path)
        self.workflow: WorkflowDefinition | None = None
        self.project_name = project_name
        self.project: ProjectConfig | None = None
        self.pool = pool

        # Runtime state
        self.running: dict[str, RunAttempt] = {}  # issue_id -> RunAttempt
        self.claimed: set[str] = set()
        self.retry_attempts: dict[str, RetryEntry] = {}
        self.completed: set[str] = set()

        # Aggregate metrics
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cache_creation_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.total_tool_calls: int = 0
        self.total_seconds_running: float = 0
        # Most recent rate-limit window seen from any agent. Shared across the
        # whole account, so the latest observation is the accurate one.
        self.rate_limit: dict[str, Any] | None = None
        # Append-only record of runs and the human verdicts on them. In-memory
        # state is rebuilt from Linear on restart; this is the only thing that
        # remembers how previous work was judged.
        # Note: no `self.cfg` here — the workflow is not loaded until the
        # first tick, and `cfg` asserts on that. The ledger path is derived
        # from the workflow file alone, which is known at construction.
        self.ledger = Ledger.for_workflow(Path(self.workflow_path))

        # Internal
        self._linear: LinearClient | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._retry_timers: dict[str, asyncio.TimerHandle] = {}
        self._child_pids: set[int] = set()  # Track claude subprocess PIDs
        self._last_session_ids: dict[str, str] = {}  # issue_id -> last known session_id
        self._jinja = Environment(undefined=StrictUndefined)
        self._running = False
        self._last_issues: dict[str, Issue] = {}
        self._last_completed_at: dict[str, datetime] = {}  # issue_id -> last worker completion time

        # State machine tracking
        self._issue_current_state: dict[str, str] = {}
        # Which workflow each issue is running. Pinned on first dispatch and
        # kept for the issue's lifetime: editing labels mid-run must not switch
        # the pipeline under a working agent.
        self._issue_workflow: dict[str, str] = {}   # issue_id -> internal state name
        self._issue_state_runs: dict[str, int] = {}       # issue_id -> run number for current state
        # (issue_id, state, run) already announced in Linear. Two call sites
        # post the entering-a-state comment — the transition, and the worker
        # that picks the state up ~1s later — and a continuation can re-enter
        # the worker path with the same run. COG-368 got three identical
        # "Entering state: implement" comments in 1.7 seconds.
        self._announced_states: set[tuple[str, str, int]] = set()
        self._pending_gates: dict[str, str] = {}           # issue_id -> gate state name

        # Eligible-but-not-dispatched (queue panel data, refreshed each tick)
        self._queued: list[dict] = []

        # Issues that currently hold a pool slot. Used to ensure each
        # try_claim has exactly one matching release, even when the
        # worker is cancelled mid-flight from multiple paths.
        self._slot_held: set[str] = set()

    @property
    def cfg(self) -> ServiceConfig:
        assert self.workflow is not None
        return self.workflow.config

    def _project_view(self, full: WorkflowDefinition, project: ProjectConfig) -> WorkflowDefinition:
        """Build a per-project ServiceConfig view so existing self.cfg.X reads keep working."""
        project_cfg = ServiceConfig(
            tracker=project.tracker,
            polling=full.config.polling,
            workspace=project.workspace,
            hooks=project.hooks,
            claude=project.claude,
            agent=full.config.agent,
            server=full.config.server,
            linear_states=project.linear_states,
            prompts=project.prompts,
            states=project.states,
            # Without these the orchestrator sees no workflows at all, so every
            # issue silently falls back to the inline state machine and routing
            # never fires.
            workflows=project.workflows,
            routing=project.routing,
            projects=[project],
            workflow_dir=full.config.workflow_dir,
        )
        return WorkflowDefinition(config=project_cfg, prompt_template=full.prompt_template)

    def _load_workflow(self) -> list[str]:
        """Load/reload workflow file. Returns validation errors.

        Resolves `self.project_name` to the matching ProjectConfig and
        builds a per-project ServiceConfig view that the rest of the
        orchestrator can read from via `self.cfg`.
        """
        try:
            full = parse_workflow_file(self.workflow_path)
        except Exception as e:
            return [f"Workflow load error: {e}"]
        errors = validate_config(full.config)
        if errors:
            return errors

        # Resolve project
        if self.project_name is None:
            if not full.config.projects:
                return ["No projects defined"]
            project = full.config.projects[0]
            self.project_name = project.name
        else:
            project = next(
                (p for p in full.config.projects if p.name == self.project_name),
                None,
            )
            if project is None:
                return [f"Project '{self.project_name}' not found in workflow file"]

        self.workflow = self._project_view(full, project)
        self.project = project
        return []

    # ── Slot management ────────────────────────────────────────────────────

    # ── Workflow resolution ──────────────────────────────────────────────

    def _adopt_workflow_from_tracking(self, issue: Issue, tracking: dict | None) -> None:
        """Restore an issue's pinned workflow from its tracking comment.

        In-memory pins are lost on restart. Recovering from Linear keeps a
        long-running issue on the pipeline it started, rather than re-routing
        it from labels that may have changed since.
        """
        if not tracking:
            return
        name = tracking.get("workflow")
        if name and name in self.cfg.workflows:
            self._issue_workflow[issue.id] = name

    def _workflow_name_for(self, issue: Issue) -> str | None:
        """Resolve (and pin) the workflow an issue runs under.

        Pinned on first sight rather than re-read each tick, because labels are
        editable: a ticket relabelled halfway through implementation must not
        suddenly be evaluated against a different state machine.
        """
        pinned = self._issue_workflow.get(issue.id)
        if pinned and pinned in self.cfg.workflows:
            return pinned

        name = self.cfg.routing.resolve(issue.labels)
        if name and name in self.cfg.workflows:
            self._issue_workflow[issue.id] = name
            logger.info(
                f"Routed {issue.identifier} to workflow '{name}'"
                + (f" (labels: {', '.join(issue.labels)})" if issue.labels else " (no labels)"),
                extra={"linked_to": issue.identifier},
            )
            return name

        if name:
            logger.warning(
                f"{issue.identifier} routes to unknown workflow '{name}'",
                extra={"linked_to": issue.identifier},
            )
        return None

    def _workflow_for(self, issue: Issue):
        """The WorkflowSpec an issue runs under, or None."""
        name = self._workflow_name_for(issue)
        return self.cfg.workflows.get(name) if name else None

    def _entry_state_for(self, issue: Issue) -> str | None:
        """The entry state of the pipeline THIS issue runs under.

        `cfg.entry_state` answers a different question — the first agent state
        of the inline `states:` block. Every workflow but `bug-fix` happens to
        start with `investigate`, so using it looked correct until a
        bug-labelled ticket arrived and was started in a state `bug-fix` does
        not have.
        """
        workflow = self._workflow_for(issue)
        if workflow is not None:
            entry = workflow.entry_state
            if entry:
                return entry
        return self.cfg.entry_state

    def _states_for(self, issue: Issue) -> dict:
        """The state machine an issue runs under.

        Falls back to the config's inline states so a single-pipeline setup —
        and any issue whose routing failed — behaves exactly as before.
        """
        workflow = self._workflow_for(issue)
        return workflow.states if workflow else self.cfg.states


    def _has_slot(self) -> tuple[bool, str | None]:
        """Return (can_dispatch, reason_if_not). Considers pause + global cap."""
        name = self.project_name or ""
        if self.pool is not None:
            if self.pool.is_paused(name):
                return False, "paused"
            if self.pool.available_for(name) <= 0:
                return False, "no global slot"
            return True, None
        # Legacy single-project path (no shared pool)
        if max(self.cfg.agent.max_concurrent_agents - len(self.running), 0) <= 0:
            return False, "no global slot"
        return True, None

    def _claim_slot(self, issue_id: str) -> bool:
        """Claim a pool slot for this issue. No-op for legacy path."""
        if issue_id in self._slot_held:
            return True
        if self.pool is not None:
            if not self.pool.try_claim(self.project_name or ""):
                return False
        self._slot_held.add(issue_id)
        return True

    def _release_slot(self, issue_id: str) -> None:
        """Release a pool slot. Idempotent — safe to call from cancellation paths."""
        if issue_id not in self._slot_held:
            return
        self._slot_held.discard(issue_id)
        if self.pool is not None:
            self.pool.release(self.project_name or "")

    def _ensure_linear_client(self) -> LinearClient:
        if self._linear is None:
            self._linear = LinearClient(
                endpoint=self.cfg.tracker.endpoint,
                api_key=self.cfg.resolved_api_key(),
            )
        return self._linear

    async def start(self):
        """Start the orchestration loop."""
        errors = self._load_workflow()
        if errors:
            for e in errors:
                logger.error(f"Config error: {e}")
            raise RuntimeError(f"Startup validation failed: {errors}")

        logger.info(
            f"Starting orchestrator "
            f"project={self.project_name} "
            f"slug={self.cfg.tracker.project_slug} "
            f"max_agents={self.cfg.agent.max_concurrent_agents} "
            f"poll_ms={self.cfg.polling.interval_ms}"
        )

        self._running = True
        self._stop_event = asyncio.Event()

        # Startup terminal cleanup
        await self._startup_cleanup()

        # Rebuild gates from Linear so the dashboard is accurate immediately
        # after restart, not only after Stokowski dispatches in-flight tickets
        await self._rebuild_gates_from_linear()

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

        if self._linear:
            await self._linear.close()

    async def _startup_cleanup(self):
        """Remove workspaces for issues already in terminal states."""
        try:
            client = self._ensure_linear_client()
            terminal = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                self.cfg.terminal_linear_states(),
            )
            ws_root = self.cfg.workspace.resolved_root()
            for issue in terminal:
                await remove_workspace(ws_root, issue.identifier, self.cfg.hooks)
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
        client = self._ensure_linear_client()
        comments = await client.fetch_comments(issue.id)
        tracking = parse_latest_tracking(comments)
        # Restore the pipeline this issue started on before interpreting its
        # state — a state name only means something inside its own workflow.
        self._adopt_workflow_from_tracking(issue, tracking)

        entry = self._entry_state_for(issue)
        if entry is None:
            raise RuntimeError("No entry state defined in config")

        # No tracking → entry state, run 1
        if tracking is None:
            self._issue_current_state[issue.id] = entry
            self._issue_state_runs[issue.id] = 1
            return entry, 1

        if tracking["type"] == "state":
            state_name = tracking.get("state", entry)
            run = tracking.get("run", 1)
            if state_name in self._states_for(issue):
                self._issue_current_state[issue.id] = state_name
                self._issue_state_runs[issue.id] = run
                return state_name, run
            # Unknown state → restart at this workflow's entry. Loud, because
            # a state that is not in the issue's own machine means the issue
            # was started on the wrong pipeline and has been going nowhere.
            logger.warning(
                f"Issue {issue.identifier} records state '{state_name}', which is "
                f"not in workflow '{self._workflow_name_for(issue)}' — restarting "
                f"at '{entry}'",
                extra={"linked_to": issue.identifier},
            )
            self._issue_current_state[issue.id] = entry
            self._issue_state_runs[issue.id] = 1
            return entry, 1

        if tracking["type"] == "gate":
            gate_state = tracking.get("state", "")
            status = tracking.get("status", "")
            run = tracking.get("run", 1)

            if status == "waiting":
                if gate_state in self._states_for(issue):
                    self._issue_current_state[issue.id] = gate_state
                    self._issue_state_runs[issue.id] = run
                    self._pending_gates[issue.id] = gate_state
                    return gate_state, run

            elif status == "approved":
                gate_cfg = self._states_for(issue).get(gate_state)
                if gate_cfg and "approve" in gate_cfg.transitions:
                    target = gate_cfg.transitions["approve"]
                    self._issue_current_state[issue.id] = target
                    self._issue_state_runs[issue.id] = run
                    return target, run

            elif status == "rework":
                gate_cfg = self._states_for(issue).get(gate_state)
                rework_to = tracking.get("rework_to", "")
                if not rework_to and gate_cfg:
                    rework_to = gate_cfg.rework_to or ""
                if rework_to and rework_to in self._states_for(issue):
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
                extra={"linked_to": issue.identifier},
            )

    async def _enter_gate(self, issue: Issue, state_name: str):
        """Move issue to gate state and post tracking comment."""
        state_cfg = self._states_for(issue).get(state_name)
        prompt = state_cfg.prompt if state_cfg else ""
        run = self._issue_state_runs.get(issue.id, 1)

        client = self._ensure_linear_client()

        comment = make_gate_comment(
            state=state_name,
            status="waiting",
            prompt=prompt or "",
            run=run,
        )
        await client.post_comment(issue.id, comment)

        # Use the gate's own linear_state (per workflow.yaml), not a global one.
        # Previously this hardcoded `linear_states.review`, which forced every
        # gate entry — including `await_ci_and_review` — straight to "Human
        # Review", bypassing the CI+reviewer poller entirely.
        linear_key = state_cfg.linear_state if state_cfg else "review"
        target_linear_state = _resolve_linear_state_name(linear_key, self.cfg.linear_states)
        moved = await client.update_issue_state(issue.id, target_linear_state)
        if not moved:
            logger.error(
                f"Failed to move {issue.identifier} to gate linear state "
                f"'{target_linear_state}' (gate={state_name}) "
                f"— issue will remain claimed to prevent re-dispatch loop",
                extra={"linked_to": issue.identifier},
            )
            # Keep claimed so the issue doesn't get re-dispatched while
            # still in the active Linear state. Track the gate so
            # _handle_gate_responses can pick it up if the state is
            # changed manually.
            self._pending_gates[issue.id] = state_name
            self._issue_current_state[issue.id] = state_name
            self.running.pop(issue.id, None)
            self._tasks.pop(issue.id, None)
            self._release_slot(issue.id)
            # Schedule a retry to attempt the state move again
            self._schedule_retry(issue, attempt_num=0, delay_ms=10_000)
            return

        self._pending_gates[issue.id] = state_name
        self._issue_current_state[issue.id] = state_name
        # Release from running/claimed so it doesn't block slots
        self.running.pop(issue.id, None)
        self._tasks.pop(issue.id, None)
        self.claimed.discard(issue.id)
        self._release_slot(issue.id)

        logger.info(
            f"Gate entered issue={issue.identifier} gate={state_name} "
            f"run={run}",
            extra={"linked_to": issue.identifier},
        )

    async def _safe_transition(self, issue: Issue, transition_name: str):
        """Wrapper around _transition that logs errors instead of silently swallowing them."""
        try:
            await self._transition(issue, transition_name)
        except Exception as e:
            logger.error(
                f"Transition failed issue={issue.identifier} "
                f"transition={transition_name}: {e}",
                exc_info=True,
                extra={"linked_to": issue.identifier},
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
            logger.warning(f"No current state for {issue.identifier}, cannot transition", extra={"linked_to": issue.identifier})
            return

        current_cfg = self._states_for(issue).get(current_state_name)
        if not current_cfg:
            logger.warning(f"Unknown state '{current_state_name}' for {issue.identifier}", extra={"linked_to": issue.identifier})
            return

        target_name = current_cfg.transitions.get(transition_name)
        if not target_name:
            logger.warning(
                f"No '{transition_name}' transition from state '{current_state_name}' "
                f"for {issue.identifier}",
                extra={"linked_to": issue.identifier},
            )
            return

        target_cfg = self._states_for(issue).get(target_name)
        if not target_cfg:
            logger.warning(f"Transition target '{target_name}' not found in config")
            return

        run = self._issue_state_runs.get(issue.id, 1)

        if target_cfg.type == "terminal":
            # Move issue to terminal state
            terminal_state = self.cfg.terminal_linear_states()[0] if self.cfg.terminal_linear_states() else "Done"
            try:
                client = self._ensure_linear_client()
                moved = await client.update_issue_state(issue.id, terminal_state)
                if moved:
                    logger.info(f"Moved {issue.identifier} to terminal state '{terminal_state}'", extra={"linked_to": issue.identifier})
                else:
                    logger.warning(f"Failed to move {issue.identifier} to terminal state '{terminal_state}'", extra={"linked_to": issue.identifier})
            except Exception as e:
                logger.warning(f"Failed to move {issue.identifier} to terminal: {e}", extra={"linked_to": issue.identifier})
            self.ledger.record_terminal(
                project=self.project_name or "", issue_id=issue.id,
                issue=issue.identifier, state=terminal_state,
            )

            # Clean up workspace
            try:
                ws_root = self.cfg.workspace.resolved_root()
                await remove_workspace(ws_root, issue.identifier, self.cfg.hooks)
            except Exception as e:
                logger.warning(f"Failed to remove workspace for {issue.identifier}: {e}", extra={"linked_to": issue.identifier})
            # Clean up tracking state
            self._issue_current_state.pop(issue.id, None)
            self._issue_state_runs.pop(issue.id, None)
            self._announced_states = {k for k in self._announced_states if k[0] != issue.id}
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
            await self._announce_state(issue, target_name, run)

            # Ensure issue is in active Linear state
            client = self._ensure_linear_client()
            active_state = self.cfg.linear_states.active
            moved = await client.update_issue_state(issue.id, active_state)
            if not moved:
                logger.warning(f"Failed to move {issue.identifier} to active state '{active_state}'", extra={"linked_to": issue.identifier})

            self._schedule_retry(issue, attempt_num=0, delay_ms=1000)

    async def _announce_state(self, issue, state: str, run: int) -> None:
        """Post the entering-a-state comment, at most once per (state, run)."""
        key = (issue.id, state, run)
        if key in self._announced_states:
            return
        self._announced_states.add(key)
        client = self._ensure_linear_client()
        comment = make_state_comment(
            state=state,
            run=run,
            workflow=self._workflow_name_for(issue),
        )
        await client.post_comment(issue.id, comment)

    async def _handle_gate_responses(self):
        """Check for gate-approved and rework issues, handle transitions."""
        # Early return if no gate states in config
        has_gates = any(sc.type == "gate" for sc in self.cfg.all_states().values())
        if not has_gates:
            return

        client = self._ensure_linear_client()

        # Fetch gate-approved issues
        try:
            approved_issues = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                [self.cfg.linear_states.gate_approved],
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

                # Set current state to the gate so _transition can read FROM it,
                # then route through _transition. This dispatches the approve
                # transition through the existing target-type logic, which
                # correctly handles terminal targets (move to terminal Linear
                # state + clean up workspace), gate targets (enter the new
                # gate), and agent targets (post state comment + move to active
                # Linear state + schedule retry).
                #
                # Previously this branch unconditionally moved the Linear ticket
                # to `active` regardless of the approve target's type, which left
                # tickets stuck in `In Progress` forever for any workflow whose
                # gate transitions directly to a terminal state.
                self._issue_current_state[issue.id] = gate_state
                self._last_issues[issue.id] = issue
                # A gate decision IS the human verdict on the work. Recording
                # it here is what makes approval rate by classification and by
                # the agent's own stated confidence measurable later.
                self.ledger.record_gate(
                    workflow=self._workflow_name_for(issue),
                    project=self.project_name or "", issue_id=issue.id,
                    issue=issue.identifier, gate=gate_state,
                    verdict="approved", run=run,
                )
                await self._transition(issue, "approve")
                logger.info(f"Gate approved issue={issue.identifier} gate={gate_state}", extra={"linked_to": issue.identifier})
            else:
                logger.warning(
                    f"Issue {issue.identifier} is in "
                    f"'{self.cfg.linear_states.gate_approved}' but no gate could be "
                    f"resolved from its tracking comments — it will not advance",
                    extra={"linked_to": issue.identifier},
                )

        # Fetch rework issues
        try:
            rework_issues = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug,
                [self.cfg.linear_states.rework],
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
                gate_cfg = self._states_for(issue).get(gate_state)
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
                        f"gate={gate_state} run={run} max={max_rework}",
                        extra={"linked_to": issue.identifier},
                    )
                    continue

                new_run = run + 1
                self._issue_state_runs[issue.id] = new_run

                # Recorded against the run that was rejected, not the retry, so
                # the verdict attaches to the work a human actually judged.
                self.ledger.record_gate(
                    workflow=self._workflow_name_for(issue),
                    project=self.project_name or "", issue_id=issue.id,
                    issue=issue.identifier, gate=gate_state,
                    verdict="rework", run=run,
                )

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
                    logger.warning(f"Failed to move {issue.identifier} to active after rework", extra={"linked_to": issue.identifier})
                self._last_issues[issue.id] = issue
                logger.info(
                    f"Rework issue={issue.identifier} gate={gate_state} "
                    f"rework_to={rework_to} run={new_run}",
                    extra={"linked_to": issue.identifier},
                )
            else:
                # A ticket parked in Rework with no resolvable gate goes nowhere
                # and nothing says so. Silence here is what let the comment
                # ordering bug sit unnoticed across every gate.
                logger.warning(
                    f"Issue {issue.identifier} is in '{self.cfg.linear_states.rework}' "
                    f"but no gate could be resolved from its tracking comments — "
                    f"it will not advance",
                    extra={"linked_to": issue.identifier},
                )

    async def _evict_terminal_gates(self):
        """Evict gate entries for tickets that have moved to terminal states.

        Runs each tick so stale Done/Canceled/Duplicate entries are removed
        within one poll cycle without requiring a manual POST /api/v1/refresh.
        This fixes the failure mode where tickets advanced to Done in Linear
        remain in the gates list indefinitely.
        """
        if not self._pending_gates:
            return

        gate_ids = list(self._pending_gates.keys())
        try:
            client = self._ensure_linear_client()
            states = await client.fetch_issue_states_by_ids(gate_ids)
        except Exception as e:
            logger.warning(f"Gate eviction state fetch failed: {e}")
            return

        terminal_lower = {s.strip().lower() for s in self.cfg.terminal_linear_states()}

        for issue_id in gate_ids:
            current_state = states.get(issue_id)
            if current_state is None:
                continue
            if current_state.strip().lower() in terminal_lower:
                gate_state = self._pending_gates.pop(issue_id, None)
                self._issue_current_state.pop(issue_id, None)
                self._issue_state_runs.pop(issue_id, None)
                self._announced_states = {k for k in self._announced_states if k[0] != issue_id}
                self._last_session_ids.pop(issue_id, None)
                self.claimed.discard(issue_id)
                ident = self._last_issues.get(
                    issue_id, Issue(id="", identifier=issue_id, title="")
                ).identifier
                logger.info(
                    f"Gate evicted issue={ident} was={gate_state} "
                    f"(moved to terminal: {current_state})",
                    extra={"linked_to": ident},
                )

    async def _rebuild_gates_from_linear(self):
        """Rebuild _pending_gates from Linear state on startup.

        Fetches all tickets currently in gate-flagged Linear states (Awaiting CI,
        Human Review, Rework) and reconstructs their gate tracking by reading the
        most recent stokowski tracking comment on each issue.  This ensures the
        dashboard is accurate immediately after a restart, not only after
        Stokowski itself dispatches them in the new process lifetime.

        Fallback: if no tracking comment is found, derives the gate state from
        the ticket's current Linear state by matching against configured gate
        states' linear_state keys.  This handles tickets that were moved to a
        gate-flagged state by an agent (via Linear MCP) before Stokowski's own
        gate-entry comment was posted.
        """
        gate_states = self.cfg.gate_linear_states()
        if not gate_states:
            return

        # Also include Rework — reworked tickets still hold pending gate context
        all_gate_states = list(gate_states)
        rework_state = self.cfg.linear_states.rework
        if rework_state and rework_state not in all_gate_states:
            all_gate_states.append(rework_state)

        try:
            client = self._ensure_linear_client()
            issues = await client.fetch_issues_by_states(
                self.cfg.tracker.project_slug, all_gate_states
            )
        except Exception as e:
            logger.warning(f"Gate rebuild from Linear failed: {e}")
            return

        rebuilt = 0
        for issue in issues:
            if issue.id in self._pending_gates:
                continue  # Already tracked

            # Store minimal issue record so the dashboard can show the identifier
            if issue.id not in self._last_issues:
                self._last_issues[issue.id] = issue

            # Try to recover gate state and run counter from tracking comments
            gate_state: str | None = None
            run = 1
            try:
                comments = await client.fetch_comments(issue.id)
                tracking = parse_latest_tracking(comments)
            except Exception as e:
                logger.warning(
                    f"Failed to fetch comments for gate rebuild "
                    f"{issue.identifier}: {e}",
                    extra={"linked_to": issue.identifier},
                )
                tracking = None

            if tracking and tracking.get("type") == "gate":
                tracked_name = tracking.get("state", "")
                run = tracking.get("run", 1)
                if tracked_name in self._states_for(issue):
                    gate_state = tracked_name

            # Fallback: derive gate state from the current Linear state name by
            # matching against configured gate states' linear_state keys
            if not gate_state:
                current_linear = issue.state.strip().lower()
                for gname, gcfg in self._states_for(issue).items():
                    if gcfg.type == "gate":
                        gate_linear = _resolve_linear_state_name(
                            gcfg.linear_state, self.cfg.linear_states
                        )
                        if gate_linear.strip().lower() == current_linear:
                            gate_state = gname
                            run = 1
                            break

            if gate_state:
                self._pending_gates[issue.id] = gate_state
                self._issue_current_state[issue.id] = gate_state
                self._issue_state_runs[issue.id] = run
                rebuilt += 1
                logger.info(
                    f"Gate rebuilt issue={issue.identifier} "
                    f"gate={gate_state} run={run} (recovered from Linear)",
                    extra={"linked_to": issue.identifier},
                )

        if rebuilt:
            logger.info(f"Gate rebuild complete: {rebuilt} gate(s) recovered from Linear")

    async def _tick(self):
        """Single poll tick: reconcile, validate, fetch, dispatch."""
        # Reload workflow (supports hot-reload)
        errors = self._load_workflow()

        # Part 1: Reconcile running issues
        await self._reconcile()

        # Handle gate responses (approve / rework transitions)
        await self._handle_gate_responses()

        # Evict gate entries whose tickets have moved to terminal states
        await self._evict_terminal_gates()

        # Part 2: Validate config
        if errors:
            logger.warning(f"Config invalid, skipping dispatch: {errors}")
            return

        # Part 3: Fetch candidates
        try:
            client = self._ensure_linear_client()
            candidates = await client.fetch_candidate_issues(
                self.cfg.tracker.project_slug,
                self.cfg.active_linear_states(),
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
                    logger.warning(f"Failed to resolve state for {issue.identifier}: {e}", extra={"linked_to": issue.identifier})

        # Part 5: Dispatch
        self._queued = []  # reset per-tick queue snapshot

        for issue in candidates:
            if not self._is_eligible(issue):
                continue

            can, reason = self._has_slot()
            if not can:
                self._queued.append({
                    "issue_id": issue.id,
                    "issue_identifier": issue.identifier,
                    "title": issue.title,
                    "priority": issue.priority,
                    "state": issue.state,
                    "reason": reason or "blocked",
                })
                continue

            # Per-state concurrency check
            state_key = issue.state.strip().lower()
            state_limit = self.cfg.agent.max_concurrent_agents_by_state.get(state_key)
            if state_limit is not None:
                state_count = sum(
                    1
                    for r in self.running.values()
                    if self._last_issues.get(r.issue_id, Issue(id="", identifier="", title="")).state.strip().lower()
                    == state_key
                )
                if state_count >= state_limit:
                    self._queued.append({
                        "issue_id": issue.id,
                        "issue_identifier": issue.identifier,
                        "title": issue.title,
                        "priority": issue.priority,
                        "state": issue.state,
                        "reason": f"per-state cap ({state_key})",
                    })
                    continue

            # Reserve the global slot before _dispatch so the pool stays
            # consistent with what we believe is in flight.
            if not self._claim_slot(issue.id):
                self._queued.append({
                    "issue_id": issue.id,
                    "issue_identifier": issue.identifier,
                    "title": issue.title,
                    "priority": issue.priority,
                    "state": issue.state,
                    "reason": "no global slot",
                })
                continue

            self._dispatch(issue)

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

        # Blockers gate ENTRY to the pipeline, not work already under way: a
        # blocker appearing mid-run should not kill a live agent.
        #
        # This compares against the configured todo state, not the literal
        # "todo". It used to compare against the literal, which matches only
        # because Stokowski's own default happens to be named "Todo" — so the
        # check silently stopped running for any team that named its todo state
        # anything else ("Assigned, Not Started", "Ready", "Up Next"), and
        # blocked issues dispatched anyway. Nothing failed; the guard was just
        # absent. Multi-project makes that the normal case rather than the
        # exception, since two teams rarely share a state vocabulary.
        todo_state = self.cfg.linear_states.todo.strip().lower()
        if todo_state and state_lower == todo_state:
            for blocker in issue.blocked_by:
                if blocker.state and blocker.state.strip().lower() not in terminal_lower:
                    return False

        return True

    def _dispatch(self, issue: Issue, attempt_num: int | None = None):
        """Dispatch a worker for an issue."""
        self.claimed.add(issue.id)

        state_name = self._issue_current_state.get(issue.id)
        if not state_name:
            state_name = self._entry_state_for(issue)

        # If at a gate, enter it instead of dispatching a worker.
        # Release the slot we reserved — the gate path doesn't run an agent.
        state_cfg = self._states_for(issue).get(state_name) if state_name else None
        if state_cfg and state_cfg.type == "gate":
            self._release_slot(issue.id)
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
            f"attempt={attempt_num}",
            extra={"linked_to": issue.identifier},
        )

    async def _run_worker(self, issue: Issue, attempt: RunAttempt):
        """Worker coroutine: prepare workspace, run agent turns."""
        try:
            # Resolve state if not set
            if not attempt.state_name:
                state_name, run = await self._resolve_current_state(issue)
                attempt.state_name = state_name
                state_cfg = self._states_for(issue).get(state_name)
                if state_cfg and state_cfg.type == "gate":
                    # Issue should be at a gate, not running
                    await self._enter_gate(issue, state_name)
                    return

            state_name = attempt.state_name
            state_cfg = self._states_for(issue).get(state_name) if state_name else None

            claude_cfg = self.cfg.claude
            hooks_cfg = self.cfg.hooks
            runner_type = "claude"

            if state_cfg:
                claude_cfg, hooks_cfg = merge_state_config(
                    state_cfg, self.cfg.claude, self.cfg.hooks
                )
                runner_type = state_cfg.runner

            ws_root = self.cfg.workspace.resolved_root()
            ws = await ensure_workspace(ws_root, issue.identifier, self.cfg.hooks)
            attempt.workspace_path = str(ws.path)

            # Evidence directory, created fresh each turn and excluded from git
            # locally so agent screenshots can never reach the project repo.
            artifact_path = artifacts_mod.prepare(ws.path)
            # A stale report from a previous stage would otherwise be re-posted
            # verbatim as if it described this one.
            report_mod.discard(ws.path)

            # Move issue from Todo to In Progress if needed
            todo_state = self.cfg.linear_states.todo
            if todo_state and issue.state.strip().lower() == todo_state.strip().lower():
                try:
                    client = self._ensure_linear_client()
                    active_state = self.cfg.linear_states.active
                    moved = await client.update_issue_state(issue.id, active_state)
                    if moved:
                        issue.state = active_state
                        logger.info(
                            f"Moved {issue.identifier} from '{todo_state}' to '{active_state}'",
                            extra={"linked_to": issue.identifier},
                        )
                    else:
                        logger.warning(
                            f"Failed to move {issue.identifier} from '{todo_state}' to '{active_state}' "
                            f"— Linear API returned failure",
                            extra={"linked_to": issue.identifier},
                        )
                except Exception as e:
                    logger.warning(f"Failed to move {issue.identifier} to active: {e}", extra={"linked_to": issue.identifier})

            # Post state tracking comment (only for first dispatch of a state)
            if state_name:
                run = self._issue_state_runs.get(issue.id, 1)
                if attempt.attempt is None or attempt.attempt == 0:
                    await self._announce_state(issue, state_name, run)

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
            agent_env["STOKOWSKI_ARTIFACTS"] = str(artifact_path)
            agent_env["STOKOWSKI_ISSUE"] = issue.identifier
            if state_name:
                agent_env["STOKOWSKI_STATE"] = state_name

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
                            client = self._ensure_linear_client()
                            states = await client.fetch_issue_states_by_ids([issue.id])
                            current_state = states.get(issue.id, issue.state)
                            state_lower = current_state.strip().lower()
                            active_lower = [
                                s.strip().lower() for s in self.cfg.active_linear_states()
                            ]
                            if state_lower not in active_lower:
                                logger.info(
                                    f"Issue {issue.identifier} no longer active "
                                    f"(state={current_state}), stopping",
                                    extra={"linked_to": issue.identifier},
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

            await self._publish_run_report(issue, attempt, ws.path, state_name)

            self._on_worker_exit(issue, attempt)

        except asyncio.CancelledError:
            logger.info(f"Worker cancelled issue={issue.identifier}", extra={"linked_to": issue.identifier})
            attempt.status = "canceled"
            self._on_worker_exit(issue, attempt)
        except Exception as e:
            logger.error(f"Worker error issue={issue.identifier}: {e}", extra={"linked_to": issue.identifier})
            attempt.status = "failed"
            attempt.error = str(e)
            self._on_worker_exit(issue, attempt)

    async def _render_prompt_async(
        self, issue: Issue, attempt_num: int | None, state_name: str | None = None
    ) -> str:
        """Render prompt using state machine prompt assembly (async — fetches comments)."""
        states = self._states_for(issue)
        if state_name and state_name in states:
            state_cfg = states[state_name]
            run = self._issue_state_runs.get(issue.id, 1)
            last_completed = self._last_completed_at.get(issue.id)
            last_run_at = last_completed.isoformat() if last_completed else None

            # Fetch comments for lifecycle context
            comments: list[dict] | None = None
            try:
                client = self._ensure_linear_client()
                comments = await client.fetch_comments(issue.id)
            except Exception as e:
                logger.warning(f"Failed to fetch comments for prompt: {e}", extra={"linked_to": issue.identifier})

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
                global_prompt=(wf.global_prompt if (wf := self._workflow_for(issue)) else None),
            )

        # Legacy fallback
        return self._render_prompt(issue, attempt_num, state_name)

    def _render_prompt(
        self, issue: Issue, attempt_num: int | None, state_name: str | None = None
    ) -> str:
        """Render the prompt template with issue context (legacy/sync fallback)."""
        assert self.workflow is not None

        # State machine mode: call assemble_prompt without comments
        states = self._states_for(issue)
        if state_name and state_name in states:
            state_cfg = states[state_name]
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
                global_prompt=(wf.global_prompt if (wf := self._workflow_for(issue)) else None),
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

    async def _publish_run_report(
        self,
        issue: Issue,
        attempt: RunAttempt,
        workspace_path: Path,
        state_name: str | None,
    ) -> None:
        """Upload evidence, post the run report, and apply the classification label.

        Runs after every agent turn, including failed ones — a run that fell
        over having captured three screenshots is exactly when you want to see
        them. Nothing here is allowed to break the run: a Linear outage should
        cost you the report, not the work.
        """
        if attempt.status == "canceled":
            return

        try:
            client = self._ensure_linear_client()
        except Exception as e:
            logger.warning(f"No Linear client for report: {e}", extra={"linked_to": issue.identifier})
            return

        state = state_name or attempt.state_name or "run"
        run = self._issue_state_runs.get(issue.id, 1)

        # ── Evidence ────────────────────────────────────────────────────────
        uploaded: dict[str, str] = {}
        try:
            files = artifacts_mod.collect(workspace_path)
        except Exception as e:
            logger.warning(f"Artifact sweep failed: {e}", extra={"linked_to": issue.identifier})
            files = []

        for path in files:
            try:
                data = path.read_bytes()
            except OSError as e:
                logger.warning(f"Could not read artifact {path}: {e}", extra={"linked_to": issue.identifier})
                continue
            url = await client.upload_file(
                path.name, artifacts_mod.content_type_for(path), data
            )
            if url:
                uploaded[path.name] = url
                attempt.artifacts.append(path.name)

        if files and not uploaded:
            logger.warning(
                f"{len(files)} artifact(s) found but none uploaded",
                extra={"linked_to": issue.identifier},
            )

        # ── Report ──────────────────────────────────────────────────────────
        try:
            report = report_mod.load(workspace_path, attempt.result_text)
        except Exception as e:
            logger.warning(f"Could not load report: {e}", extra={"linked_to": issue.identifier})
            report = None

        if report is None and not uploaded and attempt.status != "succeeded":
            # A run that failed with nothing to show gets the normal retry
            # machinery rather than a comment saying so on every attempt.
            return

        body = report_mod.render(
            report,
            state=state,
            run=run,
            uploaded=uploaded,
            usage={
                "total_tokens": attempt.total_tokens,
                "cost_usd": attempt.cost_usd,
                "tool_calls": attempt.tool_call_count,
            },
            fallback_text=attempt.result_text,
        )

        duration = None
        if attempt.started_at:
            duration = (datetime.now(timezone.utc) - attempt.started_at).total_seconds()
        self.ledger.record_stage(
            workflow=self._workflow_name_for(issue),
            project=self.project_name or "",
            issue_id=issue.id,
            issue=issue.identifier,
            title=issue.title,
            state=state,
            run=run,
            status=attempt.status,
            report=report,
            tokens=attempt.total_tokens,
            cost_usd=attempt.cost_usd,
            tool_calls=attempt.tool_call_count,
            tool_errors=attempt.tool_error_count,
            artifacts=len(uploaded),
            model=attempt.model,
            duration_s=duration,
        )

        posted = await client.post_comment(issue.id, body)
        if posted:
            logger.info(
                f"Posted {state} report ({len(uploaded)} artifacts)",
                extra={"linked_to": issue.identifier},
            )
        else:
            logger.warning("Failed to post run report", extra={"linked_to": issue.identifier})

        # ── Classification label ────────────────────────────────────────────
        await self._apply_classification_label(client, issue, report)

        # Consume both so the next stage starts clean.
        artifacts_mod.clear(workspace_path)
        report_mod.discard(workspace_path)

    async def _apply_classification_label(
        self, client: LinearClient, issue: Issue, report: dict | None
    ) -> None:
        """Tag the issue with what the work was, and how sure the agent is.

        Both are recorded in the ledger too; the labels are so a human can
        filter the board without leaving Linear.
        """
        labels = report_mod.labels_for(report)
        if not labels:
            return

        try:
            team_id, existing_labels, on_issue = await client.fetch_team_labels(issue.id)
        except Exception as e:
            logger.warning(f"Could not read labels: {e}", extra={"linked_to": issue.identifier})
            return

        for name, colour in labels:
            label_id = existing_labels.get(name.lower())

            if label_id is None:
                if not team_id:
                    continue
                label_id = await client.create_label(team_id, name, colour)
                if not label_id:
                    logger.warning(f"Could not create label '{name}'", extra={"linked_to": issue.identifier})
                    continue

            if label_id in on_issue:
                continue

            if await client.add_label(issue.id, label_id):
                logger.info(f"Labelled {issue.identifier} '{name}'", extra={"linked_to": issue.identifier})


    def _on_child_pid(self, pid: int, is_register: bool):
        """Track child claude process PIDs for cleanup on shutdown."""
        if is_register:
            self._child_pids.add(pid)
        else:
            self._child_pids.discard(pid)

    def _on_agent_event(self, identifier: str, event_type: str, event: dict):
        """Mirror notable agent activity into the log buffer.

        Tool calls and text both arrive inside `assistant` events as content
        blocks — there is no separate tool_use event type — so this walks the
        blocks rather than switching on the top-level type.
        """
        extra = {"linked_to": identifier}

        if event_type == "assistant":
            content = (event.get("message") or {}).get("content")
            if isinstance(content, str):
                if content.strip():
                    logger.info(f"[{identifier}] {content.strip()[:160]}", extra=extra)
                return
            if not isinstance(content, list):
                return
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "tool")
                    detail = summarise_tool_input(name, block.get("input"))
                    suffix = f" {detail}" if detail else ""
                    logger.info(f"[{identifier}] {name}{suffix}", extra=extra)
                elif block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        logger.info(f"[{identifier}] {text[:160]}", extra=extra)

        elif event_type == "user":
            # Only failures are worth a log line; successes are the norm.
            content = (event.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        logger.warning(f"[{identifier}] tool error", extra=extra)

        elif event_type == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, dict):
                # Limits are account-wide, so the newest reading is the truth
                # for every project this process is running.
                self.rate_limit = {
                    "status": info.get("status"),
                    "type": info.get("rateLimitType"),
                    "resets_at": info.get("resetsAt"),
                    "overage_status": info.get("overageStatus"),
                    "using_overage": info.get("isUsingOverage"),
                }
                if info.get("status") and info.get("status") != "allowed":
                    logger.warning(
                        f"[{identifier}] rate limit {info.get('status')} "
                        f"({info.get('rateLimitType')})",
                        extra=extra,
                    )

        elif event_type == "result":
            result_text = event.get("result", "")
            if isinstance(result_text, str) and result_text:
                logger.info(f"[{identifier}] result: {result_text[:160]}", extra=extra)

    def _on_worker_exit(self, issue: Issue, attempt: RunAttempt):
        """Handle worker completion."""
        self.total_input_tokens += attempt.input_tokens
        self.total_output_tokens += attempt.output_tokens
        self.total_cache_creation_tokens += attempt.cache_creation_tokens
        self.total_cache_read_tokens += attempt.cache_read_tokens
        self.total_tokens += attempt.total_tokens
        self.total_cost_usd += attempt.cost_usd
        self.total_tool_calls += attempt.tool_call_count
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
        self._release_slot(issue.id)

        if attempt.status == "succeeded":
            if attempt.state_name and attempt.state_name in self._states_for(issue):
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
            f"error={error or 'continuation'}",
            extra={"linked_to": issue.identifier},
        )

    async def _handle_retry(self, issue_id: str):
        """Handle a retry timer firing."""
        entry = self.retry_attempts.pop(issue_id, None)
        self._retry_timers.pop(issue_id, None)

        if entry is None:
            return

        # Fetch fresh candidates to check eligibility
        try:
            client = self._ensure_linear_client()
            candidates = await client.fetch_candidate_issues(
                self.cfg.tracker.project_slug,
                self.cfg.active_linear_states(),
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
            logger.info(f"Retry: issue {entry.identifier} no longer active, releasing", extra={"linked_to": entry.identifier})
            return

        # Check slots via the same path as the dispatch loop
        can, reason = self._has_slot()
        if not can:
            self._schedule_retry(
                issue,
                attempt_num=entry.attempt,
                delay_ms=10_000,
                error=reason or "no available orchestrator slots",
            )
            return
        if not self._claim_slot(issue.id):
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
            client = self._ensure_linear_client()
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
                _ident = self.running[issue_id].issue_identifier if issue_id in self.running else issue_id
                logger.info(
                    f"Reconciliation: {_ident} is terminal ({current_state}), stopping",
                    extra={"linked_to": _ident},
                )
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()

                attempt = self.running.get(issue_id)
                if attempt:
                    ws_root = self.cfg.workspace.resolved_root()
                    await remove_workspace(
                        ws_root, attempt.issue_identifier, self.cfg.hooks
                    )

                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)
                self.claimed.discard(issue_id)
                self._release_slot(issue_id)
                # Clean up state caches so stale entries don't accumulate
                self._issue_current_state.pop(issue_id, None)
                self._issue_state_runs.pop(issue_id, None)
                self._announced_states = {k for k in self._announced_states if k[0] != issue_id}
                self._pending_gates.pop(issue_id, None)
                self._last_session_ids.pop(issue_id, None)

            elif state_lower == review_lower:
                # In review/gate state — stop worker but keep gate tracking
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()
                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)
                self._release_slot(issue_id)

            elif state_lower not in active_lower:
                # Neither active nor terminal nor review - stop without cleanup
                _ident = self.running[issue_id].issue_identifier if issue_id in self.running else issue_id
                logger.info(
                    f"Reconciliation: {_ident} not active ({current_state}), stopping",
                    extra={"linked_to": _ident},
                )
                task = self._tasks.get(issue_id)
                if task:
                    task.cancel()
                self.running.pop(issue_id, None)
                self._tasks.pop(issue_id, None)
                self.claimed.discard(issue_id)
                self._release_slot(issue_id)

    def get_state_snapshot(self) -> dict[str, Any]:
        """Get current runtime state for observability."""
        now = datetime.now(timezone.utc)
        active_seconds = sum(
            (now - r.started_at).total_seconds()
            for r in self.running.values()
            if r.started_at
        )
        project_name = self.project_name or ""

        # Totals are banked when a worker exits, so in-flight agents must be
        # added on top — otherwise the dashboard reads zero cost for the whole
        # of a long run and only jumps at the end.
        live = self.running.values()
        live_input = sum(r.input_tokens for r in live)
        live_output = sum(r.output_tokens for r in live)
        live_cache_creation = sum(r.cache_creation_tokens for r in live)
        live_cache_read = sum(r.cache_read_tokens for r in live)
        live_total = sum(r.total_tokens for r in live)
        live_cost = sum(r.cost_usd for r in live)
        live_tools = sum(r.tool_call_count for r in live)

        return {
            "generated_at": now.isoformat(),
            "project_name": project_name,
            "paused": self.pool.is_paused(project_name) if self.pool is not None else False,
            "counts": {
                "running": len(self.running),
                "retrying": len(self.retry_attempts),
                "gates": len(self._pending_gates),
                "queued": len(self._queued),
            },
            "running": [
                {
                    "project_name": project_name,
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
                        "cache_creation_tokens": r.cache_creation_tokens,
                        "cache_read_tokens": r.cache_read_tokens,
                        "total_tokens": r.total_tokens,
                    },
                    "cost_usd": round(r.cost_usd, 4),
                    "model": r.model,
                    "state_name": r.state_name,
                    "tool_call_count": r.tool_call_count,
                    "tool_error_count": r.tool_error_count,
                    "tool_counts": dict(r.tool_counts),
                    "agent_turns": r.agent_turns,
                    "compaction_count": r.compaction_count,
                    "permission_denials": r.permission_denials,
                    "artifact_count": len(r.artifacts),
                    # The dashboard renders a timeline from this; cap the wire
                    # payload so a long-running agent cannot bloat every poll.
                    "activity": [e.to_dict() for e in list(r.activity)[-40:]],
                }
                for r in self.running.values()
            ],
            "retrying": [
                {
                    "project_name": project_name,
                    "issue_id": e.issue_id,
                    "issue_identifier": e.identifier,
                    "attempt": e.attempt,
                    "error": e.error,
                }
                for e in self.retry_attempts.values()
            ],
            "gates": [
                {
                    "project_name": project_name,
                    "issue_id": issue_id,
                    "issue_identifier": self._last_issues.get(issue_id, Issue(id="", identifier=issue_id, title="")).identifier,
                    "gate_state": gate_state,
                    "run": self._issue_state_runs.get(issue_id, 1),
                }
                for issue_id, gate_state in self._pending_gates.items()
            ],
            "queued": [
                {**q, "project_name": project_name} for q in self._queued
            ],
            "rate_limit": self.rate_limit,
            "totals": {
                "input_tokens": self.total_input_tokens + live_input,
                "output_tokens": self.total_output_tokens + live_output,
                "cache_creation_tokens": self.total_cache_creation_tokens + live_cache_creation,
                "cache_read_tokens": self.total_cache_read_tokens + live_cache_read,
                "total_tokens": self.total_tokens + live_total,
                "cost_usd": round(self.total_cost_usd + live_cost, 4),
                "tool_calls": self.total_tool_calls + live_tools,
                "seconds_running": round(
                    self.total_seconds_running + active_seconds, 1
                ),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-project coordinator
# ─────────────────────────────────────────────────────────────────────────────


class MultiOrchestrator:
    """Owns N per-project Orchestrators sharing one ConcurrencyPool.

    Both single-project and multi-project workflows are run through this
    coordinator — single-project just means N=1. The coordinator handles
    shared concurrency, aggregated dashboard state, pause/resume,
    keyboard handler context, and cooperative startup/shutdown.
    """

    def __init__(self, workflow_path: str | Path):
        self.workflow_path = Path(workflow_path)
        self.pool = ConcurrencyPool()
        self.orchestrators: dict[str, Orchestrator] = {}  # project_name -> Orchestrator
        self._tasks: list[asyncio.Task] = []
        self._stop_event: asyncio.Event | None = None
        self.config: "ServiceConfig | None" = None
        # Eagerly parse config so callers can read server.port etc. before start()
        self._initial_load()

    # ── Config wiring ──────────────────────────────────────────────────────

    def _initial_load(self) -> tuple[list[ProjectConfig], list[str]]:
        """Parse the workflow file. Returns (projects, errors)."""
        try:
            full = parse_workflow_file(self.workflow_path)
        except Exception as e:
            return [], [f"Workflow load error: {e}"]
        errors = validate_config(full.config)
        if errors:
            return [], errors
        self.config = full.config
        return list(full.config.projects), []

    def _refresh_pool_caps(self) -> None:
        """Pull global cap + per-project caps from the latest workflow file."""
        try:
            full = parse_workflow_file(self.workflow_path)
        except Exception:
            return
        self.config = full.config
        agent = full.config.agent
        self.pool.global_cap = agent.max_concurrent_agents
        # Per-project caps: project block override wins, then agent map
        caps: dict[str, int] = {}
        for p in full.config.projects:
            if p.max_concurrent is not None:
                caps[p.name] = int(p.max_concurrent)
        for name, val in agent.max_concurrent_per_project.items():
            caps.setdefault(str(name), int(val))
        self.pool.per_project_caps = caps
        # Initial pause states (only applied for newly-seen projects;
        # don't clobber a runtime toggle by re-reading workflow.yaml)
        for p in full.config.projects:
            if p.paused and p.name not in self.pool.running_per_project:
                self.pool.pause(p.name)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self):
        projects, errors = self._initial_load()
        if errors:
            for e in errors:
                logger.error(f"Config error: {e}")
            raise RuntimeError(f"Startup validation failed: {errors}")

        self._refresh_pool_caps()

        for project in projects:
            orch = Orchestrator(
                workflow_path=self.workflow_path,
                project_name=project.name,
                pool=self.pool,
            )
            self.orchestrators[project.name] = orch

        logger.info(
            f"Starting MultiOrchestrator "
            f"projects=[{', '.join(self.orchestrators.keys())}] "
            f"global_cap={self.pool.global_cap}"
        )

        self._stop_event = asyncio.Event()

        # Start a periodic pool refresher so global cap and per-project
        # caps track hot-reloaded workflow.yaml changes.
        self._tasks.append(asyncio.create_task(self._pool_refresh_loop()))

        # Run all per-project orchestrators concurrently
        for orch in self.orchestrators.values():
            self._tasks.append(asyncio.create_task(orch.start()))

        # Block until stop()
        await self._stop_event.wait()

    async def _pool_refresh_loop(self):
        """Re-read workflow.yaml periodically to pick up cap changes."""
        try:
            while True:
                await asyncio.sleep(30)
                self._refresh_pool_caps()
        except asyncio.CancelledError:
            return

    async def stop(self):
        """Stop all orchestrators in parallel."""
        if self._stop_event is not None:
            self._stop_event.set()
        # Stop all per-project orchestrators
        await asyncio.gather(
            *(o.stop() for o in self.orchestrators.values()),
            return_exceptions=True,
        )
        for t in self._tasks:
            t.cancel()

    # ── Pause / resume ─────────────────────────────────────────────────────

    @property
    def project_names(self) -> list[str]:
        return list(self.orchestrators.keys())

    def is_paused(self, project_name: str) -> bool:
        return self.pool.is_paused(project_name)

    def pause(self, project_name: str) -> bool:
        if project_name not in self.orchestrators:
            return False
        self.pool.pause(project_name)
        return True

    def resume(self, project_name: str) -> bool:
        if project_name not in self.orchestrators:
            return False
        self.pool.resume(project_name)
        return True

    def toggle(self, project_name: str) -> bool:
        if project_name not in self.orchestrators:
            return False
        return self.pool.toggle(project_name)

    # ── Aggregated state for dashboard / status table ──────────────────────

    async def force_tick(self):
        """Trigger an immediate tick on every orchestrator."""
        await asyncio.gather(
            *(o._tick() for o in self.orchestrators.values()),
            return_exceptions=True,
        )

    def get_state_snapshot(self) -> dict[str, Any]:
        """Aggregate all orchestrator snapshots into one combined view."""
        now = datetime.now(timezone.utc)
        per_project: list[dict[str, Any]] = []
        running: list[dict] = []
        retrying: list[dict] = []
        gates: list[dict] = []
        queued: list[dict] = []
        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        total_tokens = 0
        total_cost = 0.0
        total_tools = 0
        total_seconds = 0.0
        rate_limit: dict[str, Any] | None = None
        for name, orch in self.orchestrators.items():
            snap = orch.get_state_snapshot()
            per_project.append({
                "name": name,
                "paused": snap["paused"],
                "counts": snap["counts"],
                "totals": snap["totals"],
            })
            running.extend(snap["running"])
            retrying.extend(snap["retrying"])
            gates.extend(snap["gates"])
            queued.extend(snap["queued"])
            total_input += snap["totals"]["input_tokens"]
            total_output += snap["totals"]["output_tokens"]
            total_cache_creation += snap["totals"]["cache_creation_tokens"]
            total_cache_read += snap["totals"]["cache_read_tokens"]
            total_tokens += snap["totals"]["total_tokens"]
            total_cost += snap["totals"]["cost_usd"]
            total_tools += snap["totals"]["tool_calls"]
            total_seconds += snap["totals"]["seconds_running"]
            # Rate limits are per-account, not per-project — any project's
            # reading describes the same shared window.
            rate_limit = snap.get("rate_limit") or rate_limit
        return {
            "generated_at": now.isoformat(),
            "projects": per_project,
            "pool": self.pool.snapshot(),
            "counts": {
                "running": len(running),
                "retrying": len(retrying),
                "gates": len(gates),
                "queued": len(queued),
                "projects": len(per_project),
            },
            "running": running,
            "retrying": retrying,
            "gates": gates,
            "queued": queued,
            "rate_limit": rate_limit,
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_creation_tokens": total_cache_creation,
                "cache_read_tokens": total_cache_read,
                "total_tokens": total_tokens,
                "cost_usd": round(total_cost, 4),
                "tool_calls": total_tools,
                "seconds_running": round(total_seconds, 1),
            },
        }
