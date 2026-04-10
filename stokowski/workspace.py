"""Workspace management - create, reuse, and clean per-issue workspaces.

Supports two modes:
  - clone: git clone per issue into workspace root (original behavior)
  - worktree: git worktree per issue from a shared repo
"""

from __future__ import annotations

import asyncio

# Lock to serialize git worktree operations (git can't handle concurrent worktree add)
_worktree_lock = asyncio.Lock()
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import HooksConfig, WorkspaceConfig

logger = logging.getLogger("stokowski.workspace")


def sanitize_key(identifier: str) -> str:
    """Replace non-safe chars with underscore for directory name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", identifier)


def _extract_issue_number(identifier: str) -> str:
    """Extract the numeric part from an identifier like DEV-123."""
    match = re.search(r"\d+", identifier)
    return match.group(0) if match else sanitize_key(identifier)


@dataclass
class WorkspaceResult:
    path: Path
    workspace_key: str
    created_now: bool
    branch_name: str | None = None


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


async def ensure_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
    workspace_cfg: WorkspaceConfig | None = None,
    branch_name: str | None = None,
) -> WorkspaceResult:
    """Create or reuse a workspace for an issue.

    In worktree mode, creates a git worktree from the repo at workspace_cfg.repo_path.
    In clone mode (default), creates a directory and runs after_create hook.
    """
    if workspace_cfg and workspace_cfg.mode == "worktree":
        return await _ensure_worktree(
            workspace_cfg, issue_identifier, hooks, branch_name
        )
    return await _ensure_clone_workspace(workspace_root, issue_identifier, hooks)


async def _ensure_clone_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
) -> WorkspaceResult:
    """Original clone-based workspace creation."""
    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    # Safety: workspace must be under root
    ws_abs = ws_path.resolve()
    root_abs = workspace_root.resolve()
    if not ws_abs.is_relative_to(root_abs):
        raise ValueError(f"Workspace path {ws_abs} escapes root {root_abs}")

    created_now = not ws_path.exists()
    ws_path.mkdir(parents=True, exist_ok=True)

    if created_now and hooks.after_create:
        ok = await run_hook(hooks.after_create, ws_path, hooks.timeout_ms, "after_create")
        if not ok:
            # Clean up failed workspace
            shutil.rmtree(ws_path, ignore_errors=True)
            raise RuntimeError(f"after_create hook failed for {issue_identifier}")

    return WorkspaceResult(path=ws_path, workspace_key=key, created_now=created_now)


async def _ensure_worktree(
    workspace_cfg: WorkspaceConfig,
    issue_identifier: str,
    hooks: HooksConfig,
    branch_name: str | None = None,
) -> WorkspaceResult:
    """Create a git worktree for an issue. Serialized to avoid git lock conflicts."""
    async with _worktree_lock:
        return await _ensure_worktree_inner(workspace_cfg, issue_identifier, hooks, branch_name)


async def _ensure_worktree_inner(
    workspace_cfg: WorkspaceConfig,
    issue_identifier: str,
    hooks: HooksConfig,
    branch_name: str | None = None,
) -> WorkspaceResult:
    """Inner worktree creation (called under lock)."""
    repo_path = workspace_cfg.resolved_repo_path()
    if not repo_path or not repo_path.exists():
        raise RuntimeError(
            f"Worktree mode requires a valid repo_path, got: {workspace_cfg.repo_path}"
        )

    issue_num = _extract_issue_number(issue_identifier)
    worktree_dir = repo_path / ".worktrees" / issue_num

    if worktree_dir.exists():
        return WorkspaceResult(
            path=worktree_dir,
            workspace_key=issue_num,
            created_now=False,
            branch_name=branch_name,
        )

    if not branch_name:
        branch_name = f"stokowski/{sanitize_key(issue_identifier)}"

    # Fetch latest main first
    fetch_proc = await asyncio.create_subprocess_exec(
        "git", "fetch", "origin", "main",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await fetch_proc.communicate()

    # Create worktree
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch_name,
        str(worktree_dir), "origin/main",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        import re
        error_msg = stderr.decode()[:500]
        if "already used by worktree" in error_msg:
            # Branch is checked out at another worktree — reuse it
            match = re.search(r"worktree at '([^']+)'", error_msg)
            if match:
                existing_path = Path(match.group(1))
                if existing_path.exists():
                    logger.info(
                        f"Reusing existing worktree for {issue_identifier} "
                        f"at {existing_path}"
                    )
                    return WorkspaceResult(
                        path=existing_path,
                        branch=branch_name,
                        created=False,
                    )
            raise RuntimeError(
                f"Failed to create worktree for {issue_identifier}: {error_msg}"
            )
        elif "already exists" in error_msg:
            # Branch exists but no worktree — try without -b
            proc2 = await asyncio.create_subprocess_exec(
                "git", "worktree", "add",
                str(worktree_dir), branch_name,
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await proc2.communicate()
            if proc2.returncode != 0:
                error_msg2 = stderr2.decode()[:500]
                # Second attempt might also hit "already used by worktree"
                if "already used by worktree" in error_msg2:
                    match2 = re.search(r"worktree at '([^']+)'", error_msg2)
                    if match2:
                        existing_path = Path(match2.group(1))
                        if existing_path.exists():
                            logger.info(
                                f"Reusing existing worktree for {issue_identifier} "
                                f"at {existing_path}"
                            )
                            return WorkspaceResult(
                                path=existing_path,
                                branch=branch_name,
                                created=False,
                            )
                raise RuntimeError(
                    f"Failed to create worktree for {issue_identifier}: "
                    f"{error_msg2}"
                )
        else:
            raise RuntimeError(
                f"Failed to create worktree for {issue_identifier}: {error_msg}"
            )

    logger.info(
        f"Created worktree issue={issue_identifier} "
        f"branch={branch_name} path={worktree_dir}"
    )

    # Run after_create hook in the worktree (e.g., pnpm install)
    if hooks.after_create:
        ok = await run_hook(hooks.after_create, worktree_dir, hooks.timeout_ms, "after_create")
        if not ok:
            # Clean up failed worktree
            await _remove_worktree(repo_path, worktree_dir, branch_name)
            raise RuntimeError(f"after_create hook failed for {issue_identifier}")

    return WorkspaceResult(
        path=worktree_dir,
        workspace_key=issue_num,
        created_now=True,
        branch_name=branch_name,
    )


async def remove_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
    workspace_cfg: WorkspaceConfig | None = None,
) -> None:
    """Remove a workspace directory for a terminal issue."""
    if workspace_cfg and workspace_cfg.mode == "worktree":
        await _remove_worktree_workspace(workspace_cfg, issue_identifier, hooks)
        return

    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    if not ws_path.exists():
        return

    if hooks.before_remove:
        await run_hook(hooks.before_remove, ws_path, hooks.timeout_ms, "before_remove")

    logger.info(f"Removing workspace issue={issue_identifier} path={ws_path}")
    shutil.rmtree(ws_path, ignore_errors=True)


async def _remove_worktree_workspace(
    workspace_cfg: WorkspaceConfig,
    issue_identifier: str,
    hooks: HooksConfig,
) -> None:
    """Remove a git worktree workspace."""
    repo_path = workspace_cfg.resolved_repo_path()
    if not repo_path:
        return

    issue_num = _extract_issue_number(issue_identifier)
    worktree_dir = repo_path / ".worktrees" / issue_num

    if not worktree_dir.exists():
        return

    if hooks.before_remove:
        await run_hook(hooks.before_remove, worktree_dir, hooks.timeout_ms, "before_remove")

    # Get the branch name before removing
    branch_proc = await asyncio.create_subprocess_exec(
        "git", "branch", "--show-current",
        cwd=str(worktree_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await branch_proc.communicate()
    branch_name = stdout.decode().strip() if branch_proc.returncode == 0 else None

    await _remove_worktree(repo_path, worktree_dir, branch_name)


async def _remove_worktree(
    repo_path: Path, worktree_dir: Path, branch_name: str | None
) -> None:
    """Remove a git worktree and optionally its branch."""
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", str(worktree_dir), "--force",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Also force-remove the directory if worktree remove didn't clean it
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)

    # Delete the branch
    if branch_name:
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-D", branch_name,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    logger.info(
        f"Removed worktree path={worktree_dir} branch={branch_name}"
    )
