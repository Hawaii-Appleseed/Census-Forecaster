"""``pipeline.calibrate_anchored``: the gains anchor at the base year, then the rake
on the anchored incomes (October 13, 2026).

The scorer is replaced by a stand-in that is cheap and linear in income, so the test
exercises the anchor, the class re-assignment and the rake together rather than the
Hawaii tax code (covered elsewhere).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tax_modeler import pipeline
from tax_modeler.calibration import cg_anchor, dotax_base
from tax_modeler.calibration.report import CalibrationReport


def _units(n=6000, seed=1) -> pd.DataFrame:
    """Per DOTAX bracket, units whose tax per filer spans DOTAX's (the joint system is
    feasible), with ``income0`` kept so the stand-in scorer can scale tax with income."""
    rng = np.random.default_rng(seed)
    rows = []
    for (lo, hi), n_b in dotax_base.filer_targets().items():
        k = max(40, int(n * n_b / 631_054))
        agi = rng.uniform(lo, min(hi, 3_000_000.0), k)
        per_filer = dotax_base.tax_targets_M()[(lo, hi)] * 1e6 / n_b
        rows.append(pd.DataFrame({
            "income": agi, "income0": agi,
            "tax0": per_filer * rng.lognormal(0.0, 0.5, k),
            "filing_status": rng.choice(
                ["single", "married_filing_jointly", "head_of_household", "married_filing_separately"],
                k, p=[0.53, 0.34, 0.1, 0.03]),
        }))
    df = pd.concat(rows, ignore_index=True)
    df["agi"] = df["income"]
    df["weight"] = rng.uniform(60, 180, len(df))
    df["weight_uncal"] = df["weight"].to_numpy()
    return df


def _score(u, deduction_params=None, tax_year=2023):
    """Tax before credits proportional to income (the gains' alternative-tax cap off)."""
    out = u.copy()
    out["hi_agi"] = out["income"]
    out["hi_tax_before_credits"] = out["tax0"] * out["income"] / out["income0"]
    out["hi_tax_liability"] = out["hi_tax_before_credits"]
    if "hi_state_tax" not in out.columns:
        out["hi_state_tax"] = out["hi_tax_liability"]
    return out


@pytest.fixture(scope="module")
def anchored():
    pytest.importorskip("pums_estimator")
    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline, "compute_base_tax", _score)
    try:
        df = _score(_units())
        base, report = pipeline.calibrate_anchored(df, deduction_params={}, tax_year=2023)
    finally:
        mp.undo()
    return df, base, report


def test_report_records_the_base_anchor(anchored):
    _, base, report = anchored
    assert isinstance(report, CalibrationReport) and report.anchored_at_base
    assert set(report.base_anchor_factors) == {"100_150", "150_200", "200_300", "300_400", "400_1m"}
    assert 1 <= len(report.base_anchor_passes) <= 2
    last = report.base_anchor_passes[-1]
    assert last["factors"] == pytest.approx(report.base_anchor_factors)
    assert set(last["residual"]) == set(report.base_anchor_factors) and 0 <= last["moved"] < 0.1
    # survives the artifact's round trip
    again = CalibrationReport.from_dict(report.to_dict())
    assert again.anchored_at_base and again.base_anchor_factors == report.base_anchor_factors


def test_base_carries_each_units_base_year_class(anchored):
    """``cg_anchor_class``: the classes the projection sees at the base year, on the
    final anchored incomes and weights (the cohorts of the per-year anchor)."""
    _, base, _ = anchored
    assert "cg_anchor_class" in base.columns
    np.testing.assert_array_equal(base["cg_anchor_class"].to_numpy(),
                                  cg_anchor.base_class_labels(base))
    np.testing.assert_allclose(base["cg_base_gains"],
                               cg_anchor.model_gains(base["income"], base["synthetic_cg_share"]))


def test_base_anchoring_leaves_non_gains_income_unchanged(anchored):
    df, base, report = anchored
    raw = cg_anchor.model_gains(df["income"], 0.0)       # no shares on the stand-in frame ...
    assert (raw == 0).all()
    # ... the imputation gives the $100K-$1M units theirs; non-gains income is what is left
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    imputed = impute_capital_gains_from_soi(df)
    g0 = cg_anchor.model_gains(imputed["income"], imputed["synthetic_cg_share"])
    g1 = cg_anchor.model_gains(base["income"], base["synthetic_cg_share"])
    np.testing.assert_allclose(base["income"] - g1, imputed["income"] - g0, rtol=1e-9, atol=1e-4)
    assert (g1 != g0).any()


def test_post_rake_class_counts_match_dotax_and_the_gains_hit_the_targets(anchored):
    _, base, report = anchored
    summ = report.margin_summary()
    assert report.converged
    assert summ["dotax_filer_count"]["within_tolerance"] == summ["dotax_filer_count"]["counted"]
    assert summ["dotax_filing_status"]["within_tolerance"] == summ["dotax_filing_status"]["counted"]
    # the anchored gains on the classes the projection will see, within the pass tolerance's
    # reach (the $400K-$1M class is the slow one: its units cross $1M as they are anchored)
    resid = cg_anchor.base_gains_residual(base, cg_anchor.base_class_labels(base))
    for c in ("100_150", "150_200", "200_300", "300_400"):
        assert abs(resid[c]) < 0.05, (c, resid)
    assert abs(resid["400_1m"]) < 0.15, resid


def test_rake_runs_on_the_anchored_incomes(anchored):
    """The base is raked on anchored incomes: its tax before credits is the stand-in's
    on the anchored income, and the DOTAX tax margin is fitted on it."""
    _, base, report = anchored
    np.testing.assert_allclose(base["hi_tax_before_credits"],
                               base["tax0"] * base["income"] / base["income0"])
    assert report.margin_summary()["dotax_tax_M"]["within_tolerance"] == \
        report.margin_summary()["dotax_tax_M"]["counted"]
    assert (base["income"] >= base["income0"] * 0.0).all()
    assert (base["income"] != base["income0"]).any()      # anchored incomes, not the PUMS ones
