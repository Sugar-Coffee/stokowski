"""Workspace management - create, reuse, and clean per-issue workspaces."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import HooksConfig, WorkspaceConfig

logger = logging.getLogger("stokowski.workspace")

BASE_CLONE_NAME = ".stokowski-base-clone"


def sanitize_key(identifier: str) -> str:
    """Replace non-safe chars with underscore for directory name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", identifier)


@dataclass
class WorkspaceResult:
    path: Path
    workspace_key: str
    created_now: bool


async def run_hook(script: str, cwd: Path, timeout_ms: int, label: str) -> bool:
    """Run a shell hook script in the workspace directory. Returns True on success."""
    logger.info(f"hook={label} cwd={cwd}")
    try:
        proc = await asyncio.create_subprocess_shell(
            script,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
        if proc.returncode != 0:
            logger.error(
                f"hook={label} failed rc={proc.returncode} stderr={stderr.decode()[:500]}"
            )
            return False
        return True
    except asyncio.TimeoutError:
        logger.error(f"hook={label} timed out after {timeout_ms}ms")
        proc.kill()
        return False
    except Exception as e:
        logger.error(f"hook={label} error: {e}")
        return False


async def _run_git_worktree_add(ws_path: Path, base_clone_path: Path, timeout_ms: int) -> bool:
    """Run `git worktree add` to create a new worktree. Returns True on success."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "--no-checkout", str(ws_path),
            cwd=str(base_clone_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.warning(f"git worktree add failed rc={proc.returncode}: {err[:200]}")
            # Worktree might already exist — check if directory is valid
            if ws_path.exists() and (ws_path / ".git").exists():
                logger.info(f"worktree already exists at {ws_path}")
                return True
            return False
        return True
    except asyncio.TimeoutError:
        logger.error(f"git worktree add timed out after {timeout_ms}ms")
        proc.kill()
        return False
    except Exception as e:
        logger.error(f"git worktree add error: {e}")
        return False


async def _run_git_clone(src: str, dest: Path, timeout_ms: int) -> bool:
    """Clone `src` into `dest`. Returns True on success."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--bare", src, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
        if proc.returncode != 0:
            logger.error(f"git clone failed rc={proc.returncode}: {stderr.decode()[:200]}")
            return False
        return True
    except asyncio.TimeoutError:
        logger.error(f"git clone timed out after {timeout_ms}ms")
        proc.kill()
        return False
    except Exception as e:
        logger.error(f"git clone error: {e}")
        return False


async def _ensure_base_clone(ws_config: WorkspaceConfig, timeout_ms: int) -> Path | None:
    """Ensure the shared base clone exists. Returns the path or None on failure."""
    if not ws_config.use_worktree or not ws_config.base_clone:
        return None

    base_path = ws_config.resolved_base_clone()
    if base_path is None:
        return None

    if base_path.exists() and (base_path / "objects").exists():
        logger.info(f"base clone already exists at {base_path}")
        return base_path

    base_path.parent.mkdir(parents=True, exist_ok=True)
    ok = await _run_git_clone(ws_config.base_clone, base_path, timeout_ms)
    if not ok:
        logger.error(f"failed to create base clone at {base_path}")
        return None

    logger.info(f"base clone created at {base_path}")
    return base_path


async def ensure_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
    ws_config: WorkspaceConfig | None = None,
) -> WorkspaceResult:
    """Create or reuse a workspace for an issue.

    In worktree mode (ws_config.use_worktree=True), creates a git worktree
    linked to a shared base bare clone instead of cloning the full repository.
    In legacy mode, falls back to the original after_create hook approach.
    """
    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    # Safety: workspace must be under root
    ws_abs = ws_path.resolve()
    root_abs = workspace_root.resolve()
    if not ws_abs.is_relative_to(root_abs):
        raise ValueError(f"Workspace path {ws_abs} escapes root {root_abs}")

    # ── Worktree mode ────────────────────────────────────────────────────────
    if ws_config is not None and ws_config.use_worktree:
        base_path = await _ensure_base_clone(ws_config, hooks.timeout_ms)
        if base_path is None:
            raise RuntimeError(
                f"worktree mode requested but base clone unavailable "
                f"for {issue_identifier}"
            )

        created_now = not ws_path.exists()

        if ws_path.exists():
            # Worktree already exists (e.g. rework run) — reuse it
            logger.info(f"reusing existing worktree at {ws_path}")
        else:
            ws_path.parent.mkdir(parents=True, exist_ok=True)
            ok = await _run_git_worktree_add(ws_path, base_path, hooks.timeout_ms)
            if not ok:
                shutil.rmtree(ws_path, ignore_errors=True)
                raise RuntimeError(
                    f"git worktree add failed for {issue_identifier}"
                )
            created_now = True

        if created_now and hooks.after_create:
            ok = await run_hook(hooks.after_create, ws_path, hooks.timeout_ms, "after_create")
            if not ok:
                logger.warning(f"after_create hook failed for {issue_identifier} (non-fatal)")

        return WorkspaceResult(path=ws_path, workspace_key=key, created_now=created_now)

    # ── Legacy clone mode ────────────────────────────────────────────────────
    created_now = not ws_path.exists()
    ws_path.mkdir(parents=True, exist_ok=True)

    if created_now and hooks.after_create:
        ok = await run_hook(hooks.after_create, ws_path, hooks.timeout_ms, "after_create")
        if not ok:
            shutil.rmtree(ws_path, ignore_errors=True)
            raise RuntimeError(f"after_create hook failed for {issue_identifier}")

    return WorkspaceResult(path=ws_path, workspace_key=key, created_now=created_now)


async def remove_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
    ws_config: WorkspaceConfig | None = None,
) -> None:
    """Remove a workspace directory for a terminal issue.

    In worktree mode, removes the worktree entry from the base clone and
    deletes the worktree directory, but leaves the base clone intact.
    """
    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    if not ws_path.exists():
        return

    if hooks.before_remove:
        await run_hook(hooks.before_remove, ws_path, hooks.timeout_ms, "before_remove")

    logger.info(f"Removing workspace issue={issue_identifier} path={ws_path}")

    if ws_config is not None and ws_config.use_worktree:
        base_path = ws_config.resolved_base_clone()
        if base_path and base_path.exists():
            # Prune the worktree entry from the base bare clone
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "prune", "--verbose",
                cwd=str(base_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=hooks.timeout_ms / 1000)
            except asyncio.TimeoutError:
                proc.kill()
            # Also try to remove the specific worktree entry
            proc2 = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", "--force", str(ws_path),
                cwd=str(base_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc2.communicate(), timeout=hooks.timeout_ms / 1000)
            except asyncio.TimeoutError:
                proc2.kill()

    shutil.rmtree(ws_path, ignore_errors=True)