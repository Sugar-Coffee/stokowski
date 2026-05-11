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

    Priority-aware slot allocation: when multiple projects want slots,
    higher-priority (lower priority number) projects are granted first.
    """
    global_cap: int = 5
    per_project_caps: dict[str, int] = field(default_factory=dict)
    running_per_project: dict[str, int] = field(default_factory=dict)
    paused: set[str] = field(default_factory=set)
    # Linear project priority per project name: lower = higher priority, 0 = unset (sorts last)
    project_priority: dict[str, int] = field(default_factory=dict)
    # Projects that have requested a slot but couldn't get one yet (ordered by priority)
    _slot_queue: dict[str, int] = field(default_factory=dict)  # name -> when queued (monotonic counter)

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

    def sorted_project_names(self) -> list[str]:
        """Return project names sorted by priority (lowest priority number = highest priority).

        Projects with priority 0 (unset in Linear) sort after those with explicit priority.
        Projects not in the priority map sort last.
        """
        def sort_key(name: str) -> tuple[int, str]:
            p = self.project_priority.get(name, 0)
            # priority 0 = unset, sort it last by treating it as 999
            effective = p if p > 0 else 999
            return (effective, name)
        return sorted(self.paused | set(self.running_per_project) | set(self.per_project_caps) | set(self.project_priority), key=sort_key)

    def request_slot(self, project_name: str) -> None:
        """Register interest in a slot for a project. Used for priority-aware dispatch."""
        if project_name not in self._slot_queue:
            self._slot_queue[project_name] = len(self._slot_queue)

    def grant_queued_slots(self) -> list[str]:
        """Grant available slots to queued projects in priority order.

        Returns list of project names that were granted a slot this call.
        """
        granted: list[str] = []
        # Sort by priority (use sorted_project_names for consistent priority ordering)
        priority_order = self.sorted_project_names()
        queue_sorted = sorted(
            self._slot_queue.keys(),
            key=lambda n: (next((i for i, p in enumerate(priority_order) if p == n), 999), self._slot_queue[n])
        )
        for name in queue_sorted:
            if self.available_for(name) <= 0:
                continue
            if self.try_claim(name):
                del self._slot_queue[name]
                granted.append(name)
        return granted

    def available_for(self, project_name: str) -> int:
        """How many more slots this project can claim right now.

        When multiple projects compete for slots, global capacity is allocated
        in priority order — higher-priority (lower priority number) projects
        receive slots first before lower-priority projects.

        A higher-priority project only pre-empts if it is *actively consuming*
        slots (running > 0). Idle higher-priority projects don't block lower ones.

        Paused projects always return 0.
        """
        if self.is_paused(project_name):
            return 0

        global_left = max(self.global_cap - self.total_running(), 0)
        if global_left == 0:
            return 0

        cap = self.per_project_caps.get(project_name)
        project_cap = cap if cap is not None else self.global_cap

        # If no priority map exists, fall back to equal distribution
        if not self.project_priority:
            return min(global_left, project_cap)

        # Priority-aware allocation: iterate projects in priority order.
        #
        # global_left = global_cap - total_running already accounts for ALL
        # currently-running agents across every project.  If we simply
        # returned global_left, lower-priority projects would see fewer slots
        # than they actually have because higher-priority agents' current
        # consumption is already baked in.
        #
        # We fix this by starting from global_left and ADDING BACK the growth
        # potential of higher-priority projects (max(cap - running, 0)) before
        # reaching the target project in priority order.
        #
        # Effect: idle higher-priority projects add 0 (no pre-emption).
        # Active higher-priority projects add their remaining growth headroom,
        # which restores the "correct" pool size for lower-priority projects.
        sorted_names = self.sorted_project_names()
        remaining = global_left

        for name in sorted_names:
            if name == project_name:
                own_available = max(project_cap - self.project_running(name), 0)
                return min(remaining, own_available)

            name_running = self.project_running(name)
            name_cap = self.per_project_caps.get(name)
            name_project_cap = name_cap if name_cap is not None else self.global_cap
            remaining += max(name_project_cap - name_running, 0)
            # Keep remaining bounded so it never exceeds what's actually free
            remaining = min(remaining, self.global_cap - self.project_running(name))

        # Target not in priority list — give it all remaining headroom
        own_available = max(project_cap - self.project_running(project_name), 0)
        return min(remaining, own_available)

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
