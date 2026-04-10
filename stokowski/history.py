"""Run history — persists completed runs to a JSON file."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("stokowski.history")

MAX_HISTORY = 500  # keep last N entries


@dataclass
class RunRecord:
    """A single completed run."""
    issue_id: str
    identifier: str
    title: str
    workflow: str
    status: str  # succeeded, failed, blocked, cancelled
    started_at: str
    completed_at: str
    duration_seconds: float
    tokens: int
    stages: list[str] = field(default_factory=list)  # state machine states traversed
    last_message: str = ""
    error: str | None = None


def history_file_path(stokowski_dir: Path) -> Path:
    """Path to the history file."""
    return stokowski_dir / "history.json"


def load_history(path: Path) -> list[dict]:
    """Load run history from JSON file."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning(f"Failed to load history: {e}")
        return []


def append_run(path: Path, record: RunRecord) -> None:
    """Append a run record to the history file."""
    try:
        history = load_history(path)
        history.append(asdict(record))
        # Trim to max size
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        # Atomic write
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".history_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(history, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning(f"Failed to save history: {e}")
