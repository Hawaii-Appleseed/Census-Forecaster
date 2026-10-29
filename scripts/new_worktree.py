"""Make a worktree that runs the model exactly like the main checkout.

    python scripts/new_worktree.py <branch>                  # ../Census-Forecaster-<branch>
    python scripts/new_worktree.py <branch> --base origin/main --path ~/elsewhere

Run it from any checkout of the repo. It

1. fetches origin and adds ``<branch>`` as a new worktree off ``--base``;
2. copies the gitignored inputs over from the main checkout: ``runs/`` (the
   later chain steps and build_simulator_population's checks read earlier
   runs), ``data/artifacts/`` and ``packages/data/raw/``. On APFS these are
   copy-on-write clones (``cp -c``), so they take seconds and no extra disk;
   elsewhere it falls back to ``rsync``. Files the branch already tracks are
   never overwritten;
3. builds the worktree's own venv (``uv sync --all-packages --frozen
   --all-extras``). The main checkout's ``.venv`` holds editable installs of
   the MAIN checkout's packages, so a worktree run with it quietly imports
   main's code;
4. checks that the new venv imports the worktree's ``tax_modeler``.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

INPUTS = ("runs", "data/artifacts", "packages/data/raw")
SKIP = {"runs/.pycache", "runs/chain_logs"}   # caches and logs, rebuilt per tree


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _main_checkout(cwd: Path) -> Path:
    first = _git("worktree", "list", "--porcelain", cwd=cwd).splitlines()[0]
    return Path(first.removeprefix("worktree "))


def _copy_tree(main_tree: Path, rel: str, path: Path) -> None:
    src, dst = main_tree / rel, path / rel
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        if child.relative_to(main_tree).as_posix() in SKIP:
            continue
        # -c clones on APFS; -n never overwrites a tracked file the branch has
        if subprocess.run(["cp", "-cRn", str(child), str(dst)],
                          capture_output=True).returncode != 0:
            subprocess.run(["rsync", "-a", "--ignore-existing", str(child), f"{dst}/"],
                           check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("branch")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--path", type=Path)
    args = ap.parse_args()

    main_tree = _main_checkout(Path.cwd())
    path = (args.path or main_tree.parent / f"{main_tree.name}-{args.branch.replace('/', '-')}")
    path = path.expanduser().resolve()
    if path.exists():
        sys.exit(f"{path} already exists")

    _git("fetch", "-q", "origin", cwd=main_tree)
    _git("worktree", "add", "-q", "-b", args.branch, str(path), args.base, cwd=main_tree)
    print(f"worktree {path} on {args.branch} (from {args.base})", flush=True)

    for rel in INPUTS:
        if (main_tree / rel).is_dir():
            _copy_tree(main_tree, rel, path)
            print(f"copied {rel}/", flush=True)
        else:
            print(f"main checkout has no {rel}/; skipped", flush=True)

    if not shutil.which("uv"):
        sys.exit("uv is not on PATH; install it, then run "
                 "`uv sync --all-packages --frozen --all-extras` in the worktree")
    subprocess.run(["uv", "sync", "--all-packages", "--frozen", "--all-extras", "-q"],
                   cwd=path, check=True)

    where = subprocess.run([str(path / ".venv/bin/python"), "-c",
                            "import tax_modeler; print(tax_modeler.__file__)"],
                           cwd=path, check=True, capture_output=True, text=True).stdout.strip()
    if not Path(where).resolve().is_relative_to(path):
        sys.exit(f"the new venv imports tax_modeler from {where}, not the worktree")
    print(f"venv ready: tax_modeler from {Path(where).relative_to(path)}")
    print(f"\ncd {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
