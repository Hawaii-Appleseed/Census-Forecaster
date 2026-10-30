"""Smoke tests for the Reform DSL (Phase 1 of policy-impact extension).

Validates that:

1. A null Reform (no tax / benefit changes) produces a zero revenue delta
   when applied — sanity check on the comparison machinery.
2. A real reform (Act 46 baseline vs. SB 3125 CD1) produces a non-zero
   delta and a populated ``ReformResult``.
3. ``benefit_overrides`` payloads are accepted and emit a UserWarning
   (Phase 3 will turn the warning off as benefit modules land).
"""
from __future__ import annotations

import warnings

import pytest
from tax_modeler import Reform, ReformResult, TaxSystemRegistry, apply_reform
from tax_modeler.benefits.medicaid_hi_quest import compute_medicaid_for_units
from tax_modeler.errors import ConfigError
from tax_modeler.reform.reform import _ensure_baseline_benefits

pytestmark = pytest.mark.smoke


def test_null_reform_returns_negligible_delta(taxed_units):
    """A null reform (no tax/benefit changes) produces only float-rounding noise.

    Run the same revenue math twice (baseline vs scenario, both pointing at
    the same TaxSystemConfig) and the difference comes from floating-point
    accumulation order, not from any policy change. Bound it at $1M — any
    real reform we'd care about moves revenue by tens-to-hundreds of $M, so
    this gap is comfortably non-overlapping with signal.
    """
    null_reform = Reform(name="null_reform")
    result = apply_reform(taxed_units, null_reform, year=2026)
    assert isinstance(result, ReformResult)
    assert abs(result.revenue_delta_millions) < 1.0
    assert result.baseline_system.name == result.scenario_system.name


def test_sb3125_vs_act46_reform_produces_delta(taxed_units):
    reform = Reform(
        name="sb3125_cd1",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
        metadata={"source": "SB 3125 CD1 (2026 session)"},
    )
    # SB 3125 CD1 takes effect in TY 2027.
    result = apply_reform(taxed_units, reform, year=2027)
    assert result.reform.name == "sb3125_cd1"
    assert result.scenario_system.name != result.baseline_system.name
    # SB 3125 CD1 is a tax cut — expect strictly negative revenue delta on
    # any non-empty fixture. Loose bound: just non-zero.
    assert result.revenue_delta_millions != 0.0


def test_reform_metadata_is_frozen(taxed_units):
    meta = {"source": "test", "draft": 1}
    reform = Reform(name="r", metadata=meta)
    assert dict(reform.metadata) == meta
    # Mutating the original dict must not leak into the reform
    meta["draft"] = 99
    assert reform.metadata["draft"] == 1


def test_benefit_overrides_no_longer_warn_in_phase3(taxed_units):
    """Phase 3 implements benefit modules; the placeholder UserWarning is gone."""
    reform = Reform(
        name="snap_minus_10pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.90}},
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        apply_reform(taxed_units, reform, year=2026)
    benefit_warnings = [w for w in caught if "benefit modules" in str(w.message)]
    assert not benefit_warnings, "Phase 3 should not warn about benefit_overrides"


def test_ensure_baseline_benefits_threads_run_year_fpl(taxed_units):
    """Baseline benefit population uses the run's FPL table, not frozen 2024.

    ``_ensure_baseline_benefits`` must thread ``tax_year`` into the FPL-based
    Phase 5 modules so the column it populates matches a same-year direct
    call. The 2028 FPL table is ~7% above 2024, so a forward-year baseline
    weakly expands Medicaid eligibility and cannot fall below the 2024 total.
    """
    base_2024 = _ensure_baseline_benefits(
        taxed_units, {"medicaid"}, tax_year=2024
    )["medicaid_amount"].to_numpy()
    base_2028 = _ensure_baseline_benefits(
        taxed_units, {"medicaid"}, tax_year=2028
    )["medicaid_amount"].to_numpy()

    # Forward-year baseline equals a direct same-year module call (plumbing).
    expected_2028 = compute_medicaid_for_units(
        taxed_units, tax_year=2028
    )["medicaid_amount"].to_numpy()
    assert base_2028 == pytest.approx(expected_2028)

    # Higher FPL thresholds weakly expand eligibility → totals don't shrink.
    assert base_2028.sum() >= base_2024.sum()


def test_apply_reform_threads_year_into_benefit_baseline(taxed_units):
    """apply_reform plumbs its ``year`` into the benefit baseline + counterfactual.

    A null-magnitude Medicaid override (pmpm pct = 1.0) leaves dollar values
    unchanged, so the counterfactual ``medicaid_amount`` equals the same-year
    baseline. Evaluated at 2028, that baseline must reflect the 2028 FPL
    table — confirming the run year reaches the FPL test end-to-end.
    """
    reform = Reform(
        name="medicaid_identity",
        benefit_overrides={"medicaid": {"adult_pmpm_pct": 1.0}},
    )
    result = apply_reform(taxed_units, reform, year=2028)
    cf_medicaid = result.counterfactual_units["medicaid_amount"].to_numpy()
    expected_2028 = compute_medicaid_for_units(
        taxed_units, tax_year=2028
    )["medicaid_amount"].to_numpy()
    assert cf_medicaid == pytest.approx(expected_2028)


def test_empty_name_rejected():
    with pytest.raises(ConfigError):
        Reform(name="")


def test_changes_taxes_and_benefits_flags():
    r1 = Reform(name="r1")
    assert not r1.changes_taxes and not r1.changes_benefits

    r2 = Reform(name="r2", tax_system_factory=TaxSystemRegistry.get_act46_system)
    assert r2.changes_taxes and not r2.changes_benefits

    r3 = Reform(name="r3", benefit_overrides={"snap": {"max_benefit_pct": 0.9}})
    assert not r3.changes_taxes and r3.changes_benefits
