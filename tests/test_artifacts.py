"""Tests for agent evidence collection.

The governing requirement: nothing an agent writes as evidence may end up
committed to the project repo. The Cognito monorepo has 262 loose PNGs at its
root from exactly this leaking, so the ignore behaviour is tested directly
rather than assumed.
"""

from __future__ import annotations

import subprocess

import pytest

from stokowski import artifacts


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src.ts").write_text("export const a = 1\n")
    return tmp_path


def test_prepare_creates_the_directory(repo):
    path = artifacts.prepare(repo)
    assert path.is_dir()
    assert path == repo / ".stokowski" / "artifacts"


def test_artifacts_are_invisible_to_git(repo):
    artifacts.prepare(repo)
    (repo / ".stokowski" / "artifacts" / "shot.png").write_bytes(b"\x89PNG\r\n")

    untracked = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert ".stokowski" not in untracked
    assert "src.ts" in untracked  # the sweep did not over-ignore


def test_project_gitignore_is_left_alone(repo):
    """The repo's own .gitignore belongs to the project, not to Stokowski."""
    gitignore = repo / ".gitignore"
    gitignore.write_text("node_modules/\n")
    artifacts.prepare(repo)
    assert gitignore.read_text() == "node_modules/\n"
    assert ".stokowski" in (repo / ".git" / "info" / "exclude").read_text()


def test_prepare_is_idempotent(repo):
    for _ in range(3):
        artifacts.prepare(repo)
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.stokowski/") == 1


def test_prepare_survives_a_non_git_workspace(tmp_path):
    path = artifacts.prepare(tmp_path)
    assert path.is_dir()


def test_prepare_follows_a_worktree_gitdir_pointer(tmp_path):
    """Worktrees use a `.git` file pointing elsewhere."""
    real_git = tmp_path / "realgit"
    (real_git / "info").mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").write_text(f"gitdir: {real_git}\n")

    artifacts.prepare(ws)
    assert ".stokowski" in (real_git / "info" / "exclude").read_text()


def test_collect_returns_evidence_oldest_first(repo):
    import os, time
    d = artifacts.prepare(repo)
    for i, name in enumerate(["before.png", "after.png"]):
        f = d / name
        f.write_bytes(b"x" * 10)
        os.utime(f, (1000 + i, 1000 + i))

    names = [p.name for p in artifacts.collect(repo)]
    assert names == ["before.png", "after.png"]  # capture order, not alphabetical


def test_collect_ignores_junk(repo):
    d = artifacts.prepare(repo)
    (d / "good.png").write_bytes(b"x" * 10)
    (d / "bundle.js").write_bytes(b"x" * 10)     # not an evidence type
    (d / "empty.png").write_bytes(b"")            # zero bytes
    (d / ".DS_Store").write_bytes(b"x" * 10)      # dotfile

    assert [p.name for p in artifacts.collect(repo)] == ["good.png"]


def test_collect_skips_oversized_files(repo, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 100)
    d = artifacts.prepare(repo)
    (d / "huge.png").write_bytes(b"x" * 500)
    (d / "small.png").write_bytes(b"x" * 50)
    assert [p.name for p in artifacts.collect(repo)] == ["small.png"]


def test_collect_on_a_missing_directory_is_empty(tmp_path):
    assert artifacts.collect(tmp_path) == []


def test_clear_empties_but_keeps_the_directory(repo):
    d = artifacts.prepare(repo)
    (d / "a.png").write_bytes(b"x")
    (d / "nested").mkdir()
    (d / "nested" / "b.png").write_bytes(b"x")

    artifacts.clear(repo)
    assert d.is_dir()
    assert list(d.iterdir()) == []


def test_content_types(repo):
    assert artifacts.content_type_for(repo / "a.png") == "image/png"
    assert artifacts.content_type_for(repo / "a.unknownext") == "application/octet-stream"


@pytest.mark.parametrize("name,expected", [
    ("a.png", True), ("a.WEBP", True), ("a.mp4", False), ("a.json", False),
])
def test_image_detection(repo, name, expected):
    assert artifacts.is_image(repo / name) is expected
