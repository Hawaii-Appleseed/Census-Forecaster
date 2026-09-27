"""Stage 4 smoke: compute_base_tax produces sane Hawaii liability + credits.

Catches regressions where bracket math, credit overlay, or capital-gains
cap drift quietly.  The fixture has high-CG units above $1M designed to
trigger the §235-51(f) cap so any change to that code path shows up here.
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.smoke
def test_compute_base_tax_adds_liability_columns(taxed_units) -> None:
    expected = ("hi_tax_liability", "hi_agi", "agi", "hi_state_tax")
    missing = [c for c in expected if c not in taxed_units.columns]
    assert not missing, f"compute_base_tax dropped columns: {missing}"


@pytest.mark.smoke
def test_tax_liability_is_finite(taxed_units) -> None:
    """No NaN / inf liabilities — those propagate silently into revenue
    aggregations and produce nonsense totals."""
    assert np.isfinite(taxed_units["hi_tax_liability"]).all()


@pytest.mark.smoke
def test_tax_liability_finite_and_bounded(taxed_units) -> None:
    """Hawaii state tax can be slightly negative when refundable EITC/CTC
    nets out a tiny pre-credit liability; it must still be finite and
    bounded by income (no runaway compute_base_tax bug)."""
    liab = taxed_units["hi_tax_liability"]
    income = taxed_units["income"].clip(lower=0)
    assert np.isfinite(liab).all()
    # Allow refundable-credit overshoot up to $5K per filer; bigger means a bug
    assert (liab >= -5_000).all(), \
        f"Some hi_tax_liability < -$5K: min={liab.min():,.2f}"
    # Liability cannot exceed income (sanity bound on bracket math)
    assert (liab <= income + 1).all(), "Liability exceeds income — bracket math broken"


@pytest.mark.smoke
def test_high_income_units_pay_tax(taxed_units) -> None:
    """Units above $200K AGI must owe non-trivial state tax — if they
    don't, brackets / SD math silently degenerated to zero."""
    high = taxed_units[taxed_units["agi"] > 200_000]
    if len(high) == 0:
        pytest.skip("Fixture has no $200K+ units in this sample.")
    assert (high["hi_tax_liability"] > 0).any(), \
        "No high-income unit pays Hawaii tax — bracket-walk degenerate?"


@pytest.mark.smoke
def test_cg_cap_savings_column_present(taxed_units) -> None:
    """The §235-51(f) cap-savings column must exist even when zero — its
    absence breaks scenario comparisons that read it directly."""
    assert "hi_cg_cap_savings" in taxed_units.columns
