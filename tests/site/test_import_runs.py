"""scripts/build_site.py --import-runs refuses runs that do not match HEAD's model code.

runs/ is gitignored and every worktree starts with a copy of the main checkout's, so
the files existing says nothing about which code made them.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_site", REPO / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_site   # dataclasses resolve their module through sys.modules
spec.loader.exec_module(build_site)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "model.py").write_text("RATE = 1\n")
    (tmp_path / "README.md").write_text("doc\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "model")
    monkeypatch.setattr(build_site, "REPO", tmp_path)
    return tmp_path


def _commit(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text)
    _git(repo, "commit", "-qam", rel)


def _manifest(repo: Path, sha: str) -> Path:
    path = repo / "manifest.json"
    path.write_text(json.dumps({"git_sha": sha}))
    return path


def test_a_run_at_head_is_current(repo):
    assert build_site.stale_run(_manifest(repo, _git(repo, "rev-parse", "--short", "HEAD"))) is None


def test_a_docs_only_commit_since_the_run_keeps_it_current(repo):
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    _commit(repo, "README.md", "more doc\n")
    assert build_site.stale_run(_manifest(repo, sha)) is None


def test_a_model_change_since_the_run_makes_it_stale(repo):
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    _commit(repo, "packages/model.py", "RATE = 2\n")
    assert "changed since" in build_site.stale_run(_manifest(repo, sha))


def test_uncommitted_or_unknown_code_is_stale(repo):
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    assert "uncommitted" in build_site.stale_run(_manifest(repo, sha + "-dirty"))
    assert "does not have" in build_site.stale_run(_manifest(repo, "unknown"))
    assert "does not have" in build_site.stale_run(_manifest(repo, "0123abc"))


def test_import_refuses_a_stale_run_unless_allowed(repo, monkeypatch, tmp_path_factory):
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    _commit(repo, "packages/model.py", "RATE = 2\n")
    (repo / "runs" / "r").mkdir(parents=True)
    (repo / "runs" / "r" / "out.csv").write_text("a\n1\n")
    (repo / "runs" / "r" / "manifest.json").write_text(json.dumps({"git_sha": sha}))
    monkeypatch.setattr(build_site, "DATA", tmp_path_factory.mktemp("data"))
    est = build_site.Estimate(slug="x", files={"out.csv": "r/out.csv",
                                                "manifest.json": "r/manifest.json"},
                              stamp=("manifest.json",))
    with pytest.raises(SystemExit, match="stale"):
        build_site.import_runs(est)
    build_site.import_runs(est, allow_stale=True)
    assert (build_site.DATA / "x" / "out.csv").exists()
