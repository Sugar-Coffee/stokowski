"""Multi-workflow manager — holds N orchestrators behind one process."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

logger = logging.getLogger("stokowski.manager")


class Manager:
    """Manages multiple Orchestrator instances, one per workflow."""

    def __init__(
        self,
        workflow_paths: dict[str, Path],
        shared_raw: dict[str, Any] | None = None,
        workflow_enabled: dict[str, bool] | None = None,
    ):
        from .orchestrator import Orchestrator

        self.workflow_paths = workflow_paths
        self.shared_raw = shared_raw or {}
        self.orchestrators: dict[str, Orchestrator] = {}
        self._workflow_tasks: dict[str, asyncio.Task] = {}
        self._workflow_status: dict[str, str] = {}  # "running", "stopped", "failed"
        self._workflow_started_at: dict[str, str] = {}  # ISO timestamp
        self._shared_tracker = None  # shared Linear/GitHub client

        for name, path in workflow_paths.items():
            self.orchestrators[name] = Orchestrator(path, shared_raw=self.shared_raw)
            self._workflow_status[name] = "stopped"
        self._workflow_enabled = workflow_enabled or {}

        # Create shared tracker client for workflows that use the same tracker
        self._init_shared_tracker()

    def _init_shared_tracker(self):
        """Create a shared tracker client for all workflows using the same tracker."""
        # Find the first orchestrator with tracker_enabled to get config
        for orch in self.orchestrators.values():
            try:
                errors = orch._load_workflow()
                if errors:
                    continue
                if not orch.cfg.tracker_enabled:
                    continue
                if orch.cfg.source == "github-prs":
                    continue

                # Create the shared client
                kind = orch.cfg.tracker.kind
                if kind == "github":
                    from .github_issues import GitHubIssuesClient
                    self._shared_tracker = GitHubIssuesClient(
                        owner=orch.cfg.tracker.github_owner,
                        repo=orch.cfg.tracker.github_repo,
                        token=orch.cfg.resolved_api_key(),
                    )
                else:
                    from .linear import LinearClient
                    self._shared_tracker = LinearClient(
                        endpoint=orch.cfg.tracker.endpoint,
                        api_key=orch.cfg.resolved_api_key(),
                    )
                logger.info(f"Created shared {kind} tracker client")
                break
            except Exception:
                continue

        # Inject shared client into all tracker-enabled orchestrators
        if self._shared_tracker:
            for orch in self.orchestrators.values():
                try:
                    if orch.workflow and orch.cfg.tracker_enabled and orch.cfg.source != "github-prs":
                        orch._tracker = self._shared_tracker
                except Exception:
                    pass

    async def start(self):
        """Start enabled workflows or recover previously running ones."""
        # Check for recovery — restart workflows that were running before shutdown
        previously_running = self._load_manager_state()
        if previously_running:
            logger.info(f"Recovering {len(previously_running)} previously running workflow(s)")
            for name in previously_running:
                if name in self.orchestrators:
                    self.start_workflow(name)
        else:
            # Normal start — use enabled flags
            for name in self.orchestrators:
                if self._workflow_enabled.get(name, False):
                    self.start_workflow(name)

        started = [n for n, s in self._workflow_status.items() if s == "running"]
        stopped = [n for n, s in self._workflow_status.items() if s == "stopped"]
        if started:
            logger.info(f"Started {len(started)} workflow(s): {', '.join(started)}")
        if stopped:
            logger.info(f"{len(stopped)} workflow(s) stopped (start from dashboard): {', '.join(stopped)}")

        # Keep running — wait for tasks or idle (dashboard-only mode)
        self._stop_event = asyncio.Event()
        while not self._stop_event.is_set():
            running_tasks = [t for t in self._workflow_tasks.values() if not t.done()]
            if running_tasks:
                done, _ = await asyncio.wait(
                    running_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=5.0,
                )
                for t in done:
                    exc = t.exception()
                    if exc:
                        wf_name = t.get_name().replace("workflow:", "")
                        self._workflow_status[wf_name] = "failed"
                        logger.error(f"Workflow '{wf_name}' failed: {exc}")
            else:
                # No workflows running — idle, waiting for UI to start one
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

    def start_workflow(self, name: str) -> bool:
        """Start a single workflow. Returns False if already running or unknown."""
        if name not in self.orchestrators:
            return False
        if name in self._workflow_tasks and not self._workflow_tasks[name].done():
            return False  # already running

        # Re-create orchestrator if it was stopped (stop() clears internal state)
        if self._workflow_status.get(name) in ("stopped", "failed"):
            from .orchestrator import Orchestrator
            path = self.workflow_paths[name]
            orch = Orchestrator(path, shared_raw=self.shared_raw)
            # Inject shared tracker client
            if self._shared_tracker:
                try:
                    errors = orch._load_workflow()
                    if not errors and orch.cfg.tracker_enabled and orch.cfg.source != "github-prs":
                        orch._tracker = self._shared_tracker
                except Exception:
                    pass
            self.orchestrators[name] = orch

        orch = self.orchestrators[name]
        task = asyncio.create_task(orch.start(), name=f"workflow:{name}")
        task.add_done_callback(lambda t, n=name: self._on_workflow_done(n, t))
        self._workflow_tasks[name] = task
        self._workflow_status[name] = "running"
        from datetime import datetime, timezone
        self._workflow_started_at[name] = datetime.now(timezone.utc).isoformat()
        self._save_manager_state()
        logger.info(f"Started workflow '{name}'")
        return True

    async def stop_workflow(self, name: str) -> bool:
        """Stop a single workflow. Returns False if not running or unknown."""
        if name not in self.orchestrators:
            return False
        orch = self.orchestrators[name]
        await orch.stop()
        task = self._workflow_tasks.get(name)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._workflow_status[name] = "stopped"
        self._save_manager_state()
        logger.info(f"Stopped workflow '{name}'")
        return True

    def _on_workflow_done(self, name: str, task: asyncio.Task):
        """Callback when a workflow task completes."""
        if task.exception():
            self._workflow_status[name] = "failed"
        elif self._workflow_status.get(name) != "stopped":
            self._workflow_status[name] = "stopped"

    def _save_manager_state(self):
        """Save which workflows were running for recovery."""
        import json
        state_path = None
        for orch in self.orchestrators.values():
            state_path = orch.workflow_path.parent / ".stokowski_manager_state.json"
            break
        if not state_path:
            return
        try:
            data = {
                "running_workflows": [
                    name for name, status in self._workflow_status.items()
                    if status == "running"
                ],
                "started_at": dict(self._workflow_started_at),
            }
            state_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"Failed to save manager state: {e}")

    def _load_manager_state(self) -> list[str]:
        """Load previously running workflows for recovery."""
        import json
        state_path = None
        for orch in self.orchestrators.values():
            state_path = orch.workflow_path.parent / ".stokowski_manager_state.json"
            break
        if not state_path or not state_path.exists():
            return []
        try:
            data = json.loads(state_path.read_text())
            return data.get("running_workflows", [])
        except Exception:
            return []

    async def stop(self):
        """Stop all orchestrators."""
        logger.info("Stopping all workflows")
        self._save_manager_state()
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        await asyncio.gather(
            *(orch.stop() for orch in self.orchestrators.values()),
            return_exceptions=True,
        )

    def get_aggregate_snapshot(self) -> dict:
        """Merge snapshots from all orchestrators."""
        snapshots = {}
        for name, orch in self.orchestrators.items():
            try:
                snap = orch.get_state_snapshot()
                snap["status"] = self._workflow_status.get(name, "stopped")
                snap["started_at"] = self._workflow_started_at.get(name, "")
                snapshots[name] = snap
            except Exception:
                snapshots[name] = {
                    "counts": {}, "totals": {},
                    "started_at": self._workflow_started_at.get(name, ""),
                    "status": self._workflow_status.get(name, "stopped"),
                }

        total_running = sum(s.get("counts", {}).get("running", 0) for s in snapshots.values())
        total_retrying = sum(s.get("counts", {}).get("retrying", 0) for s in snapshots.values())
        total_gates = sum(s.get("counts", {}).get("gates", 0) for s in snapshots.values())

        return {
            "workflows": snapshots,
            "counts": {
                "running": total_running,
                "retrying": total_retrying,
                "gates": total_gates,
            },
            "totals": {
                "total_tokens": sum(s.get("totals", {}).get("total_tokens", 0) for s in snapshots.values()),
                "input_tokens": sum(s.get("totals", {}).get("input_tokens", 0) for s in snapshots.values()),
                "output_tokens": sum(s.get("totals", {}).get("output_tokens", 0) for s in snapshots.values()),
                "seconds_running": round(
                    sum(s.get("totals", {}).get("seconds_running", 0) for s in snapshots.values()),
                    1,
                ),
            },
            # Flatten running/retrying/gates across workflows with workflow name
            "running": [
                {**r, "workflow": name}
                for name, s in snapshots.items() for r in s.get("running", [])
            ],
            "retrying": [
                {**r, "workflow": name}
                for name, s in snapshots.items() for r in s.get("retrying", [])
            ],
            "gates": [
                {**g, "workflow": name}
                for name, s in snapshots.items() for g in s.get("gates", [])
            ],
        }

    def find_orchestrator_for_webhook(
        self, project_slug: str = "", github_repo: str = ""
    ) -> Orchestrator | None:
        """Route a webhook to the matching orchestrator."""
        for orch in self.orchestrators.values():
            if orch.workflow is None:
                continue
            cfg = orch.cfg
            if github_repo and cfg.tracker.kind == "github":
                key = f"{cfg.tracker.github_owner}/{cfg.tracker.github_repo}"
                if key == github_repo:
                    return orch
            if project_slug and cfg.tracker.project_slug == project_slug:
                return orch
        return None
