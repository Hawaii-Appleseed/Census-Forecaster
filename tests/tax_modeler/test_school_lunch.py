"""Tests for ``tax_modeler.benefits.school_lunch.compute_school_lunch_for_units``."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.benefits.school_lunch import (
    compute_school_lunch_for_units,
    hawaii_school_lunch_parameters,
)


def _frame(rows):
    return pd.DataFrame(rows)


def test_no_kids_no_lunch():
    df = _frame([{
        "filing_status": "single", "num_dependents": 0, "income": 15_000,
    }])
    out = compute_school_lunch_for_units(df, tax_year=2024)
    assert out["school_lunch_amount"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_low_income_with_kids_gets_free_rate():
    """A single mother at $20k income with 2 kids gets free meals × 180 days × 2."""
    df = _frame([{
        "filing_status": "head_of_household", "num_dependents": 2, "income": 20_000,
    }])
    out = compute_school_lunch_for_units(df, tax_year=2024)
    p = hawaii_school_lunch_parameters(2024)
    expected = p.free_rate_per_meal * p.school_days * 2
    assert out["school_lunch_amount"].iloc[0] == pytest.approx(expected, rel=1e-4)


def test_reduced_price_band():
    """A 1-kid family at 150% FPL (between 130% and 185%) gets reduced rate."""
    df = _frame([{
        "filing_status": "head_of_household", "num_dependents": 1, "income": 35_000,
    }])
    out = compute_school_lunch_for_units(df, tax_year=2024)
    p = hawaii_school_lunch_parameters(2024)
    expected_reduced = p.reduced_rate_per_meal * p.school_days * 1
    # Should be reduced, not free.
    assert out["school_lunch_amount"].iloc[0] == pytest.approx(expected_reduced, rel=1e-3)


def test_high_income_no_lunch_resource():
    df = _frame([{
        "filing_status": "single", "num_dependents": 2, "income": 200_000,
    }])
    out = compute_school_lunch_for_units(df, tax_year=2024)
    assert out["school_lunch_amount"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_year_specific_rates():
    """TY2022 Hawaii rates differ from TY2024."""
    df = _frame([{
        "filing_status": "head_of_household", "num_dependents": 1, "income": 15_000,
    }])
    out_22 = compute_school_lunch_for_units(df, tax_year=2022)
    out_24 = compute_school_lunch_for_units(df, tax_year=2024)
    assert out_22["school_lunch_amount"].iloc[0] < out_24["school_lunch_amount"].iloc[0]
