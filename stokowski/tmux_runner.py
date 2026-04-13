"""Tmux-based agent runner — launches agents in tmux windows with native UI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import ClaudeConfig, HooksConfig
from .models import Issue, RunAttempt

logger = logging.getLogger("stokowski.tmux_runner")

# --- Constants ---

TMUX_SESSION = "stokowski"
TMUX_CMD_TIMEOUT = 10  # seconds per tmux subprocess call
POLL_INTERVAL_S = 2.0
STARTUP_TIMEOUT_S = 60.0

# Patterns for detecting Claude Code's idle prompt (last non-empty line).
# Tuned after manual testing — add patterns as needed.
IDLE_PROMPT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^>\s*$"),       # bare ">"
    re.compile(r"^❯\s*$"),      # Unicode prompt
    re.compile(r"^\$\s*$"),     # shell $ (Claude exited back to shell)
]

# Module-level tracker for live REPL windows (issue_id -> pane target)
_active_panes: dict[str, str] = {}


# --- Tmux helpers ---

async def _tmux(*args: str) -> tuple[int, str, str]:
    """Run a tmux command. Returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TMUX_CMD_TIMEOUT,
        )
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except FileNotFoundError:
        raise TmuxNotFound("tmux command not found — is tmux installed?")
    except asyncio.TimeoutError:
        raise TmuxError("tmux command timed out")


async def ensure_tmux_session(session: str = TMUX_SESSION) -> None:
    """Create the tmux session if it doesn't already exist."""
    rc, _, _ = await _tmux("has-session", "-t", session)
    if rc != 0:
        await _tmux(
            "new-session", "-d", "-s", session,
            "-x", "220", "-y", "50",
        )
        logger.info(f"Created tmux session: {session}")


async def create_agent_window(
    session: str,
    window_name: str,
    workspace_path: Path,
    claude_cmd: list[str],
    env: dict[str, str] | None = None,
) -> str:
    """Create a tmux window running claude. Returns pane target string."""
    # Build shell command with env var exports
    parts: list[str] = []
    if env:
        parent_env = os.environ
        for key, val in env.items():
            if parent_env.get(key) != val:
                parts.append(f"export {key}={shlex.quote(val)};")
    parts.append(shlex.join(claude_cmd))
    shell_cmd = " ".join(parts)

    await _tmux(
        "new-window",
        "-t", session,
        "-n", window_name,
        "-c", str(workspace_path),
        shell_cmd,
    )
    target = f"{session}:{window_name}"
    logger.info(f"Created tmux window: {target}")
    return target


async def send_prompt(pane_target: str, prompt: str) -> None:
    """Paste a prompt into the Claude REPL via bracketed paste."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="stk-prompt-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(prompt)
        await _tmux("load-buffer", path)
        await _tmux("paste-buffer", "-p", "-t", pane_target)
        # Small delay so the paste registers before we send Enter
        await asyncio.sleep(0.2)
        await _tmux("send-keys", "-t", pane_target, "Enter")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def capture_pane(pane_target: str) -> str:
    """Capture full scrollback content of a tmux pane."""
    rc, stdout, _ = await _tmux(
        "capture-pane", "-p", "-t", pane_target, "-S", "-",
    )
    return stdout


async def is_pane_alive(pane_target: str) -> bool:
    """Check whether the tmux window still exists."""
    session, _, window = pane_target.partition(":")
    rc, stdout, _ = await _tmux(
        "list-windows", "-t", session, "-F", "#{window_name}",
    )
    if rc != 0:
        return False
    return window in stdout.strip().split("\n")


async def kill_agent_window(session: str, window_name: str) -> None:
    """Kill a tmux window. Idempotent — ignores errors if already dead."""
    try:
        await _tmux("kill-window", "-t", f"{session}:{window_name}")
    except TmuxError:
        pass


# --- Idle detection ---

def is_idle(pane_content: str) -> bool:
    """Check if the last non-empty line matches Claude's idle prompt."""
    lines = pane_content.rstrip().split("\n")
    # Walk backwards to find last non-empty line
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return any(p.match(stripped) for p in IDLE_PROMPT_PATTERNS)
    return False


# --- Response extraction ---

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def extract_response(full_capture: str, pre_prompt_capture: str) -> str:
    """Extract Claude's response by diffing pre-prompt and post-prompt captures.

    Returns the new text that appeared after the prompt was submitted,
    excluding the final idle prompt line.
    """
    pre_lines = pre_prompt_capture.rstrip().split("\n")
    full_lines = full_capture.rstrip().split("\n")

    # Find where new content starts
    start = len(pre_lines)

    # Remove the final idle prompt line
    end = len(full_lines)
    for i in range(len(full_lines) - 1, start - 1, -1):
        stripped = full_lines[i].strip()
        if stripped and any(p.match(stripped) for p in IDLE_PROMPT_PATTERNS):
            end = i
            break

    if start >= end:
        return ""

    response = "\n".join(full_lines[start:end])
    return strip_ansi(response).strip()


# --- Command builders ---

def build_tmux_cmd(cli: str, claude_cfg: ClaudeConfig) -> list[str]:
    """Build REPL launch command for the given CLI (no -p, no --output-format).

    cli: "claude" (default), "gemini", or "codex".
    """
    if cli == "gemini":
        return _build_tmux_gemini_cmd(claude_cfg)
    if cli == "codex":
        return _build_tmux_codex_cmd(claude_cfg)
    return _build_tmux_claude_cmd(claude_cfg)


def _build_tmux_claude_cmd(claude_cfg: ClaudeConfig) -> list[str]:
    """Build claude REPL launch command."""
    args = [claude_cfg.command]

    if claude_cfg.permission_mode == "auto":
        args.append("--dangerously-skip-permissions")
    elif claude_cfg.permission_mode == "allowedTools" and claude_cfg.allowed_tools:
        args.extend(["--allowedTools", ",".join(claude_cfg.allowed_tools)])

    if claude_cfg.model:
        args.extend(["--model", claude_cfg.model])

    if claude_cfg.append_system_prompt:
        args.extend(["--append-system-prompt", claude_cfg.append_system_prompt])

    if claude_cfg.mcp_config:
        for cfg in claude_cfg.mcp_config:
            args.extend(["--mcp-config", cfg])

    return args


def _build_tmux_gemini_cmd(claude_cfg: ClaudeConfig) -> list[str]:
    """Build gemini REPL launch command."""
    args = ["gemini"]

    if claude_cfg.permission_mode == "auto":
        args.extend(["--approval-mode", "yolo"])
    elif claude_cfg.permission_mode == "allowedTools" and claude_cfg.allowed_tools:
        args.extend(["--allowed-tools"] + claude_cfg.allowed_tools)

    if claude_cfg.model:
        args.extend(["--model", claude_cfg.model])

    return args


def _build_tmux_codex_cmd(claude_cfg: ClaudeConfig) -> list[str]:
    """Build codex REPL launch command."""
    args = ["codex"]

    if claude_cfg.permission_mode == "auto":
        args.append("--dangerously-bypass-approvals-and-sandbox")

    if claude_cfg.model:
        args.extend(["--model", claude_cfg.model])

    return args


# --- Window name helper ---

def _sanitize_window_name(identifier: str) -> str:
    """Make an issue identifier safe for use as a tmux window name."""
    return identifier.replace(".", "-").replace(":", "-").replace("/", "-")


# --- Exceptions ---

class TmuxError(Exception):
    pass


class TmuxNotFound(TmuxError):
    pass


# --- Main entry point ---

async def run_tmux_turn(
    claude_cfg: ClaudeConfig,
    hooks_cfg: HooksConfig,
    prompt: str,
    workspace_path: Path,
    issue: Issue,
    attempt: RunAttempt,
    on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
    on_pid: Callable[[int, bool], None] | None = None,
    env: dict[str, str] | None = None,
    cli: str = "claude",
) -> RunAttempt:
    """Run a single agent turn in a tmux window with native CLI UI.

    cli: which CLI to launch — "claude" (default), "gemini", or "codex".
    """
    window_name = _sanitize_window_name(issue.identifier)
    pane_target = f"{TMUX_SESSION}:{window_name}"

    logger.info(
        f"Launching claude (tmux) issue={issue.identifier} "
        f"turn={attempt.turn_count + 1}"
    )

    # --- Before-run hook ---
    if hooks_cfg.before_run:
        from .workspace import run_hook

        ok = await run_hook(
            hooks_cfg.before_run, workspace_path,
            hooks_cfg.timeout_ms, "before_run",
        )
        if not ok:
            attempt.status = "failed"
            attempt.error = "before_run hook failed"
            return attempt

    # --- Update attempt metadata ---
    attempt.status = "streaming"
    attempt.started_at = attempt.started_at or datetime.now(timezone.utc)
    attempt.turn_count += 1
    attempt.last_event_at = datetime.now(timezone.utc)

    try:
        # --- Ensure tmux session ---
        await ensure_tmux_session()

        # --- Get or create window ---
        existing_target = _active_panes.get(issue.id)
        if existing_target and await is_pane_alive(existing_target):
            pane_target = existing_target
        else:
            # Create new window
            cmd = build_tmux_cmd(cli, claude_cfg)
            pane_target = await create_agent_window(
                TMUX_SESSION, window_name, workspace_path, cmd, env,
            )
            _active_panes[issue.id] = pane_target

            # Wait for Claude REPL to show idle prompt (startup)
            if not await _wait_for_idle(
                pane_target, STARTUP_TIMEOUT_S, label="startup",
            ):
                attempt.status = "failed"
                attempt.error = "Claude REPL did not start within timeout"
                await kill_agent_window(TMUX_SESSION, window_name)
                _active_panes.pop(issue.id, None)
                return attempt

        # --- Capture baseline ---
        pre_prompt = await capture_pane(pane_target)

        # --- Send prompt ---
        await send_prompt(pane_target, prompt)

        # --- Poll for completion ---
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        last_content = ""
        last_change = loop.time()
        turn_timeout_s = claude_cfg.turn_timeout_ms / 1000
        stall_timeout_s = claude_cfg.stall_timeout_ms / 1000

        # Give Claude a moment to start processing before polling
        await asyncio.sleep(POLL_INTERVAL_S)

        while True:
            # Check if pane is still alive
            if not await is_pane_alive(pane_target):
                attempt.status = "failed"
                attempt.error = "Claude REPL exited unexpectedly"
                _active_panes.pop(issue.id, None)
                break

            current = await capture_pane(pane_target)

            if current != last_content:
                last_content = current
                last_change = loop.time()
                attempt.last_event_at = datetime.now(timezone.utc)

            # Check for idle prompt (turn complete)
            idle_seconds = loop.time() - last_change
            if idle_seconds >= POLL_INTERVAL_S and is_idle(current):
                # Claude is done — extract response
                response = extract_response(current, pre_prompt)
                attempt.last_message = response

                # Check for signals
                if "STOKOWSKI:BLOCKED" in response:
                    attempt.status = "blocked"
                elif "STOKOWSKI:REWORK" in response:
                    attempt.status = "rework"
                if "STOKOWSKI:NEEDS_REVIEW" in response:
                    attempt.needs_review = True

                if attempt.status == "streaming":
                    attempt.status = "succeeded"
                break

            # Stall detection
            if stall_timeout_s > 0 and idle_seconds > stall_timeout_s:
                logger.warning(
                    f"Tmux stall detected issue={issue.identifier} "
                    f"idle={idle_seconds:.0f}s"
                )
                attempt.status = "stalled"
                attempt.error = f"No output for {idle_seconds:.0f}s"
                await kill_agent_window(TMUX_SESSION, window_name)
                _active_panes.pop(issue.id, None)
                break

            # Turn timeout
            elapsed = loop.time() - start_time
            if elapsed > turn_timeout_s:
                logger.warning(
                    f"Tmux turn timeout issue={issue.identifier}"
                )
                attempt.status = "timed_out"
                attempt.error = f"Turn exceeded {turn_timeout_s}s"
                await kill_agent_window(TMUX_SESSION, window_name)
                _active_panes.pop(issue.id, None)
                break

            await asyncio.sleep(POLL_INTERVAL_S)

    except TmuxNotFound as e:
        attempt.status = "failed"
        attempt.error = str(e)
        logger.error(f"tmux not found: {e}")
    except TmuxError as e:
        attempt.status = "failed"
        attempt.error = str(e)
        logger.error(f"tmux error issue={issue.identifier}: {e}")
    except Exception as e:
        attempt.status = "failed"
        attempt.error = str(e)
        logger.error(f"Tmux runner error issue={issue.identifier}: {e}")

    # --- Cleanup window on terminal statuses ---
    if attempt.status in ("blocked", "failed", "stalled", "timed_out"):
        await _safe_kill(TMUX_SESSION, window_name)
        _active_panes.pop(issue.id, None)

    # --- After-run hook ---
    if hooks_cfg.after_run:
        from .workspace import run_hook

        await run_hook(
            hooks_cfg.after_run, workspace_path,
            hooks_cfg.timeout_ms, "after_run",
        )

    logger.info(
        f"Tmux turn complete issue={issue.identifier} "
        f"status={attempt.status}"
    )
    return attempt


# --- Internal helpers ---

async def _wait_for_idle(
    pane_target: str,
    timeout_s: float,
    label: str = "",
) -> bool:
    """Poll capture_pane until an idle prompt is detected. Returns True on success."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        content = await capture_pane(pane_target)
        if is_idle(content):
            return True

    logger.warning(f"Idle wait timed out ({label}) target={pane_target}")
    return False


async def _safe_kill(session: str, window_name: str) -> None:
    """Kill a window, swallowing all errors."""
    try:
        await kill_agent_window(session, window_name)
    except Exception:
        pass


# --- Cleanup ---

async def cleanup_all_windows(session: str = TMUX_SESSION) -> None:
    """Kill the entire stokowski tmux session. Best-effort."""
    _active_panes.clear()
    try:
        await _tmux("kill-session", "-t", session)
        logger.info(f"Killed tmux session: {session}")
    except Exception:
        pass
