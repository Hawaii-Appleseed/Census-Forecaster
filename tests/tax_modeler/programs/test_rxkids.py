"""Tests for tax_modeler.programs.rxkids_hi."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
from tax_modeler.errors import ConfigError
from tax_modeler.poverty.impact import compute_poverty_impact
from tax_modeler.poverty.spm import compute_spm_resources
from tax_modeler.programs import (
    RxKidsHIParams,
    compute_rxkids_for_units,
    hawaii_rxkids_parameters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_units(rows: list[dict]) -> pd.DataFrame:
    """Minimal tax-unit DataFrame with the columns the impact module
    requires. Mirrors the helper in tests/tax_modeler/poverty/test_impact.py.
    """
    defaults = {
        "filing_status": "head_of_household",
        "num_dependents": 0,
        "num_qualifying_children": 0,
        "total_cash_income": 15_000.0,
        "earned_income": 15_000.0,
        "income": 15_000.0,
        "weight": 100.0,
        "hi_tax_liability": 0.0,
        "eitc_amount": 0.0,
        "ctc_total": 0.0,
        "ctc_refundable": 0.0,
        "hi_eitc_amount": 0.0,
        "tenure": "renter",
        "county": "Honolulu",
        "house_district": 1,
        "senate_district": 1,
        "PUMA": 301,
    }
    out = pd.DataFrame([{**defaults, **r} for r in rows])
    out = arpa_ctc_for_tax_units(out)
    return out


# ---------------------------------------------------------------------------
# Unit-level behavior of compute_rxkids_for_units
# ---------------------------------------------------------------------------


def test_rxkids_zero_for_high_income():
    """A unit with income > income_fpl_cap × FPL → rxkids_amount == 0."""
    # Universal default cap = 10.0 × FPL. HI 2024 FPL(3) = $28,590.
    # 10 × $28,590 = $285,900. Use $400k to safely clear universal cap.
    units = _make_units([
        {
            "filing_status": "head_of_household",
            "num_dependents": 2,
            "num_qualifying_children": 2,
            "income": 400_000.0,
            "total_cash_income": 400_000.0,
            "earned_income": 400_000.0,
        },
    ])
    out = compute_rxkids_for_units(units, tax_year=2024)
    assert out["rxkids_amount"].iloc[0] == pytest.approx(0.0)


def test_rxkids_postnatal_scales_with_children():
    """More dependents → larger postnatal rxkids_amount (linear)."""
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 15_000.0},
        {"num_dependents": 4, "num_qualifying_children": 4, "income": 15_000.0},
    ])
    out = compute_rxkids_for_units(units, tax_year=2024)
    one_kid = out["rxkids_amount"].iloc[0]
    four_kids = out["rxkids_amount"].iloc[1]
    assert four_kids > one_kid > 0
    # Postnatal is linear in n_kids (the FPL cap rises with hh_size so
    # both units remain eligible). Expect ~4× the lift.
    assert four_kids == pytest.approx(4.0 * one_kid, rel=1e-9)


def test_rxkids_birth_count_col_overrides_proxy():
    """When birth_count_col is supplied, it drives births (not the n_dep proxy)."""
    # Two units, same num_dependents but different OBSERVED birth counts.
    units = _make_units([
        {"num_dependents": 3, "num_qualifying_children": 3, "income": 15_000.0,
         "observed_births": 0},
        {"num_dependents": 3, "num_qualifying_children": 3, "income": 15_000.0,
         "observed_births": 1},
    ])
    out = compute_rxkids_for_units(
        units, tax_year=2024, birth_count_col="observed_births",
    )
    # Unit with 0 observed births gets nothing despite having dependents;
    # the proxy (n_dep × rate) would have paid it.
    assert out["rxkids_amount"].iloc[0] == pytest.approx(0.0)
    assert out["rxkids_amount"].iloc[1] > 0.0
    # Amount uses the observed count (1 birth), not n_dep=3.
    p = hawaii_rxkids_parameters()
    expected_post = 1 * p.postnatal_monthly_per_child * p.postnatal_months * p.takeup_rate
    assert out["rxkids_postnatal_amount"].iloc[1] == pytest.approx(expected_post)


def test_rxkids_birth_count_col_absent_falls_back_to_proxy():
    """A missing birth_count_col silently falls back to the n_dep proxy."""
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 15_000.0},
    ])
    proxy = compute_rxkids_for_units(units, tax_year=2024)
    # Naming a column that does not exist must reproduce the proxy result.
    fallback = compute_rxkids_for_units(
        units, tax_year=2024, birth_count_col="not_a_column",
    )
    assert fallback["rxkids_amount"].iloc[0] == pytest.approx(
        proxy["rxkids_amount"].iloc[0]
    )
    assert proxy["rxkids_amount"].iloc[0] > 0.0


def test_rxkids_is_nontaxable_in_spm():
    """With is_taxable=False, rxkids_amount lands in spm_resources but
    NOT in total_cash_income.
    """
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 12_000.0},
    ])
    out = compute_rxkids_for_units(units, tax_year=2024)
    rxk = float(out["rxkids_amount"].iloc[0])
    assert rxk > 0, "fixture should produce a non-zero RxKids amount"

    # total_cash_income unchanged
    assert out["total_cash_income"].iloc[0] == pytest.approx(
        units["total_cash_income"].iloc[0]
    )

    # SPM resources WITHOUT rxkids
    no_rxk, _ = compute_spm_resources(out, rxkids_col=None)
    # SPM resources WITH rxkids
    with_rxk, _ = compute_spm_resources(out, rxkids_col="rxkids_amount")
    delta = float(with_rxk["spm_resources"].iloc[0] - no_rxk["spm_resources"].iloc[0])
    assert delta == pytest.approx(rxk, rel=1e-9)


def test_rxkids_takeup_rate_monotone():
    """Higher takeup_rate → larger rxkids_amount (strictly, when > 0)."""
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 12_000.0},
    ])
    low = compute_rxkids_for_units(
        units, tax_year=2024,
        params=RxKidsHIParams(takeup_rate=0.50),
    )
    high = compute_rxkids_for_units(
        units, tax_year=2024,
        params=RxKidsHIParams(takeup_rate=1.00),
    )
    assert float(low["rxkids_amount"].iloc[0]) > 0
    assert float(high["rxkids_amount"].iloc[0]) == pytest.approx(
        2.0 * float(low["rxkids_amount"].iloc[0])
    )


# ---------------------------------------------------------------------------
# Integration: poverty impact + by_household_type
# ---------------------------------------------------------------------------


def test_rxkids_hoh_poverty_rate_lower_with_program():
    """HoH baseline poverty rate decreases when --apply-rxkids ON."""
    # Build a cohort of HoH filers near the 2024 HI renter SPM threshold.
    # 1A+2C Honolulu renter threshold ≈ $36.4k. Incomes ~$28k-$33k so SPM
    # resources + credits sit just below threshold.
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(30):
        n_kids = int(rng.integers(2, 4))  # 2-3 kids
        income = float(rng.uniform(28_000, 33_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "income": income,
            "total_cash_income": income,
            "earned_income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(2_000, 5_000)),
            "ctc_refundable": n_kids * 1_500.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
        })
    units = _make_units(rows)

    # Inflate the rxkids payment so it has a clear-the-threshold lift on
    # this synthetic cohort (the production defaults are calibrated to
    # cost ranges, not to lift this particular cohort).
    units = compute_rxkids_for_units(
        units, tax_year=2024,
        params=RxKidsHIParams(
            postnatal_monthly_per_child=600.0,
            child_under_age_share=1.0,
            takeup_rate=1.0,
        ),
    )

    result = compute_poverty_impact(
        units, tax_year=2024, scenarios=("rxkids_hi",),
    )
    s = result.by_state.iloc[0]
    # rxkids_hi reduces poverty rate among HoH filers.
    assert s["poverty_rate_rxkids_hi_hoh"] <= s["poverty_rate_hoh_baseline"] + 1e-9
    # Strict decrease on this synthetic cohort.
    assert s["poverty_rate_rxkids_hi_hoh"] < s["poverty_rate_hoh_baseline"]


def test_rxkids_lift_positive_for_synthetic_eligible_cohort():
    """At least some persons lifted for HoH families near the poverty threshold.

    TY2024 SPM threshold for 1A+2C Honolulu renter ≈ $36.4k. Units are set
    near the threshold so an $8k-$12k rxkids payment bridges the gap.
    """
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(30):
        n_kids = int(rng.integers(1, 3))  # 1-2 kids
        income = float(rng.uniform(25_000, 32_000))
        rows.append({
            "filing_status": "head_of_household",
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "income": income,
            "total_cash_income": income,
            "earned_income": income,
            "weight": 100.0,
            "eitc_amount": float(rng.uniform(1_500, 3_000)),
            "ctc_refundable": n_kids * 1_500.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(500, 1_200)),
        })
    units = _make_units(rows)
    # postnatal $1k/mo × 6 mo × all-children age-share = $6k–$12k per unit,
    # enough to bridge the remaining gap for units near the threshold.
    units = compute_rxkids_for_units(
        units, tax_year=2024,
        params=RxKidsHIParams(
            postnatal_monthly_per_child=1_000.0,
            child_under_age_share=1.0,
            takeup_rate=1.0,
        ),
    )
    result = compute_poverty_impact(
        units, tax_year=2024, scenarios=("rxkids_hi",),
    )
    s = result.by_state.iloc[0]
    assert s["persons_lifted_rxkids_hi"] > 0
    # by_household_type frame populated.
    assert "filing_status" in result.by_household_type.columns
    assert "head_of_household" in set(result.by_household_type["filing_status"])


def test_rxkids_singletype_baseline_unchanged_without_scenario():
    """If 'rxkids_hi' is not in scenarios, baseline + other scenarios
    should be identical to a run that has no rxkids_amount column at all.
    """
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 12_000.0},
    ])
    units_with_rxk = compute_rxkids_for_units(units, tax_year=2024)

    r_no_col = compute_poverty_impact(
        units, tax_year=2024, scenarios=("no_eitc",),
    ).by_state.iloc[0]
    r_with_col = compute_poverty_impact(
        units_with_rxk, tax_year=2024, scenarios=("no_eitc",),
    ).by_state.iloc[0]
    assert r_no_col["poverty_rate_baseline"] == pytest.approx(
        r_with_col["poverty_rate_baseline"]
    )
    assert r_no_col["persons_lifted_no_eitc"] == pytest.approx(
        r_with_col["persons_lifted_no_eitc"]
    )


def test_rxkids_unknown_override_raises():
    units = _make_units([{"num_dependents": 1, "num_qualifying_children": 1}])
    with pytest.raises(ConfigError):
        compute_rxkids_for_units(units, overrides={"foo_bar": 1.0})


def test_rxkids_takeup_out_of_range_raises():
    units = _make_units([{"num_dependents": 1, "num_qualifying_children": 1}])
    with pytest.raises(ConfigError):
        compute_rxkids_for_units(units, params=RxKidsHIParams(takeup_rate=1.5))


def test_rxkids_defaults_match_hawaii_factory():
    """hawaii_rxkids_parameters() returns the canonical default set."""
    p = hawaii_rxkids_parameters()
    # Statutory eligibility variant: $1,500 one-time prenatal,
    # $500/mo × 6 months postnatal, 300% FPL income cap (+ Medicaid OR
    # clause), 196% FPL Medicaid pregnancy pathway for the prenatal arm.
    assert p.prenatal_monthly == pytest.approx(1500.0)
    assert p.prenatal_months == 1
    assert p.postnatal_monthly_per_child == pytest.approx(500.0)
    assert p.postnatal_months == 6
    assert p.income_fpl_cap == pytest.approx(3.00)
    assert p.pregnant_fpl_cap == pytest.approx(1.96)
    assert p.takeup_rate == pytest.approx(0.90)
    assert p.prenatal_takeup_rate is None  # library default: arms uniform
    assert p.is_taxable is False


def test_rxkids_arm_specific_takeup():
    """A lower prenatal_takeup_rate scales only the prenatal arm; the postnatal
    arm stays on takeup_rate. (Mothers enroll prenatally at a lower rate than
    newborns are enrolled — Flint ~90% vs ~98%.)"""
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 30_000.0,
         "medicaid_receives": False},
    ])
    uniform = compute_rxkids_for_units(units, tax_year=2024)  # both arms 0.90
    split = compute_rxkids_for_units(
        units, tax_year=2024,
        params=RxKidsHIParams(takeup_rate=0.90, prenatal_takeup_rate=0.828),
    )
    # Postnatal arm unchanged; prenatal arm scaled by 0.828/0.90 = 0.92.
    assert float(split["rxkids_postnatal_amount"].iloc[0]) == pytest.approx(
        float(uniform["rxkids_postnatal_amount"].iloc[0]))
    assert float(split["rxkids_prenatal_amount"].iloc[0]) == pytest.approx(
        float(uniform["rxkids_prenatal_amount"].iloc[0]) * (0.828 / 0.90))


# ---------------------------------------------------------------------------
# Statutory eligibility: Medicaid (clause 1) OR 300% FPL incl. unborn (clause 2)
# ---------------------------------------------------------------------------
#
# 2024 HI HHS FPL anchors used below:
#   FPL(1)=16,770  FPL(2)=22,680  FPL(3)=28,590


def test_rxkids_clause2_income_under_300pct_eligible():
    """Clause 2: a postnatal unit at ~220% FPL (no Medicaid) is eligible;
    the same unit at ~350% FPL is not."""
    # HoH + 1 dependent → postnatal family size 2, FPL(2)=22,680.
    eligible = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1,
         "income": 50_000.0, "medicaid_receives": False},  # 50k/22.68k ≈ 2.20
    ])
    ineligible = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1,
         "income": 80_000.0, "medicaid_receives": False},  # 80k/22.68k ≈ 3.53
    ])
    out_e = compute_rxkids_for_units(eligible, tax_year=2024)
    out_i = compute_rxkids_for_units(ineligible, tax_year=2024)
    assert float(out_e["rxkids_amount"].iloc[0]) > 0
    assert float(out_i["rxkids_amount"].iloc[0]) == pytest.approx(0.0)


def test_rxkids_clause1_medicaid_overrides_income_test():
    """Clause 1: a unit above 300% FPL still qualifies if medicaid_receives
    is True; the identical unit without Medicaid does not (proves the OR)."""
    base_row = {"num_dependents": 1, "num_qualifying_children": 1,
                "income": 80_000.0}  # ≈ 353% of FPL(2) — fails clause 2
    on_medicaid = _make_units([{**base_row, "medicaid_receives": True}])
    off_medicaid = _make_units([{**base_row, "medicaid_receives": False}])
    out_on = compute_rxkids_for_units(on_medicaid, tax_year=2024)
    out_off = compute_rxkids_for_units(off_medicaid, tax_year=2024)
    assert float(out_on["rxkids_amount"].iloc[0]) > 0
    assert float(out_off["rxkids_amount"].iloc[0]) == pytest.approx(0.0)


def test_rxkids_married_and_repeat_births_covered():
    """Both arms are birth-driven, so a MARRIED family (MFJ) with a child —
    previously excluded from the prenatal arm — now draws a prenatal payment
    too. A childless filer (no birth event) draws nothing."""
    units = _make_units([
        # Married couple with their (first) newborn dependent, income-eligible.
        {"filing_status": "married_filing_jointly", "num_dependents": 1,
         "num_qualifying_children": 1, "income": 50_000.0,
         "medicaid_receives": False},
        # Childless single filer → no birth event → no payment.
        {"filing_status": "single", "num_dependents": 0,
         "num_qualifying_children": 0, "income": 30_000.0,
         "medicaid_receives": False},
    ])
    out = compute_rxkids_for_units(units, tax_year=2024)
    mfj = out.iloc[0]
    assert float(mfj["rxkids_prenatal_amount"]) > 0   # married first birth covered
    assert float(mfj["rxkids_postnatal_amount"]) > 0
    assert float(out.iloc[1]["rxkids_amount"]) == pytest.approx(0.0)


def test_rxkids_prenatal_is_half_postnatal_per_birth():
    """Each eligible birth draws one prenatal ($1,500) and one postnatal
    ($3,000) payment, so the prenatal arm is exactly half the postnatal arm."""
    units = _make_units([
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 30_000.0,
         "medicaid_receives": False},
    ])
    out = compute_rxkids_for_units(units, tax_year=2024)
    pre = float(out["rxkids_prenatal_amount"].iloc[0])
    post = float(out["rxkids_postnatal_amount"].iloc[0])
    assert pre > 0 and post > 0
    assert pre == pytest.approx(0.5 * post)


def test_rxkids_medicaid_pregnancy_pathway_binds_below_196():
    """Clause 1 pregnancy pathway (196% FPL): with the income cap set below
    196%, a family at ~150% FPL still qualifies for the PRENATAL arm via the
    Medicaid pregnancy pathway, but not the postnatal arm."""
    # HoH + 1 dep → size 2, FPL(2)=22,680. income=34,000 ≈ 1.50× FPL.
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 34_000.0,
         "medicaid_receives": False},
    ])
    # income_fpl_cap below the family's ratio so clause 2 fails, but the
    # 196% pregnancy pathway (clause 1) still covers the prenatal arm.
    out = compute_rxkids_for_units(
        units, tax_year=2024, params=RxKidsHIParams(income_fpl_cap=1.0),
    )
    assert float(out["rxkids_prenatal_amount"].iloc[0]) > 0
    assert float(out["rxkids_postnatal_amount"].iloc[0]) == pytest.approx(0.0)


def test_magi_proxy_adds_back_nontaxable_social_security():
    """The forecast's MAGI proxy counts 100% of Social Security: it adds the
    non-taxable 15% (ssp_full − ssp) back onto the model's gross income."""
    import sys
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parents[3] / "analyses"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import forecast_rxkids_2028 as fc

    df = pd.DataFrame({
        "income": [50_000.0],          # already includes 0.85 × SS = 8,500
        "primary_ssp": [8_500.0],      # 85% taxable portion
        "primary_ssp_full": [10_000.0],
        "secondary_ssp": [0.0],
        "secondary_ssp_full": [0.0],
    })
    magi = fc._magi_proxy(df)
    # MAGI adds back the non-taxable 15% (1,500) → 51,500.
    assert float(magi.iloc[0]) == pytest.approx(51_500.0)


def test_rxkids_family_grain_income_test():
    """When family-grain columns are supplied, the FPL test uses household
    income/size, not the tax unit's. A unit income-eligible at its own small
    tax-unit size is excluded once the larger household income is tested."""
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 40_000.0,
         "medicaid_receives": False, "fam_inc": 120_000.0, "fam_size": 3},
    ])
    # Tax-unit grain: 40k / FPL(2)=22,680 ≈ 1.76× → eligible.
    tu = compute_rxkids_for_units(units, tax_year=2024)
    assert float(tu["rxkids_amount"].iloc[0]) > 0
    # Family grain: 120k / FPL(3)=28,590 ≈ 4.2× → ineligible.
    fam = compute_rxkids_for_units(
        units, tax_year=2024,
        family_income_col="fam_inc", family_size_col="fam_size",
    )
    assert float(fam["rxkids_amount"].iloc[0]) == pytest.approx(0.0)


def test_rxkids_missing_medicaid_column_falls_back_to_clause2(caplog):
    """If medicaid_receives is absent, the function applies clause 2 only
    and warns. Income-eligible units still qualify; income-ineligible ones
    cannot sneak in via the (unavailable) Medicaid clause."""
    import logging

    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 50_000.0},
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 90_000.0},
    ])
    assert "medicaid_receives" not in units.columns
    with caplog.at_level(logging.WARNING):
        out = compute_rxkids_for_units(units, tax_year=2024)
    assert any("medicaid_receives" in r.message for r in caplog.records)
    assert float(out["rxkids_amount"].iloc[0]) > 0       # clause 2 still works
    assert float(out["rxkids_amount"].iloc[1]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Medicaid-clause vs income-only-clause split (forecast_rxkids_2028._apply_rxkids)
# ---------------------------------------------------------------------------


def _forecast_module():
    """Import the analyses/ forecast script (mirrors the magi-proxy test)."""
    import sys
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parents[3] / "analyses"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import forecast_rxkids_2028 as fc
    return fc


def test_rxkids_clause_split_partitions_amount_exactly():
    """rxkids_medicaid_amount + rxkids_income_only_amount must equal
    rxkids_amount exactly, for every unit, since it's a mutually-exclusive
    partition by medicaid_receives — not an independent re-derivation."""
    fc = _forecast_module()
    units = _make_units([
        # Medicaid-eligible (well under 138% FPL adult pathway).
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 10_000.0},
        # Income-eligible under a permissive override but NOT Medicaid
        # (forced medicaid_receives=False before compute_medicaid_for_units
        # would even run — here we bypass that by testing at high income
        # under an effectively-universal RxKids gate).
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 200_000.0},
        # Ineligible for either.
        {"num_dependents": 0, "num_qualifying_children": 0, "income": 500_000.0},
    ])
    out = fc._apply_rxkids(units, tax_year=2024, overrides={"income_fpl_cap": 100.0})
    combined = out["rxkids_amount"].to_numpy()
    split_sum = (
        out["rxkids_medicaid_amount"] + out["rxkids_income_only_amount"]
    ).to_numpy()
    assert split_sum == pytest.approx(combined)
    post_combined = out["rxkids_postnatal_amount"].to_numpy()
    post_split_sum = (
        out["rxkids_postnatal_medicaid_amount"]
        + out["rxkids_postnatal_income_only_amount"]
    ).to_numpy()
    assert post_split_sum == pytest.approx(post_combined)
    # The very-high-income, no-dependents unit clears neither clause.
    assert float(out["rxkids_amount"].iloc[2]) == pytest.approx(0.0)


def test_rxkids_clause_split_is_mutually_exclusive_per_unit():
    """A unit's benefit lands entirely in ONE of the two split columns —
    never split across both — since eligibility for a given unit is
    determined by a single medicaid_receives boolean."""
    fc = _forecast_module()
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 10_000.0},
        {"num_dependents": 2, "num_qualifying_children": 2, "income": 45_000.0},
    ])
    out = fc._apply_rxkids(units, tax_year=2024, overrides={"income_fpl_cap": 100.0})
    for i in range(len(out)):
        med = float(out["rxkids_medicaid_amount"].iloc[i])
        inc = float(out["rxkids_income_only_amount"].iloc[i])
        assert med == pytest.approx(0.0) or inc == pytest.approx(0.0), (
            "unit should not have a nonzero amount in both clause columns"
        )


def test_rxkids_statutory_default_is_entirely_medicaid_clause():
    """At the DEFAULT statutory parameters (income_fpl_cap=3.00), Hawaii's
    Medicaid children/CHIP pathway (child_fpl_cap=3.13, see
    tax_modeler.benefits.medicaid_hi_quest.MedicaidParameters) is WIDER than
    RxKids's own 300% FPL income test. Every birth-driving family has >=1
    dependent (a birth is itself a dependent), so any family that clears the
    300% income clause is, by construction, also under 313% and therefore
    already medicaid_receives=True via the children pathway.

    Net effect: for the statutory default, clause 2 (income-only) currently
    contributes ZERO additional eligible births beyond clause 1 (Medicaid) —
    confirmed against the real PUMS run (2026-08-18), which reports
    eligible_families_income_only == 0 and births_served_income_only == 0.
    This is a structural fact of the current parameter configuration, not
    noise — if either FPL cap changes, re-verify this still holds before
    trusting the split as a sanity check.
    """
    fc = _forecast_module()
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 10_000.0},
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 55_000.0},
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 65_000.0},  # near the 300% edge
    ])
    out = fc._apply_rxkids(units, tax_year=2024)  # default income_fpl_cap=3.00
    eligible = out["rxkids_amount"] > 0
    assert eligible.any()
    assert (out.loc[eligible, "rxkids_income_only_amount"] == 0).all()
    assert (out.loc[eligible, "rxkids_medicaid_amount"] > 0).all()


def test_rxkids_universal_scenario_produces_a_real_income_only_split():
    """Under the (near-)universal design, the income-only clause DOES pick up
    real families — the higher the income cap, the more non-Medicaid
    families it reaches. This is the contrast case for the statutory-default
    test above: the split isn't dead code, it's just structurally empty at
    the statutory default's specific FPL caps."""
    fc = _forecast_module()
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 10_000.0},   # Medicaid
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 200_000.0},  # income-only, universal
    ])
    out = fc._apply_rxkids(units, tax_year=2024, overrides={"income_fpl_cap": 100.0})
    assert float(out["rxkids_medicaid_amount"].iloc[0]) > 0
    assert float(out["rxkids_income_only_amount"].iloc[0]) == pytest.approx(0.0)
    assert float(out["rxkids_medicaid_amount"].iloc[1]) == pytest.approx(0.0)
    assert float(out["rxkids_income_only_amount"].iloc[1]) > 0


# ---------------------------------------------------------------------------
# Forecast scenarios: universal eligibility + postnatal-duration variants
# (analyses/forecast_rxkids_2028.py prices statutory_6mo / universal_6mo / universal_12mo)
# ---------------------------------------------------------------------------


def test_rxkids_universal_costs_at_least_statutory():
    """Universal eligibility (income_fpl_cap ≈ ∞) reaches every birth family, so
    weighted cost is ≥ the statutory (≤300% FPL OR Medicaid) gate — strictly
    greater whenever a mid/high-income birth family exists on the frame."""
    units = _make_units([
        # ~1.3× FPL(2): income-eligible under BOTH gates.
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 30_000.0,
         "medicaid_receives": False, "weight": 100.0},
        # ~3.5× FPL(2): only the universal gate reaches this family.
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 80_000.0,
         "medicaid_receives": False, "weight": 100.0},
    ])
    statutory = compute_rxkids_for_units(
        units, tax_year=2024, overrides={"income_fpl_cap": 3.00})
    universal = compute_rxkids_for_units(
        units, tax_year=2024, overrides={"income_fpl_cap": 100.0})

    def wsum(df):
        return float((df["rxkids_amount"] * df["weight"]).fillna(0).sum())

    assert wsum(universal) > wsum(statutory) > 0
    # The mid-income family is excluded by the statutory gate, included universally.
    assert float(statutory["rxkids_amount"].iloc[1]) == pytest.approx(0.0)
    assert float(universal["rxkids_amount"].iloc[1]) > 0.0


def test_rxkids_12mo_doubles_postnatal_prenatal_unchanged():
    """The +6-month design doubles the postnatal arm (linear in months) and
    leaves the one-time prenatal arm unchanged."""
    units = _make_units([
        {"num_dependents": 1, "num_qualifying_children": 1, "income": 30_000.0,
         "medicaid_receives": False},
    ])
    six = compute_rxkids_for_units(
        units, tax_year=2024,
        overrides={"income_fpl_cap": 100.0, "postnatal_months": 6})
    twelve = compute_rxkids_for_units(
        units, tax_year=2024,
        overrides={"income_fpl_cap": 100.0, "postnatal_months": 12})
    assert float(twelve["rxkids_postnatal_amount"].iloc[0]) == pytest.approx(
        2.0 * float(six["rxkids_postnatal_amount"].iloc[0]))
    assert float(twelve["rxkids_prenatal_amount"].iloc[0]) == pytest.approx(
        float(six["rxkids_prenatal_amount"].iloc[0]))


def test_forecast_scenario_specs():
    """The shipped scenario panel encodes the intended policy levers."""
    fc = _forecast_module()
    by = fc.SCENARIO_BY_KEY
    assert by["statutory_6mo"]["overrides"] == {"income_fpl_cap": 3.00, "postnatal_months": 6}
    assert by["universal_6mo"]["overrides"]["income_fpl_cap"] == fc.UNIVERSAL_FPL_CAP
    assert by["universal_6mo"]["overrides"]["postnatal_months"] == 6
    assert by["universal_12mo"]["overrides"]["income_fpl_cap"] == fc.UNIVERSAL_FPL_CAP
    assert by["universal_12mo"]["overrides"]["postnatal_months"] == 12


def test_forecast_selected_scenarios_validation():
    """--scenarios resolves to validated specs, always including the headline."""
    fc = _forecast_module()
    chosen = fc._selected_scenarios("universal_12mo")
    keys = [s["key"] for s in chosen]
    # The default headline is prepended even if the user omits it (it backs the
    # detailed legacy outputs), and canonical SCENARIOS order is preserved.
    assert fc.DEFAULT_SCENARIO_KEY in keys
    assert "universal_12mo" in keys
    assert keys == [s["key"] for s in fc.SCENARIOS if s["key"] in set(keys)]
    with pytest.raises(SystemExit):
        fc._selected_scenarios("not_a_scenario")


def test_forecast_county_rows_sum_to_state():
    """Per-county program cost sums (within rounding) to the weighted state cost."""
    fc = _forecast_module()
    frame = pd.DataFrame({
        "county": ["Honolulu", "Honolulu", "Maui", "Hawaii", "Kauai"],
        "weight": [100.0, 50.0, 80.0, 60.0, 40.0],
        "rxkids_prenatal_amount": [1500.0, 0.0, 1500.0, 750.0, 0.0],
        "rxkids_postnatal_amount": [3000.0, 0.0, 3000.0, 1500.0, 0.0],
    })
    frame["rxkids_amount"] = (
        frame["rxkids_prenatal_amount"] + frame["rxkids_postnatal_amount"])
    rows = fc._county_rows(frame, pre_payment=1500.0, post_payment=3000.0)
    county_total = sum(r["cost_total"] for r in rows)
    state_total = fc._weighted_cost(frame, "rxkids_amount")
    assert county_total == pytest.approx(state_total, abs=len(rows) + 1)
    # Prenatal/postnatal county splits also reconcile to the state arms.
    assert sum(r["cost_prenatal"] for r in rows) == pytest.approx(
        fc._weighted_cost(frame, "rxkids_prenatal_amount"), abs=len(rows) + 1)
    assert sum(r["cost_postnatal"] for r in rows) == pytest.approx(
        fc._weighted_cost(frame, "rxkids_postnatal_amount"), abs=len(rows) + 1)


# ---------------------------------------------------------------------------
# DOH preliminary-births nowcast (closes the NVSR final-data publication gap)
# ---------------------------------------------------------------------------


def test_doh_ratio_uses_post_covid_regime_only():
    """The occurrence->residence ratio must be calibrated on the post-2020
    travel regime. Including 2018-19 (ratio ~1.10, driven by non-resident
    births that the travel collapse erased) would bias every nowcast low."""
    fc = _forecast_module()
    mean, sd = fc._doh_ratio()
    # Post-COVID regime sits just above 1.0; pre-COVID was ~1.10.
    assert 1.0 < mean < 1.02, f"ratio {mean} looks like it swept in pre-COVID years"
    assert sd > 0.0, "dispersion must be carried into the nowcast MOE, not dropped"
    for year in fc.doh_ratio_years():
        assert year >= fc.DOH_RATIO_FIRST_YEAR


def test_doh_maturation_drops_unregistered_trailing_months():
    """Months within DOH_MATURATION_MONTHS of the snapshot are not yet fully
    registered and must be excluded (in the 2026-07-06 pull June is ~35% short)."""
    fc = _forecast_module()
    snap_year = int(fc.DOH_SNAPSHOT[:4])
    snap_month = int(fc.DOH_SNAPSHOT[5:7])
    assert fc._doh_mature_months(snap_year - 1) == 12      # prior year fully mature
    assert fc._doh_mature_months(snap_year + 1) == 0       # future year has nothing
    partial = fc._doh_mature_months(snap_year)
    assert partial == snap_month - fc.DOH_MATURATION_MONTHS
    assert 0 < partial < 12


def test_doh_maturation_respects_year_boundary(monkeypatch):
    """A snapshot taken early in year Y+1 must NOT treat all of year Y as
    mature: December of Y is only weeks old and is still under-registered.

    The earlier per-calendar-year form returned a flat 12 for any year before
    the snapshot year, which silently understated the most recent (and highest-
    influence) nowcast on any January/February refresh."""
    fc = _forecast_module()
    monkeypatch.setattr(fc, "DOH_SNAPSHOT", "2027-01-15")
    # Jan 2027 snapshot, 2-month maturation -> Nov 2026 is the newest usable month.
    assert fc._doh_mature_months(2026) == 11
    assert fc._doh_mature_months(2025) == 12    # a full year back is genuinely done
    assert fc._doh_mature_months(2027) == 0

    # A February snapshot exposes the same boundary one month further on.
    monkeypatch.setattr(fc, "DOH_SNAPSHOT", "2027-02-10")
    assert fc._doh_mature_months(2026) == 12    # Dec 2026 now 2 months old: usable


def test_doh_ratio_years_derived_not_hardcoded(monkeypatch):
    """Adding a new NVSR final must widen the ratio calibration automatically.
    Hardcoding the year tuple silently excluded newly-landed finals."""
    fc = _forecast_module()
    before = fc.doh_ratio_years()
    assert max(before) == max(y for y in fc.HI_BIRTHS_BY_YEAR
                              if y in fc.HI_DOH_BIRTHS_MONTHLY)

    # Simulate the 2025 NVSR final landing; DOH already has 2025.
    monkeypatch.setitem(fc.HI_BIRTHS_BY_YEAR, 2025, 14_500)
    assert 2025 in fc.doh_ratio_years()
    assert len(fc.doh_ratio_years()) == len(before) + 1
    # Pre-regime years stay excluded regardless.
    assert all(y >= fc.DOH_RATIO_FIRST_YEAR for y in fc.doh_ratio_years())


def test_nvsr_final_always_wins_over_doh_nowcast():
    """A year NVSR has finalised must never be nowcast from DOH."""
    fc = _forecast_module()
    nowcast_years = {n["year"] for n in fc._doh_nowcast_births()}
    assert not (nowcast_years & set(fc.HI_BIRTHS_BY_YEAR))


def test_doh_nowcast_widens_moe_for_annualised_years():
    """A partial year annualised from k<12 months carries annualisation variance
    on top of Poisson + ratio noise, so its relative MOE must exceed a complete
    year's. Without this the ensemble would over-trust a 5-month extrapolation."""
    fc = _forecast_module()
    ncs = {n["year"]: n for n in fc._doh_nowcast_births()}
    complete = [n for n in ncs.values() if not n["annualised"]]
    annualised = [n for n in ncs.values() if n["annualised"]]
    if not (complete and annualised):
        pytest.skip("snapshot has no complete/annualised nowcast pair to compare")
    rel = lambda n: n["moe"] / n["estimate"]
    assert rel(annualised[0]) > rel(complete[0])


def test_doh_nowcast_extends_series_and_tightens_projection():
    """The nowcast must add post-NVSR observations and, by closing the ~18-month
    publication gap, produce a tighter interval than NVSR-only extrapolation."""
    fc = _forecast_module()
    with_doh = fc._project_births(2028, use_doh_nowcast=True)
    without = fc._project_births(2028, use_doh_nowcast=False)

    assert with_doh["nowcasts"], "expected DOH nowcast points past the NVSR wall"
    assert without["nowcasts"] == []
    assert all(n["year"] > without["last_final_year"] for n in with_doh["nowcasts"])

    width = lambda p: p["ci90_high"] - p["ci90_low"]
    assert width(with_doh) < width(without)
    # Sanity: the projected cohort stays in a plausible band for Hawaiʻi.
    assert 10_000 < with_doh["point"] < 18_000


def test_birth_nowcast_note_reports_provenance_both_ways():
    """Report notes must disclose the conversion factor and the snapshot when
    nowcasting, and say so plainly when running NVSR-only."""
    fc = _forecast_module()
    note = fc._birth_nowcast_note(fc._project_births(2028, use_doh_nowcast=True))
    assert fc.DOH_SNAPSHOT in note and "occurrence" in note
    off = fc._birth_nowcast_note(fc._project_births(2028, use_doh_nowcast=False))
    assert "--no-doh-nowcast" in off


# ---------------------------------------------------------------------------
# Birth weighting basis — person weight, not the filing-status hybrid weight
# ---------------------------------------------------------------------------


def _unit_with_deps(weight, dep_ages_pwgtp, filing_status="married_filing_jointly"):
    """One tax-unit row whose dependents_details carry (age, pwgtp) pairs."""
    return {
        "weight": weight,
        "filing_status": filing_status,
        "num_dependents": len(dep_ages_pwgtp),
        "dependents_details": [
            {"age": a, "relationship": 25, "citizenship": 1,
             "months_in_home": 12, "school_level": 0, "disabled": False,
             "pwgtp": pw}
            for a, pw in dep_ages_pwgtp
        ],
    }


def test_observed_births_uses_person_weight_not_unit_weight():
    """`observed_births x weight` must reproduce the infants' OWN person-weighted
    total. The unit weight is a DOTAX filing-status-calibrated hybrid (WGTP x
    share factors) — right for revenue, wrong for counting babies. Weighting
    infants by it undercounted them and tilted the mix toward HoH (factor 1.30),
    i.e. toward lower-income families, inflating the eligible share."""
    fc = _forecast_module()
    df = pd.DataFrame([
        _unit_with_deps(100.0, [(0, 150.0)]),                      # infant PWGTP > unit weight
        _unit_with_deps(200.0, [(0, 180.0), (0, 190.0)]),          # twins
        _unit_with_deps(50.0, [(3, 60.0)]),                        # no infant
        _unit_with_deps(80.0, [(0, 95.0)], "head_of_household"),
    ])
    out = fc._observed_births(df)

    # Head-count is preserved for reporting...
    assert out["observed_births_n"].tolist() == [1, 2, 0, 1]
    # ...but the effective count is person-weight-consistent.
    weighted = float((out["observed_births"] * out["weight"]).sum())
    assert weighted == pytest.approx(150.0 + 180.0 + 190.0 + 95.0)

    # The legacy head-count basis would have given a different (wrong) answer.
    legacy = float((out["observed_births_n"] * out["weight"]).sum())
    assert legacy != pytest.approx(weighted)


def test_observed_births_falls_back_when_pwgtp_absent():
    """Frames built before 'pwgtp' was carried (or synthetic fixtures) must fall
    back to the head-count basis rather than silently producing zero births."""
    fc = _forecast_module()
    df = pd.DataFrame([{
        "weight": 100.0,
        "num_dependents": 1,
        "dependents_details": [
            {"age": 0, "relationship": 25, "citizenship": 1,
             "months_in_home": 12, "school_level": 0, "disabled": False},
        ],
    }])
    out = fc._observed_births(df)
    assert out["observed_births"].tolist() == [1]
    assert out["observed_births_n"].tolist() == [1]


def test_dependent_details_carry_person_weight():
    """The constructor must record each dependent's own PWGTP so demographic
    consumers can weight people correctly."""
    from tax_modeler.units.constructor import TaxUnitConstructor
    hh = pd.DataFrame(
        {"AGEP": [30, 0], "RELSHIPP": [20, 25], "CIT": [1, 1],
         "SCHL": [21, 0], "DIS": [2, 2], "PWGTP": [110.0, 125.0]},
        index=["p1", "p2"],
    )
    details = TaxUnitConstructor._build_dependent_details(["p2"], hh)
    assert len(details) == 1
    assert details[0]["age"] == 0
    assert details[0]["pwgtp"] == pytest.approx(125.0)


# ---------------------------------------------------------------------------
# County-split calibration — DOH shares, not raw (noisy/imputed) PUMS shares
# ---------------------------------------------------------------------------


def test_county_shares_sum_to_one():
    """NVSR_COUNTY_SHARE must be a proper partition -- the county recalibration
    relies on this to leave the state total unchanged (sums to the same target
    the state-level calibration already computed)."""
    fc = _forecast_module()
    assert sum(fc.NVSR_COUNTY_SHARE.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(fc.NVSR_COUNTY_SHARE) == {"Honolulu", "Hawaii", "Maui", "Kauai"}


def _county_frame(rows):
    """rows: list of (county, weight, births)."""
    return pd.DataFrame([
        {"county": c, "weight": w, "observed_births": b} for c, w, b in rows
    ])


def test_calibrate_births_by_county_matches_doh_shares_exactly():
    """After calibration, each county's weighted birth total must equal
    NVSR_COUNTY_SHARE[county] x target -- not whatever raw PUMS happened to
    imply. This is the core behavior: DOH decides the split, PUMS only
    decides which specific units within a county carry the (rescaled)
    births."""
    fc = _forecast_module()
    df = _county_frame([
        ("Honolulu", 100.0, 5.0),   # raw weighted 500 -- wildly PUMS-skewed
        ("Hawaii", 100.0, 1.0),     # raw weighted 100 -- tiny sample
        ("Maui", 100.0, 1.0),
        ("Kauai", 100.0, 1.0),
    ])
    target = 10_000.0
    info = fc._calibrate_births_by_county(df, target, use_county_shares=True)
    assert info["mode"] == "nvsr_county_shares"

    w = df["weight"].to_numpy(float)
    b = df["observed_births"].to_numpy(float)
    for i, county in enumerate(df["county"]):
        weighted = b[i] * w[i]
        expected = fc.NVSR_COUNTY_SHARE[county] * target
        assert weighted == pytest.approx(expected, rel=1e-6), county

    # State total is exactly reproduced -- redistribution, not a level change.
    assert float((b * w).sum()) == pytest.approx(target, rel=1e-6)


def test_calibrate_births_by_county_leaves_unmapped_counties_alone():
    """A county absent from NVSR_COUNTY_SHARE (e.g. 'Unknown') must keep its
    pre-existing (state-level-scaled) value rather than being zeroed or
    crashing the redistribution."""
    fc = _forecast_module()
    df = _county_frame([
        ("Honolulu", 100.0, 1.0),
        ("Unknown", 50.0, 2.0),
    ])
    before_unknown = float(df.loc[1, "weight"] * df.loc[1, "observed_births"])
    fc._calibrate_births_by_county(df, 1000.0, use_county_shares=True)
    after_unknown = float(df.loc[1, "weight"] * df.loc[1, "observed_births"])
    assert after_unknown == pytest.approx(before_unknown)


def test_calibrate_births_by_county_noop_flag():
    """--no-county-share-calibration (use_county_shares=False) must leave observed_births
    untouched -- the escape hatch back to raw PUMS shares."""
    fc = _forecast_module()
    df = _county_frame([("Honolulu", 100.0, 5.0), ("Hawaii", 100.0, 1.0)])
    before = df["observed_births"].tolist()
    info = fc._calibrate_births_by_county(df, 10_000.0, use_county_shares=False)
    assert info["mode"] == "raw_pums_shares"
    assert df["observed_births"].tolist() == before


def test_calibrate_births_by_county_zero_raw_gets_finite_factor():
    """A county with zero raw sampled births (small-sample edge case) must not
    produce inf/nan -- falls back to factor=1.0 (contributes nothing, rather
    than crashing or injecting a phantom population)."""
    fc = _forecast_module()
    df = _county_frame([
        ("Honolulu", 100.0, 5.0), ("Hawaii", 100.0, 0.0),
        ("Maui", 100.0, 0.0), ("Kauai", 100.0, 0.0),
    ])
    info = fc._calibrate_births_by_county(df, 10_000.0, use_county_shares=True)
    assert all(np.isfinite(v["factor"]) for v in info["county_factors"].values())
    assert df["observed_births"].tolist() == [5.0 * info["county_factors"]["Honolulu"]["factor"], 0.0, 0.0, 0.0]


def test_calibrate_births_state_total_invariant_to_county_flag():
    """Whether county recalibration is on or off, the STATE total that
    _calibrate_births computes must be identical -- county calibration only
    reslices an already-fixed total, it never changes it.

    Requires every DOH-covered county to have >=1 sampled infant (true in the
    real PUMS frame: Honolulu/Hawaii/Maui/Kauai all have nonzero samples) --
    a county with zero raw sample gets factor=1.0 (see
    test_calibrate_births_by_county_zero_raw_gets_finite_factor) and its DOH
    share genuinely can't attach anywhere, so the invariant only holds when
    the fixture mirrors that well-posed case."""
    fc = _forecast_module()
    rows = [
        {"weight": 100.0, "county": c,
         "dependents_details": [{"age": 0, "relationship": 25, "citizenship": 1,
                                  "months_in_home": 12, "school_level": 0,
                                  "disabled": False, "pwgtp": 100.0}]}
        for c in ("Honolulu", "Hawaii", "Maui", "Kauai")
    ]
    df_a = fc._observed_births(pd.DataFrame(rows))
    df_b = df_a.copy(deep=True)

    class Args:
        use_proxy_births = False
        no_birth_projection = True
        no_doh_nowcast = True
        no_county_share_calibration = False
    class ArgsNoCounty(Args):
        no_county_share_calibration = True

    info_a = fc._calibrate_births(df_a, 2028, Args())
    info_b = fc._calibrate_births(df_b, 2028, ArgsNoCounty())
    assert info_a["target"] == pytest.approx(info_b["target"])
    assert info_a["calibrated_weighted"] == pytest.approx(info_b["calibrated_weighted"])

    w_a = df_a["weight"].to_numpy(float); b_a = df_a["observed_births"].to_numpy(float)
    w_b = df_b["weight"].to_numpy(float); b_b = df_b["observed_births"].to_numpy(float)
    assert float((b_a * w_a).sum()) == pytest.approx(float((b_b * w_b).sum()))


def test_nvsr_series_matches_wonder_and_is_monotone_plausible():
    """HI_BIRTHS_BY_YEAR must match CDC WONDER (dataset D66, Hawaii, by year).

    Four values were wrong before 2026-08-07 (2018/2019 by ~9%), which
    manufactured a spurious ~1.10 DOH/NVSR ratio in those years and got
    rationalised as a 'pre-COVID birth-tourism regime'. Pinning the series
    against its source stops that recurring.
    """
    fc = _forecast_module()
    wonder = {2018: 16972, 2019: 16797, 2020: 15785, 2021: 15620,
              2022: 15535, 2023: 14808, 2024: 14917}
    for year, expected in wonder.items():
        assert fc.HI_BIRTHS_BY_YEAR[year] == expected, (
            f"{year}: expected WONDER value {expected}, "
            f"got {fc.HI_BIRTHS_BY_YEAR[year]}")

    # Sanity the old series failed: births should not RISE into the pandemic.
    s = fc.HI_BIRTHS_BY_YEAR
    assert s[2018] > s[2020] > s[2022], "2018-2022 should decline, not rise"


def test_occurrence_residence_ratio_is_stable_single_regime():
    """With the series corrected the DOH/NVSR ratio is ~1.003 in EVERY year --
    there is no pre/post-COVID regime, and the calibration should span all
    available years rather than excluding early ones."""
    fc = _forecast_module()
    ratios = {
        y: sum(fc.HI_DOH_BIRTHS_MONTHLY[y]) / fc.HI_BIRTHS_BY_YEAR[y]
        for y in fc.doh_ratio_years()
    }
    assert len(ratios) >= 7, "all overlapping years should be in the calibration"
    for y, r in ratios.items():
        assert 1.000 < r < 1.010, f"{y}: ratio {r:.4f} outside the stable band"
    mean, sd = fc._doh_ratio()
    assert 1.002 < mean < 1.004
    assert sd < 0.002, "a large sd would mean the single-regime claim is wrong"


def test_county_shares_are_residence_basis_not_occurrence():
    """NVSR_COUNTY_SHARE must carry the WONDER residence split, which gives
    Honolulu a materially LOWER share than DOH's occurrence counts (neighbour-
    island mothers deliver on Oahu). Guards against silently reverting to the
    occurrence-based numbers."""
    fc = _forecast_module()
    # Occurrence (DOH, 2018-2025) had Honolulu at ~73.4%; residence is ~70.9%.
    assert fc.NVSR_COUNTY_SHARE["Honolulu"] < 0.72
    assert fc.NVSR_COUNTY_SHARE["Maui"] > 0.10      # occurrence understated Maui
    assert fc.NVSR_COUNTY_SHARE["Hawaii"] > 0.13


# ---------------------------------------------------------------------------
# Birth projector selection (Kalman by default, on back-test evidence)
# ---------------------------------------------------------------------------


def test_birth_projection_defaults_to_kalman():
    """Kalman is the default because it won the walk-forward back-test on every
    metric AND fixed 50%-coverage intervals -- not because it is fancier."""
    fc = _forecast_module()
    assert fc.BIRTH_PROJECTION_METHOD == "kalman"
    proj = fc._project_births(2028)
    assert proj["method"] == "kalman"
    assert proj["projected"] is True


def test_birth_projection_methods_both_runnable_and_plausible():
    """Both projectors must run on the production series and land in a sane
    band; the ensemble stays available for comparison."""
    fc = _forecast_module()
    points = {}
    for method in ("kalman", "ensemble"):
        p = fc._project_births(2028, method=method)
        assert p["method"] == method
        assert 10_000 < p["point"] < 18_000, method
        assert p["ci90_low"] < p["point"] < p["ci90_high"], method
        points[method] = p
    # The two disagree (they are genuinely different estimators); if they ever
    # coincide exactly, the method switch has silently stopped taking effect.
    assert points["kalman"]["point"] != points["ensemble"]["point"]


def test_kalman_interval_is_wider_than_the_undercovering_ensemble():
    """The back-test measured ensemble CI90 coverage at 50% (target 90%) --
    i.e. too tight. The replacement must not be even tighter."""
    fc = _forecast_module()
    k = fc._project_births(2028, method="kalman")
    e = fc._project_births(2028, method="ensemble")
    width = lambda p: p["ci90_high"] - p["ci90_low"]
    assert width(k) > width(e)


def test_unknown_birth_projection_method_falls_back_not_crashes():
    """An unrecognised method must degrade to the ensemble rather than abort a
    forecast run (the CLI constrains choices, but library callers may not)."""
    fc = _forecast_module()
    p = fc._project_births(2028, method="not-a-method")
    assert p["projected"] is True
    assert 10_000 < p["point"] < 18_000


# ---------------------------------------------------------------------------
# Empirical calibration of the Kalman birth path (bias + conformal kappa)
# ---------------------------------------------------------------------------


def test_kalman_birth_path_applies_bias_and_kappa():
    """The production Kalman projection must NOT quote the raw analytical
    output: the repo's discipline requires empirically calibrated PIs, and the
    46-fold back-test measured a systematic +2% point bias and an over-covering
    (97.8%) analytical interval. Point = raw * exp(-b); half-width shrunk by
    kappa on the bias-corrected SE."""
    import math
    fc = _forecast_module()
    p = fc._project_births(2028, method="kalman")
    cal = p.get("calibration")
    assert cal, "kalman path must attach its calibration metadata"
    shrink = math.exp(-fc.BIRTH_KALMAN_LOG_BIAS)
    assert p["point"] == pytest.approx(cal["raw_point"] * shrink, rel=1e-9)
    # Interval is symmetric about the corrected point.
    assert (p["point"] - p["ci90_low"]) == pytest.approx(
        p["ci90_high"] - p["point"], rel=1e-9)
    # Ensemble path carries no such metadata (it is not calibrated here).
    assert "calibration" not in fc._project_births(2028, method="ensemble")


def test_kalman_calibration_constants_in_derivable_range():
    """Pin the constants to the neighbourhood the back-test derives them in;
    a wildly different value on re-derivation means the series or the filter
    changed and the write-up needs revisiting, not just the constant."""
    fc = _forecast_module()
    assert 0.0 < fc.BIRTH_KALMAN_LOG_BIAS < 0.05     # +2.0% pooled at derivation
    assert 0.5 < fc.BIRTH_KALMAN_SE_KAPPA < 1.2      # 0.862 at derivation


def test_nvsr_series_extends_to_wonder_2007():
    """The series spans the full WONDER D66 window so the back-test has 46
    folds and the filter enters recent years with an established trend state.
    Spot-pin the ends and the 2017->2018 join that exposed the old error."""
    fc = _forecast_module()
    assert fc.HI_BIRTHS_BY_YEAR[2007] == 19134
    assert fc.HI_BIRTHS_BY_YEAR[2017] == 17517
    assert len(fc.HI_BIRTHS_BY_YEAR) == 18
    # The join: a smooth -3.1% step, not the absurd -12% cliff the wrong
    # 2018 value (15,404) implied.
    step = fc.HI_BIRTHS_BY_YEAR[2018] / fc.HI_BIRTHS_BY_YEAR[2017] - 1
    assert -0.05 < step < 0.0


# ---------------------------------------------------------------------------
# Headline design: $1,500 + 3 x $500, and "people affected" = births served
# ---------------------------------------------------------------------------


def test_default_scenario_is_the_three_month_design():
    """$1,500 one-time prenatal + 3 x $500 monthly = $3,000 per birth."""
    fc = _forecast_module()
    assert fc.DEFAULT_SCENARIO_KEY == "statutory_3mo"
    sc = fc.SCENARIO_BY_KEY["statutory_3mo"]
    assert sc["overrides"]["postnatal_months"] == 3
    assert sc["overrides"]["income_fpl_cap"] == 3.00

    from tax_modeler.programs import hawaii_rxkids_parameters
    p = hawaii_rxkids_parameters()
    prenatal = p.prenatal_monthly * p.prenatal_months
    postnatal = p.postnatal_monthly_per_child * sc["overrides"]["postnatal_months"]
    assert prenatal == pytest.approx(1500.0)
    assert postnatal == pytest.approx(1500.0)
    assert prenatal + postnatal == pytest.approx(3000.0)


def test_births_served_is_invariant_to_postnatal_window():
    """Shortening the postnatal window is a pure COST lever: the same births
    are served, they just draw fewer instalments. If this ever fails, someone
    has made reach depend on payment duration."""
    fc = _forecast_module()
    # Same infant-recipient count, same take-up -> same births regardless of
    # how many months the postnatal arm pays out.
    assert fc._births_served(7172.0, 0.90) == pytest.approx(7172.0 / 0.90)
    assert fc._births_served(0.0, 0.90) == 0.0
    assert fc._births_served(100.0, 0.0) == 0.0   # guard, no ZeroDivisionError


def test_births_served_is_not_the_recipient_count():
    """Each served birth generates ~2 recipient-payments (prenatal + postnatal),
    so recipients must never be quoted as 'people affected'."""
    fc = _forecast_module()
    rec_infants, takeup = 7172.0, 0.90
    births = fc._births_served(rec_infants, takeup)
    recipients = 6598.0 + rec_infants          # pregnancies + infants
    assert recipients > births * 1.5, (
        "recipient-payments should be roughly double births served; if they are "
        "close, the two metrics have been conflated"
    )
