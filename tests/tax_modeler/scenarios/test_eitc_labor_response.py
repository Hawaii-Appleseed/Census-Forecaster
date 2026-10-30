"""Tests for tax_modeler.scenarios.eitc_labor_response."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits.eitc import classify_eitc_region
from tax_modeler.poverty.spm import _EMPLOYEE_FICA_RATE
from tax_modeler.scenarios.eitc_labor_response import (
    INTENSIVE_LOSS_COLUMN,
    LFP_EXIT_PROB_COLUMN,
    LFP_LOSS_COLUMN,
    LFP_SNAP_OFFSET_COLUMN,
    apply_hi_eitc_intensive_response,
    apply_hi_eitc_lfp_response,
    compute_snap_offset_for_lfp_exit,
)


def _make(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "filing_status": "head_of_household",
        "earned_income": 25_000.0,
        "eitc_amount": 4_000.0,
        "hi_eitc_amount": 1_600.0,  # 40% × $4k federal
        "federal_tax_liability": 0.0,
        "weight": 100.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_non_hoh_filers_unchanged():
    df = _make([
        {"filing_status": "single"},
        {"filing_status": "married_filing_jointly"},
        {"filing_status": "married_filing_separately"},
    ])
    out, _ = apply_hi_eitc_lfp_response(df)
    np.testing.assert_array_equal(out[LFP_LOSS_COLUMN].to_numpy(), np.zeros(3))
    np.testing.assert_array_equal(out[LFP_EXIT_PROB_COLUMN].to_numpy(), np.zeros(3))


def test_hoh_with_zero_earnings_unaffected():
    df = _make([{"earned_income": 0.0}])
    out, _ = apply_hi_eitc_lfp_response(df)
    assert out[LFP_LOSS_COLUMN].iloc[0] == 0.0
    assert out[LFP_EXIT_PROB_COLUMN].iloc[0] == 0.0


def test_hoh_with_zero_hi_eitc_unaffected():
    df = _make([{"hi_eitc_amount": 0.0}])
    out, _ = apply_hi_eitc_lfp_response(df)
    assert out[LFP_LOSS_COLUMN].iloc[0] == 0.0


def test_exit_prob_matches_elasticity_formula():
    df = _make([{}])
    out, diag = apply_hi_eitc_lfp_response(df, elasticity=0.5)
    expected = 0.5 * abs(math.log(1.20 / 1.40))
    assert out[LFP_EXIT_PROB_COLUMN].iloc[0] == pytest.approx(expected, rel=1e-9)
    assert diag["p_exit"] == pytest.approx(expected, rel=1e-9)


def test_resource_loss_formula_components():
    """Verify loss_if_exit = earnings + fed_eitc + 0.5*hi_eitc - FICA - fed_tax."""
    earned = 30_000.0
    fed_eitc = 5_000.0
    hi_eitc = 2_000.0
    fed_tax = 0.0
    df = _make([{
        "earned_income": earned,
        "eitc_amount": fed_eitc,
        "hi_eitc_amount": hi_eitc,
        "federal_tax_liability": fed_tax,
    }])
    out, _ = apply_hi_eitc_lfp_response(df, elasticity=0.5)
    p = 0.5 * abs(math.log(1.20 / 1.40))
    expected_loss_if_exit = (
        earned + fed_eitc + 0.5 * hi_eitc - _EMPLOYEE_FICA_RATE * earned - fed_tax
    )
    expected = p * expected_loss_if_exit
    assert out[LFP_LOSS_COLUMN].iloc[0] == pytest.approx(expected, rel=1e-9)


def test_zero_elasticity_disables_response():
    df = _make([{}])
    out, diag = apply_hi_eitc_lfp_response(df, elasticity=0.0)
    assert out[LFP_LOSS_COLUMN].iloc[0] == 0.0
    assert diag["affected_filers_weighted"] == 0.0
    assert diag["expected_lfp_exits_weighted"] == 0.0


def test_elasticity_scales_loss_linearly():
    df = _make([{}])
    out_low, _ = apply_hi_eitc_lfp_response(df, elasticity=0.3)
    out_high, _ = apply_hi_eitc_lfp_response(df, elasticity=0.6)
    # 0.6 / 0.3 = 2.0 scaling on the loss
    assert out_high[LFP_LOSS_COLUMN].iloc[0] == pytest.approx(
        2.0 * out_low[LFP_LOSS_COLUMN].iloc[0], rel=1e-9
    )


def test_inplace_modifies_input():
    df = _make([{}])
    out, _ = apply_hi_eitc_lfp_response(df, inplace=True)
    assert out is df
    assert LFP_LOSS_COLUMN in df.columns


def test_copy_default_does_not_mutate_input():
    df = _make([{}])
    df_before = df.copy()
    out, _ = apply_hi_eitc_lfp_response(df, inplace=False)
    assert out is not df
    assert LFP_LOSS_COLUMN not in df.columns
    pd.testing.assert_frame_equal(df, df_before)


def test_missing_required_column_raises():
    df = _make([{}]).drop(columns=["hi_eitc_amount"])
    with pytest.raises(KeyError, match="hi_eitc_amount"):
        apply_hi_eitc_lfp_response(df)


def test_missing_federal_tax_column_treated_as_zero():
    df = _make([{}]).drop(columns=["federal_tax_liability"])
    out, _ = apply_hi_eitc_lfp_response(df)
    # Should still produce a non-zero loss for the HoH filer
    assert out[LFP_LOSS_COLUMN].iloc[0] > 0


def test_loss_clamped_non_negative():
    """If baseline federal tax somehow exceeds earnings + credits, clamp at 0."""
    df = _make([{
        "earned_income": 1_000.0,
        "eitc_amount": 100.0,
        "hi_eitc_amount": 40.0,
        "federal_tax_liability": 100_000.0,  # absurd, but tests the clamp
    }])
    out, _ = apply_hi_eitc_lfp_response(df)
    assert out[LFP_LOSS_COLUMN].iloc[0] == 0.0


def test_diagnostics_keys():
    df = _make([{}, {"filing_status": "single"}])
    _, diag = apply_hi_eitc_lfp_response(df)
    expected_keys = {
        "elasticity", "delta_log_eitc", "p_exit",
        "affected_filers_weighted", "avg_exit_prob",
        "expected_lfp_exits_weighted",
        "aggregate_lost_earnings_$M", "aggregate_resource_loss_$M",
    }
    assert set(diag.keys()) >= expected_keys


def test_aggregate_diagnostics_match_per_filer_loss():
    """Aggregate resource loss equals weight-sum of per-filer loss."""
    df = _make([
        {"weight": 100.0},
        {"weight": 200.0, "earned_income": 15_000.0,
         "eitc_amount": 2_500.0, "hi_eitc_amount": 1_000.0},
        {"filing_status": "single"},  # unaffected
    ])
    out, diag = apply_hi_eitc_lfp_response(df)
    expected_agg_m = float(
        (out[LFP_LOSS_COLUMN].to_numpy() * df["weight"].to_numpy()).sum() / 1e6
    )
    assert diag["aggregate_resource_loss_$M"] == pytest.approx(expected_agg_m, rel=1e-9)


# ---------------------------------------------------------------------------
# 1C: classify_eitc_region
# ---------------------------------------------------------------------------


def test_classify_eitc_region_phase_in():
    region = classify_eitc_region(
        earned_income=np.array([5_000.0, 10_000.0]),
        num_qualifying_children=np.array([1, 2]),
        filing_status=pd.Series(["head_of_household", "head_of_household"]),
        tax_year=2025,
    )
    assert region[0] == "phase_in"
    assert region[1] == "phase_in"


def test_classify_eitc_region_plateau_boundaries():
    # TY 2025: 1-kid phase_in_ends=12,730; phaseout_start_single=23,350.
    region = classify_eitc_region(
        earned_income=np.array([12_730.0, 18_000.0, 23_350.0]),
        num_qualifying_children=np.array([1, 1, 1]),
        filing_status=pd.Series(["head_of_household"] * 3),
        tax_year=2025,
    )
    # Boundary convention: equal to phase_in_ends or phaseout_start → plateau.
    assert (region == "plateau").all(), f"got {region}"


def test_classify_eitc_region_phase_out():
    # TY 2025: 1-kid phaseout_start_single=23,350.
    region = classify_eitc_region(
        earned_income=np.array([25_000.0, 35_000.0]),
        num_qualifying_children=np.array([1, 2]),
        filing_status=pd.Series(["head_of_household", "head_of_household"]),
        tax_year=2025,
    )
    assert region[0] == "phase_out"
    assert region[1] == "phase_out"


def test_classify_eitc_region_joint_phaseout_higher():
    # MFJ has higher phaseout_start; same earnings classifies differently.
    region = classify_eitc_region(
        earned_income=np.array([25_000.0, 25_000.0]),
        num_qualifying_children=np.array([1, 1]),
        filing_status=pd.Series(["head_of_household", "married_filing_jointly"]),
        tax_year=2025,
    )
    assert region[0] == "phase_out"  # > $23,350 single phaseout_start
    assert region[1] == "plateau"     # < $30,470 joint phaseout_start


# ---------------------------------------------------------------------------
# 1B: SNAP offset
# ---------------------------------------------------------------------------


def _make_snap_unit(rows: list[dict]) -> pd.DataFrame:
    """HoH filer with full SNAP-compute columns + LFP exit probability set."""
    defaults = {
        "hh_id": "H1",
        "filing_status": "head_of_household",
        "income": 25_000.0,
        "earned_income": 25_000.0,
        "num_dependents": 2,
        "snap_amount": 3_500.0,
        LFP_EXIT_PROB_COLUMN: 0.077,
        "weight": 100.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_snap_offset_zero_when_no_exit_prob():
    df = _make_snap_unit([{LFP_EXIT_PROB_COLUMN: 0.0}])
    out, diag = compute_snap_offset_for_lfp_exit(df)
    assert out[LFP_SNAP_OFFSET_COLUMN].iloc[0] == 0.0
    assert diag["aggregate_snap_offset_$M"] == 0.0


def test_snap_offset_positive_when_earner_exits():
    """A working single mom with positive baseline SNAP gets non-trivial offset
    after the counterfactual zeroes her earnings."""
    df = _make_snap_unit([{}])
    out, diag = compute_snap_offset_for_lfp_exit(df)
    # offset = p_exit × (snap_cf - snap_baseline); SNAP_cf > snap_baseline
    # because gross monthly income drops to 0.
    offset = out[LFP_SNAP_OFFSET_COLUMN].iloc[0]
    assert offset > 0, f"expected positive offset, got {offset}"
    assert diag["aggregate_snap_offset_$M"] > 0


def test_snap_offset_scales_with_exit_prob():
    df_a = _make_snap_unit([{LFP_EXIT_PROB_COLUMN: 0.05}])
    df_b = _make_snap_unit([{LFP_EXIT_PROB_COLUMN: 0.10}])
    out_a, _ = compute_snap_offset_for_lfp_exit(df_a)
    out_b, _ = compute_snap_offset_for_lfp_exit(df_b)
    # 0.10 / 0.05 = 2× scaling
    assert out_b[LFP_SNAP_OFFSET_COLUMN].iloc[0] == pytest.approx(
        2.0 * out_a[LFP_SNAP_OFFSET_COLUMN].iloc[0], rel=1e-9
    )


def test_snap_offset_inplace_default_does_not_mutate():
    df = _make_snap_unit([{}])
    df_before = df.copy()
    out, _ = compute_snap_offset_for_lfp_exit(df, inplace=False)
    assert out is not df
    assert LFP_SNAP_OFFSET_COLUMN not in df.columns
    pd.testing.assert_frame_equal(df, df_before)


# ---------------------------------------------------------------------------
# 1C: apply_hi_eitc_intensive_response
# ---------------------------------------------------------------------------


def _make_intensive_unit(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "filing_status": "head_of_household",
        "earned_income": 10_000.0,     # 1-kid phase-in region (TY25: < 12,730)
        "eitc_amount": 3_400.0,        # 0.34 × 10,000
        "hi_eitc_amount": 1_360.0,
        "num_qualifying_children": 1,
        LFP_EXIT_PROB_COLUMN: 0.077,
        "weight": 100.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_intensive_response_zero_for_non_hoh():
    df = _make_intensive_unit([
        {"filing_status": "single"},
        {"filing_status": "married_filing_jointly"},
    ])
    out, _ = apply_hi_eitc_intensive_response(df, tax_year=2025)
    assert (out[INTENSIVE_LOSS_COLUMN] == 0.0).all()


def test_intensive_response_zero_for_plateau_workers():
    # TY 2025: 1-kid plateau is [12,730, 23,350].
    df = _make_intensive_unit([{"earned_income": 18_000.0}])
    out, _ = apply_hi_eitc_intensive_response(df, tax_year=2025)
    assert out[INTENSIVE_LOSS_COLUMN].iloc[0] == 0.0


def test_intensive_response_zero_for_phase_out_workers():
    # TY 2025: 1-kid phaseout_start_single = 23,350.
    df = _make_intensive_unit([{"earned_income": 28_000.0}])
    out, _ = apply_hi_eitc_intensive_response(df, tax_year=2025)
    assert out[INTENSIVE_LOSS_COLUMN].iloc[0] == 0.0


def test_intensive_response_positive_for_phase_in_hoh():
    df = _make_intensive_unit([{}])
    out, diag = apply_hi_eitc_intensive_response(df, tax_year=2025)
    assert out[INTENSIVE_LOSS_COLUMN].iloc[0] > 0
    assert diag["aggregate_resource_loss_$M"] > 0


def test_intensive_response_scales_with_elasticity():
    df = _make_intensive_unit([{}])
    out_low, _ = apply_hi_eitc_intensive_response(df, tax_year=2025, elasticity_phase_in=0.10)
    out_high, _ = apply_hi_eitc_intensive_response(df, tax_year=2025, elasticity_phase_in=0.20)
    assert out_high[INTENSIVE_LOSS_COLUMN].iloc[0] == pytest.approx(
        2.0 * out_low[INTENSIVE_LOSS_COLUMN].iloc[0], rel=1e-9
    )


def test_intensive_response_disabled_at_zero_elasticity():
    df = _make_intensive_unit([{}])
    out, diag = apply_hi_eitc_intensive_response(df, tax_year=2025, elasticity_phase_in=0.0)
    assert out[INTENSIVE_LOSS_COLUMN].iloc[0] == 0.0
    assert diag["aggregate_resource_loss_$M"] == 0.0


def test_intensive_response_reduced_by_lfp_exit_prob():
    """The (1 - p_exit) residual scales the intensive loss down."""
    df_a = _make_intensive_unit([{LFP_EXIT_PROB_COLUMN: 0.0}])
    df_b = _make_intensive_unit([{LFP_EXIT_PROB_COLUMN: 0.5}])
    out_a, _ = apply_hi_eitc_intensive_response(df_a, tax_year=2025)
    out_b, _ = apply_hi_eitc_intensive_response(df_b, tax_year=2025)
    # 0.5 / 1.0 = 0.5× scaling (residual non-exit fraction)
    assert out_b[INTENSIVE_LOSS_COLUMN].iloc[0] == pytest.approx(
        0.5 * out_a[INTENSIVE_LOSS_COLUMN].iloc[0], rel=1e-9
    )
