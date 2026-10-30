"""Tests for the poverty-path SDR glue (compute_poverty_sdr)."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.errors import DataValidationError
from tax_modeler.poverty.impact import _aggregate
from tax_modeler.poverty.uncertainty import compute_poverty_sdr


def _frame() -> pd.DataFrame:
    """Three SPM units, one scenario, two replicate weights.

    Baseline poor: A (100<120) and C (110<120); B not poor (200).
    Under no_eitc, B drops to 110<120 and flips poor → contributes its
    1 person to persons_lifted.
    """
    return pd.DataFrame({
        "county": ["Honolulu", "Honolulu", "Maui"],
        "weight": [10.0, 10.0, 10.0],
        "WGTP1": [12.0, 8.0, 10.0],
        "WGTP2": [8.0, 12.0, 10.0],
        "n_persons": [2.0, 1.0, 3.0],
        "spm_threshold": [120.0, 120.0, 120.0],
        "spm_resources": [100.0, 200.0, 110.0],
        "spm_resources_no_eitc": [90.0, 110.0, 90.0],
    })


def test_points_reproduce_aggregate():
    """SDR point estimates (weight-matrix column 0) must equal impact._aggregate."""
    df = _frame()
    npp = df["n_persons"].to_numpy(float)
    w = df["weight"].to_numpy(float)
    agg = _aggregate(
        df, group_col=None,
        threshold=df["spm_threshold"].to_numpy(float),
        person_weight=w * npp, filer_weight=w, scenarios=["no_eitc"],
    ).iloc[0]
    sdr = compute_poverty_sdr(
        df, replicate_weight_cols=["WGTP1", "WGTP2"],
        scenarios=["no_eitc"], group_col=None,
    ).iloc[0]
    for metric in (
        "poverty_rate_baseline",
        "persons_in_poverty_baseline",
        "poverty_gap_baseline_$",
        "persons_lifted_no_eitc",
        "gap_closed_no_eitc_$",
    ):
        assert sdr[metric] == pytest.approx(agg[metric]), metric


def test_known_answer_standard_errors():
    df = _frame()
    sdr = compute_poverty_sdr(
        df, replicate_weight_cols=["WGTP1", "WGTP2"],
        scenarios=["no_eitc"], group_col=None,
    ).iloc[0]
    # persons_in_poverty: totals 50/54/46 -> var=(4/2)*(16+16)=64 -> se=8.
    assert sdr["persons_in_poverty_baseline"] == pytest.approx(50.0)
    assert sdr["persons_in_poverty_baseline_se"] == pytest.approx(8.0)
    # persons_lifted: totals 10/8/12 -> var=(4/2)*(4+4)=16 -> se=4.
    assert sdr["persons_lifted_no_eitc"] == pytest.approx(10.0)
    assert sdr["persons_lifted_no_eitc_se"] == pytest.approx(4.0)
    # gap baseline: totals 300/340/260 -> var=(4/2)*(1600+1600)=6400 -> se=80.
    assert sdr["poverty_gap_baseline_$"] == pytest.approx(300.0)
    assert sdr["poverty_gap_baseline_$_se"] == pytest.approx(80.0)


def test_ci_brackets_point_and_columns_present():
    df = _frame()
    sdr = compute_poverty_sdr(
        df, replicate_weight_cols=["WGTP1", "WGTP2"],
        scenarios=["no_eitc"], group_col=None,
    ).iloc[0]
    for metric in ("poverty_rate_baseline", "persons_in_poverty_baseline",
                   "persons_lifted_no_eitc"):
        for suffix in ("_se", "_ci90_low", "_ci90_high"):
            assert f"{metric}{suffix}" in sdr.index
        assert sdr[f"{metric}_ci90_low"] <= sdr[metric] <= sdr[f"{metric}_ci90_high"]


def test_group_col_emits_one_row_per_group():
    df = _frame()
    sdr = compute_poverty_sdr(
        df, replicate_weight_cols=["WGTP1", "WGTP2"],
        scenarios=["no_eitc"], group_col="county",
    )
    assert set(sdr["county"]) == {"Honolulu", "Maui"}
    assert len(sdr) == 2


def test_empty_replicate_cols_raises():
    df = _frame()
    with pytest.raises(DataValidationError):
        compute_poverty_sdr(df, replicate_weight_cols=[], scenarios=["no_eitc"])


def test_missing_columns_raises():
    df = _frame().drop(columns=["spm_resources_no_eitc"])
    with pytest.raises(DataValidationError):
        compute_poverty_sdr(
            df, replicate_weight_cols=["WGTP1", "WGTP2"], scenarios=["no_eitc"],
        )
