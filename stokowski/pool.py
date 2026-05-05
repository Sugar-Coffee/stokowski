"""Shared concurrency pool for multi-project orchestration.

Each `Orchestrator` instance still owns its own `running` dict for
tracking what *it* dispatched, but the decision of whether a dispatch
is allowed funnels through a `ConcurrencyPool` so the global cap and
per-project caps are honoured fairly across all projects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("stokowski.pool")


@dataclass
class ConcurrencyPool:
    """Tracks the global agent budget across all projects.

    Not asyncio-safe in a strict sense, but the orchestrator runs all
    dispatch decisions on the asyncio event loop thread, so claim/release
    are effectively serialised.
    """
    global_cap: int = 5
    per_project_caps: dict[str, int] = field(default_factory=dict)
    running_per_project: dict[str, int] = field(default_factory=dict)
    paused: set[str] = field(default_factory=set)

    def is_paused(self, project_name: str) -> bool:
        return project_name in self.paused

    def pause(self, project_name: str) -> None:
        if project_name not in self.paused:
            self.paused.add(project_name)
            logger.info(f"Paused project: {project_name}")

    def resume(self, project_name: str) -> None:
        if project_name in self.paused:
            self.paused.discard(project_name)
            logger.info(f"Resumed project: {project_name}")

    def toggle(self, project_name: str) -> bool:
        """Toggle pause state. Returns the new paused state (True = paused)."""
        if project_name in self.paused:
            self.resume(project_name)
            return False
        self.pause(project_name)
        return True

    def total_running(self) -> int:
        return sum(self.running_per_project.values())

    def project_running(self, project_name: str) -> int:
        return self.running_per_project.get(project_name, 0)

    def project_cap(self, project_name: str) -> int | None:
        """Return the per-project cap, or None if unlimited (subject to global)."""
        return self.per_project_caps.get(project_name)

    def available_for(self, project_name: str) -> int:
        """How many more slots this project can claim right now.

        Considers: pause state, per-project cap, global cap minus current
        running across all projects.
        """
        if self.is_paused(project_name):
            return 0
        global_left = max(self.global_cap - self.total_running(), 0)
        cap = self.per_project_caps.get(project_name)
        if cap is None:
            return global_left
        project_left = max(cap - self.project_running(project_name), 0)
        return min(global_left, project_left)

    def try_claim(self, project_name: str) -> bool:
        """Atomically claim one slot. Returns True if claimed."""
        if self.available_for(project_name) <= 0:
            return False
        self.running_per_project[project_name] = (
            self.running_per_project.get(project_name, 0) + 1
        )
        return True

    def release(self, project_name: str) -> None:
        """Release one slot for a project. Idempotent for projects already at 0."""
        current = self.running_per_project.get(project_name, 0)
        if current <= 0:
            return
        self.running_per_project[project_name] = current - 1

    def snapshot(self) -> dict:
        return {
            "global_cap": self.global_cap,
            "global_running": self.total_running(),
            "global_available": max(self.global_cap - self.total_running(), 0),
            "projects": [
                {
                    "name": name,
                    "running": self.running_per_project.get(name, 0),
                    "cap": self.per_project_caps.get(name),
                    "paused": name in self.paused,
                    "available": self.available_for(name),
                }
                for name in sorted(
                    set(self.running_per_project)
                    | set(self.per_project_caps)
                    | set(self.paused)
                )
            ],
        }
