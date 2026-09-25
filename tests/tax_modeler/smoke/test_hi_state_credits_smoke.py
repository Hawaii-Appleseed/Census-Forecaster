"""Smoke tests for Hawaii state-level refundable credits.

Covers:
* HI state EITC (HRS §235-55.75, 40% of federal, refundable TY 2023+)
* HI Food/Excise Tax Credit (HRS §235-55.85)
* HI Low-Income Household Renters Credit (HRS §235-55.7)
* Reform DSL integration for all three
* SPM resource calc ingests all three
"""
from __future__ import annotations

import pytest

from tax_modeler import (
    HawaiiEitcParameters,
    HawaiiFoodExciseParameters,
    HawaiiRentersCreditParameters,
    Reform,
    apply_reform,
    compute_hi_eitc_for_units,
    compute_hi_food_excise_for_units,
    compute_hi_renters_for_units,
    compute_spm_resources,
)
from tax_modeler.errors import ConfigError


pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# HI state EITC
# ---------------------------------------------------------------------------


def test_hi_eitc_is_40pct_of_federal(taxed_units):
    df = compute_hi_eitc_for_units(taxed_units)
    assert "hi_eitc_amount" in df.columns
    fed = taxed_units["eitc_amount"].fillna(0).clip(lower=0)
    expected = fed * 0.40
    # Default is refundable (TY2023+), so HI EITC = federal × 0.40 with no cap
    assert (df["hi_eitc_amount"] == expected).all()


def test_hi_eitc_rate_override(taxed_units):
    df = compute_hi_eitc_for_units(
        taxed_units, overrides={"rate_of_federal": 0.20}
    )
    fed = taxed_units["eitc_amount"].fillna(0).clip(lower=0)
    assert df["hi_eitc_amount"].sum() == pytest.approx((fed * 0.20).sum(), rel=1e-9)


def test_hi_eitc_non_refundable_capped_by_tax(taxed_units):
    """Pre-2023 HI EITC was non-refundable. Verify cap behavior."""
    df = compute_hi_eitc_for_units(
        taxed_units,
        overrides={"rate_of_federal": 0.20, "refundable": False},
    )
    if "hi_tax_before_credits" in taxed_units.columns:
        # HI EITC must not exceed tax_before_credits
        tax_cap = taxed_units["hi_tax_before_credits"].fillna(0).clip(lower=0)
        assert (df["hi_eitc_amount"] <= tax_cap + 1e-6).all()


def test_hi_eitc_unknown_override_raises(taxed_units):
    with pytest.raises(ConfigError):
        compute_hi_eitc_for_units(taxed_units, overrides={"foo": 1.0})


def test_hi_eitc_uses_pre_act_209_rate_for_ty2022(taxed_units):
    """TY2022 must use 20% non-refundable per Act 107 — Act 209 took effect TY2023.

    A backtest that uses the post-Act-209 40% rate retroactively for TY2022
    would overstate hi_eitc lift by ~2×, inflating persons-lifted-by-credit.
    """
    df_22 = compute_hi_eitc_for_units(taxed_units, tax_year=2022)
    df_23 = compute_hi_eitc_for_units(taxed_units, tax_year=2023)
    fed = taxed_units["eitc_amount"].fillna(0).clip(lower=0).sum()
    # Refundable case (TY23+): 40% paid out in full.
    assert df_23["hi_eitc_amount"].sum() == pytest.approx(fed * 0.40, rel=1e-9)
    # Pre-Act-209: non-refundable cap means amount ≤ 20% × federal.
    assert df_22["hi_eitc_amount"].sum() <= fed * 0.20 + 1e-6


# ---------------------------------------------------------------------------
# HI Food/Excise Tax Credit
# ---------------------------------------------------------------------------


def test_hi_food_excise_runs(taxed_units):
    df = compute_hi_food_excise_for_units(taxed_units)
    assert "hi_food_excise_amount" in df.columns
    assert (df["hi_food_excise_amount"] >= 0).all()


def test_hi_food_excise_phases_out_with_income(taxed_units):
    df = compute_hi_food_excise_for_units(taxed_units)
    # Single filers above $50k should get nothing
    high_income_single = df[
        (df["filing_status"] == "single") & (df["income"] >= 50_000)
    ]
    if len(high_income_single) > 0:
        assert high_income_single["hi_food_excise_amount"].sum() == 0


def test_hi_food_excise_amount_override(taxed_units):
    base = compute_hi_food_excise_for_units(taxed_units)
    doubled = compute_hi_food_excise_for_units(
        taxed_units, overrides={"amount_pct": 2.0}
    )
    base_total = base["hi_food_excise_amount"].sum()
    if base_total == 0:
        pytest.skip("no eligible filers on the fixture")
    assert doubled["hi_food_excise_amount"].sum() == pytest.approx(
        base_total * 2.0, rel=1e-6
    )


# ---------------------------------------------------------------------------
# HI Low-Income Renters Credit
# ---------------------------------------------------------------------------


def test_hi_renters_runs(taxed_units):
    df = compute_hi_renters_for_units(taxed_units)
    assert "hi_renters_amount" in df.columns
    assert (df["hi_renters_amount"] >= 0).all()


def test_hi_renters_takeup_pct_override(taxed_units):
    base = compute_hi_renters_for_units(taxed_units)
    full_takeup = compute_hi_renters_for_units(
        taxed_units, overrides={"takeup_pct": 1.0}
    )
    base_total = base["hi_renters_amount"].sum()
    if base_total == 0:
        pytest.skip("no eligible renters on the fixture")
    # 30% default vs. 100% override → 100/30 ratio
    assert full_takeup["hi_renters_amount"].sum() == pytest.approx(
        base_total / 0.30, rel=1e-6
    )


def test_hi_renters_high_income_zero(taxed_units):
    df = compute_hi_renters_for_units(taxed_units)
    high = df[df["income"] > 100_000]
    if len(high) > 0:
        assert high["hi_renters_amount"].sum() == 0


# ---------------------------------------------------------------------------
# Reform DSL integration
# ---------------------------------------------------------------------------


def test_reform_hi_eitc_doubled(taxed_units):
    """A reform that doubles the HI EITC rate (40% → 80%) flows through."""
    reform = Reform(
        name="hi_eitc_doubled",
        benefit_overrides={"hi_eitc": {"rate_of_federal": 0.80}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    assert "hi_eitc" in result.benefit_flow_deltas_millions
    # Doubling the rate should produce a positive delta if any federal EITC
    # is being claimed
    fed_total = float((taxed_units["eitc_amount"] * taxed_units["weight"]).sum())
    if fed_total == 0:
        pytest.skip("no federal EITC on the fixture")
    assert result.benefit_flow_deltas_millions["hi_eitc"] > 0


def test_reform_hi_food_excise_expansion(taxed_units):
    reform = Reform(
        name="hi_food_excise_plus_50pct",
        benefit_overrides={"hi_food_excise": {"amount_pct": 1.50}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    deltas = result.benefit_flow_deltas_millions
    if "hi_food_excise" in deltas and deltas["hi_food_excise"] != 0:
        # Expansion should be a positive flow
        assert deltas["hi_food_excise"] > 0


def test_reform_combined_hi_state_credits(taxed_units):
    reform = Reform(
        name="hi_state_credits_expansion",
        benefit_overrides={
            "hi_eitc": {"rate_of_federal": 0.50},          # 40% → 50%
            "hi_food_excise": {"amount_pct": 1.5},         # +50%
            "hi_renters": {"amount_pct": 1.5},             # +50%
        },
    )
    result = apply_reform(taxed_units, reform, year=2026)
    deltas = result.benefit_flow_deltas_millions
    assert {"hi_eitc", "hi_food_excise", "hi_renters"}.issubset(set(deltas))


# ---------------------------------------------------------------------------
# SPM integration
# ---------------------------------------------------------------------------


def test_spm_includes_hi_state_credits(taxed_units):
    df = compute_hi_eitc_for_units(taxed_units)
    df = compute_hi_food_excise_for_units(df)
    df = compute_hi_renters_for_units(df)
    resources, meta = compute_spm_resources(df)
    # All three HI state-credit columns are now used (not zeroed)
    assert "hi_eitc" not in meta.zeroed_components
    assert "hi_food_excise" not in meta.zeroed_components
    assert "hi_renters" not in meta.zeroed_components


def test_spm_does_not_double_count_food_excise_credit():
    """Regression (audit F3): the §235-55.85 food/excise credit must be counted
    once. ``hi_tax_liability`` is NET of a simplified version of the credit
    (``hi_low_income_credit``); when the dedicated ``hi_food_excise_amount`` is
    also supplied, compute_spm_resources must gross the tax line back up so the
    credit isn't counted twice (once via reduced tax, once via +food_excise).
    """
    import pandas as pd

    # tax_before_credits = 300; embedded credit = 110; net liability = 190.
    # The dedicated model also reports the same $110 credit.
    df = pd.DataFrame({
        "total_cash_income": [20_000.0],
        "hi_tax_liability": [190.0],            # net of the $110 embedded credit
        "hi_low_income_credit": [110.0],        # the embedded §235-55.85 credit
        "hi_food_excise_amount": [110.0],       # dedicated graduated model
        "weight": [100.0],
        "filing_status": ["single"],
        "num_dependents": [0],
    })
    resources, _ = compute_spm_resources(df, federal_tax_fallback=False)
    # Counted once: income - tax_before_credits + food_excise = 20000 - 300 + 110.
    assert resources["spm_resources"].iloc[0] == pytest.approx(19_810.0)

    # Without the embedded-credit column the function cannot un-net, so it falls
    # back to the (double-counting) behavior and warns — diff is exactly $110.
    df_nocol = df.drop(columns=["hi_low_income_credit"])
    res_double, _ = compute_spm_resources(df_nocol, federal_tax_fallback=False)
    assert res_double["spm_resources"].iloc[0] == pytest.approx(19_920.0)
    assert res_double["spm_resources"].iloc[0] - resources["spm_resources"].iloc[0] == pytest.approx(110.0)
