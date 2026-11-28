"""scripts/prune_worktrees.py: which worktrees it removes, on a throwaway repo and origin."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("prune_worktrees", REPO / "scripts" / "prune_worktrees.py")
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


def _commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    _git(tmp_path, "clone", "-q", str(origin), str(main))
    _commit(main, "a")
    _git(main, "push", "-q", "origin", "HEAD:main")
    _git(main, "fetch", "-q", "origin")
    return main


def _worktree(main: Path, branch: str) -> Path:
    path = main.parent / branch.replace("/", "-")
    _git(main, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
    return path


def test_merged_clean_worktree_goes_with_its_branch(repo):
    tree = _worktree(repo, "done")
    _commit(tree, "b")
    _git(tree, "push", "-q", "origin", "done:main")
    assert prune.sweep(repo, go=True, here=repo) == [tree]
    assert not tree.exists()
    assert "done" not in _git(repo, "branch")


def test_dirty_fresh_and_unpushed_worktrees_stay(repo):
    dirty = _worktree(repo, "dirty")
    _commit(dirty, "c")
    _git(dirty, "push", "-q", "origin", "dirty:main")
    (dirty / "c").write_text("edited")
    fresh = _worktree(repo, "fresh")              # no commits yet: "in main" but just made
    local = _worktree(repo, "local")
    _commit(local, "d")
    assert prune.sweep(repo, go=True, pushed=True, here=repo) == []
    assert dirty.exists() and fresh.exists() and local.exists()


def test_pushed_unmerged_goes_only_with_the_flag_and_keeps_the_branch(repo):
    tree = _worktree(repo, "data/x")
    _commit(tree, "e")
    _git(tree, "push", "-q", "origin", "data/x")
    assert prune.sweep(repo, go=True, here=repo) == []
    assert prune.sweep(repo, go=True, pushed=True, here=repo) == [tree]
    assert "data/x" in _git(repo, "branch")


def test_dry_run_and_the_tree_you_stand_in_are_left_alone(repo):
    tree = _worktree(repo, "done")
    _commit(tree, "b")
    _git(tree, "push", "-q", "origin", "done:main")
    assert prune.sweep(repo, go=False, here=repo) == []
    assert prune.sweep(repo, go=True, here=tree) == []
    assert tree.exists()
