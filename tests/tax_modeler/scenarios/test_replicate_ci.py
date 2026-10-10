"""Calibrated replicate weights (rake once, score replicates) and the SDR
bands on the Act 24 deltas (``tax_modeler.uncertainty.replicates`` /
``.revenue``)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.scenarios.behavioral_response import BehavioralParams, score_with_response
from tax_modeler.uncertainty.replicates import (
    calibration_ratio,
    replicate_weight_matrix,
    sdr_totals,
    snapshot_uncalibrated_weight,
)
from tax_modeler.uncertainty.revenue import AGI_CLASSES, act24_delta_sdr, sdr_row_columns


def _frame() -> pd.DataFrame:
    # Three PUMS rows (ratios 2, 0.5, 1) and one synthetic row with no basis.
    return pd.DataFrame({
        "weight":       [20.0, 10.0, 5.0, 300.0],
        "weight_uncal": [10.0, 20.0, 5.0, 0.0],
        "weight_r01":   [11.0, 19.0, 6.0, 0.0],
        "weight_r02":   [9.0, 21.0, 4.0, 0.0],
        "is_synthetic_ultra_high": [False, False, False, True],
    })


def test_calibration_ratio_is_applied_to_each_replicate():
    W = replicate_weight_matrix(_frame())
    assert W.shape == (4, 3)
    np.testing.assert_allclose(W[:, 0], [20.0, 10.0, 5.0, 300.0])
    # weight_r x weight / weight_uncal on the PUMS rows ...
    np.testing.assert_allclose(W[:3, 1], [22.0, 9.5, 6.0])
    np.testing.assert_allclose(W[:3, 2], [18.0, 10.5, 4.0])
    # ... and the synthetic row carries its weight in every replicate.
    np.testing.assert_allclose(W[3, 1:], [300.0, 300.0])


def test_rows_without_a_basis_or_a_finite_replicate_carry_the_weight():
    df = _frame().drop(columns="is_synthetic_ultra_high")
    df.loc[0, "weight_r02"] = np.nan
    W = replicate_weight_matrix(df)
    assert W[0, 2] == 20.0          # NaN replicate cell
    assert W[3, 1] == 300.0         # weight_uncal == 0: no basis
    np.testing.assert_allclose(calibration_ratio(df), [2.0, 0.5, 1.0, 1.0])


def test_matrix_requires_replicate_columns():
    with pytest.raises(ValueError, match="weight_r01"):
        replicate_weight_matrix(pd.DataFrame({"weight": [1.0], "weight_uncal": [1.0]}))


def test_snapshot_keeps_the_first_basis():
    df = pd.DataFrame({"weight": [4.0, 6.0]})
    once = snapshot_uncalibrated_weight(df)
    once["weight"] = once["weight"] * 3
    twice = snapshot_uncalibrated_weight(once)
    np.testing.assert_allclose(twice["weight_uncal"], [4.0, 6.0])


def test_sdr_se_reproduces_the_fay_formula_by_hand():
    # Two replicates: V = (4 / R) Σ (θ_r − θ_0)² with R = 2.
    W = replicate_weight_matrix(_frame())
    v = np.array([1.0, 2.0, 3.0, 4.0])
    theta = v @ W                       # [θ_0, θ_1, θ_2]
    est = sdr_totals({"t": v}, W)["t"]
    expected_var = (4.0 / 2.0) * ((theta[1] - theta[0]) ** 2 + (theta[2] - theta[0]) ** 2)
    assert est.point == pytest.approx(theta[0])
    assert est.se == pytest.approx(math.sqrt(expected_var))
    assert est.ci_high - est.point == pytest.approx(1.645 * est.se)
    # Hand values: θ_0 = 20+20+15+1200 = 1255; θ_1 = 22+19+18+1200 = 1259;
    # θ_2 = 18+21+12+1200 = 1251 -> V = 2 x (16 + 16) = 64, SE = 8.
    assert est.point == 1255.0
    assert est.se == pytest.approx(8.0)
    cols = sdr_row_columns({"t_$M": est})
    assert set(cols) == {"t_se_$M", "t_ci90_low_$M", "t_ci90_high_$M"}


def test_sdr_subset_mask_restricts_the_sum_but_not_the_factor():
    W = replicate_weight_matrix(_frame())
    v = np.array([1.0, 2.0, 3.0, 4.0])
    est = sdr_totals({"t": v}, W, mask=np.array([True, True, True, False]))["t"]
    assert est.point == 55.0
    assert est.se == pytest.approx(8.0)


@pytest.fixture
def scored_units(tail_units):
    """The scenario fixture with a basis and four replicates: PUMS rows get
    Poisson noise around the basis, the tail none."""
    rng = np.random.default_rng(3)
    df = tail_units.copy()
    pums = ~df["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    df["weight_uncal"] = np.where(pums, df["weight"] / 1.1, 0.0)   # rake of x1.1
    for r in range(1, 5):
        df[f"weight_r{r:02d}"] = np.where(
            pums, rng.poisson(df["weight_uncal"].clip(lower=1.0)), 0.0).astype(float)
    return df


def test_act24_delta_sdr_points_are_the_scored_figures(scored_units):
    calc = TaxCalculator()
    base_cfg = TaxSystemRegistry.get_act46_system(2027)
    scen_cfg = TaxSystemRegistry.get_sb3125_cd2_system(2027)
    revenue, responded, diag = score_with_response(
        scored_units, BehavioralParams.mid(), target_year=2027,
        baseline_cfg=base_cfg, scenario_cfg=scen_cfg, calculator=calc,
    )
    totals, classes = act24_delta_sdr(
        scored_units, responded, baseline_cfg=base_cfg, scenario_cfg=scen_cfg,
        calculator=calc, pte_loss_m=diag["pte_revenue_loss_$M"],
    )
    assert totals["act46_baseline_$M"].point == pytest.approx(revenue["baseline_$M"], abs=1e-9)
    assert totals["bracket_delta_static_$M"].point == pytest.approx(revenue["static_$M"], abs=1e-9)
    assert totals["bracket_delta_post_$M"].point == pytest.approx(revenue["behavioral_$M"], abs=1e-9)
    assert totals["eti_response_$M"].point == pytest.approx(
        revenue["behavioral_$M"] - revenue["static_$M"], abs=1e-9)
    assert all(t.se >= 0 for t in totals.values())
    # Classes partition the frame: their static deltas add to the total.
    assert classes["bracket_delta_static_$M"].sum() == pytest.approx(revenue["static_$M"], abs=1e-9)
    assert set(classes["agi_class"]) <= {c[0] for c in AGI_CLASSES}


def test_no_replicate_variation_means_zero_se(scored_units):
    """Replicates equal to the basis on every PUMS row: the band collapses,
    whatever the rake and the migration response did to ``weight``."""
    df = scored_units.copy()
    pums = ~df["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    for r in range(1, 5):
        df[f"weight_r{r:02d}"] = np.where(pums, df["weight_uncal"], 0.0)
    calc = TaxCalculator()
    base_cfg = TaxSystemRegistry.get_act46_system(2027)
    scen_cfg = TaxSystemRegistry.get_sb3125_cd2_system(2027)
    _, responded, _ = score_with_response(
        df, BehavioralParams.mid(), target_year=2027,
        baseline_cfg=base_cfg, scenario_cfg=scen_cfg, calculator=calc,
    )
    assert (responded["weight"] < df["weight"]).any()      # migration moved weights
    totals, _ = act24_delta_sdr(df, responded, baseline_cfg=base_cfg,
                                scenario_cfg=scen_cfg, calculator=calc)
    assert all(t.se == 0.0 for t in totals.values())
