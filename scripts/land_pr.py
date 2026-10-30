"""Merge a PR, wait for the site to deploy, check the live pages, clean up.

    python scripts/land_pr.py <number>              # merge (if open), verify, clean up
    python scripts/land_pr.py <number> --no-merge   # already merged: verify and clean up

1. Merge. Waits for the PR's checks first (``gh pr checks --watch``) and stops if
   one fails. Uses a merge commit, as the repo does; ``gh pr merge`` dies
   intermittently with a GraphQL error, so it falls back to the REST endpoint.
2. Deploy. The Pages workflow runs only when ``site/**`` changed. If it did, this
   waits for that run on the merge commit.
3. Verify. Fetches every changed ``site/`` file from the live site and compares
   it byte for byte with the merge commit, then checks that the live simulator
   population's ``web_format_version`` matches the live ``kernel.js``.
4. Clean up. Removes the PR branch's worktree (only if it has no uncommitted
   changes) and deletes the local branch. The remote branch is left to GitHub's
   "Automatically delete head branches" setting.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GH_REPO = "Hawaii-Appleseed/Census-Forecaster"
LIVE = "https://hawaii-appleseed.github.io/Census-Forecaster/"


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, check=check, capture_output=True, text=True)


def _pr(number: int) -> dict:
    return json.loads(_run("gh", "pr", "view", str(number), "--repo", GH_REPO, "--json",
                           "state,headRefName,mergeCommit").stdout)


def _pr_files(number: int) -> list[str]:
    """Every file the PR changes. ``gh pr view --json files`` stops at 100, so on a big PR the
    ``site/`` files can be missing from it and the deploy check is silently skipped; the REST
    endpoint is paged, and ``--paginate`` reads every page."""
    out = _run("gh", "api", "--paginate", f"repos/{GH_REPO}/pulls/{number}/files",
               "--jq", ".[].filename").stdout
    return out.splitlines()


def _merge(number: int) -> None:
    print("waiting for checks...", flush=True)
    if subprocess.run(["gh", "pr", "checks", str(number), "--repo", GH_REPO, "--watch",
                       "--fail-fast"], cwd=REPO).returncode:
        sys.exit("a check failed; not merging")
    if _run("gh", "pr", "merge", str(number), "--repo", GH_REPO, "--merge",
            check=False).returncode:
        _run("gh", "api", "-X", "PUT", f"repos/{GH_REPO}/pulls/{number}/merge",
             "-f", "merge_method=merge")
    print(f"merged #{number}", flush=True)


def _wait_for_pages(sha: str) -> None:
    for _ in range(30):   # the run can take a few seconds to appear
        runs = json.loads(_run("gh", "run", "list", "--repo", GH_REPO, "-w", "pages",
                               "--commit", sha, "--json", "databaseId").stdout)
        if runs:
            print("waiting for the Pages deploy...", flush=True)
            if subprocess.run(["gh", "run", "watch", str(runs[0]["databaseId"]), "--repo",
                               GH_REPO, "--exit-status"], cwd=REPO,
                              stdout=subprocess.DEVNULL).returncode:
                sys.exit("the Pages deploy failed")
            return
        time.sleep(5)
    sys.exit(f"no Pages run appeared for {sha[:7]}")


def _live(path: str, sha: str) -> bytes:
    url = LIVE + path.removeprefix("site/").removesuffix("index.html") + f"?v={sha[:7]}"
    with urllib.request.urlopen(url, timeout=30) as r:   # ?v= skips the CDN's cached copy
        return r.read()


def _verify(paths: list[str], sha: str) -> bool:
    ok = True
    for path in paths:
        shown = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=REPO, capture_output=True)
        if shown.returncode:   # the PR deleted it
            continue
        want = shown.stdout
        for _ in range(6):   # the CDN can lag the deploy by a minute
            if _live(path, sha) == want:
                break
            time.sleep(10)
        else:
            print(f"  STALE {path}")
            ok = False
    version = json.loads(_live("site/data/tax-simulator/population.json", sha))["web_format_version"]
    kernel = re.search(rb"WEB_FORMAT_VERSION = (\d+)", _live("site/assets/simulator/kernel.js", sha))
    if not kernel or int(kernel.group(1)) != version:
        print(f"  simulator population is format {version}, kernel.js expects "
              f"{kernel and int(kernel.group(1))}")
        ok = False
    print(f"live site {'matches' if ok else 'does NOT match'} {sha[:7]} "
          f"({len(paths)} changed site files)", flush=True)
    return ok


def _clean_up(branch: str) -> None:
    porcelain = _run("git", "worktree", "list", "--porcelain").stdout.split("\n\n")
    for block in porcelain:
        if f"branch refs/heads/{branch}" in block:
            tree = block.splitlines()[0].removeprefix("worktree ")
            if _run("git", "worktree", "remove", tree, check=False).returncode:
                print(f"kept {tree}: it has uncommitted changes")
                return
            print(f"removed worktree {tree}")
    if _run("git", "branch", "-D", branch, check=False).returncode == 0:
        print(f"deleted local branch {branch}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("number", type=int)
    ap.add_argument("--no-merge", action="store_true")
    args = ap.parse_args()

    pr = _pr(args.number)
    if pr["state"] == "OPEN" and not args.no_merge:
        _merge(args.number)
        pr = _pr(args.number)
    if pr["state"] != "MERGED":
        sys.exit(f"#{args.number} is {pr['state']}")
    sha = pr["mergeCommit"]["oid"]
    _run("git", "fetch", "-q", "origin")

    site_files = [path for path in _pr_files(args.number) if path.startswith("site/")]
    ok = True
    if site_files:
        _wait_for_pages(sha)
        ok = _verify(site_files, sha)
    else:
        print("no site/ changes, so nothing deploys")
    _clean_up(pr["headRefName"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
