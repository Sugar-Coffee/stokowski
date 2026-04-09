"""Multi-workflow manager — holds N orchestrators behind one process."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

logger = logging.getLogger("stokowski.manager")


class Manager:
    """Manages multiple Orchestrator instances, one per workflow."""

    def __init__(self, workflow_paths: dict[str, Path]):
        from .orchestrator import Orchestrator

        self.orchestrators: dict[str, Orchestrator] = {}
        for name, path in workflow_paths.items():
            self.orchestrators[name] = Orchestrator(path)

    async def start(self):
        """Start all orchestrators concurrently."""
        tasks = {
            name: asyncio.create_task(orch.start(), name=f"workflow:{name}")
            for name, orch in self.orchestrators.items()
        }
        self._tasks = tasks
        logger.info(f"Starting {len(tasks)} workflows: {', '.join(tasks.keys())}")
        # Wait until all complete or one raises
        done, _ = await asyncio.wait(
            tasks.values(), return_when=asyncio.FIRST_EXCEPTION
        )
        for t in done:
            if t.exception():
                raise t.exception()

    async def stop(self):
        """Stop all orchestrators."""
        logger.info("Stopping all workflows")
        await asyncio.gather(
            *(orch.stop() for orch in self.orchestrators.values()),
            return_exceptions=True,
        )

    def get_aggregate_snapshot(self) -> dict:
        """Merge snapshots from all orchestrators."""
        snapshots = {}
        for name, orch in self.orchestrators.items():
            try:
                snapshots[name] = orch.get_state_snapshot()
            except Exception:
                snapshots[name] = {"counts": {}, "totals": {}}

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
            # Flatten running/retrying/gates across workflows for dashboard compat
            "running": [
                r for s in snapshots.values() for r in s.get("running", [])
            ],
            "retrying": [
                r for s in snapshots.values() for r in s.get("retrying", [])
            ],
            "gates": [
                g for s in snapshots.values() for g in s.get("gates", [])
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
