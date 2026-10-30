"""Tests that apply_top_income_growth_premium clears stale hi_* tax columns.

When top-earner AGI is scaled upward, any pre-computed tax columns on the
affected rows are now stale. The function must NaN them out so consumers
that read tax columns without calling _compute_base_tax() get a loud NaN
failure instead of a silently wrong pre-premium value.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from tax_modeler.scenarios.behavioral_response import apply_top_income_growth_premium

_STALE_COLS = (
    "hi_tax_liability",
    "hi_taxable_income",
    "hi_tax_before_credits",
    "hi_low_income_credit",
    "hi_standard_deduction",
    "hi_itemized_deduction",
    "hi_cg_cap_savings",
    "hi_state_tax",
)


def _make_df() -> pd.DataFrame:
    """Two rows: one above threshold ($2M), one below ($200K)."""
    rows = [
        {
            "income": 2_000_000.0,
            "filing_status": "married_filing_jointly",
            "weight": 1.0,
        },
        {
            "income": 200_000.0,
            "filing_status": "single",
            "weight": 5.0,
        },
    ]
    df = pd.DataFrame(rows)
    # Populate all stale tax columns with sentinel values so we can detect
    # whether they were cleared or preserved.
    for col in _STALE_COLS:
        df[col] = 12_345.0
    return df


def test_above_threshold_tax_cols_cleared():
    """Rows above the premium threshold must have NaN tax columns post-call."""
    df = _make_df()
    result = apply_top_income_growth_premium(
        df, target_year=2027, base_year=2024,
    )
    above = result[result["income"] >= 500_000]
    assert len(above) == 1, "expected exactly one row above threshold"
    for col in _STALE_COLS:
        val = above[col].iloc[0]
        assert math.isnan(val), (
            f"Column {col!r} on above-threshold row should be NaN after "
            f"premium applied, got {val!r}"
        )


def test_below_threshold_tax_cols_preserved():
    """Rows below the premium threshold must retain their original tax columns."""
    df = _make_df()
    result = apply_top_income_growth_premium(
        df, target_year=2027, base_year=2024,
    )
    below = result[result["income"] < 500_000]
    assert len(below) == 1, "expected exactly one row below threshold"
    for col in _STALE_COLS:
        val = below[col].iloc[0]
        assert val == pytest.approx(12_345.0), (
            f"Column {col!r} on below-threshold row should be unchanged (12345), "
            f"got {val!r}"
        )


def test_no_premium_leaves_cols_intact():
    """When annual_premium=0, no rows are affected and all cols stay."""
    df = _make_df()
    result = apply_top_income_growth_premium(
        df, target_year=2027, base_year=2024, annual_premium=0.0,
    )
    for col in _STALE_COLS:
        vals = result[col].to_numpy(dtype=float)
        assert np.allclose(vals, 12_345.0), (
            f"Column {col!r} changed despite annual_premium=0: {vals}"
        )
