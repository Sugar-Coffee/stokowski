"""Tests for the tmux-based agent runner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stokowski.config import ClaudeConfig, HooksConfig
from stokowski.models import Issue, RunAttempt
from stokowski.tmux_runner import (
    IDLE_PROMPT_PATTERNS,
    TMUX_SESSION,
    _sanitize_window_name,
    build_tmux_cmd,
    cleanup_all_windows,
    extract_response,
    is_idle,
    run_tmux_turn,
    strip_ansi,
)


# --- build_tmux_cmd ---


class TestBuildTmuxCmd:
    """Test command building for all supported CLIs."""

    # --- Claude ---

    def test_claude_auto_permissions(self):
        cfg = ClaudeConfig(command="claude", permission_mode="auto")
        args = build_tmux_cmd("claude", cfg)
        assert args == ["claude", "--dangerously-skip-permissions"]

    def test_claude_no_stream_json_flags(self):
        cfg = ClaudeConfig()
        args = build_tmux_cmd("claude", cfg)
        assert "-p" not in args
        assert "--output-format" not in args
        assert "stream-json" not in args
        assert "--verbose" not in args

    def test_claude_model_override(self):
        cfg = ClaudeConfig(model="claude-opus-4-1")
        args = build_tmux_cmd("claude", cfg)
        assert "--model" in args
        assert "claude-opus-4-1" in args

    def test_claude_allowed_tools(self):
        cfg = ClaudeConfig(
            permission_mode="allowedTools",
            allowed_tools=["Bash", "Read"],
        )
        args = build_tmux_cmd("claude", cfg)
        assert "--dangerously-skip-permissions" not in args
        assert "--allowedTools" in args
        assert "Bash,Read" in args

    def test_claude_mcp_config(self):
        cfg = ClaudeConfig(mcp_config=["/path/to/mcp.json", "inline-json"])
        args = build_tmux_cmd("claude", cfg)
        assert args.count("--mcp-config") == 2

    def test_claude_append_system_prompt(self):
        cfg = ClaudeConfig(append_system_prompt="Be helpful")
        args = build_tmux_cmd("claude", cfg)
        assert "--append-system-prompt" in args
        assert "Be helpful" in args

    # --- Gemini ---

    def test_gemini_auto_permissions(self):
        cfg = ClaudeConfig(permission_mode="auto")
        args = build_tmux_cmd("gemini", cfg)
        assert args[0] == "gemini"
        assert "--approval-mode" in args
        assert "yolo" in args
        assert "--dangerously-skip-permissions" not in args

    def test_gemini_allowed_tools_space_separated(self):
        cfg = ClaudeConfig(
            permission_mode="allowedTools",
            allowed_tools=["Bash", "Read"],
        )
        args = build_tmux_cmd("gemini", cfg)
        assert "--allowed-tools" in args
        # Gemini uses space-separated, not comma-separated
        assert "Bash" in args
        assert "Read" in args
        assert "--allowedTools" not in args

    def test_gemini_model_override(self):
        cfg = ClaudeConfig(model="gemini-2.5-pro")
        args = build_tmux_cmd("gemini", cfg)
        assert "--model" in args
        assert "gemini-2.5-pro" in args

    def test_gemini_no_stream_json_flags(self):
        cfg = ClaudeConfig()
        args = build_tmux_cmd("gemini", cfg)
        assert "-p" not in args
        assert "--output-format" not in args

    # --- Codex ---

    def test_codex_auto_permissions(self):
        cfg = ClaudeConfig(permission_mode="auto")
        args = build_tmux_cmd("codex", cfg)
        assert args[0] == "codex"
        assert "--dangerously-bypass-approvals-and-sandbox" in args
        assert "--dangerously-skip-permissions" not in args

    def test_codex_model_override(self):
        cfg = ClaudeConfig(model="o3")
        args = build_tmux_cmd("codex", cfg)
        assert "--model" in args
        assert "o3" in args

    def test_codex_no_stream_json_flags(self):
        cfg = ClaudeConfig()
        args = build_tmux_cmd("codex", cfg)
        assert "-p" not in args
        assert "--output-format" not in args
        assert "--json" not in args
        assert "exec" not in args

    # --- Default ---

    def test_unknown_cli_defaults_to_claude(self):
        cfg = ClaudeConfig(command="claude", permission_mode="auto")
        args = build_tmux_cmd("unknown", cfg)
        assert args[0] == "claude"


# --- Idle detection ---


class TestIdleDetection:
    def test_bare_prompt(self):
        assert is_idle(">") is True

    def test_prompt_with_trailing_space(self):
        assert is_idle(">  ") is True

    def test_unicode_prompt(self):
        assert is_idle("some output\n\n❯") is True

    def test_shell_prompt(self):
        assert is_idle("$ ") is True

    def test_not_idle_mid_output(self):
        assert is_idle("Working on the task...\nReading files...") is False

    def test_not_idle_tool_use(self):
        assert is_idle("Using tool: Bash\nRunning command...") is False

    def test_prompt_with_content_above(self):
        content = "I've completed the task.\n\nAll tests pass.\n\n>"
        assert is_idle(content) is True

    def test_empty_content(self):
        assert is_idle("") is False
        assert is_idle("   \n\n  ") is False

    def test_prompt_in_middle_not_at_end(self):
        content = ">\nStill working..."
        assert is_idle(content) is False


# --- Response extraction ---


class TestResponseExtraction:
    def test_extract_basic(self):
        pre = "Welcome to Claude.\n\n>"
        full = "Welcome to Claude.\n\n>\nI've fixed the bug.\n\n>"
        result = extract_response(full, pre)
        assert "I've fixed the bug." in result

    def test_extract_empty_response(self):
        pre = "Welcome to Claude.\n\n>"
        full = "Welcome to Claude.\n\n>\n>"
        result = extract_response(full, pre)
        assert result == ""

    def test_strip_ansi_codes(self):
        text = "\x1b[32mgreen\x1b[0m normal"
        assert strip_ansi(text) == "green normal"

    def test_extract_with_ansi(self):
        pre = "Welcome.\n>"
        full = "Welcome.\n>\n\x1b[1mBold result\x1b[0m\n>"
        result = extract_response(full, pre)
        assert "Bold result" in result
        assert "\x1b" not in result


# --- Signal detection via run_tmux_turn ---


class TestSignalDetection:
    """Test that signals in response text are correctly detected."""

    def test_blocked_signal(self):
        pre = "Start\n>"
        full = "Start\n>\nI cannot proceed. STOKOWSKI:BLOCKED\n>"
        response = extract_response(full, pre)
        assert "STOKOWSKI:BLOCKED" in response

    def test_rework_signal(self):
        pre = "Start\n>"
        full = "Start\n>\nTests fail. STOKOWSKI:REWORK\n>"
        response = extract_response(full, pre)
        assert "STOKOWSKI:REWORK" in response

    def test_needs_review_signal(self):
        pre = "Start\n>"
        full = "Start\n>\nDone. STOKOWSKI:NEEDS_REVIEW\n>"
        response = extract_response(full, pre)
        assert "STOKOWSKI:NEEDS_REVIEW" in response


# --- Window name sanitization ---


class TestSanitizeWindowName:
    def test_dots_replaced(self):
        assert _sanitize_window_name("v1.2.3") == "v1-2-3"

    def test_colons_replaced(self):
        assert _sanitize_window_name("session:1") == "session-1"

    def test_slashes_replaced(self):
        assert _sanitize_window_name("org/repo#1") == "org-repo#1"

    def test_normal_identifier_unchanged(self):
        assert _sanitize_window_name("DEV-123") == "DEV-123"


# --- Helpers for mocking tmux ---


def _make_mock_subprocess(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
):
    """Create a mock for asyncio.create_subprocess_exec."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode()),
    )
    proc.returncode = returncode
    return proc


def _make_tmux_mocker(responses: dict[str, tuple[int, str, str]] | None = None):
    """Create a side_effect function for mocking _tmux calls.

    responses: dict mapping the first tmux subcommand to (rc, stdout, stderr).
    Unmatched commands return (0, "", "").
    """
    default = (0, "", "")
    resps = responses or {}

    async def mock_tmux(*args: str) -> tuple[int, str, str]:
        cmd = args[0] if args else ""
        return resps.get(cmd, default)

    return mock_tmux


# --- run_tmux_turn integration tests ---


class TestRunTmuxTurn:
    """Integration tests for run_tmux_turn with mocked tmux calls."""

    def _make_issue(self, identifier: str = "DEV-1") -> Issue:
        return Issue(
            id="issue-1",
            identifier=identifier,
            title="Test issue",
            state="Active",
        )

    def _make_attempt(self) -> RunAttempt:
        return RunAttempt(issue_id="issue-1", issue_identifier="DEV-1")

    def _make_cfg(self, **overrides) -> ClaudeConfig:
        defaults = dict(
            command="claude",
            permission_mode="auto",
            turn_timeout_ms=10_000,  # short for tests
            stall_timeout_ms=5_000,
        )
        defaults.update(overrides)
        return ClaudeConfig(**defaults)

    @pytest.mark.asyncio
    async def test_successful_turn(self):
        """Happy path: window created, prompt sent, idle detected."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        # Track call sequence
        capture_calls = 0

        async def mock_tmux(*args):
            nonlocal capture_calls
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")  # session exists
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "list-windows":
                return (0, "DEV-1\n", "")
            if cmd == "load-buffer":
                return (0, "", "")
            if cmd == "paste-buffer":
                return (0, "", "")
            if cmd == "send-keys":
                return (0, "", "")
            if cmd == "capture-pane":
                capture_calls += 1
                if capture_calls <= 2:
                    # First captures: startup idle
                    return (0, "Welcome to Claude.\n\n>", "")
                elif capture_calls == 3:
                    # Pre-prompt baseline
                    return (0, "Welcome to Claude.\n\n>", "")
                elif capture_calls == 4:
                    # Still working
                    return (0, "Welcome to Claude.\n\n>\nWorking...", "")
                else:
                    # Done — idle prompt returns
                    return (0, "Welcome to Claude.\n\n>\nTask complete.\n\n>", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "succeeded"
        assert "Task complete." in result.last_message
        assert result.turn_count == 1

    @pytest.mark.asyncio
    async def test_startup_timeout(self):
        """Claude never shows idle prompt during startup."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        async def mock_tmux(*args):
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "capture-pane":
                # Never idle — still loading
                return (0, "Loading...", "")
            if cmd == "kill-window":
                return (0, "", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()
        old_startup = tmux_runner.STARTUP_TIMEOUT_S
        tmux_runner.STARTUP_TIMEOUT_S = 3  # short for test

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes
            tmux_runner.STARTUP_TIMEOUT_S = old_startup

        assert result.status == "failed"
        assert "did not start" in result.error

    @pytest.mark.asyncio
    async def test_claude_crash_during_turn(self):
        """Claude REPL exits mid-turn."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        capture_calls = 0

        async def mock_tmux(*args):
            nonlocal capture_calls
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "list-windows":
                if capture_calls <= 3:
                    return (0, "DEV-1\n", "")
                # Window died
                return (0, "other-window\n", "")
            if cmd in ("load-buffer", "paste-buffer", "send-keys"):
                return (0, "", "")
            if cmd == "capture-pane":
                capture_calls += 1
                if capture_calls <= 2:
                    return (0, "Ready\n>", "")
                elif capture_calls == 3:
                    return (0, "Ready\n>", "")  # baseline
                else:
                    return (0, "Working...", "")
            if cmd == "kill-window":
                return (0, "", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "failed"
        assert "exited unexpectedly" in result.error

    @pytest.mark.asyncio
    async def test_blocked_signal_in_response(self):
        """Agent output contains STOKOWSKI:BLOCKED."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        capture_calls = 0

        async def mock_tmux(*args):
            nonlocal capture_calls
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "list-windows":
                return (0, "DEV-1\n", "")
            if cmd in ("load-buffer", "paste-buffer", "send-keys"):
                return (0, "", "")
            if cmd == "capture-pane":
                capture_calls += 1
                if capture_calls <= 3:
                    return (0, "Ready\n>", "")
                return (0, "Ready\n>\nCannot proceed. STOKOWSKI:BLOCKED\n>", "")
            if cmd == "kill-window":
                return (0, "", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "blocked"

    @pytest.mark.asyncio
    async def test_rework_signal_in_response(self):
        """Agent output contains STOKOWSKI:REWORK."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        capture_calls = 0

        async def mock_tmux(*args):
            nonlocal capture_calls
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "list-windows":
                return (0, "DEV-1\n", "")
            if cmd in ("load-buffer", "paste-buffer", "send-keys"):
                return (0, "", "")
            if cmd == "capture-pane":
                capture_calls += 1
                if capture_calls <= 3:
                    return (0, "Ready\n>", "")
                return (0, "Ready\n>\nTests fail. STOKOWSKI:REWORK\n>", "")
            if cmd == "kill-window":
                return (0, "", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "rework"

    @pytest.mark.asyncio
    async def test_before_run_hook_failure(self):
        """before_run hook fails — early return."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()
        hooks = HooksConfig(before_run="exit 1")

        with patch("stokowski.workspace.run_hook", new_callable=AsyncMock) as mock_hook:
            mock_hook.return_value = False
            result = await run_tmux_turn(
                claude_cfg=cfg,
                hooks_cfg=hooks,
                prompt="Fix the bug",
                workspace_path=Path("/tmp/ws"),
                issue=issue,
                attempt=attempt,
            )

        assert result.status == "failed"
        assert "before_run hook failed" in result.error

    @pytest.mark.asyncio
    async def test_tmux_not_installed(self):
        """tmux binary not found."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        from stokowski.tmux_runner import TmuxNotFound

        async def mock_tmux(*args):
            raise TmuxNotFound("tmux command not found")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "failed"
        assert "tmux" in result.error.lower()

    @pytest.mark.asyncio
    async def test_needs_review_flag_set(self):
        """STOKOWSKI:NEEDS_REVIEW sets the needs_review flag."""
        issue = self._make_issue()
        attempt = self._make_attempt()
        cfg = self._make_cfg()

        capture_calls = 0

        async def mock_tmux(*args):
            nonlocal capture_calls
            cmd = args[0] if args else ""
            if cmd == "has-session":
                return (0, "", "")
            if cmd == "new-window":
                return (0, "", "")
            if cmd == "list-windows":
                return (0, "DEV-1\n", "")
            if cmd in ("load-buffer", "paste-buffer", "send-keys"):
                return (0, "", "")
            if cmd == "capture-pane":
                capture_calls += 1
                if capture_calls <= 3:
                    return (0, "Ready\n>", "")
                return (0, "Ready\n>\nDone. STOKOWSKI:NEEDS_REVIEW\n>", "")
            return (0, "", "")

        from stokowski import tmux_runner
        old_panes = tmux_runner._active_panes.copy()
        tmux_runner._active_panes.clear()

        try:
            with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
                result = await run_tmux_turn(
                    claude_cfg=cfg,
                    hooks_cfg=HooksConfig(),
                    prompt="Fix the bug",
                    workspace_path=Path("/tmp/ws"),
                    issue=issue,
                    attempt=attempt,
                )
        finally:
            tmux_runner._active_panes = old_panes

        assert result.status == "succeeded"
        assert result.needs_review is True


# --- Cleanup ---


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_kills_session(self):
        from stokowski import tmux_runner

        called_with = []

        async def mock_tmux(*args):
            called_with.append(args)
            return (0, "", "")

        with patch.object(tmux_runner, "_tmux", side_effect=mock_tmux):
            await cleanup_all_windows()

        assert any("kill-session" in args for args in called_with)
