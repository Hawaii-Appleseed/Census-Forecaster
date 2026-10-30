"""Integration tests for the end-to-end pipeline runner.

All tests use synthetic tax units passed via ``tax_units_df`` so that no
local PUMS files or Census API keys are required.  The PUMS-loading and
TaxUnitConstructor stages (steps 1–2) are tested separately in the units/
and smoke test suites.
"""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.liability.hawaii import calculate_hawaii_tax_for_units
from tax_modeler.pipeline import (
    PipelineResult,
    _enrich_for_credits,
    run_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_constructor_style_units(n: int = 20) -> pd.DataFrame:
    """Synthetic units in TaxUnitConstructor output format.

    Uses PUMA codes from multiple counties so geographic differentiation
    is exercised.  ``dependents`` is a list of person-ID strings (the raw
    constructor format), not enriched dicts.
    """
    templates = [
        # (filing_status,  income,  n_dep, puma,  wagp,   intp)
        ("single",                     18_000, 0, "0200", 18_000, 0),
        ("head_of_household",          45_000, 2, "0200", 45_000, 0),
        ("married_filing_jointly",     85_000, 1, "0301", 75_000, 0),
        ("married_filing_jointly",    220_000, 0, "0301", 170_000, 50_000),
        ("married_filing_separately", 110_000, 0, "0305", 110_000, 0),
    ]
    rows = []
    for i in range(n):
        fs, income, n_dep, puma, wagp, intp = templates[i % len(templates)]
        rows.append({
            "SERIALNO": f"HI{i:04d}",
            "PUMA": puma,
            "filing_status": fs,
            "income": float(income),
            "num_dependents": n_dep,
            "dependents": [f"P{i:04d}_dep{d}" for d in range(n_dep)],  # person IDs
            "weight": 200.0,
            "hh_weight": 200.0,
            "primary_wagp": float(wagp),
            "primary_semp": 0.0,
            "primary_intp": float(intp),
            "secondary_wagp": 0.0,
            "secondary_semp": 0.0,
            "secondary_intp": 0.0,
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def taxed_units() -> pd.DataFrame:
    """Pre-taxed units (stages 1–4 complete, no calibration) for use by
    tests that call run_pipeline(tax_units_df=...)."""
    raw = _make_constructor_style_units(n=25)
    enriched = _enrich_for_credits(raw)
    return calculate_hawaii_tax_for_units(enriched)


# ---------------------------------------------------------------------------
# Enrichment tests
# ---------------------------------------------------------------------------

def test_enrich_converts_dependent_list():
    """_enrich_for_credits replaces person-ID list with credit-ready dicts."""
    raw = _make_constructor_style_units(n=5)
    enriched = _enrich_for_credits(raw)

    # person-ID list preserved in new column
    assert "dependent_person_ids" in enriched.columns

    # dependents is now a list of dicts with required keys
    hoh_row = enriched[enriched["filing_status"] == "head_of_household"].iloc[0]
    assert isinstance(hoh_row["dependents"], list)
    assert len(hoh_row["dependents"]) == hoh_row["num_dependents"]
    if hoh_row["num_dependents"] > 0:
        dep = hoh_row["dependents"][0]
        assert "age" in dep and "relationship" in dep and "months_in_home" in dep


def test_enrich_derives_earned_income():
    """earned_income = primary_wagp + primary_semp + secondary_wagp + secondary_semp."""
    raw = _make_constructor_style_units(n=5)
    enriched = _enrich_for_credits(raw)

    assert "earned_income" in enriched.columns
    assert (enriched["earned_income"] >= 0).all()
    # For single filer row: earned_income == primary_wagp
    single = enriched[enriched["filing_status"] == "single"].iloc[0]
    assert single["earned_income"] == pytest.approx(single["primary_wagp"])


def test_enrich_derives_investment_income():
    """investment_income captures primary_intp (+ secondary if present)."""
    raw = _make_constructor_style_units(n=5)
    enriched = _enrich_for_credits(raw)

    assert "investment_income" in enriched.columns
    # High-income MFJ has intp=50000
    mfj_high = enriched[
        (enriched["filing_status"] == "married_filing_jointly") &
        (enriched["primary_intp"] == 50_000)
    ].iloc[0]
    assert mfj_high["investment_income"] == pytest.approx(50_000)


def test_enrich_is_idempotent():
    """Calling _enrich_for_credits twice does not change the result."""
    raw = _make_constructor_style_units(n=5)
    once = _enrich_for_credits(raw)
    twice = _enrich_for_credits(once)

    pd.testing.assert_frame_equal(
        once[["earned_income", "investment_income"]].reset_index(drop=True),
        twice[["earned_income", "investment_income"]].reset_index(drop=True),
    )


# ---------------------------------------------------------------------------
# Pipeline runner tests
# ---------------------------------------------------------------------------

def test_pipeline_returns_result_type(taxed_units):
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    assert isinstance(result, PipelineResult)


def test_pipeline_metadata(taxed_units):
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)

    assert result.target_year == 2026
    assert result.n_units == len(taxed_units)
    assert result.n_weighted_filers > 0
    assert result.elapsed_seconds > 0


def test_pipeline_state_summary_keys(taxed_units):
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    summary = result.state_summary

    for key in ("hi_net_tax_revenue", "hi_gross_tax_revenue",
                "total_income", "total_weighted_filers"):
        assert key in summary, f"Missing key {key!r}"


def test_pipeline_projected_income_grows(taxed_units):
    """All projected incomes must be >= base (positive growth for Hawaii 2026)."""
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    proj = result.projected_units

    assert "income_base_year" in proj.columns
    assert (proj["income"] >= proj["income_base_year"]).all()


def test_pipeline_by_county_has_multiple_rows(taxed_units):
    """Units span multiple PUMAs → at least 2 counties in by_county output."""
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    assert len(result.by_county) >= 2


def test_pipeline_projection_metadata_columns(taxed_units):
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    proj = result.projected_units

    assert (proj["projection_year"] == 2026).all()
    assert (proj["projection_method"] == "ensemble").all()
    assert "income_ci90_low" in proj.columns
    assert "income_ci90_high" in proj.columns


def test_pipeline_geography_auto_assigned(taxed_units):
    """PUMA → county auto-assignment must produce county column in projected units."""
    assert "county" not in taxed_units.columns  # input has no county
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    assert "county" in result.projected_units.columns
    assert result.projected_units["county"].notna().all()


def test_pipeline_estimator_usable(taxed_units):
    """The returned RevenueEstimator can run all standard queries."""
    result = run_pipeline(target_year=2026, tax_units_df=taxed_units)
    est = result.estimator

    by_fs = est.by_filing_status()
    assert len(by_fs) > 0

    by_q = est.by_income_quintile()
    assert len(by_q) == 5
