"""Lightweight persistent state for crash recovery.

Stores aggregate metrics, schedule fire timestamps, and retry state
in a JSON file under the workspace root. Purely optional — if the file
is missing or corrupt, the orchestrator starts fresh.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("stokowski.state")


@dataclass
class PersistedIssueState:
    """Per-issue state for crash recovery."""
    issue_id: str = ""
    identifier: str = ""
    current_state: str = ""       # internal state machine state
    run: int = 1
    session_id: str | None = None
    workspace_path: str = ""


@dataclass
class PersistedState:
    last_schedule_fire_iso: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_seconds_running: float = 0
    retry_attempts: dict[str, dict] = field(default_factory=dict)
    # Per-issue tracking for crash recovery
    issues: dict[str, dict] = field(default_factory=dict)  # issue_id -> PersistedIssueState as dict


def state_file_path(workflow_path: Path) -> Path:
    """Derive state file path — stored next to the workflow YAML."""
    return workflow_path.resolve().parent / f".stokowski_state_{workflow_path.stem}.json"


def load_state(path: Path) -> PersistedState:
    """Load persisted state from JSON. Returns defaults if missing or corrupt."""
    if not path.exists():
        return PersistedState()
    try:
        data = json.loads(path.read_text())
        return PersistedState(
            last_schedule_fire_iso=data.get("last_schedule_fire_iso"),
            total_input_tokens=int(data.get("total_input_tokens", 0)),
            total_output_tokens=int(data.get("total_output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            total_seconds_running=float(data.get("total_seconds_running", 0)),
            retry_attempts=data.get("retry_attempts", {}),
        )
    except Exception as e:
        logger.warning(f"Failed to load state from {path}, starting fresh: {e}")
        return PersistedState()


def save_state(path: Path, state: PersistedState) -> None:
    """Atomically write state to JSON (write-to-temp + rename)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(state)
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=".stokowski_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning(f"Failed to save state to {path}: {e}")


# ---------------------------------------------------------------------------
# Session ID persistence — saves per-issue session IDs to sessions.json
# ---------------------------------------------------------------------------

def sessions_file_path(stokowski_dir: Path) -> Path:
    """Path to the sessions file."""
    return stokowski_dir / "sessions.json"


def load_sessions(path: Path) -> dict[str, dict]:
    """Load sessions map from JSON file."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        logger.warning(f"Failed to load sessions from {path}: {e}")
        return {}


def save_session(
    stokowski_dir: Path,
    identifier: str,
    session_id: str | None,
    session_ids: list[str],
) -> None:
    """Update the sessions file with session IDs for an issue."""
    if not session_ids:
        return
    path = sessions_file_path(stokowski_dir)
    try:
        sessions = load_sessions(path)
        existing = sessions.get(identifier, {})
        # Merge session_ids — keep existing ones, append new
        all_ids: list[str] = existing.get("session_ids", [])
        for sid in session_ids:
            if sid not in all_ids:
                all_ids.append(sid)
        sessions[identifier] = {
            "session_id": session_id or (all_ids[-1] if all_ids else ""),
            "session_ids": all_ids,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Atomic write
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=".sessions_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(sessions, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning(f"Failed to save session for {identifier}: {e}")
