"""Tests that the ``tax_year`` parameter threads into each benefit module's
FPL income test.

Forward-projected runs (e.g. TY2028) must test benefit eligibility against
same-year FPL thresholds, not the frozen 2024 table. The deterministic
fixtures below place a household-of-3's income between the 2024 and the
(higher) CPI-projected 2028 FPL thresholds so the eligibility / benefit
tier provably flips with ``tax_year``.
"""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.benefits._fpl import hawaii_fpl
from tax_modeler.benefits.childcare import compute_childcare_for_units
from tax_modeler.benefits.liheap import compute_liheap_for_units
from tax_modeler.benefits.school_lunch import (
    compute_school_lunch_for_units,
    hawaii_school_lunch_parameters,
)
from tax_modeler.benefits.wic import compute_wic_for_units


def _unit(income: float, *, num_dependents: int = 1, earned_income: float | None = None) -> pd.DataFrame:
    """One married-joint tax unit → household size = 2 + num_dependents."""
    row = {
        "filing_status": "married_filing_jointly",
        "num_dependents": num_dependents,
        "income": income,
    }
    if earned_income is not None:
        row["earned_income"] = earned_income
    return pd.DataFrame([row])


def test_2028_fpl_is_above_2024_for_size_3():
    """Sanity guard for the deterministic income points used below."""
    assert hawaii_fpl(2028, household_size=3) > hawaii_fpl(2024, household_size=3)


def test_school_lunch_tier_rises_with_tax_year():
    # size-3 income $39k: ratio 1.36 (2024) → reduced; 1.23 (2028) → free.
    # The reimbursement-rate table only spans 2022-2025, so hold rates fixed
    # via explicit ``params`` — that isolates the FPL-year effect (tax_year
    # then only feeds the FPL income test).
    fixed_rates = hawaii_school_lunch_parameters(2024)
    units = _unit(39_000)
    amt_2024 = compute_school_lunch_for_units(
        units, tax_year=2024, params=fixed_rates
    )["school_lunch_amount"].iloc[0]
    amt_2028 = compute_school_lunch_for_units(
        units, tax_year=2028, params=fixed_rates
    )["school_lunch_amount"].iloc[0]
    assert amt_2024 > 0  # reduced-price under the frozen 2024 FPL table
    assert amt_2028 > amt_2024  # free-meal rate once the threshold rises


def test_liheap_eligibility_flips_on_with_tax_year():
    # size-3 income $45k: ratio 1.57 (2024) ≥ 1.50 cap → $0; 1.42 (2028) → eligible.
    units = _unit(45_000)
    amt_2024 = compute_liheap_for_units(units, tax_year=2024)["liheap_amount"].iloc[0]
    amt_2028 = compute_liheap_for_units(units, tax_year=2028)["liheap_amount"].iloc[0]
    assert amt_2024 == 0
    assert amt_2028 > 0


def test_wic_eligibility_flips_on_with_tax_year():
    # size-3 income $55k: ratio 1.92 (2024) ≥ 1.85 cap → $0; 1.74 (2028) → eligible.
    units = _unit(55_000)
    amt_2024 = compute_wic_for_units(units, tax_year=2024)["wic_amount"].iloc[0]
    amt_2028 = compute_wic_for_units(units, tax_year=2028)["wic_amount"].iloc[0]
    assert amt_2024 == 0
    assert amt_2028 > 0


def test_childcare_eligibility_flips_on_with_tax_year():
    # size-3 income $75k earned: ratio 2.62 (2024) ≥ 2.50 cap → $0; 2.37 (2028) → eligible.
    units = _unit(75_000, earned_income=75_000)
    amt_2024 = compute_childcare_for_units(units, tax_year=2024)["childcare_amount"].iloc[0]
    amt_2028 = compute_childcare_for_units(units, tax_year=2028)["childcare_amount"].iloc[0]
    assert amt_2024 == 0
    assert amt_2028 > 0


@pytest.mark.parametrize(
    "compute_fn, out_col, income, kwargs",
    [
        (compute_school_lunch_for_units, "school_lunch_amount", 39_000, {}),
        (compute_liheap_for_units, "liheap_amount", 45_000, {}),
        (compute_wic_for_units, "wic_amount", 55_000, {}),
        (compute_childcare_for_units, "childcare_amount", 75_000, {"earned_income": 75_000}),
    ],
)
def test_default_tax_year_matches_explicit_2024(compute_fn, out_col, income, kwargs):
    """The defaulted ``tax_year`` (2024) keeps existing callers byte-identical."""
    units = _unit(income, **kwargs)
    default = compute_fn(units)[out_col].iloc[0]
    explicit = compute_fn(units, tax_year=2024)[out_col].iloc[0]
    assert default == explicit
