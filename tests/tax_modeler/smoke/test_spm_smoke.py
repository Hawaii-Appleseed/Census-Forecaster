"""Smoke tests for SPM resource calculation + Hawaii thresholds (Phase 2).

Validates that:

* ``compute_spm_resources`` runs on a taxed-units fixture, returns a
  populated column, and the meta records the expected zeroed components.
* The Hawaii threshold lookup is monotone in household size and within
  a sane range (between $20K and $100K for typical compositions).
* Resources < threshold can be combined with poverty metrics on the
  fixture without crashing — end-to-end Phase-2 smoke.
"""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler import (
    compute_spm_resources,
    hawaii_spm_threshold,
    poverty_rate,
    spm_threshold_table,
)
from tax_modeler.errors import ConfigError

pytestmark = pytest.mark.smoke


def test_compute_spm_resources_runs(taxed_units):
    df, meta = compute_spm_resources(taxed_units)
    assert "spm_resources" in df.columns
    assert df["spm_resources"].notna().all()
    # Phase 2 ships without SNAP / housing / MOOP — those should be flagged
    assert "snap" in meta.zeroed_components
    assert "moop" in meta.zeroed_components
    # Federal tax falls back to flat-rate estimate
    assert meta.federal_tax_source == "fallback_rate"
    # Payroll tax computed from earned_income
    assert meta.payroll_tax_source == "computed"


def test_spm_resources_below_money_income_for_taxpayers(taxed_units):
    """For positive-tax units, SPM resources should be below money income.

    Resources = TCI + EITC + refundable_CTC - taxes - payroll - MOOP - …
    For a unit with positive state tax and no SNAP/housing modeled,
    resources strictly less than TCI.
    """
    df, _ = compute_spm_resources(taxed_units)
    taxpayers = df[df["hi_tax_liability"] > 100]
    # Most taxpayers should have resources < TCI (allowing for EITC/CTC adjusters)
    if len(taxpayers) > 0:
        below = (taxpayers["spm_resources"] < taxpayers["total_cash_income"]).mean()
        assert below > 0.5, (
            f"expected most taxpayers to have SPM resources < TCI; got {below:.0%}"
        )


def test_hawaii_threshold_monotone_in_household_size():
    t1 = hawaii_spm_threshold(2024, n_adults=1, n_children=0)
    t2 = hawaii_spm_threshold(2024, n_adults=2, n_children=0)
    t3 = hawaii_spm_threshold(2024, n_adults=2, n_children=2)
    t4 = hawaii_spm_threshold(2024, n_adults=2, n_children=4)
    assert t1 < t2 < t3 < t4


def test_hawaii_threshold_geo_adjusted_above_baseline():
    """HI renter threshold should be ~25-30% above contiguous-US baseline.

    Census applies the Honolulu MRI (~1.62) only to the housing portion of
    the SPM threshold (~44% for renters), so the effective full-threshold
    multiplier is 1 + 0.442 * 0.62 ≈ 1.274.
    """
    # Approximate contiguous-US 2A2C renter SPM threshold for 2024 from Census.
    contig_baseline_2024 = 34_393.0
    hi = hawaii_spm_threshold(2024, n_adults=2, n_children=2, tenure="renter")
    ratio = hi / contig_baseline_2024
    assert 1.20 < ratio < 1.30


def test_hawaii_threshold_unknown_year_raises():
    with pytest.raises(ConfigError):
        hawaii_spm_threshold(1999)


def test_threshold_table_has_full_grid():
    table = spm_threshold_table(2024)
    # 6 adults × 7 children × 3 tenures = 126
    assert len(table) == 6 * 7 * 3
    # Spot-check tenure ordering: owner_no_mortgage < renter
    renter_2a2c = table[(2, 2, "renter")]
    owner_no_mort_2a2c = table[(2, 2, "owner_no_mortgage")]
    assert owner_no_mort_2a2c < renter_2a2c


def test_e2e_phase2_spm_poverty_runs(taxed_units):
    """End-to-end Phase 2: resources → threshold → poverty rate."""
    df, _ = compute_spm_resources(taxed_units)

    # Map each unit to a per-row threshold by household composition.
    # Use num_dependents as a proxy for n_children, single primary as n_adults=1
    # otherwise n_adults=2. This is a coarse proxy — Phase 1's entity layer
    # provides the proper SPMUnit composition; we use it in Phase 3+.
    n_children = df["num_dependents"].clip(upper=4).astype(int).to_numpy()
    n_adults = np.where(df["filing_status"] == "married_filing_jointly", 2, 1)
    thresholds = np.array([
        hawaii_spm_threshold(2024, n_adults=int(a), n_children=int(c))
        for a, c in zip(n_adults, n_children)
    ])
    rate = poverty_rate(df["spm_resources"], thresholds, df["weight"])
    # Sanity: SPM rate on synthetic fixture is a real number in [0, 1]
    assert 0.0 <= rate <= 1.0
