"""Stage 3 smoke: enrich_for_credits derives credit-ready columns.

The enrichment step replaces the dependent person-ID list with synthetic
dependent dicts and derives ``earned_income``, ``investment_income``,
``total_cash_income``, ``num_qualifying_children``.  Without these,
downstream EITC/CTC math silently zeros out.
"""
from __future__ import annotations

import pytest

REQUIRED_COLUMNS = (
    "earned_income",
    "investment_income",
    "total_cash_income",
    "num_qualifying_children",
    "dependents",
    "dependents_details",
)


@pytest.mark.smoke
def test_enrich_adds_credit_columns(enriched_units) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in enriched_units.columns]
    assert not missing, f"enrich_for_credits dropped columns: {missing}"


@pytest.mark.smoke
def test_enriched_dependents_are_dicts(enriched_units) -> None:
    """Dependents must be a list of dicts with `age`, not a list of strings."""
    sample = enriched_units["dependents"].iloc[0]
    assert isinstance(sample, list)
    if sample:
        assert isinstance(sample[0], dict)
        assert "age" in sample[0]


@pytest.mark.smoke
def test_total_cash_income_positive(enriched_units) -> None:
    """TCI is clipped to non-negative — used for quintile binning so a
    negative would put a unit in the wrong bin."""
    assert (enriched_units["total_cash_income"] >= 0).all()


@pytest.mark.smoke
def test_qualifying_children_capped(enriched_units) -> None:
    """EITC caps qualifying children at 3; constructor must respect that
    even when num_dependents is higher."""
    assert enriched_units["num_qualifying_children"].max() <= 3
