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
from pathlib import Path

logger = logging.getLogger("stokowski.state")


@dataclass
class PersistedState:
    last_schedule_fire_iso: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_seconds_running: float = 0
    retry_attempts: dict[str, dict] = field(default_factory=dict)


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
