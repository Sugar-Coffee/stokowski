"""Evidence artifacts produced by agents.

Agents are told to write screenshots and other evidence into a known directory
inside the workspace. Stokowski sweeps that directory after each turn, uploads
what it finds to Linear, and deletes the local copies.

The directory has to live *inside* the git clone rather than beside it: the
tools that produce this evidence (Playwright MCP, the iOS simulator MCP) refuse
to write outside the working directory they were launched in. Putting it inside
means it must be ignored, and that ignore has to be invisible to the project —
so it goes in `.git/info/exclude`, which is local to the clone, rather than the
repo's own `.gitignore`.
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
from pathlib import Path

logger = logging.getLogger("stokowski.artifacts")

# Relative to the workspace root.
ARTIFACT_SUBDIR = Path(".stokowski") / "artifacts"

# Linear rejects very large uploads and a huge file is rarely the evidence you
# wanted anyway; skip rather than fail the whole sweep.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

# Anything else the agent leaves lying around is ignored, so a stray node_modules
# or build output cannot be mistaken for evidence.
ALLOWED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif",
    ".pdf", ".mp4", ".mov", ".webm",
    ".txt", ".md", ".json", ".log", ".csv", ".html", ".diff", ".patch",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}


def artifact_dir(workspace_path: Path) -> Path:
    return workspace_path / ARTIFACT_SUBDIR


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def content_type_for(path: Path) -> str:
    guess, _ = mimetypes.guess_type(path.name)
    return guess or "application/octet-stream"


def _git_exclude_file(workspace_path: Path) -> Path | None:
    """Locate `.git/info/exclude`, following the gitdir pointer if present."""
    git_path = workspace_path / ".git"

    if git_path.is_dir():
        return git_path / "info" / "exclude"

    if git_path.is_file():
        # Worktrees and submodules use a `gitdir: <path>` pointer file.
        try:
            content = git_path.read_text().strip()
        except OSError:
            return None
        if content.startswith("gitdir:"):
            target = Path(content.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = (workspace_path / target).resolve()
            return target / "info" / "exclude"

    return None


def _ensure_git_ignored(workspace_path: Path) -> None:
    """Add the artifact dir to the clone's local excludes.

    Deliberately not the project's `.gitignore` — that is the project's file and
    Stokowski has no business editing it. `.git/info/exclude` achieves the same
    thing for this clone only and never shows up in a diff.
    """
    exclude_file = _git_exclude_file(workspace_path)
    if exclude_file is None:
        return  # Not a git workspace; nothing to protect against.

    entry = f"/{ARTIFACT_SUBDIR.parts[0]}/"
    try:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_file.read_text() if exclude_file.exists() else ""
        if entry in existing.split():
            return
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        with exclude_file.open("a") as fh:
            fh.write(f"{prefix}# Stokowski agent evidence — never committed\n{entry}\n")
        logger.debug(f"Added {entry} to {exclude_file}")
    except OSError as e:
        logger.warning(f"Could not update git excludes at {exclude_file}: {e}")


def prepare(workspace_path: Path) -> Path:
    """Create the artifact directory and make sure git will not see it."""
    target = artifact_dir(workspace_path)
    target.mkdir(parents=True, exist_ok=True)
    _ensure_git_ignored(workspace_path)
    return target


def collect(workspace_path: Path) -> list[Path]:
    """Return artifact files the agent produced, oldest first.

    Ordering is by modification time so a before/after pair reads in the order
    it was captured rather than alphabetically.
    """
    target = artifact_dir(workspace_path)
    if not target.is_dir():
        return []

    found: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            logger.debug(f"Skipping unsupported artifact type: {path.name}")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        if size > MAX_ARTIFACT_BYTES:
            logger.warning(
                f"Skipping oversized artifact {path.name} "
                f"({size / 1_048_576:.1f}MB > {MAX_ARTIFACT_BYTES / 1_048_576:.0f}MB)"
            )
            continue
        found.append(path)

    found.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return found


def clear(workspace_path: Path) -> None:
    """Empty the artifact directory once its contents have been uploaded."""
    target = artifact_dir(workspace_path)
    if not target.is_dir():
        return
    for child in target.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError as e:
            logger.warning(f"Could not remove artifact {child}: {e}")
