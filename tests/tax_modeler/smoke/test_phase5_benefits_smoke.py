"""Smoke tests for Phase 5 federal-cost-share + in-kind programs.

Validates basic shape and reform-DSL integration for ACA PTC, Medicaid
(HI Med-QUEST), WIC, LIHEAP, childcare, and housing modules.
"""
from __future__ import annotations

import pytest
from tax_modeler import (
    Reform,
    apply_reform,
    compute_aca_ptc_for_units,
    compute_childcare_for_units,
    compute_housing_for_units,
    compute_liheap_for_units,
    compute_medicaid_for_units,
    compute_spm_resources,
    compute_wic_for_units,
)
from tax_modeler.errors import ConfigError

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# ACA PTC
# ---------------------------------------------------------------------------


def test_aca_ptc_runs(taxed_units):
    df = compute_aca_ptc_for_units(taxed_units)
    assert "aca_ptc_amount" in df.columns
    assert (df["aca_ptc_amount"] >= 0).all()


def test_aca_ptc_high_income_zero(taxed_units):
    df = compute_aca_ptc_for_units(taxed_units)
    high_income = df[df["income"] > 500_000]
    if len(high_income) > 0:
        assert high_income["aca_ptc_amount"].sum() == 0


def test_aca_ptc_credit_pct_override(taxed_units):
    base = compute_aca_ptc_for_units(taxed_units)
    half = compute_aca_ptc_for_units(taxed_units, overrides={"credit_pct": 0.5})
    base_total = base["aca_ptc_amount"].sum()
    if base_total == 0:
        pytest.skip("no PTC-eligible units on the fixture")
    assert half["aca_ptc_amount"].sum() == pytest.approx(base_total * 0.5, rel=1e-6)


def test_aca_ptc_unknown_override_raises(taxed_units):
    with pytest.raises(ConfigError):
        compute_aca_ptc_for_units(taxed_units, overrides={"foo": 1.0})


# ---------------------------------------------------------------------------
# Medicaid
# ---------------------------------------------------------------------------


def test_medicaid_runs(taxed_units):
    df = compute_medicaid_for_units(taxed_units)
    assert "medicaid_amount" in df.columns
    assert "medicaid_receives" in df.columns
    assert (df["medicaid_amount"] >= 0).all()


def test_medicaid_low_income_eligible(taxed_units):
    df = compute_medicaid_for_units(taxed_units)
    very_low = df[df["income"] < 10_000]
    if len(very_low) > 0:
        # At least one very-low-income unit should qualify under expansion
        assert very_low["medicaid_receives"].any()


def test_medicaid_pmpm_override(taxed_units):
    base = compute_medicaid_for_units(taxed_units)
    doubled = compute_medicaid_for_units(taxed_units, overrides={"adult_pmpm_pct": 2.0})
    base_total = base["medicaid_amount"].sum()
    if base_total == 0:
        pytest.skip("no Medicaid-eligible adults on the fixture")
    # Doubling adult PMPM should at minimum increase total (some children
    # and aged also contribute via different multipliers).
    assert doubled["medicaid_amount"].sum() > base_total


# ---------------------------------------------------------------------------
# WIC
# ---------------------------------------------------------------------------


def test_wic_runs(taxed_units):
    df = compute_wic_for_units(taxed_units)
    assert "wic_amount" in df.columns
    assert (df["wic_amount"] >= 0).all()


def test_wic_requires_dependents(taxed_units):
    df = compute_wic_for_units(taxed_units)
    no_kids = df[df["num_dependents"] == 0]
    assert no_kids["wic_amount"].sum() == 0


# ---------------------------------------------------------------------------
# LIHEAP
# ---------------------------------------------------------------------------


def test_liheap_runs(taxed_units):
    df = compute_liheap_for_units(taxed_units)
    assert "liheap_amount" in df.columns


def test_liheap_high_income_zero(taxed_units):
    df = compute_liheap_for_units(taxed_units)
    high = df[df["income"] > 200_000]
    if len(high) > 0:
        assert high["liheap_amount"].sum() == 0


# ---------------------------------------------------------------------------
# Childcare
# ---------------------------------------------------------------------------


def test_childcare_runs(taxed_units):
    df = compute_childcare_for_units(taxed_units)
    assert "childcare_amount" in df.columns
    assert (df["childcare_amount"] >= 0).all()


def test_childcare_requires_kids_and_earnings(taxed_units):
    df = compute_childcare_for_units(taxed_units)
    no_kids = df[df["num_dependents"] == 0]
    assert no_kids["childcare_amount"].sum() == 0


# ---------------------------------------------------------------------------
# Housing
# ---------------------------------------------------------------------------


def test_housing_runs(taxed_units):
    df = compute_housing_for_units(taxed_units)
    assert "housing_subsidy_amount" in df.columns
    assert (df["housing_subsidy_amount"] >= 0).all()


def test_aca_ptc_default_tax_year_matches_2024(taxed_units):
    """ACA PTC's defaulted tax_year (2024) keeps existing callers stable."""
    default = compute_aca_ptc_for_units(taxed_units)["aca_ptc_amount"]
    explicit = compute_aca_ptc_for_units(taxed_units, tax_year=2024)["aca_ptc_amount"]
    assert default.equals(explicit)


def test_aca_ptc_accepts_forward_tax_year(taxed_units):
    """A forward-projected tax_year threads into the %FPL test without error."""
    df = compute_aca_ptc_for_units(taxed_units, tax_year=2028)
    assert "aca_ptc_amount" in df.columns
    assert (df["aca_ptc_amount"] >= 0).all()


def test_housing_default_tax_year_matches_2024(taxed_units):
    """Housing's defaulted tax_year (2024) keeps existing callers stable."""
    default = compute_housing_for_units(taxed_units)["housing_subsidy_amount"]
    explicit = compute_housing_for_units(taxed_units, tax_year=2024)["housing_subsidy_amount"]
    assert default.equals(explicit)


def test_housing_accepts_forward_tax_year(taxed_units):
    """A forward-projected tax_year threads into the FPL income proxy."""
    df = compute_housing_for_units(taxed_units, tax_year=2028)
    assert "housing_subsidy_amount" in df.columns
    assert (df["housing_subsidy_amount"] >= 0).all()


def test_housing_subsidy_decreases_with_income(taxed_units):
    """Tenant rent rises with income → subsidy falls. On any non-trivial
    fixture, the lowest-income eligible unit's subsidy should be >= the
    highest-income eligible unit's subsidy."""
    df = compute_housing_for_units(taxed_units)
    eligible = df[df["housing_subsidy_amount"] > 0].sort_values("income")
    if len(eligible) < 2:
        pytest.skip("need ≥2 housing-eligible units")
    assert eligible["housing_subsidy_amount"].iloc[0] >= eligible["housing_subsidy_amount"].iloc[-1]


# ---------------------------------------------------------------------------
# Reform-DSL integration
# ---------------------------------------------------------------------------


def test_reform_aca_cut_flows_through(taxed_units):
    reform = Reform(
        name="aca_ptc_minus_50pct",
        benefit_overrides={"aca_ptc": {"credit_pct": 0.5}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    deltas = result.benefit_flow_deltas_millions
    assert "aca_ptc" in deltas


def test_reform_combined_safety_net(taxed_units):
    reform = Reform(
        name="big_safety_net_cut",
        benefit_overrides={
            "snap": {"max_benefit_pct": 0.90},
            "wic": {"package_value_pct": 0.80},
            "liheap": {"benefit_amount_pct": 0.50},
            "housing": {"payment_standard_pct": 0.85},
        },
    )
    result = apply_reform(taxed_units, reform, year=2026)
    deltas = result.benefit_flow_deltas_millions
    assert set(deltas) >= {"snap", "wic", "liheap", "housing"}


# ---------------------------------------------------------------------------
# SPM integration: Phase 5 benefits flow into resources
# ---------------------------------------------------------------------------


def test_spm_resources_use_phase5_benefits(taxed_units):
    df = compute_aca_ptc_for_units(taxed_units)
    df = compute_medicaid_for_units(df)
    df = compute_wic_for_units(df)
    df = compute_liheap_for_units(df)
    df = compute_childcare_for_units(df)
    df = compute_housing_for_units(df)
    resources, meta = compute_spm_resources(df)
    # Phase 5 columns should be in `used`, not `zeroed`
    assert "aca_ptc" not in meta.zeroed_components
    assert "wic" not in meta.zeroed_components
    assert "housing_subsidy" not in meta.zeroed_components
    assert "liheap" not in meta.zeroed_components
    assert "childcare_subsidy" not in meta.zeroed_components
