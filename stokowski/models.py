"""Core domain models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# Rolling activity trail depth per attempt. Deep enough for the dashboard to
# show meaningful history, shallow enough that a long run cannot grow unbounded.
ACTIVITY_MAXLEN = 250


@dataclass
class ActivityEntry:
    """One notable thing the agent did, for the dashboard timeline."""

    at: datetime
    kind: str                       # tool | tool_result | text | thinking | result | system | rate_limit | warning
    label: str                      # tool name, or short category label
    detail: str = ""                # command, file path, or text snippet
    status: str | None = None       # None | "error" | "warn"

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "status": self.status,
        }


@dataclass
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


@dataclass
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    state: str = ""
    branch_name: str | None = None
    url: str | None = None
    labels: list[str] = field(default_factory=list)
    blocked_by: list[BlockerRef] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RunAttempt:
    issue_id: str
    issue_identifier: str
    attempt: int | None = None
    workspace_path: str = ""
    started_at: datetime | None = None
    status: str = "pending"
    session_id: str | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    turn_count: int = 0
    last_event_at: datetime | None = None
    last_event: str | None = None
    last_message: str = ""
    completed_at: datetime | None = None
    state_name: str | None = None       # current internal state machine state

    # ── Usage ────────────────────────────────────────────────────────────
    # Cache tokens are tracked separately because they dominate real runs and
    # are billed at different rates (creation 1.25x, read 0.1x). `total_tokens`
    # is the sum of all four — the stream has no total_tokens field of its own.
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None
    model_usage: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── Activity ─────────────────────────────────────────────────────────
    activity: deque[ActivityEntry] = field(
        default_factory=lambda: deque(maxlen=ACTIVITY_MAXLEN)
    )
    tool_counts: dict[str, int] = field(default_factory=dict)
    tool_call_count: int = 0
    tool_error_count: int = 0
    pending_tools: dict[str, str] = field(default_factory=dict)  # tool_use_id -> name

    # ── Outcome detail ───────────────────────────────────────────────────
    agent_turns: int = 0            # turns reported by the CLI (not our turn_count)
    duration_ms: int = 0
    duration_api_ms: int = 0
    stop_reason: str | None = None
    permission_denials: int = 0
    compaction_count: int = 0
    result_is_error: bool = False
    result_text: str = ""
    rate_limit: dict[str, Any] | None = None

    # ── Artifacts ────────────────────────────────────────────────────────
    # Files the agent produced as evidence (screenshots etc). These live
    # outside the git clone so they can never be committed to the project.
    artifacts: list[str] = field(default_factory=list)

    def billed_token_summary(self) -> dict[str, int]:
        """Token breakdown for reporting."""
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "cache_creation": self.cache_creation_tokens,
            "cache_read": self.cache_read_tokens,
            "thinking": self.thinking_tokens,
            "total": self.total_tokens,
        }


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int = 1
    due_at_ms: float = 0
    error: str | None = None
