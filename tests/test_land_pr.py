"""scripts/land_pr.py: the file list it verifies the live site against."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("land_pr", REPO / "scripts" / "land_pr.py")
land_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(land_pr)


def _fake_run(stdout: str, calls: list):
    def run(*cmd, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


def test_every_file_of_a_big_pr_is_listed_not_the_first_hundred(monkeypatch):
    names = [f"packages/x/f{i}.py" for i in range(250)] + ["site/act-24/index.html", "site/data/a.csv"]
    calls: list = []
    monkeypatch.setattr(land_pr, "_run", _fake_run("\n".join(names) + "\n", calls))
    files = land_pr._pr_files(59)
    assert len(files) == 252 and "site/act-24/index.html" in files
    cmd = calls[0]
    assert cmd[:3] == ("gh", "api", "--paginate") and cmd[3] == f"repos/{land_pr.GH_REPO}/pulls/59/files"


def test_a_pr_with_no_site_files_lists_none_of_them(monkeypatch):
    monkeypatch.setattr(land_pr, "_run", _fake_run("a.py\nb.py\n", []))
    assert [f for f in land_pr._pr_files(1) if f.startswith("site/")] == []


def test_the_pr_view_no_longer_asks_for_the_capped_file_list(monkeypatch):
    calls: list = []
    monkeypatch.setattr(land_pr, "_run", _fake_run('{"state": "OPEN", "headRefName": "b", "mergeCommit": null}', calls))
    land_pr._pr(5)
    assert "files" not in calls[0][-1].split(",")


def test_a_failed_live_fetch_is_retried_not_raised(monkeypatch):
    """A 404 or timeout from the CDN before the deploy reaches it used to raise out of the
    retry loop, before the clean-up ran."""
    import urllib.error

    def fail(*a, **k):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(land_pr.urllib.request, "urlopen", fail)
    monkeypatch.setattr(land_pr.time, "sleep", lambda s: None)
    assert land_pr._live("site/index.html", "abcdef1") is None
    assert land_pr._verify([], "abcdef1") is False
