"""Phase 6 policy-level smoke test: SNAP cut → poverty rate increases.

Mirrors the removed ``forecast_snap_10pct_cut.py`` demo, but in <1s on the
synthetic fixture and with assertions on the qualitative shape.
"""
from __future__ import annotations

import pytest

from tax_modeler import (
    Reform,
    apply_reform,
    compute_hi_eitc_for_units,
    compute_hi_food_excise_for_units,
    compute_hi_renters_for_units,
    compute_snap_for_units,
    compute_spm_resources,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
    hawaii_spm_threshold,
    poverty_rate,
)


pytestmark = pytest.mark.smoke


def _populate_benefits(units):
    df = compute_snap_for_units(units)
    df = compute_ssi_for_units(df)
    df = compute_ssi_hi_supplement_for_units(df)
    df = compute_hi_eitc_for_units(df)
    df = compute_hi_food_excise_for_units(df)
    df = compute_hi_renters_for_units(df)
    return df


def test_snap_cut_reduces_q1_resources(taxed_units):
    units = _populate_benefits(taxed_units)
    if units["snap_amount"].sum() == 0:
        pytest.skip("no SNAP-eligible households on fixture")

    reform = Reform(
        name="snap_minus_10pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.90}},
    )
    result = apply_reform(units, reform, year=2026)

    # Negative SNAP flow delta
    assert result.benefit_flow_deltas_millions["snap"] < 0

    # SPM resources lower under cut
    base_resources, _ = compute_spm_resources(units)
    cf_resources, _ = compute_spm_resources(result.counterfactual_units)
    assert (cf_resources["spm_resources"].sum()
            < base_resources["spm_resources"].sum())


def test_snap_cut_weakly_raises_poverty(taxed_units):
    units = _populate_benefits(taxed_units)
    if units["snap_amount"].sum() == 0:
        pytest.skip("no SNAP-eligible households on fixture")

    reform = Reform(
        name="snap_minus_50pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.50}},
    )
    result = apply_reform(units, reform, year=2026)

    threshold = hawaii_spm_threshold(2024)
    base_resources, _ = compute_spm_resources(units)
    cf_resources, _ = compute_spm_resources(result.counterfactual_units)
    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )
    cf_rate = poverty_rate(
        cf_resources["spm_resources"], threshold, cf_resources["weight"]
    )
    # Cuts can only weakly raise poverty
    assert cf_rate >= base_rate


def test_snap_full_elimination_largest_impact(taxed_units):
    """A 100% SNAP elimination produces a larger SPM-resource delta than 10% cut."""
    units = _populate_benefits(taxed_units)
    if units["snap_amount"].sum() == 0:
        pytest.skip("no SNAP-eligible households on fixture")

    small = apply_reform(units, Reform(
        name="snap_minus_10pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.90}}
    ), year=2026)
    huge = apply_reform(units, Reform(
        name="snap_eliminated",
        benefit_overrides={"snap": {"max_benefit_pct": 0.0}}
    ), year=2026)
    assert (abs(huge.benefit_flow_deltas_millions["snap"])
            > abs(small.benefit_flow_deltas_millions["snap"]))
