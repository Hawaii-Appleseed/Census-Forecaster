"""Phase E/F — DOTAX-calibrated income concentration model tests.

Tests for :mod:`tax_modeler.projection.revenue_concentration`.

Design
------
All tests are self-contained: no API calls, no DOTAX SOI CSV files required.
The module uses hardcoded calibrated representative incomes derived from
DOTAX Table 12A 2022 data (from ``DotaxTable12AValidator``).

Test organisation
-----------------
1. ``TestBracketData`` — validate the hardcoded DOTAX bracket entries.
2. ``TestExpectedRevenuePerFiler`` — core estimation function.
3. ``TestConcentrationMultiplier`` — calibration and economic properties.
4. ``TestTopQuintileFraction`` — revenue concentration tracking.
5. ``TestMakeConcentrationAdjustedFn`` — drop-in callable construction.
6. ``TestBracketCreepEffect`` — bracket creep as income grows.
7. ``TestB19082Interface`` — top_income_scale parameter for B19082 integration.
8. ``TestRevenueBacktestIntegration`` — concentration-adjusted fn in backtest.
9. ``TestConcentrationMarginalFn`` — effective marginal / delta-method helper.
"""
from __future__ import annotations

import math

import pytest
from tax_modeler.projection.hawaii_tax_real import hawaii_tax_weighted
from tax_modeler.projection.revenue_concentration import (
    _TOP_QUINTILE_BRACKET_START,
    DOTAX_2022_BRACKETS,
    DOTAX_2022_MEDIAN_INCOME,
    TOP_QUINTILE_REVENUE_SHARE_2022,
    concentration_marginal_fn,
    concentration_multiplier,
    expected_revenue_per_filer,
    make_concentration_adjusted_fn,
    top_quintile_revenue_fraction,
)

# ---------------------------------------------------------------------------
# 1. Bracket data validation
# ---------------------------------------------------------------------------

class TestBracketData:
    """Structural integrity of the hardcoded DOTAX 2022 bracket table."""

    def test_twelve_brackets(self):
        assert len(DOTAX_2022_BRACKETS) == 12

    def test_brackets_sorted_by_agi_min(self):
        lows = [b.agi_min for b in DOTAX_2022_BRACKETS]
        assert lows == sorted(lows)

    def test_top_bracket_has_inf_upper(self):
        assert DOTAX_2022_BRACKETS[-1].agi_max == math.inf

    def test_total_returns_matches_dotax(self):
        """Total return count matches DOTAX Table 12A: 635,117 returns."""
        total = sum(b.n_returns for b in DOTAX_2022_BRACKETS)
        assert total == 635_117

    def test_rep_incomes_positive_and_finite(self):
        for b in DOTAX_2022_BRACKETS:
            assert b.rep_income > 0, f"Non-positive rep_income for bracket {b.agi_min}"
            assert math.isfinite(b.rep_income)

    def test_rep_incomes_monotone(self):
        """Higher AGI bracket → higher representative income."""
        rep_incomes = [b.rep_income for b in DOTAX_2022_BRACKETS]
        assert rep_incomes == sorted(rep_incomes), (
            "Representative incomes are not monotone with AGI brackets"
        )

    def test_avg_tax_monotone(self):
        """Average tax is non-decreasing across brackets."""
        taxes = [b.avg_tax_before_credits for b in DOTAX_2022_BRACKETS]
        assert taxes == sorted(taxes)

    def test_top_quintile_bracket_index(self):
        """Top quintile starts at the $75k AGI bracket (index 6)."""
        assert DOTAX_2022_BRACKETS[_TOP_QUINTILE_BRACKET_START].agi_min == 75_000

    def test_top_quintile_revenue_constant(self):
        """Module-level constant matches expected value."""
        assert abs(TOP_QUINTILE_REVENUE_SHARE_2022 - 0.810) < 0.001


# ---------------------------------------------------------------------------
# 2. expected_revenue_per_filer
# ---------------------------------------------------------------------------

class TestExpectedRevenuePerFiler:
    """Core estimation function tests."""

    def test_dotax_2022_calibration_exact(self):
        """At the 2022 base year, model reproduces DOTAX $4,770 within 0.1%."""
        rev = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME)
        assert abs(rev - 4_770.0) / 4_770.0 < 0.001, (
            f"Calibration mismatch: model={rev:.2f}, DOTAX=4770.00"
        )

    def test_zero_income_returns_zero(self):
        assert expected_revenue_per_filer(0.0) == 0.0

    def test_negative_income_returns_zero(self):
        assert expected_revenue_per_filer(-10_000.0) == 0.0

    def test_returns_finite_positive_for_plausible_income(self):
        rev = expected_revenue_per_filer(90_000.0)
        assert math.isfinite(rev)
        assert rev > 0.0

    def test_revenue_increases_with_income(self):
        """Higher projected income → higher expected revenue."""
        revs = [expected_revenue_per_filer(m) for m in [70_000, 83_737, 100_000, 120_000]]
        for i in range(1, len(revs)):
            assert revs[i] > revs[i - 1], (
                f"Revenue not increasing at income index {i}"
            )

    def test_revenue_exceeds_dotax_base_when_income_grows(self):
        """When income grows 5%, projected revenue grows more than 5%."""
        base = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME)
        projected = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME * 1.05)
        assert projected > base * 1.05, (
            f"Revenue growth {(projected/base - 1):.3%} should exceed "
            f"income growth 5.0% (progressive bracket creep)"
        )

    def test_custom_tax_fn_respected(self):
        """Custom tax_fn is applied instead of default."""
        # A flat-rate tax fn (no concentration effect from distribution)
        flat_fn = lambda income: income * 0.05
        rev = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME, tax_fn=flat_fn)
        # Should be 5% of weighted-average income level, not 5% of median
        default_rev = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME)
        assert rev != pytest.approx(default_rev, rel=0.01)

    def test_order_of_magnitude_reasonable(self):
        """At 2022 median income, result should be in [$2k, $10k] range."""
        rev = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME)
        assert 2_000 < rev < 10_000

    def test_base_median_income_parameter(self):
        """Changing base_median_income scales the growth factor."""
        # If base is half the projected, growth = 2.0
        rev_2x = expected_revenue_per_filer(
            100_000, base_median_income=50_000
        )
        rev_1x = expected_revenue_per_filer(
            100_000, base_median_income=100_000
        )
        # 2x growth should give significantly more revenue
        assert rev_2x > rev_1x * 1.5, (
            f"Revenue at 2× growth ({rev_2x:.0f}) should be much higher than "
            f"revenue at 1× growth ({rev_1x:.0f})"
        )


# ---------------------------------------------------------------------------
# 3. concentration_multiplier
# ---------------------------------------------------------------------------

class TestConcentrationMultiplier:
    """concentration_multiplier = E[tax(Y)] / tax(median)."""

    def test_base_year_multiplier_less_than_one(self):
        """At the 2022 calibration: E[tax(Y)] < tax(median) because median
        is above the revenue-weighted mean (the $1M+ tail is balanced by the
        many low-income filers).  The calibrated model corrects the
        over-estimation of hawaii_tax_weighted at median income.
        """
        mult = concentration_multiplier(DOTAX_2022_MEDIAN_INCOME)
        assert mult < 1.0, (
            f"Concentration multiplier {mult:.3f} should be < 1.0 at base year: "
            "the bracket-weighted model gives less than tax(median) because "
            "most filers are below the median income level"
        )

    def test_multiplier_positive_and_finite(self):
        for income in [50_000, 83_737, 120_000, 200_000]:
            mult = concentration_multiplier(income)
            assert math.isfinite(mult) and mult > 0, (
                f"Non-positive multiplier {mult} at income={income}"
            )

    def test_multiplier_for_flat_tax_equals_mean_rep_income_ratio(self):
        """For a flat-rate tax, mult = mean(rep_income) / projected_median < 1.

        The DOTAX 2022 filer population is left-skewed: 129k returns in the
        $0–$10k bracket pull the weighted mean rep_income (~$65k) well below
        the ACS median household income ($83.7k).  A flat-rate tax preserves
        this ratio exactly, giving a multiplier ≈ 0.78.
        """
        flat_fn = lambda y: y * 0.05  # flat 5% — linear, no progressivity
        mult = concentration_multiplier(DOTAX_2022_MEDIAN_INCOME, tax_fn=flat_fn)
        # For a flat rate: mult = mean(rep_income) / median
        total_w = sum(b.n_returns * b.rep_income for b in DOTAX_2022_BRACKETS)
        total_n = sum(b.n_returns for b in DOTAX_2022_BRACKETS)
        expected = (total_w / total_n) / DOTAX_2022_MEDIAN_INCOME
        assert abs(mult - expected) < 1e-9, (
            f"Flat-tax multiplier {mult:.4f} should equal mean_rep/median {expected:.4f}"
        )
        # The mean filer income is below the ACS median → multiplier < 1
        assert mult < 1.0, "Flat-tax multiplier should be < 1 (mean filer income < ACS median)"
        assert mult > 0.5, "Flat-tax multiplier should be in a plausible range"

    def test_multiplier_less_than_one_at_median_for_real_brackets(self):
        """For the real bracket schedule, the multiplier at median is < 1.

        This is because the revenue-weighted mean income (weighted toward
        lower-income filers who are more numerous) is below the ACS median.
        The DOTAX $4,770 average is substantially below hawaii_tax_weighted($83,737).
        """
        mult = concentration_multiplier(DOTAX_2022_MEDIAN_INCOME, tax_fn=hawaii_tax_weighted)
        assert mult < 1.0


# ---------------------------------------------------------------------------
# 4. top_quintile_revenue_fraction
# ---------------------------------------------------------------------------

class TestTopQuintileFraction:
    """Revenue fraction from high-income filers."""

    def test_base_year_matches_constant(self):
        """At base year, fraction ≈ TOP_QUINTILE_REVENUE_SHARE_2022 = 0.810."""
        frac = top_quintile_revenue_fraction(DOTAX_2022_MEDIAN_INCOME)
        assert abs(frac - TOP_QUINTILE_REVENUE_SHARE_2022) < 0.005, (
            f"Fraction {frac:.3f} not near 0.810"
        )

    def test_fraction_in_01(self):
        for income in [70_000, 83_737, 100_000, 150_000]:
            frac = top_quintile_revenue_fraction(income)
            assert 0.0 <= frac <= 1.0, f"Fraction {frac} out of [0,1] at {income}"

    def test_fraction_stable_under_proportional_income_growth(self):
        """Under uniform proportional scaling, top-quintile fraction stays roughly stable.

        The model fixes the 2022 DOTAX filer distribution (bracket counts are
        constants) and scales rep_incomes proportionally.  Real-world bracket
        creep (filers crossing into higher nominal brackets) is NOT modelled
        here; that effect requires a separate nominal-bracket-fixed model.

        Under proportional scaling:
        - Top bracket ($1.1M) is already at the ceiling marginal rate → grows
          proportionally to income.
        - Middle brackets ($40k–$100k) enter steeper rate zones as they scale →
          their tax grows faster than proportionally.
        The net effect: the top-quintile share changes by only a few percent
        and can drift slightly in either direction.  The fraction should remain
        in the range [0.70, 0.90] across a wide range of income levels.
        """
        for scale in [0.8, 1.0, 1.2, 1.5, 2.0]:
            income = DOTAX_2022_MEDIAN_INCOME * scale
            frac = top_quintile_revenue_fraction(income)
            assert 0.70 < frac < 0.90, (
                f"At income_scale={scale}, top-quintile fraction {frac:.3f} "
                "outside expected range [0.70, 0.90]"
            )

    def test_81_pct_top_quintile_revenue_at_base(self):
        """Top 28% of filers generate ~81% of revenue — the key Phase E finding."""
        frac = top_quintile_revenue_fraction(DOTAX_2022_MEDIAN_INCOME)
        assert 0.78 <= frac <= 0.84, (
            f"Top quintile revenue share {frac:.3f} not in expected 78-84% range"
        )


# ---------------------------------------------------------------------------
# 5. make_concentration_adjusted_fn
# ---------------------------------------------------------------------------

class TestMakeConcentrationAdjustedFn:
    """Verify the drop-in callable construction."""

    def test_returns_callable(self):
        fn = make_concentration_adjusted_fn()
        assert callable(fn)

    def test_callable_matches_expected_revenue(self):
        """Returned callable gives same result as expected_revenue_per_filer."""
        fn = make_concentration_adjusted_fn()
        income = 90_000.0
        assert fn(income) == pytest.approx(
            expected_revenue_per_filer(income), rel=1e-9
        )

    def test_callable_zero_income(self):
        fn = make_concentration_adjusted_fn()
        assert fn(0.0) == 0.0

    def test_custom_base_respected(self):
        """Custom base_median_income changes the calibration point."""
        fn_50k = make_concentration_adjusted_fn(base_median_income=50_000)
        fn_84k = make_concentration_adjusted_fn(base_median_income=83_737)
        income = 100_000.0
        # Different base → different growth factor → different result
        assert fn_50k(income) != pytest.approx(fn_84k(income), rel=0.01), (
            "Different base_median_income should give different results"
        )


# ---------------------------------------------------------------------------
# 6. Bracket creep effect
# ---------------------------------------------------------------------------

class TestBracketCreepEffect:
    """Revenue grows faster than income due to progressive bracket structure."""

    def test_revenue_elasticity_exceeds_one(self):
        """Revenue-to-income elasticity > 1 for progressive brackets.

        A 10% income growth should produce more than 10% revenue growth
        (bracket creep effect).
        """
        income_1 = DOTAX_2022_MEDIAN_INCOME
        income_2 = income_1 * 1.10  # 10% growth
        rev_1 = expected_revenue_per_filer(income_1)
        rev_2 = expected_revenue_per_filer(income_2)
        income_growth = income_2 / income_1 - 1  # 0.10
        revenue_growth = rev_2 / rev_1 - 1
        elasticity = revenue_growth / income_growth
        assert elasticity > 1.0, (
            f"Revenue elasticity {elasticity:.3f} should exceed 1.0 "
            "(progressive tax amplifies income growth)"
        )

    def test_revenue_grows_faster_than_income(self):
        """5% income growth → more than 5% revenue growth."""
        base = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME)
        proj = expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME * 1.05)
        assert proj / base > 1.05

    def test_elasticity_reasonable_range(self):
        """Revenue elasticity should be in [1.0, 2.5] at Hawaii median income."""
        income_growth = 0.01
        income_1 = DOTAX_2022_MEDIAN_INCOME
        income_2 = income_1 * (1 + income_growth)
        rev_1 = expected_revenue_per_filer(income_1)
        rev_2 = expected_revenue_per_filer(income_2)
        elasticity = ((rev_2 / rev_1 - 1) / income_growth)
        assert 1.0 < elasticity < 2.5, (
            f"Revenue elasticity {elasticity:.3f} outside plausible range [1, 2.5]"
        )


# ---------------------------------------------------------------------------
# 7. B19082 interface — top_income_scale parameter
# ---------------------------------------------------------------------------

class TestB19082Interface:
    """Test the top_income_scale parameter for B19082 integration."""

    def test_no_scale_is_baseline(self):
        """top_income_scale=None gives same result as uniform growth."""
        income = 90_000.0
        rev_uniform = expected_revenue_per_filer(income, top_income_scale=None)
        # Uniform growth (scale=1.0 means top brackets grow same as median)
        rev_scale1 = expected_revenue_per_filer(income, top_income_scale=1.0)
        assert rev_uniform == pytest.approx(rev_scale1, rel=1e-9)

    def test_top_income_above_1_increases_revenue(self):
        """When top-income brackets grow faster, revenue increases more."""
        income = 90_000.0
        rev_baseline = expected_revenue_per_filer(income, top_income_scale=1.0)
        rev_top_growth = expected_revenue_per_filer(income, top_income_scale=1.10)
        assert rev_top_growth > rev_baseline, (
            "Top-income scale > 1 should increase revenue vs uniform growth"
        )

    def test_top_income_below_1_decreases_revenue(self):
        """When top-income brackets contract (e.g. high-earner out-migration), revenue falls."""
        income = 90_000.0
        rev_baseline = expected_revenue_per_filer(income, top_income_scale=1.0)
        rev_top_decline = expected_revenue_per_filer(income, top_income_scale=0.90)
        assert rev_top_decline < rev_baseline, (
            "Top-income scale < 1 should decrease revenue vs uniform growth"
        )

    def test_top_income_sensitivity_proportional_to_revenue_share(self):
        """Revenue lift from top-only 10% growth is ~81% of uniform 10% growth.

        The key Phase E insight: top-quintile filers generate ~81% of revenue.
        Therefore:
        - A 10% shock to only top brackets (bottom brackets unchanged) should
          contribute roughly 81% of the revenue lift that uniform 10% growth
          would generate.
        - The remaining ~19% comes from lower-income brackets not reached.

        In the model: ``top_income_scale=1.10`` at base projected_median means
        top brackets scale by 1.10 while bottom brackets are unchanged (scale=1.0).
        Uniform growth means ALL brackets scale by 1.10.

        So: top_lift / median_lift ≈ 0.81 (top quintile revenue share).
        """
        base_income = DOTAX_2022_MEDIAN_INCOME
        rev_base = expected_revenue_per_filer(base_income)

        # Uniform 10% growth (all brackets scale by 1.10)
        rev_uniform_10pct = expected_revenue_per_filer(base_income * 1.10, top_income_scale=1.0)
        # Top-only 10% growth (top brackets scale by 1.10, bottom unchanged)
        rev_top_10pct = expected_revenue_per_filer(base_income, top_income_scale=1.10)

        uniform_lift = rev_uniform_10pct / rev_base - 1
        top_lift = rev_top_10pct / rev_base - 1

        # Top-only lift should be in [60%, 95%] of uniform lift,
        # reflecting that top brackets generate ~81% of revenue.
        ratio = top_lift / uniform_lift
        assert 0.60 < ratio < 0.95, (
            f"top_lift ({top_lift:.3%}) should be 60-95% of uniform_lift ({uniform_lift:.3%}); "
            f"got ratio={ratio:.3f}.  Expected ~0.81 (top quintile revenue share)."
        )
        # Both must be positive (income growth must increase revenue)
        assert top_lift > 0 and uniform_lift > 0

    def test_b19082_workflow_documented(self):
        """Demonstrate expected B19082 integration workflow (no real data)."""
        # Suppose B19082_005E shows top quintile share grew from 50% to 52%
        # (a 4% increase in top-quintile share while median income grew 5%)
        base_top_share = 0.50
        projected_top_share = 0.52
        base_median = DOTAX_2022_MEDIAN_INCOME
        projected_median = base_median * 1.05  # 5% median growth

        # top_income_scale = (top_share_projected / top_share_base)
        top_income_scale = projected_top_share / base_top_share  # = 1.04

        rev_with_b19082 = expected_revenue_per_filer(
            projected_median, top_income_scale=top_income_scale
        )
        rev_without_b19082 = expected_revenue_per_filer(
            projected_median, top_income_scale=1.0
        )
        # Using B19082 (top-income growing faster) should give more revenue
        assert rev_with_b19082 > rev_without_b19082


# ---------------------------------------------------------------------------
# 8. Revenue backtest integration
# ---------------------------------------------------------------------------

class TestRevenueBacktestIntegration:
    """Smoke tests wiring the concentration model into run_revenue_backtest."""

    @pytest.fixture(scope="class")
    def panel_growing(self):
        from common.models import AcsObservation
        return {
            ("15003", "B19013_001E"): [
                AcsObservation(
                    estimate=75_000 * (1.025 ** i),
                    moe=1_000.0,
                    year=y,
                    vintage="1y",
                    geoid="15003",
                    indicator="B19013_001E",
                )
                for i, y in enumerate(range(2010, 2025))
            ]
        }

    def test_concentration_fn_as_tax_fn_in_backtest(self, panel_growing):
        """make_concentration_adjusted_fn() works as a drop-in tax_fn."""
        from tax_modeler.projection.hawaii_tax_real import hawaii_marginal_weighted
        from tax_modeler.projection.revenue_backtest import run_revenue_backtest

        conc_fn = make_concentration_adjusted_fn()
        summaries = run_revenue_backtest(
            panel_growing,
            anchors=[2018, 2020, 2022],
            horizon=2,
            tax_fn=conc_fn,
            marginal_fn=hawaii_marginal_weighted,
        )
        for name, s in summaries.items():
            if s.n > 0:
                assert math.isfinite(s.mean_abs_pct_error), (
                    f"{name}: non-finite MAPE with concentration tax_fn"
                )

    def test_concentration_mape_reasonable(self, panel_growing):
        """Revenue MAPE with concentration model < 20% on growing series."""
        from tax_modeler.projection.hawaii_tax_real import hawaii_marginal_weighted
        from tax_modeler.projection.revenue_backtest import run_revenue_backtest

        conc_fn = make_concentration_adjusted_fn()
        summaries = run_revenue_backtest(
            panel_growing,
            anchors=[2018, 2020, 2022],
            horizon=2,
            tax_fn=conc_fn,
            marginal_fn=hawaii_marginal_weighted,
        )
        for name, s in summaries.items():
            if s.n > 0:
                assert s.mean_abs_pct_error < 0.20, (
                    f"Method {name}: MAPE {s.mean_abs_pct_error:.3f} exceeds 20%"
                )

    def test_ensemble_beats_carry_forward_with_concentration_fn(self, panel_growing):
        """Core ship gate: ensemble MAPE < carry_forward MAPE with concentration model."""
        from tax_modeler.projection.hawaii_tax_real import hawaii_marginal_weighted
        from tax_modeler.projection.revenue_backtest import run_revenue_backtest

        conc_fn = make_concentration_adjusted_fn()
        summaries = run_revenue_backtest(
            panel_growing,
            anchors=[2018, 2020, 2022],
            horizon=2,
            tax_fn=conc_fn,
            marginal_fn=hawaii_marginal_weighted,
        )
        assert summaries["ensemble"].mean_abs_pct_error < \
               summaries["carry_forward"].mean_abs_pct_error


# ---------------------------------------------------------------------------
# 9. concentration_marginal_fn — effective marginal for SE propagation
# ---------------------------------------------------------------------------

class TestConcentrationMarginalFn:
    """Tests for concentration_marginal_fn (Phase F delta-method helper)."""

    def test_positive_at_median(self):
        """Marginal is strictly positive at the 2022 base income."""
        mr = concentration_marginal_fn(DOTAX_2022_MEDIAN_INCOME)
        assert mr > 0, f"Marginal {mr} should be positive at median income"

    def test_finite_at_median(self):
        mr = concentration_marginal_fn(DOTAX_2022_MEDIAN_INCOME)
        assert math.isfinite(mr), f"Non-finite marginal {mr} at median"

    def test_zero_income_returns_zero(self):
        assert concentration_marginal_fn(0.0) == 0.0
        assert concentration_marginal_fn(-1.0) == 0.0

    def test_less_than_one(self):
        """Effective marginal is a rate, must be in (0, 1) for any income."""
        for income in [50_000, 83_737, 150_000, 300_000]:
            mr = concentration_marginal_fn(income)
            assert 0 < mr < 1.0, (
                f"Marginal {mr:.4f} at {income} outside (0, 1)"
            )

    def test_consistent_with_central_difference(self):
        """Value matches an independent finite difference with different step."""
        income = 90_000.0
        mr_fn = concentration_marginal_fn(income)  # uses eps_fraction=1e-5
        h = 500.0  # independent step
        mr_manual = (
            expected_revenue_per_filer(income + h)
            - expected_revenue_per_filer(income - h)
        ) / (2 * h)
        assert abs(mr_fn - mr_manual) < 1e-4, (
            f"concentration_marginal_fn {mr_fn:.6f} disagrees with "
            f"manual diff {mr_manual:.6f} (diff={abs(mr_fn-mr_manual):.2e})"
        )

    def test_increases_with_income(self):
        """Effective marginal is non-decreasing in income (progressive model)."""
        incomes = [40_000, 70_000, 100_000, 150_000, 250_000]
        marginals = [concentration_marginal_fn(y) for y in incomes]
        for i in range(1, len(marginals)):
            assert marginals[i] >= marginals[i - 1] - 1e-6, (
                f"Marginal not non-decreasing: "
                f"{incomes[i-1]}→{incomes[i]}: "
                f"{marginals[i-1]:.5f}→{marginals[i]:.5f}"
            )

    def test_se_propagation_positive(self):
        """Delta-method SE propagation: se_rev = marginal × se_income > 0."""
        income = DOTAX_2022_MEDIAN_INCOME
        se_income = 5_000.0
        mr = concentration_marginal_fn(income)
        se_rev = abs(mr) * se_income
        assert se_rev > 0, f"SE propagation produced {se_rev}"
        assert math.isfinite(se_rev)

    def test_custom_base_median_respected(self):
        """Different base_median_income changes the effective marginal."""
        income = 90_000.0
        mr_default = concentration_marginal_fn(income)
        mr_50k_base = concentration_marginal_fn(income, base_median_income=50_000.0)
        # Different base → different growth factor → different result
        assert mr_default != pytest.approx(mr_50k_base, rel=0.01), (
            "Different base_median_income should give different marginals"
        )

    def test_custom_tax_fn_respected(self):
        """Flat-rate tax_fn gives zero marginal (no bracket structure)."""
        # For a flat rate, d(rate × income × growth)/d(income) = rate × (mean_rep/base)
        # which is a constant independent of income (given fixed base).
        flat_rate = 0.05
        flat_fn = lambda y: flat_rate * y
        mr = concentration_marginal_fn(DOTAX_2022_MEDIAN_INCOME, tax_fn=flat_fn)
        # For flat tax: marginal = flat_rate × mean(rep_income) / base_median
        total_w = sum(b.n_returns * b.rep_income for b in DOTAX_2022_BRACKETS)
        total_n = sum(b.n_returns for b in DOTAX_2022_BRACKETS)
        expected_mr = flat_rate * (total_w / total_n) / DOTAX_2022_MEDIAN_INCOME
        assert abs(mr - expected_mr) < 1e-6, (
            f"Flat-tax marginal {mr:.6f} should equal {expected_mr:.6f}"
        )
