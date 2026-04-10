"""State machine tracking.

Tracking data is stored in the persistent state file, not in
issue descriptions or comments. This avoids polluting the issue
with machine-readable markup that Linear renders as visible text.

Legacy support: the parser still reads old <!-- stokowski:state -->
comment format for backwards compatibility during migration.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("stokowski.tracking")

_TRACKING_MARKER = "<!-- stokowski:tracking"
_TRACKING_PATTERN = re.compile(
    r"<!-- stokowski:tracking\s*\n(.*?)\n-->", re.DOTALL
)

# Legacy comment patterns (for backwards compat during migration)
_LEGACY_STATE = re.compile(r"<!-- stokowski:state ({.*?}) -->")
_LEGACY_GATE = re.compile(r"<!-- stokowski:gate ({.*?}) -->")


def build_tracking_block(payload: dict[str, Any]) -> str:
    """Build the hidden tracking block for the issue description."""
    return f"<!-- stokowski:tracking\n{json.dumps(payload)}\n-->"


def parse_tracking_from_description(description) -> dict[str, Any] | None:
    """Extract tracking data from issue description."""
    if not isinstance(description, str):
        return None
    match = _TRACKING_PATTERN.search(description)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def update_description_tracking(
    description: str, payload: dict[str, Any]
) -> str:
    """Update or append tracking block in issue description."""
    block = build_tracking_block(payload)
    if _TRACKING_MARKER in description:
        # Replace existing block
        return _TRACKING_PATTERN.sub(block, description)
    else:
        # Append to end
        return description.rstrip() + "\n\n" + block


def make_state_payload(state: str, run: int = 1) -> dict[str, Any]:
    """Build tracking payload for a state entry."""
    return {
        "type": "state",
        "state": state,
        "run": run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_gate_payload(
    state: str,
    status: str,
    rework_to: str | None = None,
    run: int = 1,
) -> dict[str, Any]:
    """Build tracking payload for a gate event."""
    payload: dict[str, Any] = {
        "type": "gate",
        "state": state,
        "status": status,
        "run": run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if rework_to:
        payload["rework_to"] = rework_to
    return payload


def parse_latest_tracking(
    description: str, comments: list[dict] | None = None
) -> dict[str, Any] | None:
    """Parse tracking data from description, falling back to legacy comments.

    Checks description first (new format), then comments (old format).
    """
    # New format: tracking block in description
    result = parse_tracking_from_description(description)
    if result:
        return result

    # Legacy: scan comments for old <!-- stokowski:state/gate --> format
    if comments:
        latest: dict[str, Any] | None = None
        for comment in comments:
            body = comment.get("body", "")
            state_match = _LEGACY_STATE.search(body)
            if state_match:
                try:
                    data = json.loads(state_match.group(1))
                    data["type"] = "state"
                    latest = data
                except json.JSONDecodeError:
                    pass
            gate_match = _LEGACY_GATE.search(body)
            if gate_match:
                try:
                    data = json.loads(gate_match.group(1))
                    data["type"] = "gate"
                    latest = data
                except json.JSONDecodeError:
                    pass
        return latest

    return None


def get_last_tracking_timestamp(
    description: str, comments: list[dict] | None = None
) -> str | None:
    """Get timestamp of the latest tracking entry."""
    tracking = parse_latest_tracking(description, comments)
    return tracking.get("timestamp") if tracking else None


def get_comments_since(
    comments: list[dict], since_timestamp: str | None
) -> list[dict]:
    """Filter comments to non-tracking comments after a timestamp."""
    result = []
    since_dt = None
    if since_timestamp:
        try:
            since_dt = datetime.fromisoformat(
                since_timestamp.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            pass

    for comment in comments:
        body = comment.get("body", "")
        # Skip legacy tracking comments
        if "<!-- stokowski:" in body:
            continue

        if since_dt:
            created = comment.get("createdAt", "")
            if created:
                try:
                    created_dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    if created_dt <= since_dt:
                        continue
                except (ValueError, AttributeError):
                    pass

        result.append(comment)

    return result
