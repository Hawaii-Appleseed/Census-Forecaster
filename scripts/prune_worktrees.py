"""Remove worktrees whose work is already on GitHub.

    python scripts/prune_worktrees.py                  # list what would go; removes nothing
    python scripts/prune_worktrees.py --go             # remove merged, clean worktrees
    python scripts/prune_worktrees.py --go --pushed    # also unmerged ones whose branch is pushed

Each worktree holds a venv and copies of runs/ and data/, so a dozen stale ones
fill the disk. ``land_pr.py`` removes the worktree of the PR it lands; this
catches the rest (branches that never had a PR, or were landed from inside
their own worktree).

A worktree goes only when ``git status`` is clean (no modified or untracked
files) and its branch has had at least one commit, and then only if

- merged: the branch tip is in origin/main. Its local branch is deleted too.
- pushed (with ``--pushed``): origin/<branch> holds the branch tip. The local
  branch is kept, so ``git worktree add <path> <branch>`` brings it back.

Gitignored files in the worktree (``runs/``, ``.venv``, the copied data) go with
it, as with ``git worktree remove``. The main checkout, detached worktrees and
the worktree you are standing in are never removed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def main_checkout(cwd: Path) -> Path:
    first = _git("worktree", "list", "--porcelain", cwd=cwd).stdout.splitlines()[0]
    return Path(first.removeprefix("worktree "))


def worktrees(main_tree: Path) -> list[tuple[Path, str | None]]:
    """(path, branch) for every worktree but the main checkout; branch is None when detached."""
    out = _git("worktree", "list", "--porcelain", cwd=main_tree).stdout
    trees = []
    for block in out.strip().split("\n\n")[1:]:
        lines = block.splitlines()
        branch = next((line.removeprefix("branch refs/heads/") for line in lines
                       if line.startswith("branch refs/heads/")), None)
        trees.append((Path(lines[0].removeprefix("worktree ")), branch))
    return trees


def verdict(main_tree: Path, path: Path, branch: str | None, pushed: bool) -> tuple[str | None, str]:
    """("merged" or "pushed", why) when the worktree can go, else (None, why it stays)."""
    if branch is None:
        return None, "detached HEAD"
    if not path.is_dir():
        return None, "missing on disk (git worktree prune clears it)"
    if _git("status", "--porcelain", cwd=path).stdout.strip():
        return None, "uncommitted changes"
    # A branch just made from origin/main is "in main" too; its reflog has only the creation.
    reflog = _git("reflog", "show", "--format=%H", f"refs/heads/{branch}", cwd=main_tree,
                  check=False).stdout.split()
    if len(reflog) <= 1:
        return None, "no commits yet"
    if _git("merge-base", "--is-ancestor", branch, "origin/main", cwd=main_tree,
            check=False).returncode == 0:
        return "merged", "in origin/main"
    on_origin = _git("merge-base", "--is-ancestor", branch, f"origin/{branch}", cwd=main_tree,
                     check=False).returncode == 0
    if pushed and on_origin:
        return "pushed", f"origin/{branch} has it"
    return None, "pushed, not merged (--pushed removes it)" if on_origin else "not on origin"


def sweep(main_tree: Path, go: bool, pushed: bool = False, here: Path | None = None) -> list[Path]:
    """Remove (or with go=False, list) the worktrees verdict() lets go. Returns those removed."""
    here = (here or Path.cwd()).resolve()
    _git("fetch", "-q", "--prune", "origin", cwd=main_tree)
    removed = []
    for path, branch in worktrees(main_tree):
        kind, why = verdict(main_tree, path, branch, pushed)
        if kind and (here == path.resolve() or path.resolve() in here.parents):
            kind, why = None, "you are standing in it"
        if not kind:
            print(f"keep    {path.name}: {why}")
            continue
        if not go:
            print(f"would remove {path.name}: {why}")
            continue
        if _git("worktree", "remove", str(path), cwd=main_tree, check=False).returncode:
            print(f"keep    {path.name}: git refused to remove it")
            continue
        if kind == "merged":
            _git("branch", "-D", branch, cwd=main_tree, check=False)
        print(f"removed {path.name}: {why}")
        removed.append(path)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--go", action="store_true", help="remove; without it, only list")
    ap.add_argument("--pushed", action="store_true",
                    help="also remove clean worktrees whose branch is pushed but not merged")
    args = ap.parse_args()
    sweep(main_checkout(Path.cwd()), go=args.go, pushed=args.pushed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
