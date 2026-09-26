"""Regression tests for three defects in the income-tax reform-scoring path,
found September 2026 (SB3125_CD1_FORECAST.md, "Scoring-path fixes").

1. ``apply_top_income_growth_premium`` blanks the tax columns of the units it
   rescales. The scorers filled the blank itemized deduction with 0, so every
   filer above $500K was scored on the standard deduction alone. The scorers
   now raise on a NaN, and ``refresh_stale_hawaii_tax`` recomputes the units.
2. ``per_unit_tax`` never subtracted credits (it read a key the credit
   calculator does not return). It is now ``TaxCalculator.unit_liabilities``'
   net tax, the same per-unit tax ``compare_systems`` sums.
3. Without a ``num_exemptions`` column every return got one personal
   exemption. The scorers now count filer, spouse (joint) and dependents.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.adjustments.hawaii_credits import HawaiiTaxCredits
from tax_modeler.config.tax_system_config import (
    TaxCalculator,
    TaxSystemRegistry,
    compare_systems,
)
from tax_modeler.liability.hawaii import exemption_counts
from tax_modeler.projection.tax_unit_projector import (
    _HAWAII_TAX_COLUMNS,
    refresh_stale_hawaii_tax,
)
from tax_modeler.scenarios.behavioral_response import (
    BehavioralParams,
    apply_behavioral_response,
    apply_top_income_growth_premium,
)
from tax_modeler.scenarios.quintile_analysis import per_unit_tax

YEAR = 2027
PREMIUM = 0.01   # the MID top-income premium, 1% a year from 2024


@pytest.fixture(scope="module")
def calc():
    return TaxCalculator()


@pytest.fixture(scope="module")
def act46():
    return TaxSystemRegistry.get_act46_system(YEAR)


@pytest.fixture(scope="module")
def act24():
    return TaxSystemRegistry.get_sb3125_cd2_system(YEAR)


def _raw_units(county: bool = False) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            (12_000, "head_of_household", 2),        # refundable credits exceed the tax
            (45_000, "single", 0),
            (95_000, "married_filing_jointly", 2),
            (180_000, "married_filing_separately", 0),
            (420_000, "married_filing_jointly", 1),  # below the premium threshold
            (650_000, "single", 0),                  # above it
            (2_500_000, "married_filing_jointly", 3),
        ],
        columns=["income", "filing_status", "num_dependents"],
    )
    df["weight"] = [10.0, 20.0, 15.0, 5.0, 3.0, 2.0, 1.0]
    if county:
        df["county"] = ["Honolulu", "Maui", None, "Hawaii", "Kauai", "Maui", "Honolulu"]
    return df


def _projected(county: bool = False) -> pd.DataFrame:
    """Units with the hi_* columns the projector's tax step leaves behind.

    With no hi_* columns every unit is stale, so the refresh computes all of
    them with the projector's own (per-county) deduction params.
    """
    return refresh_stale_hawaii_tax(_raw_units(county), target_year=YEAR)


def _premium(df: pd.DataFrame) -> pd.DataFrame:
    return apply_top_income_growth_premium(df, target_year=YEAR, annual_premium=PREMIUM)


# ---------------------------------------------------------------------------
# 1. Premium-blanked deductions
# ---------------------------------------------------------------------------

class TestRefreshStaleHawaiiTax:

    @pytest.mark.parametrize("county", [False, True], ids=["state-proxy", "per-county"])
    def test_premium_then_refresh_equals_scoring_the_scaled_income(self, county):
        base = _projected(county)
        assert not base["hi_itemized_deduction"].isna().any()

        scaled = _premium(base)
        top = (scaled["income"] >= 500_000).to_numpy()
        assert scaled.loc[top, "hi_itemized_deduction"].isna().all()   # blanked on purpose

        refreshed = refresh_stale_hawaii_tax(scaled, target_year=YEAR)
        from_scratch = refresh_stale_hawaii_tax(
            scaled.drop(columns=list(_HAWAII_TAX_COLUMNS)), target_year=YEAR,
        )
        for col in _HAWAII_TAX_COLUMNS:
            np.testing.assert_array_equal(
                refreshed[col].to_numpy(), from_scratch[col].to_numpy(), err_msg=col)
            # Units the premium did not touch are returned as they were.
            np.testing.assert_array_equal(
                refreshed[col].to_numpy()[~top], base[col].to_numpy()[~top], err_msg=col)
        np.testing.assert_array_equal(refreshed["hi_agi"], refreshed["income"])

    def test_itemizing_top_filers_keep_their_deduction(self, calc, act46):
        refreshed = refresh_stale_hawaii_tax(_premium(_projected()), target_year=YEAR)
        top = refreshed["income"] >= 500_000
        assert (refreshed.loc[top, "hi_itemized_deduction"] > 50_000).all()

        # What the old fillna(0) did: standard deduction only, so more tax.
        sd_only = refreshed.copy()
        sd_only.loc[top, "hi_itemized_deduction"] = 0.0
        right = per_unit_tax(refreshed, act46, calc)[top.to_numpy()]
        wrong = per_unit_tax(sd_only, act46, calc)[top.to_numpy()]
        assert (right < wrong).all()

    def test_positional_with_duplicate_index(self):
        # The projector's per-county concat leaves duplicate index labels.
        scaled = _premium(_projected()).set_axis([0, 0, 1, 1, 2, 2, 3])
        refreshed = refresh_stale_hawaii_tax(scaled, target_year=YEAR)
        clean = refresh_stale_hawaii_tax(scaled.reset_index(drop=True), target_year=YEAR)
        for col in _HAWAII_TAX_COLUMNS:
            np.testing.assert_array_equal(refreshed[col].to_numpy(), clean[col].to_numpy())

    def test_income_change_without_blanking_is_caught(self):
        # apply_macro_recession_shock rescales income but leaves hi_* in place.
        base = _projected()
        shocked = base.copy()
        shocked["income"] = shocked["income"] * 0.98
        refreshed = refresh_stale_hawaii_tax(shocked, target_year=YEAR)
        np.testing.assert_array_equal(refreshed["hi_agi"], shocked["income"])
        assert (refreshed["hi_tax_before_credits"] <= base["hi_tax_before_credits"]).all()

    def test_nothing_stale_is_a_no_op(self):
        base = _projected()
        pd.testing.assert_frame_equal(refresh_stale_hawaii_tax(base, target_year=YEAR), base)


class TestScorersRejectBlankedDeductions:

    def test_compare_systems(self, calc, act46, act24):
        with pytest.raises(ValueError, match="refresh_stale_hawaii_tax"):
            compare_systems(_premium(_projected()), act46, act24, calculator=calc)

    def test_per_unit_tax(self, calc, act46):
        with pytest.raises(ValueError, match="hi_itemized_deduction is NaN"):
            per_unit_tax(_premium(_projected()), act46, calc)

    def test_behavioral_response(self, calc, act46, act24):
        with pytest.raises(ValueError, match="hi_itemized_deduction is NaN"):
            apply_behavioral_response(
                _premium(_projected()), BehavioralParams.mid(), target_year=YEAR,
                baseline_cfg=act46, scenario_cfg=act24, calculator=calc,
            )

    def test_refreshed_frame_scores(self, calc, act46, act24):
        refreshed = refresh_stale_hawaii_tax(_premium(_projected()), target_year=YEAR)
        cmp = compare_systems(refreshed, act46, act24, calculator=calc)
        assert np.isfinite(cmp["revenue_millions"]).all()


# ---------------------------------------------------------------------------
# 2. per_unit_tax is net of credits
# ---------------------------------------------------------------------------

class TestPerUnitTaxNetOfCredits:

    def test_equals_the_kernel_and_sums_to_compare_systems(self, calc, act46, act24):
        units = _projected()
        w = units["weight"].to_numpy()
        for cfg, row in ((act46, 0), (act24, 1)):
            net = per_unit_tax(units, cfg, calc)
            np.testing.assert_array_equal(net, calc.unit_liabilities(units, cfg)["net"])
            revenue = compare_systems(units, act46, act24, calculator=calc).iloc[row]
            assert (net * w).sum() / 1e6 == pytest.approx(revenue["revenue_millions"], abs=1e-12)

    def test_credits_are_subtracted(self, calc, act46):
        units = _projected()
        parts = calc.unit_liabilities(units, act46)
        credits = HawaiiTaxCredits(year=YEAR)
        for i, u in units.iterrows():
            expected = credits.calculate_total_credits(
                agi=u["income"], filing_status=u["filing_status"],
                num_dependents=int(u["num_dependents"]),
                tax_before_credits=parts["before_credits"][i],
            )["total"]
            assert parts["credits"][i] == pytest.approx(expected)
        assert (parts["credits"] > 0).any()
        np.testing.assert_array_equal(parts["net"], parts["before_credits"] - parts["credits"])

    def test_refundable_excess_is_not_floored(self, calc, act46):
        # A low-income head of household with two children: the refundable
        # food/excise credit exceeds the tax, as in compare_systems.
        assert per_unit_tax(_projected(), act46, calc)[0] < 0


# ---------------------------------------------------------------------------
# 3. Statutory exemption count
# ---------------------------------------------------------------------------

class TestStatutoryExemptions:

    def test_counts(self):
        df = pd.DataFrame({
            "filing_status": ["single", "married_filing_jointly", "head_of_household",
                              "married_filing_separately", "married_filing_jointly"],
            "num_dependents": [0, 2, 2, 0, np.nan],
        })
        np.testing.assert_array_equal(exemption_counts(df), [1, 4, 3, 1, 2])

    def test_an_explicit_column_wins(self):
        df = pd.DataFrame({"filing_status": ["married_filing_jointly"],
                           "num_dependents": [2], "num_exemptions": [1.0]})
        np.testing.assert_array_equal(exemption_counts(df), [1.0])

    def test_scorer_uses_the_statutory_count(self, calc, act46):
        units = _projected()
        parts = calc.unit_liabilities(units, act46)
        joint = 2   # $95K joint return with two dependents: four exemptions
        u = units.iloc[joint]
        sd = calc.get_standard_deduction(act46.standard_deduction_year, u["filing_status"])
        expected = calc.calculate_tax(
            u["income"], act46, u["filing_status"], num_exemptions=4,
            deduction_override=max(sd, u["hi_itemized_deduction"]),
        )["tax_liability"]
        assert parts["before_credits"][joint] == pytest.approx(expected)

    def test_loop_and_vectorized_scorers_agree(self, calc, act46):
        units = _projected()
        loop = calc.calculate_revenue(units, act46)
        vec = calc.calculate_revenue_vectorized(units, act46)
        assert loop["total_revenue_millions"] == pytest.approx(vec["total_revenue_millions"], abs=1e-9)

    def test_exemption_increase_costs_every_exemption(self, calc, act46):
        import dataclasses

        units = _projected()
        doubled = dataclasses.replace(act46, name="doubled",
                                      personal_exemption=2 * act46.personal_exemption)
        with_count = compare_systems(units, act46, doubled, calculator=calc).iloc[2]
        one_each = units.assign(num_exemptions=1.0)
        old_way = compare_systems(one_each, act46, doubled, calculator=calc).iloc[2]
        assert with_count["revenue_millions"] < old_way["revenue_millions"] < 0


class TestRentersSeparateFilers:

    def test_separate_filers_use_their_own_threshold(self):
        credits = HawaiiTaxCredits(year=YEAR)
        assert credits.low_income_renters_credit(25_000, "married_filing_separately") == 0
        assert credits.low_income_renters_credit(15_000, "married_filing_separately") == 0.30 * 100
        assert credits.low_income_renters_credit(25_000, "single") == 0.30 * 50
