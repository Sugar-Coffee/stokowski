"""Core domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    message_log: list[dict] = field(default_factory=list)  # dashboard event history


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int = 1
    due_at_ms: float = 0
    error: str | None = None
