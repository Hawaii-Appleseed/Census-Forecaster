"""Integration tests for project_tax_units_forward.

Tests the full pipeline: income scaling → Hawaii tax recalc →
federal credit recalc → RevenueEstimator consumption.

These tests require census_forecaster panel data (bundled in-repo)
but do NOT require local PUMS files or Census API keys.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
from tax_modeler.revenue.estimator import RevenueEstimator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tax_units() -> pd.DataFrame:
    """Synthetic tax units covering four filing statuses and three counties.

    Incomes span the full Hawaii bracket range so that bracket recalculation
    is exercised.  The high-income MFJ unit (Honolulu, $250k) is likely to
    switch to itemized deductions; the low-income single (Hawaii County, $18k)
    will stay on the standard deduction — so per-county deduction
    differentiation can be verified.
    """
    rows = [
        # Low-income single, Hawaii County — EITC eligible
        {
            "filing_status": "single",
            "income": 18_000.0,
            "earned_income": 18_000.0,
            "investment_income": 0.0,
            "num_dependents": 1,
            "num_qualifying_children": 1,
            "dependents": [{"age": 5, "relationship": "child", "months_in_home": 12}],
            "weight": 150,
            "county": "Hawaii",
        },
        # Mid-income head-of-household, Maui
        {
            "filing_status": "head_of_household",
            "income": 55_000.0,
            "earned_income": 55_000.0,
            "investment_income": 0.0,
            "num_dependents": 2,
            "num_qualifying_children": 2,
            "dependents": [
                {"age": 7, "relationship": "child", "months_in_home": 12},
                {"age": 10, "relationship": "child", "months_in_home": 12},
            ],
            "weight": 200,
            "county": "Maui",
        },
        # High-income MFJ, Honolulu — likely itemizer
        {
            "filing_status": "married_filing_jointly",
            "income": 250_000.0,
            "earned_income": 200_000.0,
            "investment_income": 50_000.0,
            "num_dependents": 0,
            "num_qualifying_children": 0,
            "dependents": [],
            "weight": 80,
            "county": "Honolulu",
        },
        # Mid-income MFJ with children, Honolulu
        {
            "filing_status": "married_filing_jointly",
            "income": 90_000.0,
            "earned_income": 90_000.0,
            "investment_income": 0.0,
            "num_dependents": 2,
            "num_qualifying_children": 2,
            "dependents": [
                {"age": 4, "relationship": "child", "months_in_home": 12},
                {"age": 8, "relationship": "child", "months_in_home": 12},
            ],
            "weight": 300,
            "county": "Honolulu",
        },
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_income_grows_after_projection():
    """All projected incomes must be >= base incomes (positive growth assumption)."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)

    assert "income_base_year" in proj.columns
    assert (proj["income"] >= proj["income_base_year"]).all(), (
        f"Some incomes shrank:\n{proj[['county', 'income_base_year', 'income']]}"
    )


def test_income_ci90_bounds_ordered():
    """90% CI bounds must be: low <= point estimate <= high."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)

    assert (proj["income_ci90_low"] <= proj["income"]).all()
    assert (proj["income"] <= proj["income_ci90_high"]).all()


def test_taxes_recalculated():
    """hi_tax_liability must be computed and have correct relative ordering."""
    base = _make_tax_units()
    assert "hi_tax_liability" not in base.columns  # input has no pre-computed tax

    proj = project_tax_units_forward(base, target_year=2026)

    assert "hi_tax_liability" in proj.columns
    assert proj["hi_tax_liability"].notna().all()

    # High-income MFJ ($250k) should pay more than low-income single ($18k).
    mfj_250k = proj[
        (proj["filing_status"] == "married_filing_jointly") &
        (proj["income_base_year"] > 200_000)
    ]
    single_18k = proj[proj["income_base_year"] < 20_000]
    assert mfj_250k["hi_tax_liability"].values[0] > single_18k["hi_tax_liability"].values[0]


def test_per_county_income_differentiation():
    """Honolulu and Hawaii County must receive different growth factors."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)

    honolulu_mask = proj["county"] == "Honolulu"
    hawaii_mask = proj["county"] == "Hawaii"

    hnl_ratio = (
        proj.loc[honolulu_mask, "income"].values /
        proj.loc[honolulu_mask, "income_base_year"].values
    )
    haw_ratio = (
        proj.loc[hawaii_mask, "income"].values /
        proj.loc[hawaii_mask, "income_base_year"].values
    )

    # Ratios must be finite and positive
    assert all(r > 0 for r in hnl_ratio)
    assert all(r > 0 for r in haw_ratio)

    # The two counties must have different growth factors (not identical)
    # Allow a small tolerance for floating-point coincidence.
    assert abs(hnl_ratio[0] - haw_ratio[0]) > 1e-6, (
        f"Honolulu and Hawaii County got identical growth factors: {hnl_ratio[0]:.6f}"
    )


def test_metadata_columns_present():
    """Projection metadata columns must be present and correctly populated."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026, method="ensemble")

    assert "projection_year" in proj.columns
    assert "projection_base_year" in proj.columns
    assert "projection_method" in proj.columns

    assert (proj["projection_year"] == 2026).all()
    assert (proj["projection_method"] == "ensemble").all()


def test_revenue_estimator_state_summary_valid():
    """state_summary() must return a valid dict with positive gross revenue.

    Note: projected net revenue can be *lower* than a naive standard-deduction
    base because the projector applies scaled itemized deductions, which are
    larger than the standard deduction for high-income filers.  We therefore
    test structure and gross-tax positivity rather than a direction comparison.
    """
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)
    summary = RevenueEstimator(proj).state_summary()

    for key in ("hi_net_tax_revenue", "hi_gross_tax_revenue",
                "total_income", "total_weighted_filers"):
        assert key in summary, f"Missing key {key!r} in state_summary()"

    assert summary["total_weighted_filers"] == pytest.approx(730, rel=0.01)
    assert summary["total_income"] > 0
    # The two high-income Honolulu units still owe gross tax before credits
    assert summary["hi_gross_tax_revenue"] > 0


def test_revenue_estimator_by_county():
    """by_county() must return differentiated per-county revenue."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)
    est = RevenueEstimator(proj)

    by_county = est.by_county()
    assert len(by_county) >= 3, "Expected at least 3 counties in output"

    # county is a regular column (not the index) — filter accordingly.
    hnl_row = by_county[by_county["county"] == "Honolulu"]
    assert len(hnl_row) == 1, f"Expected 1 Honolulu row, got {len(hnl_row)}"
    # Honolulu has two MFJ units; gross tax must be positive.
    assert hnl_row["hi_gross_tax"].iloc[0] > 0


def test_schema_preserved():
    """Output DataFrame must have the same columns as input (plus metadata)."""
    base = _make_tax_units()
    proj = project_tax_units_forward(base, target_year=2026)

    for col in base.columns:
        assert col in proj.columns, f"Column {col!r} missing from projected output"

    # New columns added by projector
    for col in ["income_base_year", "income_ci90_low", "income_ci90_high",
                "projection_year", "projection_base_year", "projection_method"]:
        assert col in proj.columns, f"Expected new column {col!r} missing"


def test_empty_dataframe_passthrough():
    """Empty input should return an empty DataFrame without errors."""
    base = _make_tax_units()
    empty = base.iloc[:0].copy()
    proj = project_tax_units_forward(empty, target_year=2026)
    assert proj.empty


def test_no_county_column_uses_state_proxy():
    """Without a county column, all units should receive the Honolulu proxy factor."""
    base = _make_tax_units().drop(columns=["county"])
    proj = project_tax_units_forward(base, target_year=2026)

    ratios = proj["income"].values / proj["income_base_year"].values
    # All units must have received the same growth factor
    assert max(ratios) - min(ratios) < 1e-9, (
        f"Expected uniform growth factor without county, got varying ratios: {ratios}"
    )


# ---------------------------------------------------------------------------
# Year-aware credit recalculation (Phase 2 of EITC/CTC geo plan)
# ---------------------------------------------------------------------------

def _two_kid_30k_unit() -> pd.DataFrame:
    """A 2-child single filer at $30K EI — squarely in the EITC flat region.

    Uses PUMS-style RELSHIPP relationship codes (25 = biological child) and
    citizenship code 1 so that both CTC and EITC qualifying-child tests pass.
    The earned income exceeds the EITC phase-in point for 2 kids across all
    years and stays below the single-filer phaseout start for all years.
    """
    return pd.DataFrame([{
        "filing_status": "single",
        "income": 30_000.0,
        "earned_income": 30_000.0,
        "investment_income": 0.0,
        "num_dependents": 2,
        "num_qualifying_children": 2,
        # CTC uses ``dependents`` (string code path).
        "dependents": [
            {"age": 8, "relationship": "25", "citizenship": "1"},
            {"age": 12, "relationship": "25", "citizenship": "1"},
        ],
        # EITC uses ``dependents_details`` with integer codes.
        "dependents_details": [
            {"age": 8, "relationship": 25, "citizenship": 1},
            {"age": 12, "relationship": 25, "citizenship": 1},
        ],
    }])


def test_recalculate_ctc_refundable_cap_changes_by_year():
    """_recalculate_ctc must apply the target year's refundable-cap parameter."""
    from tax_modeler.projection.tax_unit_projector import _recalculate_ctc

    # HoH at $28k: income tax is small, so the per-child cap binds.
    df = _two_kid_30k_unit().assign(
        filing_status="head_of_household", income=28_000.0, earned_income=28_000.0,
    )

    out_2022 = _recalculate_ctc(df, tax_year=2022)
    out_2025 = _recalculate_ctc(df, tax_year=2025)

    # Tax before credits: 10% × (28k − SD) = 860 (TY2022) / 437.5 (TY2025).
    # Remainder vs 15% × (28k − 2.5k) = 3_825 vs cap: 2×1_500 / 2×1_700.
    assert out_2022["ctc_refundable"].iloc[0] == pytest.approx(3_000)
    assert out_2025["ctc_refundable"].iloc[0] == pytest.approx(3_400)
    assert out_2022["ctc_nonrefundable"].iloc[0] == pytest.approx(860)
    assert out_2025["ctc_nonrefundable"].iloc[0] == pytest.approx(437.5)
    # Max credit: $2,000/child (TY2022) → $2,200/child (TY2025).
    assert out_2022["ctc_total"].iloc[0] == pytest.approx(3_860)
    assert out_2025["ctc_total"].iloc[0] == pytest.approx(3_837.5)


def test_recalculate_eitc_grows_with_irs_parameter_indexing():
    """At the same nominal EI, EITC must be at least as large in TY 2025 as TY 2022.

    EI=$30k for 2 kids sits in the phaseout region for both years (single
    filer phaseout starts ~$20k). As the IRS inflates phaseout-start and
    max-credit each year, the credit at a fixed nominal EI strictly grows.
    """
    from tax_modeler.credits.eitc import calculate_eitc_for_tax_units

    df = _two_kid_30k_unit()
    out_2022 = calculate_eitc_for_tax_units(df, tax_year=2022)
    out_2025 = calculate_eitc_for_tax_units(df, tax_year=2025)

    e22 = out_2022["eitc_amount"].iloc[0]
    e25 = out_2025["eitc_amount"].iloc[0]
    assert e22 > 0
    assert e25 > e22, f"Expected EITC TY2025 > TY2022 at fixed $30K EI; got 2022={e22:.2f}, 2025={e25:.2f}"
