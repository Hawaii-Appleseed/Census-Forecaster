"""Tests for the manifested run-output store (tax_modeler.runs)."""

from __future__ import annotations

import pandas as pd

from tax_modeler.runs import (
    list_runs,
    read_run_manifest,
    tidy_long,
    write_run_manifest,
)


def test_write_and_read_manifest(tmp_path):
    run = tmp_path / "sb3125_cd2"
    run.mkdir()
    (run / "enhanced.csv").write_text("scenario,tax_year,x\nMID,2027,1\n")

    path = write_run_manifest(
        run,
        script="forecast_sb3125_enhanced.py --cd 2",
        params={"cd": "2", "years": [2027, 2028]},
        inputs={"cache": "tax_units_cache.parquet"},
    )
    assert path.exists()

    m = read_run_manifest(run)
    assert m["script"] == "forecast_sb3125_enhanced.py --cd 2"
    assert m["params"]["cd"] == "2"
    assert m["params_fingerprint"]  # non-empty
    assert m["outputs"] == ["enhanced.csv"]  # manifest itself excluded
    assert m["created_at"] and m["git_sha"]


def test_manifest_records_a_given_git_sha(tmp_path):
    write_run_manifest(tmp_path, script="x.py", git_sha="abc1234-dirty")
    assert read_run_manifest(tmp_path)["git_sha"] == "abc1234-dirty"


def test_manifest_fingerprint_stable(tmp_path):
    a = write_run_manifest(tmp_path / "a", script="s", params={"x": 1, "y": 2})
    b = write_run_manifest(tmp_path / "b", script="s", params={"y": 2, "x": 1})
    fa = read_run_manifest(tmp_path / "a")["params_fingerprint"]
    fb = read_run_manifest(tmp_path / "b")["params_fingerprint"]
    assert a != b and fa == fb  # order-insensitive


def test_list_runs(tmp_path):
    write_run_manifest(tmp_path / "run1", script="s1")
    write_run_manifest(tmp_path / "run2", script="s2")
    (tmp_path / "not_a_run").mkdir()

    runs = list_runs(tmp_path)
    assert [r["run_dir"] for r in runs] == ["run1", "run2"]
    assert list_runs(tmp_path / "missing") == []


def test_tidy_long():
    df = pd.DataFrame({
        "scenario": ["MID", "MID"],
        "tax_year": [2027, 2028],
        "total_$M": [10.0, 12.0],
        "filers": [100, 110],
        "note": ["a", "b"],  # non-numeric — dropped
    })
    long = tidy_long(df, ["scenario", "tax_year"])
    assert set(long.columns) == {"scenario", "tax_year", "metric", "value"}
    assert len(long) == 4  # 2 rows x 2 numeric metrics
    assert set(long["metric"]) == {"total_$M", "filers"}
    mid27 = long[(long.tax_year == 2027) & (long.metric == "total_$M")]
    assert float(mid27["value"].iloc[0]) == 10.0
