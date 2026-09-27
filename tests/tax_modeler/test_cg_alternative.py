"""The HRS §235-51(f) alternative tax on capital gains
(``tax_modeler.liability.cg_alternative``) and ``TaxSystemConfig``'s switch
between it and the legacy stacked shortcut the registry systems keep.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from tax_modeler.config.tax_system_config import (
    TaxCalculator,
    TaxSystemRegistry,
    itemized_deductions,
)
from tax_modeler.liability.cg_alternative import alternative_tax, cap_floor
from tax_modeler.reform.income_tax_spec import current_law_system
from tax_modeler.simulator.systems import system_to_json

YEARS = (2027, 2028, 2029, 2030, 2031)
# floors, decimal rates, tax owed at each floor (TaxCalculator._bracket_schedule's form)
FLOORS = np.array([0.0, 10_000.0, 50_000.0])
RATES = np.array([0.02, 0.06, 0.10])
CUM = np.array([0.0, 200.0, 2_600.0])


@pytest.fixture(scope="module")
def calc():
    return TaxCalculator()


def _units(n, seed=0):
    rng = np.random.default_rng(seed)
    statuses = np.array(["single", "married_filing_jointly", "head_of_household",
                         "married_filing_separately"])
    income = rng.lognormal(11.5, 1.4, n)
    income[: n // 4] = rng.uniform(300_000, 6_000_000, n // 4)
    return pd.DataFrame({
        "income": income, "filing_status": statuses[rng.integers(0, 4, n)],
        "num_dependents": rng.integers(0, 4, n), "weight": rng.uniform(1, 50, n),
        "hi_itemized_deduction": rng.uniform(0, 1, n) * income * 0.2,
        "synthetic_cg_share": np.where(rng.random(n) < 0.5, rng.uniform(0, 0.9, n), 0.0),
    })


def _tax(ti, gains, cap):
    return alternative_tax(np.atleast_1d(np.asarray(ti, float)), np.atleast_1d(np.asarray(gains, float)),
                           FLOORS, RATES, CUM, cap)


def _regular(ti):
    return _tax(ti, np.zeros(np.size(ti)), None)


# ---------------------------------------------------------------------------
# the formula
# ---------------------------------------------------------------------------

class TestAlternativeTax:

    def test_cap_floor(self):
        assert cap_floor(FLOORS, RATES, 0.0725) == 50_000      # first rate >= 7.25%: 10%
        assert cap_floor(FLOORS, RATES, 0.06) == 10_000        # a rate equal to the cap reaches it
        assert cap_floor(FLOORS, RATES, 0.0) == 0
        assert cap_floor(FLOORS, RATES, 0.11) == np.inf
        # rates falling with income: the first bracket that reaches the cap
        assert cap_floor(np.array([0.0, 1e4, 5e4]), np.array([0.09, 0.05, 0.12]), 0.0725) == 0

    @pytest.mark.parametrize("ti, gains, want", [
        (40_000, 20_000, 200 + 30_000 * 0.06),            # below the cap's floor: bracket rates
        (80_000, 50_000, 2_600 + 0.0725 * 30_000),        # straddling it: 6% to $50K, then 7.25%
        (200_000, 50_000, 2_600 + 100_000 * 0.10 + 0.0725 * 50_000),   # above it
        (30_000, 100_000, 200 + 20_000 * 0.06),           # gains above taxable income, below the floor
        (70_000, 1_000_000, 2_600 + 0.0725 * 20_000),     # gains above taxable income, above it
        (0, 5_000, 0.0),
    ])
    def test_hand_cases(self, ti, gains, want):
        assert _tax(ti, gains, 0.0725)[0] == pytest.approx(want, rel=1e-12)

    @pytest.mark.parametrize("floors, pct, ti, gains, regular, want, literal", [
        # 1% to $10K, 10% to $20K, 5% to $40K, 12%: (B) is $10K, not the
        # $20K taxed below 7.25% (the 1% and 5% brackets together)
        ([0, 10_000, 20_000, 40_000], [1, 10, 5, 12], 30_000, 25_000, 1_600.0, 1_550.0, 1_600.0),
        # 1.4% to $48K, 8% to $200K, 6% to $500K, 13%: (B) is $48K, not $348K
        ([0, 48_000, 200_000, 500_000], [1.4, 8, 6, 13], 600_000, 500_000, 43_832.0, 41_082.0, 39_982.0),
    ])
    def test_rates_falling_below_the_cap_use_the_first_bracket_that_reaches_it(
            self, floors, pct, ti, gains, regular, want, literal):
        """Where a rate falls back below the cap, (B), "taxable income taxed
        at a rate below" the cap, is read as the floor of the first bracket
        whose rate reaches it. The literal sum of the below-cap segments can
        give more or less tax; the page discloses the reading (plan.js
        ``falling_cg`` warning, the Capital Gains section). Rates that never
        fall are unaffected: the below-cap brackets are then a prefix."""
        f, r = np.array(floors, float), np.array(pct, float) / 100
        cum = np.concatenate([[0.0], np.cumsum(np.diff(f) * r[:-1])])
        got = alternative_tax(np.array([ti], float), np.array([gains], float), f, r, cum, 0.0725)[0]
        assert alternative_tax(np.array([ti], float), np.zeros(1), f, r, cum, 0.0725)[0] == pytest.approx(regular)
        assert got == pytest.approx(want, rel=1e-12)
        # the literal reading, for the record
        below = sum(max(0.0, min(ti, top) - lo) for lo, top, rate in zip(f, [*f[1:], np.inf], r)
                    if rate < 0.0725)
        base = max(ti - min(gains, ti), below)
        idx = np.searchsorted(f, base, side="right") - 1
        lit = min(regular, cum[idx] + (base - f[idx]) * r[idx] + 0.0725 * (ti - base))
        assert lit == pytest.approx(literal)

    def test_zero_gains_is_the_regular_tax_exactly(self):
        ti = np.linspace(0, 3e6, 1_001)
        for cap in (0.0, 0.05, 0.0725, 0.09, 0.10, 0.2):
            np.testing.assert_array_equal(_tax(ti, np.zeros_like(ti), cap), _regular(ti))

    def test_a_cap_above_every_rate_is_the_regular_tax_exactly(self):
        rng = np.random.default_rng(1)
        ti, g = rng.uniform(0, 3e6, 2_000), rng.uniform(0, 3e6, 2_000)
        for cap in (0.1000001, 0.2, 0.99):
            np.testing.assert_array_equal(_tax(ti, g, cap), _regular(ti))
        # at the top rate itself, equal up to rounding
        np.testing.assert_allclose(_tax(ti, g, 0.10), _regular(ti), rtol=1e-12)

    def test_cap_zero_leaves_gains_untaxed(self):
        rng = np.random.default_rng(2)
        ti, g = rng.uniform(0, 3e6, 2_000), rng.uniform(0, 3e6, 2_000)
        np.testing.assert_array_equal(_tax(ti, g, 0.0), _regular(ti - np.minimum(g, ti)))

    def test_no_alternative_tax_is_the_regular_tax(self):
        rng = np.random.default_rng(3)
        ti, g = rng.uniform(0, 3e6, 500), rng.uniform(0, 3e6, 500)
        np.testing.assert_array_equal(_tax(ti, g, None), _regular(ti))

    def test_never_above_the_regular_tax_and_rises_with_the_rate(self):
        rng = np.random.default_rng(4)
        ti, g = rng.uniform(0, 3e6, 2_000), rng.uniform(0, 3e6, 2_000)
        prev = _tax(ti, g, 0.0)
        for cap in (0.03, 0.0725, 0.09, 0.2):
            t = _tax(ti, g, cap)
            assert (t <= _regular(ti)).all() and (t >= prev - 1e-9).all()
            prev = t


# ---------------------------------------------------------------------------
# TaxCalculator: statute vs the stacked shortcut
# ---------------------------------------------------------------------------

def _stacked_before_credits(calc, tax_units, config):
    """TaxCalculator.unit_liabilities' before-credit tax at 65e1a6e, verbatim
    (the stacked shortcut; default column names)."""
    from tax_modeler.liability.hawaii import statutory_exemption_counts

    n = len(tax_units)
    incomes = tax_units["income"].to_numpy(dtype=float)
    statuses = tax_units["filing_status"].to_numpy()
    if "num_dependents" in tax_units.columns:
        raw_dep = tax_units["num_dependents"].to_numpy(dtype=float)
    else:
        raw_dep = np.zeros(n, dtype=float)
    if "num_exemptions" in tax_units.columns:
        raw_exemp = tax_units["num_exemptions"].to_numpy(dtype=float)
    else:
        raw_exemp = statutory_exemption_counts(statuses, raw_dep)
    nan_exemp = np.isnan(raw_exemp)
    num_exemptions = np.where(nan_exemp, 1.0, raw_exemp)
    sd_per_filer = np.empty(n, dtype=float)
    for fs in np.unique(statuses):
        sd_per_filer[statuses == fs] = calc.standard_deduction_for(config, fs)
    itemized = itemized_deductions(tax_units)
    deductions = sd_per_filer if itemized is None else np.maximum(sd_per_filer, itemized)

    exemption_amount = num_exemptions * config.personal_exemption
    taxable_full = np.maximum(0.0, incomes - deductions - exemption_amount)

    # CG income for §235-16 cap: auto-detect synthetic_cg_share column.
    cg_shares = (
        tax_units["synthetic_cg_share"].fillna(0.0).to_numpy(dtype=float)
        if "synthetic_cg_share" in tax_units.columns
        else np.zeros(n, dtype=float)
    )
    cg_income = np.maximum(0.0, incomes * cg_shares)
    has_cg = cg_income > 0

    # Ordinary taxable = max(0, ordinary_agi - ded - exemption)
    ordinary_agi = np.maximum(0.0, incomes - cg_income)
    taxable_ordinary = np.maximum(0.0, ordinary_agi - deductions - exemption_amount)

    # Bracket tax (vectorized): full and ordinary
    tax_full = calc._vectorized_bracket_tax(taxable_full, statuses, config)
    # Skip the ordinary pass when no CG present anywhere
    if has_cg.any():
        tax_ordinary = calc._vectorized_bracket_tax(taxable_ordinary, statuses, config)
        cg_tax_uncapped = np.maximum(0.0, tax_full - tax_ordinary)
        cg_tax_capped = np.minimum(cg_tax_uncapped, cg_income * calc.HAWAII_CG_CAP_RATE)
        liabilities = np.where(has_cg, tax_ordinary + cg_tax_capped, tax_full)
    else:
        liabilities = tax_full
    liabilities[nan_exemp] = 0.0
    return liabilities


class TestTaxCalculator:

    @pytest.mark.parametrize("factory", [TaxSystemRegistry.get_act46_system,
                                         TaxSystemRegistry.get_sb3125_cd2_system])
    def test_registry_systems_keep_the_stacked_shortcut(self, factory):
        for y in YEARS:
            cfg = factory(y)
            assert (cfg.cg_alt_tax, cfg.capital_gains_rate_pct) == ("stacked", 7.25)
            assert current_law_system(y).cg_alt_tax == "statute"

    def test_the_shortcut_is_7_25_only(self):
        cfg = TaxSystemRegistry.get_sb3125_cd2_system(2027)
        with pytest.raises(ValueError, match="stacked"):
            dataclasses.replace(cfg, capital_gains_rate_pct=9.0)
        with pytest.raises(ValueError, match="cg_alt_tax"):
            dataclasses.replace(cfg, cg_alt_tax="statutory")
        dataclasses.replace(cfg, cg_alt_tax="statute", capital_gains_rate_pct=None)   # fine

    @pytest.mark.parametrize("factory", [TaxSystemRegistry.get_act46_system,
                                         TaxSystemRegistry.get_sb3125_cd2_system])
    def test_stacked_is_bitwise_the_old_code(self, calc, factory):
        units = _units(3_000, seed=5)
        for y in YEARS:
            np.testing.assert_array_equal(calc.unit_liabilities(units, factory(y))["before_credits"],
                                          _stacked_before_credits(calc, units, factory(y)))

    def test_statute_at_most_stacked_at_most_regular(self, calc):
        units = _units(4_000, seed=6)
        for y in YEARS:
            stacked = calc.unit_liabilities(units, TaxSystemRegistry.get_sb3125_cd2_system(y))
            statute = calc.unit_liabilities(units, current_law_system(y))
            regular = calc.unit_liabilities(
                units, dataclasses.replace(current_law_system(y), capital_gains_rate_pct=None))
            s, k, r = (u["before_credits"] for u in (statute, stacked, regular))
            assert (s <= k + 1e-9).all() and (k <= r + 1e-9).all()
            assert (s < k - 1).any() and (k < r - 1).any()
            # credits follow the tax they are limited by
            np.testing.assert_array_equal(statute["net"], s - statute["credits"])

    def test_statute_without_gains_is_the_bracket_tax(self, calc):
        units = _units(2_000, seed=7).assign(synthetic_cg_share=0.0)
        for y in (2027, 2031):
            a = calc.unit_liabilities(units, TaxSystemRegistry.get_sb3125_cd2_system(y))
            b = calc.unit_liabilities(units, current_law_system(y))
            for key in a:
                np.testing.assert_array_equal(a[key], b[key])

    def test_calculate_tax_applies_the_statute(self, calc):
        """calculate_tax does not raise for statutory systems (calculate_revenue
        would turn an exception into a silent zero) and agrees with
        unit_liabilities."""
        units = _units(300, seed=8)
        units["num_exemptions"] = 1
        for cfg in (current_law_system(2027),
                    dataclasses.replace(current_law_system(2029), capital_gains_rate_pct=9.0),
                    dataclasses.replace(current_law_system(2029), capital_gains_rate_pct=None)):
            want = calc.unit_liabilities(units, cfg)["before_credits"]
            ded = np.maximum([calc.standard_deduction_for(cfg, fs) for fs in units["filing_status"]],
                             units["hi_itemized_deduction"])
            got = [calc.calculate_tax(r.income, cfg, r.filing_status, 1, deduction_override=d,
                                      cg_income=r.income * r.synthetic_cg_share)["tax_liability"]
                   for r, d in zip(units.itertuples(), ded, strict=True)]
            np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-6)

    def test_loop_and_vectorized_scorers_agree_under_the_statute(self, calc):
        # calculate_revenue's loop logs and scores 0 for any unit calculate_tax
        # raises on, so an exception there would be a silent revenue loss
        units = _units(400, seed=10)
        for cfg in (current_law_system(2027),
                    dataclasses.replace(current_law_system(2031), capital_gains_rate_pct=9.0),
                    dataclasses.replace(current_law_system(2031), capital_gains_rate_pct=None)):
            loop = calc.calculate_revenue(units, cfg)
            vec = calc.calculate_revenue_vectorized(units, cfg)
            assert loop["total_revenue_millions"] == pytest.approx(vec["total_revenue_millions"],
                                                                   rel=1e-12)

    def test_system_to_json_emits_the_rate(self, calc):
        cfg = current_law_system(2027)
        assert system_to_json(cfg, calc)["capital_gains_rate"] == 7.25
        assert system_to_json(dataclasses.replace(cfg, capital_gains_rate_pct=9.0),
                              calc)["capital_gains_rate"] == 9.0
        assert system_to_json(dataclasses.replace(cfg, capital_gains_rate_pct=None),
                              calc)["capital_gains_rate"] == "ordinary"
        with pytest.raises(ValueError, match="cg_alt_tax"):
            system_to_json(TaxSystemRegistry.get_sb3125_cd2_system(2027), calc)


# ---------------------------------------------------------------------------
# the capital-gains page's Scorer
# ---------------------------------------------------------------------------

def _old_scorer_tax(sc, income, nltcg, cap):
    """forecast_cg_rate_options.Scorer.tax at 65e1a6e, verbatim."""
    ti = sc.taxable(income)
    out = np.zeros(len(ti))
    for fs, (floors, rates, cum) in sc.sched.items():
        m = sc.status == fs
        regular, _ = sc._bracket(ti[m], floors, rates, cum)
        if cap is None:
            out[m] = regular
            continue
        # "taxable income taxed at a rate below" the cap: the floor of the
        # first bracket whose rate reaches it.
        at_or_above = rates >= cap - 1e-12
        below_cap_top = floors[np.argmax(at_or_above)] if at_or_above.any() else np.inf
        ncg = np.clip(nltcg[m], 0.0, ti[m])
        base = np.maximum(ti[m] - ncg, np.minimum(ti[m], below_cap_top))
        alt = sc._bracket(base, floors, rates, cum)[0] + cap * (ti[m] - base)
        out[m] = np.minimum(regular, alt)
    return out


def test_page_scorer_tax_is_bitwise_the_old_body(calc, cg_page):
    units = _units(3_000, seed=9)
    income = units["income"].to_numpy()
    nltcg = income * units["synthetic_cg_share"].to_numpy()
    for cfg in (TaxSystemRegistry.get_sb3125_cd2_system(2027), TaxSystemRegistry.get_act46_system(2029),
                current_law_system(2031)):
        sc = cg_page.Scorer(units, cfg, calc)
        for cap in (0.0725, 0.09, None, 0.0, 0.2):
            for inc in (income, income * 0.93):
                np.testing.assert_array_equal(sc.tax(inc, nltcg, cap), _old_scorer_tax(sc, inc, nltcg, cap))
