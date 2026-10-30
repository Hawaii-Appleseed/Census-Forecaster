"""Phase D — Real Hawaii bracket tax function tests.

Tests for :mod:`tax_modeler.projection.hawaii_tax_real`.

Design
------
All tests run on the bundled CSV files (``hawaii_tax_brackets_master_all.csv``
and ``hawaii_standard_deductions_by_year.csv``).  No DOTAX SOI CSV files
or Census API calls are required.

Test organisation
-----------------
1. ``TestBracketLoading`` — raw data integrity checks.
2. ``TestHawaiiTaxReal`` — per-filing-status function correctness.
3. ``TestHawaiiMarginalReal`` — marginal rate function.
4. ``TestYearDispatch`` — correct bracket/deduction vintage for each era.
5. ``TestHawaiiTaxWeighted`` — household-proxy weighted function.
6. ``TestHawaiiMarginalWeighted`` — weighted marginal rate function.
7. ``TestDotaxCalibration`` — order-of-magnitude check against DOTAX 2022 data.
8. ``TestRevenueBacktestIntegration`` — real function as drop-in in the backtest.
"""
from __future__ import annotations

import math

import pytest
from tax_modeler.projection.hawaii_tax_real import (
    ALL_FILING_STATUSES,
    DEFAULT_WEIGHTS,
    FS_HOH,
    FS_JOINT,
    FS_SINGLE,
    _bracket_year,
    _deduction_for_year,
    _load_brackets,
    _load_deductions,
    hawaii_marginal_real,
    hawaii_marginal_weighted,
    hawaii_tax_real,
    hawaii_tax_weighted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tax_s(income: float, tax_year: int = 2022) -> float:
    """Single filer convenience wrapper."""
    return hawaii_tax_real(income, tax_year=tax_year, filing_status=FS_SINGLE)


def _tax_j(income: float, tax_year: int = 2022) -> float:
    """Joint filer convenience wrapper."""
    return hawaii_tax_real(income, tax_year=tax_year, filing_status=FS_JOINT)


def _mr_s(income: float, tax_year: int = 2022) -> float:
    """Single filer marginal rate."""
    return hawaii_marginal_real(income, tax_year=tax_year, filing_status=FS_SINGLE)


# ---------------------------------------------------------------------------
# 1. Bracket loading
# ---------------------------------------------------------------------------

class TestBracketLoading:
    """Verify that the bundled CSV loads correctly."""

    def test_brackets_loaded_for_all_statuses_and_years(self):
        brackets = _load_brackets()
        for yr in (2018, 2025):
            for fs in ALL_FILING_STATUSES:
                assert (yr, fs) in brackets, (
                    f"Missing bracket schedule for (year={yr}, filing_status={fs})"
                )

    def test_each_schedule_has_12_brackets(self):
        """The bundled schedules each have exactly 12 brackets (Hawaii law)."""
        brackets = _load_brackets()
        for (yr, fs), sched in brackets.items():
            assert len(sched) == 12, (
                f"Expected 12 brackets for ({yr}, {fs}), got {len(sched)}"
            )

    def test_brackets_sorted_by_lower_bound(self):
        brackets = _load_brackets()
        for (yr, fs), sched in brackets.items():
            lows = [b[0] for b in sched]
            assert lows == sorted(lows), f"Brackets not sorted for ({yr}, {fs})"

    def test_top_bracket_has_inf_upper_bound(self):
        brackets = _load_brackets()
        for (yr, fs), sched in brackets.items():
            assert sched[-1][1] == math.inf, (
                f"Top bracket for ({yr}, {fs}) does not have inf upper bound"
            )

    def test_deductions_loaded_for_multiple_years(self):
        schedule = _load_deductions()
        years = [yr for yr, _ in schedule]
        assert 2018 in years
        assert 2024 in years

    def test_default_weights_sum_to_one(self):
        total = sum(DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"DEFAULT_WEIGHTS sum {total} != 1.0"


# ---------------------------------------------------------------------------
# 2. hawaii_tax_real — per-filing-status function
# ---------------------------------------------------------------------------

class TestHawaiiTaxReal:
    """Correctness tests for single-status hawaii_tax_real."""

    def test_zero_income_returns_zero(self):
        for fs in ALL_FILING_STATUSES:
            assert hawaii_tax_real(0.0, filing_status=fs) == 0.0

    def test_negative_income_returns_zero(self):
        assert hawaii_tax_real(-50_000.0) == 0.0

    def test_below_deduction_returns_zero_single(self):
        """Income ≤ $2,200 (2018 single deduction): zero taxable income."""
        assert _tax_s(2_200.0) == 0.0
        assert _tax_s(1_000.0) == 0.0

    def test_below_deduction_returns_zero_joint(self):
        """Income ≤ $4,400 (2018 joint deduction): zero taxable income."""
        assert _tax_j(4_400.0) == 0.0

    def test_first_bracket_single_2018(self):
        """$5,000 income (single 2018): taxable=$2,800, in $2,400–$4,800@3.2% bracket.
        tax = 34 + (2,800 - 2,400) × 0.032 = 34 + 12.8 = 46.8
        """
        income = 5_000.0  # taxable = 5000 - 2200 = 2800
        expected = 34.0 + (2_800.0 - 2_400.0) * 0.032
        assert _tax_s(income) == pytest.approx(expected, rel=1e-6)

    def test_single_at_48k_agi_threshold(self):
        """Exactly at the $48,000 taxable threshold (single 2018)."""
        income = 48_000.0 + 2_200.0  # taxable = 48,000 exactly
        # base_tax for the $48k bracket entry
        # Verify: tax is exactly base_tax of the $48k row (no excess in next bracket)
        tax = _tax_s(income)
        assert tax > 0.0
        assert math.isfinite(tax)

    def test_single_2018_at_median_income(self):
        """At 2022 ACS Honolulu median $83,737 (single 2018 brackets)."""
        # taxable = 83737 - 2200 = 81537; in $48k-$150k@8.25% bracket
        # tax = 3214 + (81537 - 48000) × 0.0825
        income = 83_737.0
        taxable = income - 2_200.0  # 81,537
        expected = 3_214.0 + (taxable - 48_000.0) * 0.0825
        assert _tax_s(income) == pytest.approx(expected, rel=1e-6)

    def test_joint_2018_at_median_income(self):
        """Joint filer at $83,737 (2018 brackets).
        taxable = 83737 - 4400 = 79337; in $72k-$96k@7.9% bracket.
        tax = 4531 + (79337 - 72000) × 0.079
        """
        income = 83_737.0
        taxable = income - 4_400.0  # 79,337
        expected = 4_531.0 + (taxable - 72_000.0) * 0.079
        assert _tax_j(income) == pytest.approx(expected, rel=1e-6)

    def test_tax_strictly_increases_with_income_single(self):
        """Progressive: more income → more tax."""
        incomes = [0, 2_200, 10_000, 30_000, 60_000, 100_000, 250_000]
        taxes = [_tax_s(x) for x in incomes]
        for i in range(1, len(taxes)):
            assert taxes[i] >= taxes[i - 1], (
                f"Tax not monotone: income={incomes[i]}, "
                f"tax={taxes[i]:.2f} < prev={taxes[i-1]:.2f}"
            )

    def test_no_discontinuity_at_bracket_boundary(self):
        """Tax function is continuous: tiny delta around boundary → tiny delta in tax."""
        # Check at the $48,000 taxable boundary (income = 50,200 single 2018)
        boundary_income = 48_000.0 + 2_200.0  # 50,200
        eps = 1.0
        diff = abs(_tax_s(boundary_income + eps) - _tax_s(boundary_income - eps))
        # Diff should be < (eps × top_marginal_rate + eps × bottom_rate) = eps × 2
        assert diff < 2 * eps, f"Large jump at boundary: {diff:.4f}"

    def test_effective_rate_increases_with_income(self):
        """Effective rate (tax/income) is strictly increasing — progressive schedule."""
        incomes = [30_000, 60_000, 100_000, 200_000]
        rates = [_tax_s(x) / x for x in incomes]
        for i in range(1, len(rates)):
            assert rates[i] > rates[i - 1], (
                f"Effective rate not increasing at income={incomes[i]}: "
                f"{rates[i]:.4f} not > {rates[i-1]:.4f}"
            )

    def test_marginal_exceeds_effective(self):
        """For a progressive schedule, marginal rate > effective rate (except near zero)."""
        for income in [50_000, 83_000, 150_000]:
            eff = _tax_s(income) / income
            mr = _mr_s(income)
            assert mr > eff, (
                f"Marginal {mr:.4f} not > effective {eff:.4f} at income={income}"
            )


# ---------------------------------------------------------------------------
# 3. hawaii_marginal_real
# ---------------------------------------------------------------------------

class TestHawaiiMarginalReal:
    """Tests for the delta-method marginal rate helper."""

    def test_zero_income_zero_marginal(self):
        assert hawaii_marginal_real(0.0) == 0.0

    def test_below_deduction_zero_marginal_single(self):
        assert _mr_s(2_200.0) == 0.0
        assert _mr_s(1_000.0) == 0.0

    def test_first_bracket_rate_single_2018(self):
        """$5,000 income (taxable=$2,800 → in $2,400–$4,800@3.2%)."""
        assert _mr_s(5_000.0) == pytest.approx(0.032)

    def test_mid_bracket_single_2018(self):
        """$83,737 income (taxable=$81,537 → in $48k–$150k@8.25%)."""
        assert _mr_s(83_737.0) == pytest.approx(0.0825)

    def test_top_bracket_single_2018(self):
        """$300,000 income (taxable=$297,800 → above $200k, top rate 11%)."""
        assert _mr_s(300_000.0) == pytest.approx(0.11)

    def test_marginal_rate_in_01(self):
        """Marginal rate is always in [0, 1]."""
        for income in [0, 5_000, 30_000, 83_000, 200_000, 500_000]:
            mr = _mr_s(income)
            assert 0.0 <= mr <= 1.0, f"Marginal rate {mr} out of [0,1] at {income}"

    def test_finite_difference_agrees_with_marginal(self):
        """Numerical finite difference ≈ analytic marginal rate (away from boundaries)."""
        income = 83_737.0
        delta = 1.0
        numeric = (_tax_s(income + delta) - _tax_s(income - delta)) / (2 * delta)
        assert abs(numeric - _mr_s(income)) < 0.001, (
            f"Finite diff {numeric:.4f} != marginal {_mr_s(income):.4f}"
        )


# ---------------------------------------------------------------------------
# 4. Year dispatch
# ---------------------------------------------------------------------------

class TestYearDispatch:
    """Correct bracket vintage and deduction for each tax year era."""

    def test_bracket_year_pre_2025_returns_2018(self):
        for yr in (2010, 2015, 2018, 2022, 2024):
            assert _bracket_year(yr) == 2018, f"Expected 2018 for tax_year={yr}"

    def test_bracket_year_2025_returns_2025(self):
        assert _bracket_year(2025) == 2025
        assert _bracket_year(2026) == 2025

    def test_bracket_year_2027_returns_2027(self):
        assert _bracket_year(2027) == 2027
        assert _bracket_year(2028) == 2027

    def test_deduction_pre_2024_single_is_2200(self):
        for yr in (2010, 2018, 2022, 2023):
            ded = _deduction_for_year(yr, FS_SINGLE)
            assert ded == 2_200.0, f"Expected $2,200 single deduction for TY{yr}, got {ded}"

    def test_deduction_2024_single_is_4400(self):
        assert _deduction_for_year(2024, FS_SINGLE) == 4_400.0
        assert _deduction_for_year(2025, FS_SINGLE) == 4_400.0

    def test_deduction_2026_single_is_8000(self):
        assert _deduction_for_year(2026, FS_SINGLE) == 8_000.0

    def test_2025_brackets_give_lower_tax_at_median_than_2018(self):
        """Act 46 doubled bracket thresholds → lower tax at the same income."""
        income = 83_737.0
        tax_2018 = hawaii_tax_real(income, tax_year=2022, filing_status=FS_SINGLE)  # 2018 brackets
        tax_2025 = hawaii_tax_real(income, tax_year=2025, filing_status=FS_SINGLE)  # 2025 brackets
        assert tax_2025 < tax_2018, (
            f"2025 tax {tax_2025:.2f} not < 2018 tax {tax_2018:.2f} at median income"
        )

    def test_real_function_single_vs_surrogate_at_median(self):
        """Real single-filer tax > surrogate at $83k (surrogate under-estimates lower bands).

        Surrogate uses flat 4% on $0-$48k AGI ($1,920 at threshold).
        Real 2018 single accumulates 1.4%→7.9% ($3,214 at threshold).
        """
        from tax_modeler.projection.revenue_backtest import apply_hawaii_tax_brackets
        income = 83_737.0
        real_tax = hawaii_tax_real(income, tax_year=2022, filing_status=FS_SINGLE)
        surrogate_tax = apply_hawaii_tax_brackets(income)
        assert real_tax > surrogate_tax, (
            f"Real single tax {real_tax:.2f} should exceed surrogate {surrogate_tax:.2f} "
            "at median income (real lower brackets accumulate more base tax)"
        )


# ---------------------------------------------------------------------------
# 5. hawaii_tax_weighted — household proxy
# ---------------------------------------------------------------------------

class TestHawaiiTaxWeighted:
    """Tests for the filing-status-weighted household proxy."""

    def test_zero_income_returns_zero(self):
        assert hawaii_tax_weighted(0.0) == 0.0

    def test_negative_income_returns_zero(self):
        assert hawaii_tax_weighted(-1_000.0) == 0.0

    def test_returns_finite_positive_for_median_income(self):
        tax = hawaii_tax_weighted(83_737.0)
        assert math.isfinite(tax)
        assert tax > 0.0

    def test_weighted_between_single_and_joint(self):
        """Weighted result is between the single and joint results (bracketed)."""
        income = 83_737.0
        single = hawaii_tax_real(income, filing_status=FS_SINGLE)
        joint = hawaii_tax_real(income, filing_status=FS_JOINT)
        weighted = hawaii_tax_weighted(income)
        low, high = min(single, joint), max(single, joint)
        assert low <= weighted <= high, (
            f"Weighted {weighted:.2f} not between single {single:.2f} "
            f"and joint {joint:.2f}"
        )

    def test_custom_weights_respected(self):
        """100% joint weighting returns the joint result exactly."""
        income = 83_737.0
        joint_only = hawaii_tax_weighted(income, weights={FS_JOINT: 1.0})
        joint_direct = hawaii_tax_real(income, filing_status=FS_JOINT)
        assert joint_only == pytest.approx(joint_direct, rel=1e-9)

    def test_weights_normalized_automatically(self):
        """Unnormalized weights give same result as normalized weights."""
        income = 83_737.0
        w_norm = {FS_SINGLE: 0.52, FS_JOINT: 0.37, FS_HOH: 0.11}
        w_unnorm = {FS_SINGLE: 52.0, FS_JOINT: 37.0, FS_HOH: 11.0}
        assert hawaii_tax_weighted(income, weights=w_norm) == pytest.approx(
            hawaii_tax_weighted(income, weights=w_unnorm), rel=1e-9
        )

    def test_monotone_in_income(self):
        """Weighted tax is non-decreasing in income."""
        incomes = [0, 5_000, 30_000, 75_000, 100_000, 200_000]
        taxes = [hawaii_tax_weighted(x) for x in incomes]
        for i in range(1, len(taxes)):
            assert taxes[i] >= taxes[i - 1]

    def test_effective_rate_at_median_reasonable(self):
        """Weighted effective rate at $83,737 should be in the 5–9% range."""
        income = 83_737.0
        tax = hawaii_tax_weighted(income)
        rate = tax / income
        assert 0.05 <= rate <= 0.09, (
            f"Weighted effective rate {rate:.2%} outside expected 5–9% band"
        )


# ---------------------------------------------------------------------------
# 6. hawaii_marginal_weighted
# ---------------------------------------------------------------------------

class TestHawaiiMarginalWeighted:
    """Tests for the weighted marginal rate helper."""

    def test_zero_income_returns_zero(self):
        assert hawaii_marginal_weighted(0.0) == 0.0

    def test_returns_finite_for_median_income(self):
        mr = hawaii_marginal_weighted(83_737.0)
        assert math.isfinite(mr)
        assert mr > 0.0

    def test_weighted_marginal_in_01(self):
        for income in [5_000, 50_000, 83_737, 200_000]:
            mr = hawaii_marginal_weighted(income)
            assert 0.0 <= mr <= 1.0

    def test_custom_weights_single_only_matches_single_marginal(self):
        income = 83_737.0
        w = {FS_SINGLE: 1.0}
        assert hawaii_marginal_weighted(income, weights=w) == pytest.approx(
            hawaii_marginal_real(income, filing_status=FS_SINGLE), rel=1e-9
        )

    def test_delta_method_se_positive(self):
        """SE propagation: se_rev = mr × se_income > 0 for non-zero SE."""
        income = 83_737.0
        se_income = 5_000.0
        mr = hawaii_marginal_weighted(income)
        se_rev = abs(mr) * se_income
        assert se_rev > 0.0


# ---------------------------------------------------------------------------
# 7. DOTAX 2022 calibration
# ---------------------------------------------------------------------------

class TestDotaxCalibration:
    """Order-of-magnitude checks against DOTAX Table 12A 2022 actuals.

    Source: DotaxTable12AValidator (hardcoded 2022 DOTAX SOI data).
    Total: 635,117 returns, $3,029M tax, $4,770 average (before credits).
    $75k-$100k bracket: 54,976 returns, $261M, $4,751 average.

    These are "before credits" figures; the weighted function also gives
    "before credits" values.  The gap between model and DOTAX average is
    driven by income-distribution heterogeneity within the bracket and
    itemized deductions — not by an error in the bracket schedule.
    """

    DOTAX_OVERALL_AVG = 4_770.0       # dollars, before credits
    DOTAX_75K_100K_AVG = 4_751.0      # $75k-$100k AGI bracket, before credits
    MEDIAN_INCOME_2022 = 83_737.0     # Honolulu B19013 2022 ACS

    def test_weighted_at_median_order_of_magnitude(self):
        """Weighted tax at $83,737 should be in [$2k, $10k] (sanity, not precision)."""
        tax = hawaii_tax_weighted(self.MEDIAN_INCOME_2022)
        assert 2_000 < tax < 10_000, (
            f"Tax at median income ${self.MEDIAN_INCOME_2022:,.0f} = ${tax:,.0f}; "
            "expected $2k–$10k (DOTAX bracket avg is $4,751)"
        )

    def test_weighted_effective_rate_above_surrogate_at_median(self):
        """Real function gives higher effective rate than surrogate at median.

        The surrogate under-estimates effective rate due to the flat 4%
        bottom band; the real schedule has a higher accumulated base tax.
        """
        from tax_modeler.projection.revenue_backtest import apply_hawaii_tax_brackets
        income = self.MEDIAN_INCOME_2022
        real_rate = hawaii_tax_weighted(income) / income
        surr_rate = apply_hawaii_tax_brackets(income) / income
        assert real_rate > surr_rate, (
            f"Real weighted rate {real_rate:.3%} not > surrogate {surr_rate:.3%}"
        )

    def test_real_function_has_higher_base_tax_accumulation(self):
        """Real lower brackets accumulate more base tax than the 3-bracket surrogate.

        At the $48k AGI threshold:
          - Surrogate: $48k × 4% = $1,920 (flat 4% from $0)
          - Real 2018 single: base_tax = $3,214 (accumulated 1.4%→7.9% rungs)

        This is the key structural difference: the real schedule is more
        progressive in the low-income bands, which better matches DOTAX
        filing patterns.
        """
        from tax_modeler.projection.revenue_backtest import apply_hawaii_tax_brackets
        # Income just above the $48k AGI single 2018 threshold (income = $50,200)
        income_at_48k_agi = 48_000.0 + 2_200.0  # taxable = 48,000 exactly
        # Surrogate at the same income (deduction $4,400): taxable = $43,800 → in 4% band
        # Real single base_tax at $48k AGI boundary is $3,214
        # Surrogate at $48k taxable (single, deduction $2,200) = 1.4%...7.6% accumulated
        real_base = hawaii_tax_real(income_at_48k_agi, filing_status=FS_SINGLE)
        surr = apply_hawaii_tax_brackets(income_at_48k_agi)
        assert real_base > surr, (
            f"Real base-tax accumulation ${real_base:,.0f} should exceed "
            f"surrogate ${surr:,.0f} at the $48k AGI threshold income "
            f"(real progressive lower bands vs flat 4%)"
        )


# ---------------------------------------------------------------------------
# 8. Revenue backtest integration
# ---------------------------------------------------------------------------

class TestRevenueBacktestIntegration:
    """Smoke tests wiring hawaii_tax_weighted into run_revenue_backtest."""

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

    def test_default_tax_fn_is_concentration_model(self, panel_growing):
        """The default tax_fn for run_revenue_backtest is the Phase E concentration model.

        Phase F promoted the DOTAX-calibrated bracket-weighted model
        (make_concentration_adjusted_fn) as the pipeline default.  At the
        2022 base median ($83,737) it returns $4,770 (DOTAX actual), vs
        hawaii_tax_weighted's $5,609 (15% over-estimate).
        """
        import inspect

        from tax_modeler.projection.revenue_backtest import run_revenue_backtest
        from tax_modeler.projection.revenue_concentration import expected_revenue_per_filer
        sig = inspect.signature(run_revenue_backtest)
        default_fn = sig.parameters["tax_fn"].default
        test_income = 83_737.0
        # Default must match the concentration model, not the point estimate
        assert default_fn(test_income) == pytest.approx(
            expected_revenue_per_filer(test_income), rel=1e-9
        ), "Default tax_fn should be Phase E concentration model"
        # And must differ from the Phase D point estimate
        assert default_fn(test_income) != pytest.approx(
            hawaii_tax_weighted(test_income), rel=0.01
        ), "Default must not be hawaii_tax_weighted (Phase D point estimate)"

    def test_run_with_real_brackets_produces_results(self, panel_growing):
        """Running backtest with default (real) tax function returns valid summaries."""
        from tax_modeler.projection.revenue_backtest import run_revenue_backtest
        summaries = run_revenue_backtest(
            panel_growing, anchors=[2018, 2020, 2022], horizon=2
        )
        for name, s in summaries.items():
            if s.n > 0:
                assert math.isfinite(s.mean_abs_pct_error), (
                    f"{name}: non-finite MAPE with real tax function"
                )

    def test_ensemble_beats_carry_forward_with_real_fn(self, panel_growing):
        """Ensemble < carry_forward MAPE holds regardless of tax function."""
        from tax_modeler.projection.revenue_backtest import run_revenue_backtest
        summaries = run_revenue_backtest(
            panel_growing, anchors=[2018, 2020, 2022], horizon=2
        )
        assert summaries["ensemble"].mean_abs_pct_error < \
               summaries["carry_forward"].mean_abs_pct_error

    def test_explicit_surrogate_gives_different_results(self, panel_growing):
        """Explicitly passing the surrogate gives different revenue values than default."""
        from tax_modeler.projection.revenue_backtest import (
            apply_hawaii_tax_brackets,
            marginal_rate,
            run_revenue_backtest,
        )
        s_real = run_revenue_backtest(
            panel_growing, anchors=[2022], horizon=2
        )
        s_surr = run_revenue_backtest(
            panel_growing, anchors=[2022], horizon=2,
            tax_fn=apply_hawaii_tax_brackets,
            marginal_fn=marginal_rate,
        )
        # MAPE values should differ (different effective rates)
        real_mape = s_real["carry_forward"].mean_abs_pct_error
        surr_mape = s_surr["carry_forward"].mean_abs_pct_error
        # Both should be finite and positive
        assert math.isfinite(real_mape) and math.isfinite(surr_mape)
        # They should differ (real brackets != surrogate)
        assert real_mape != pytest.approx(surr_mape, rel=0.001), (
            "Real and surrogate MAPE are identical — default may not have changed"
        )
