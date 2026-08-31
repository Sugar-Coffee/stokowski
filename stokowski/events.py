"""Claude Code stream-json event parsing.

The CLI emits NDJSON on stdout under `--output-format stream-json --verbose`.
This module owns the mapping from those raw events onto `RunAttempt` state so
that `runner.py` can stay focused on process lifecycle.

Event shapes (verified against Claude Code, not inferred):

    {"type":"system","subtype":"init","session_id":...,"model":...,"tools":[...]}
    {"type":"assistant","message":{"content":[{"type":"thinking"|"text"|"tool_use",...}]}}
    {"type":"user","message":{"content":[{"type":"tool_result","is_error":bool,...}]}}
    {"type":"rate_limit_event","rate_limit_info":{"rateLimitType":"five_hour",...}}
    {"type":"result","subtype":"success","usage":{...},"total_cost_usd":...}

Note there is NO top-level "tool_use" event — tool calls arrive as content
blocks inside `assistant` messages, and their outcomes as `tool_result` blocks
inside `user` messages. Parsing for a top-level event yields a permanently
idle-looking dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .models import ActivityEntry, RunAttempt

logger = logging.getLogger("stokowski.events")

# Callback signature: (issue_identifier, event_type, raw_event)
EventCallback = Callable[[str, str, dict[str, Any]], None]

# Tool name -> the input field that best describes what it is doing.
# Ordered tuples so we can fall back when the preferred field is absent.
_TOOL_DETAIL_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("command",),
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Glob": ("pattern",),
    "Grep": ("pattern",),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
    "Task": ("description", "prompt"),
    "Agent": ("description", "prompt"),
    "Skill": ("skill",),
    "TodoWrite": (),
}

_MAX_DETAIL = 160


def display_tool_name(name: str) -> str:
    """Shorten MCP tool names for display.

    `mcp__playwright__browser_take_screenshot` is 38 characters and crowds out
    the argument, which is the part that says what the agent is doing.
    """
    if name.startswith("mcp__"):
        parts = name[len("mcp__"):].split("__", 1)
        if len(parts) == 2:
            return f"{parts[0]}:{parts[1]}"
        return parts[0]
    return name


def summarise_tool_input(name: str, tool_input: Any) -> str:
    """Produce a short human-readable description of a tool call."""
    if not isinstance(tool_input, dict):
        return ""

    for field in _TOOL_DETAIL_FIELDS.get(name, ()):
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MAX_DETAIL]

    if name in _TOOL_DETAIL_FIELDS:
        # Known tool with no useful field (e.g. TodoWrite) — stay quiet.
        return ""

    # Unknown tool (MCP servers, custom tools): show the first short string.
    for key, value in tool_input.items():
        if isinstance(value, str) and value.strip() and len(value) <= 200:
            return f"{key}={value.strip()}"[:_MAX_DETAIL]
    return ""


def _record(attempt: RunAttempt, kind: str, label: str, detail: str = "", status: str | None = None) -> None:
    """Append to the attempt's rolling activity trail and update the headline."""
    entry = ActivityEntry(
        at=datetime.now(timezone.utc),
        kind=kind,
        label=label,
        detail=detail,
        status=status,
    )
    attempt.activity.append(entry)

    # `last_message` is the one-line headline shown in the dashboard and CLI.
    if kind == "tool":
        attempt.last_message = f"{label}: {detail}" if detail else label
    elif kind in ("text", "result"):
        attempt.last_message = detail[:200]
    elif kind == "thinking":
        attempt.last_message = f"thinking… {detail[:160]}" if detail else "thinking…"


def _accumulate_usage(attempt: RunAttempt, usage: dict) -> None:
    """Add one invocation's usage onto the attempt totals.

    Each `claude -p` invocation reports usage for that invocation only, and a
    worker may run many invocations via --resume, so these accumulate. The
    payload has no `total_tokens` field; cache tokens are separate and dominate
    real runs (a trivial turn measured 4 input vs 45,255 cache-read).
    """
    def _int(key: str) -> int:
        value = usage.get(key, 0)
        return value if isinstance(value, int) else 0

    attempt.input_tokens += _int("input_tokens")
    attempt.output_tokens += _int("output_tokens")
    attempt.cache_creation_tokens += _int("cache_creation_input_tokens")
    attempt.cache_read_tokens += _int("cache_read_input_tokens")
    attempt.total_tokens = (
        attempt.input_tokens
        + attempt.output_tokens
        + attempt.cache_creation_tokens
        + attempt.cache_read_tokens
    )

    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        thinking = details.get("thinking_tokens", 0)
        if isinstance(thinking, int):
            attempt.thinking_tokens += thinking


def _accumulate_model_usage(attempt: RunAttempt, model_usage: dict) -> None:
    """Merge the per-model cost/token breakdown from a result event."""
    for model, stats in model_usage.items():
        if not isinstance(stats, dict):
            continue
        bucket = attempt.model_usage.setdefault(
            model,
            {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "cache_creation_tokens": 0, "cost_usd": 0.0},
        )
        bucket["input_tokens"] += stats.get("inputTokens", 0) or 0
        bucket["output_tokens"] += stats.get("outputTokens", 0) or 0
        bucket["cache_read_tokens"] += stats.get("cacheReadInputTokens", 0) or 0
        bucket["cache_creation_tokens"] += stats.get("cacheCreationInputTokens", 0) or 0
        bucket["cost_usd"] += stats.get("costUSD", 0.0) or 0.0


def process_event(
    event: dict,
    attempt: RunAttempt,
    on_event: EventCallback | None,
    identifier: str,
) -> None:
    """Fold a single stream-json event into the RunAttempt."""
    event_type = event.get("type", "")
    attempt.last_event = event_type

    if event_type == "system":
        _handle_system(event, attempt)
    elif event_type == "assistant":
        _handle_assistant(event, attempt)
    elif event_type == "user":
        _handle_user(event, attempt)
    elif event_type == "rate_limit_event":
        _handle_rate_limit(event, attempt)
    elif event_type == "result":
        _handle_result(event, attempt)

    if on_event:
        on_event(identifier, event_type, event)


def _handle_system(event: dict, attempt: RunAttempt) -> None:
    subtype = event.get("subtype", "")

    if subtype == "init":
        # Capture the session id up front. The result event carries it too, but
        # a turn that stalls or times out never produces one — and without it
        # the next attempt cannot --resume and restarts from scratch.
        session_id = event.get("session_id")
        if session_id:
            attempt.session_id = session_id
        model = event.get("model")
        if isinstance(model, str):
            attempt.model = model
        _record(attempt, "system", "session", model or "")

    elif subtype == "compact_boundary":
        attempt.compaction_count += 1
        _record(attempt, "system", "compacted", "context window rolled")


def _handle_assistant(event: dict, attempt: RunAttempt) -> None:
    message = event.get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        if content.strip():
            _record(attempt, "text", "assistant", content.strip())
        return

    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "tool_use":
            raw_name = block.get("name", "tool")
            detail = summarise_tool_input(raw_name, block.get("input"))
            name = display_tool_name(raw_name)
            attempt.tool_counts[name] = attempt.tool_counts.get(name, 0) + 1
            attempt.tool_call_count += 1
            tool_id = block.get("id")
            if tool_id:
                attempt.pending_tools[tool_id] = name
            _record(attempt, "tool", name, detail)

        elif block_type == "text":
            text = (block.get("text") or "").strip()
            if text:
                _record(attempt, "text", "assistant", text)

        elif block_type == "thinking":
            thinking = (block.get("thinking") or "").strip()
            if thinking:
                _record(attempt, "thinking", "thinking", thinking)


def _handle_user(event: dict, attempt: RunAttempt) -> None:
    """Tool results come back as `user` events."""
    message = event.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue

        tool_id = block.get("tool_use_id")
        name = attempt.pending_tools.pop(tool_id, "tool") if tool_id else "tool"
        is_error = bool(block.get("is_error"))

        if is_error:
            attempt.tool_error_count += 1
            detail = _stringify_result(block.get("content"))
            _record(attempt, "tool_result", name, detail, status="error")


def _stringify_result(content: Any) -> str:
    """Flatten a tool_result content payload into a short string."""
    if isinstance(content, str):
        return content.strip()[:_MAX_DETAIL]
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return (block.get("text") or "").strip()[:_MAX_DETAIL]
    return ""


def _handle_rate_limit(event: dict, attempt: RunAttempt) -> None:
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return

    attempt.rate_limit = {
        "status": info.get("status"),
        "type": info.get("rateLimitType"),
        "resets_at": info.get("resetsAt"),
        "overage_status": info.get("overageStatus"),
        "using_overage": info.get("isUsingOverage"),
    }

    # Only surface non-nominal states — "allowed" fires constantly and would
    # drown the activity trail.
    if info.get("status") and info.get("status") != "allowed":
        _record(
            attempt,
            "rate_limit",
            f"rate limit {info.get('status')}",
            str(info.get("rateLimitType") or ""),
            status="warn",
        )


def _handle_result(event: dict, attempt: RunAttempt) -> None:
    session_id = event.get("session_id")
    if session_id:
        attempt.session_id = session_id

    usage = event.get("usage")
    if isinstance(usage, dict):
        _accumulate_usage(attempt, usage)

    model_usage = event.get("modelUsage")
    if isinstance(model_usage, dict):
        _accumulate_model_usage(attempt, model_usage)

    cost = event.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        attempt.cost_usd += float(cost)

    for field, key in (("duration_ms", "duration_ms"), ("duration_api_ms", "duration_api_ms")):
        value = event.get(key)
        if isinstance(value, int):
            setattr(attempt, field, getattr(attempt, field) + value)

    num_turns = event.get("num_turns")
    if isinstance(num_turns, int):
        attempt.agent_turns += num_turns

    stop_reason = event.get("stop_reason")
    if isinstance(stop_reason, str):
        attempt.stop_reason = stop_reason

    denials = event.get("permission_denials")
    if isinstance(denials, list) and denials:
        attempt.permission_denials += len(denials)
        _record(
            attempt,
            "warning",
            "permission denied",
            f"{len(denials)} tool call(s) blocked",
            status="warn",
        )

    # `is_error` marks an in-band failure (e.g. max turns) that still exits 0.
    if event.get("is_error"):
        attempt.result_is_error = True
        subtype = event.get("subtype") or "error"
        attempt.error = attempt.error or f"Agent reported {subtype}"

    result_text = event.get("result")
    if isinstance(result_text, str) and result_text.strip():
        attempt.result_text = result_text
        _record(attempt, "result", "result", result_text.strip())
