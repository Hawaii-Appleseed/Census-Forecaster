"""Smoke tests for SNAP and SSI benefit modules (Phase 3).

Validates per-unit behavior on the synthetic fixture and Reform-DSL
overrides for SNAP / SSI / EITC / CTC.
"""
from __future__ import annotations

import pytest
from tax_modeler import (
    Reform,
    apply_reform,
    compute_snap_for_units,
    compute_spm_resources,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
    poverty_rate,
)
from tax_modeler.errors import ConfigError

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# SNAP
# ---------------------------------------------------------------------------


def test_snap_runs_on_taxed_units(taxed_units):
    df = compute_snap_for_units(taxed_units)
    assert "snap_amount" in df.columns
    assert (df["snap_amount"] >= 0).all()
    # On any realistic synthetic fixture, *some* low-income households qualify
    assert df["snap_amount"].sum() > 0, "expected at least one SNAP-eligible household"


def test_snap_high_income_zero(taxed_units):
    df = compute_snap_for_units(taxed_units)
    high_income = df[df["income"] > 200_000]
    if len(high_income) > 0:
        assert high_income["snap_amount"].sum() == 0, (
            "households with $200K+ income should not qualify for SNAP"
        )


def test_snap_max_benefit_pct_override(taxed_units):
    base = compute_snap_for_units(taxed_units)
    cut = compute_snap_for_units(taxed_units, overrides={"max_benefit_pct": 0.5})
    base_total = base["snap_amount"].sum()
    cut_total = cut["snap_amount"].sum()
    if base_total == 0:
        pytest.skip("no SNAP-eligible households on this fixture")
    # 50% reduction in max benefit ≠ 50% reduction in total (because the
    # benefit_reduction_rate × net_income still reduces benefits regardless),
    # but the cut total should be strictly smaller and at most ~50% of base.
    assert cut_total < base_total
    assert cut_total <= base_total * 0.6


def test_snap_unknown_override_raises(taxed_units):
    with pytest.raises(ConfigError):
        compute_snap_for_units(taxed_units, overrides={"not_a_real_knob": 1.0})


# ---------------------------------------------------------------------------
# SSI
# ---------------------------------------------------------------------------


def test_ssi_runs_on_taxed_units(taxed_units):
    df = compute_ssi_for_units(taxed_units)
    assert "ssi_amount" in df.columns
    assert (df["ssi_amount"] >= 0).all()


def test_ssi_only_aged_qualify(taxed_units):
    df = compute_ssi_for_units(taxed_units)
    if "primary_agep" not in df.columns:
        pytest.skip("synthetic fixture lacks primary_agep")
    young = df[df["primary_agep"] < 65]
    assert young["ssi_amount"].sum() == 0, (
        "only units with primary age >= 65 should receive SSI in this heuristic"
    )


def test_ssi_hi_supplement_requires_federal_ssi(taxed_units):
    df = compute_ssi_for_units(taxed_units)
    df = compute_ssi_hi_supplement_for_units(df)
    # Anyone with positive HI supplement also has positive federal SSI
    has_supp = df[df["ssi_hi_amount"] > 0]
    assert (has_supp["ssi_amount"] > 0).all()


def test_ssi_federal_payment_pct_override(taxed_units):
    base = compute_ssi_for_units(taxed_units)
    cut = compute_ssi_for_units(taxed_units, overrides={"federal_payment_pct": 0.5})
    base_total = base["ssi_amount"].sum()
    if base_total == 0:
        pytest.skip("no SSI-eligible units on this fixture")
    assert cut["ssi_amount"].sum() == pytest.approx(base_total * 0.5, rel=1e-6)


# ---------------------------------------------------------------------------
# Reform DSL: benefit overrides flow end-to-end
# ---------------------------------------------------------------------------


def test_reform_snap_cut_flows_through(taxed_units):
    reform = Reform(
        name="snap_minus_10pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.90}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    assert result.counterfactual_units is not None
    assert "snap" in result.benefit_flow_deltas_millions
    # 10% cut → strictly negative delta unless no one was eligible
    delta = result.benefit_flow_deltas_millions["snap"]
    if delta == 0:
        pytest.skip("synthetic fixture had no SNAP-eligible households")
    assert delta < 0


def test_reform_eitc_expansion_flows_through(taxed_units):
    reform = Reform(
        name="eitc_doubled",
        benefit_overrides={"eitc": {"amount_pct": 2.0}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    cf = result.counterfactual_units
    assert cf is not None
    base_eitc = float((taxed_units["eitc_amount"] * taxed_units["weight"]).sum())
    cf_eitc = float((cf["eitc_amount"] * cf["weight"]).sum())
    if base_eitc == 0:
        pytest.skip("no EITC recipients on the fixture")
    # Counterfactual should be ~2x baseline
    assert cf_eitc == pytest.approx(2.0 * base_eitc, rel=1e-6)
    # Recorded delta is the dollar change
    assert result.benefit_flow_deltas_millions["eitc"] == pytest.approx(
        (cf_eitc - base_eitc) / 1e6, rel=1e-3
    )


def test_reform_ctc_expansion_flows_through(taxed_units):
    reform = Reform(
        name="ctc_plus_20pct",
        benefit_overrides={"ctc": {"amount_pct": 1.20}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    cf = result.counterfactual_units
    if cf is None or "ctc_total" not in cf.columns:
        pytest.skip("CTC column unavailable on fixture")
    base_total = float((taxed_units["ctc_total"] * taxed_units["weight"]).sum())
    cf_total = float((cf["ctc_total"] * cf["weight"]).sum())
    if base_total == 0:
        pytest.skip("no CTC recipients on the fixture")
    assert cf_total == pytest.approx(1.20 * base_total, rel=1e-6)


def test_reform_combined_snap_ssi(taxed_units):
    reform = Reform(
        name="combined_safety_net_cut",
        benefit_overrides={
            "snap": {"max_benefit_pct": 0.90},
            "ssi": {"federal_payment_pct": 0.95},
        },
    )
    result = apply_reform(taxed_units, reform, year=2026)
    deltas = result.benefit_flow_deltas_millions
    assert set(deltas) >= {"snap", "ssi"}


def test_reform_unknown_program_raises(taxed_units):
    reform = Reform(
        name="bad_reform",
        benefit_overrides={"not_a_real_program": {"foo": 1.0}},
    )
    with pytest.raises(ConfigError):
        apply_reform(taxed_units, reform, year=2026)


# ---------------------------------------------------------------------------
# End-to-end Phase 3: SNAP cut → SPM poverty rate change
# ---------------------------------------------------------------------------


def test_e2e_snap_cut_raises_poverty(taxed_units):
    """The user's literal question: does a SNAP cut raise poverty?"""
    # Baseline: with full SNAP
    base_with_snap = compute_snap_for_units(taxed_units)
    base_with_snap = compute_ssi_for_units(base_with_snap)
    base_with_snap = compute_ssi_hi_supplement_for_units(base_with_snap)
    base_resources, _ = compute_spm_resources(base_with_snap)
    # Counterfactual: 50% SNAP cut
    cut_units = compute_snap_for_units(taxed_units, overrides={"max_benefit_pct": 0.5})
    cut_units = compute_ssi_for_units(cut_units)
    cut_units = compute_ssi_hi_supplement_for_units(cut_units)
    cut_resources, _ = compute_spm_resources(cut_units)

    # Use a single threshold for both (2024 HI 2A2C)
    from tax_modeler import hawaii_spm_threshold
    threshold = hawaii_spm_threshold(2024)

    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )
    cut_rate = poverty_rate(
        cut_resources["spm_resources"], threshold, cut_resources["weight"]
    )
    # SNAP cuts can only weakly raise poverty (some households were already
    # below threshold, others moved). On the synthetic fixture some SNAP must
    # be present for the assertion to be meaningful — skip otherwise.
    if base_with_snap["snap_amount"].sum() == 0:
        pytest.skip("no SNAP-eligible households on fixture")
    assert cut_rate >= base_rate
