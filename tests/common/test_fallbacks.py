"""common.fallbacks: defaults substituted for failed data are recorded, shared with forked workers, and written into
run manifests."""
from __future__ import annotations

import json
import multiprocessing as mp
import os

import pytest
from common import fallbacks


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.delenv(fallbacks.ENV, raising=False)
    fallbacks.reset()
    yield
    fallbacks.reset()


def test_record_once_per_name_and_say_so(capsys, caplog):
    with caplog.at_level("WARNING", logger="common.fallbacks"):
        fallbacks.record("x", "first")
        fallbacks.record("x", "second")
        fallbacks.record("a", "other")
    assert fallbacks.recorded() == {"a": "other", "x": "first"}
    err = capsys.readouterr().err
    assert err.count("FALLBACK x: first") == 1 and "second" not in err
    assert "FALLBACK a: other" in caplog.text


def _child(_):
    fallbacks.record("from_worker", "worker detail")
    return os.getpid()


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="needs fork")
def test_records_from_forked_workers_reach_the_parent():
    from tax_modeler.parallel import fork_map

    os.environ.pop(fallbacks.ENV, None)
    fallbacks.record("from_parent", "parent detail")
    pids = fork_map(_child, range(4)) if os.cpu_count() and os.cpu_count() > 1 else None
    if not pids or len(set(pids)) == 1 and pids[0] == os.getpid():
        pytest.skip("fork_map ran serially here")
    assert fallbacks.recorded() == {"from_parent": "parent detail", "from_worker": "worker detail"}


def test_share_with_workers_starts_a_fresh_file(monkeypatch, tmp_path):
    old = tmp_path / "parent.jsonl"
    old.write_text(json.dumps({"name": "parents", "detail": "not mine"}) + "\n")
    monkeypatch.setenv(fallbacks.ENV, str(old))
    path = fallbacks.share_with_workers()
    assert path != str(old) and os.environ[fallbacks.ENV] == path
    assert fallbacks.recorded() == {}


def test_manifest_lists_fallbacks_only_when_there_are_any(tmp_path):
    from tax_modeler.runs import read_run_manifest, write_run_manifest

    write_run_manifest(tmp_path / "clean", script="s.py", params={"a": 1})
    assert "fallbacks" not in read_run_manifest(tmp_path / "clean")
    fallbacks.record("reec_nominal_growth:4pct", "growth unavailable; 4%/yr used")
    write_run_manifest(tmp_path / "used", script="s.py", params={"a": 1})
    assert read_run_manifest(tmp_path / "used")["fallbacks"] == {
        "reec_nominal_growth:4pct": "growth unavailable; 4%/yr used"}


def test_a_missing_cor_file_is_recorded(monkeypatch):
    import census_forecaster.cor as cor
    from tax_modeler.calibration import forward_targets

    def boom(**k):
        raise FileNotFoundError("cor_iit_projections.json")

    monkeypatch.setattr(cor, "load_cor_iit_projections", boom)
    with pytest.warns(RuntimeWarning):
        got = forward_targets._load_cor_projections()
    assert got                                              # the hardcoded vintage
    assert "cor_projections:forward_targets" in fallbacks.recorded()


def test_a_missing_growth_factor_is_recorded(monkeypatch):
    import tax_modeler.projection.income_forecast as inc
    from tax_modeler.config import income_growth

    monkeypatch.setattr(inc, "get_hawaii_real_growth_factor", lambda **k: None)
    out = income_growth.apply_income_growth(100.0, is_resident=True)
    assert out > 0
    assert "income_growth:resident_ensemble" in fallbacks.recorded()
