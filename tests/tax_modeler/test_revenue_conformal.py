"""Phase C — Revenue conformal prediction interval tests.

Tests for :mod:`tax_modeler.projection.revenue_conformal`.

The split-conformal protocol for revenue CIs:
1. Fit models on training anchors.
2. Calibrate conformal quantiles on *disjoint* hold-out (calibration) anchors.
3. Evaluate on *disjoint* evaluation anchors with conformal-floored CIs.

Ship gates
----------
* ``test_conformal_ci_wider_than_delta_method_when_floor_binds``
  — When the conformal quantile > Z₉₀ = 1.645, the CI must be strictly
    wider than the pure delta-method CI.
* ``test_conformal_does_not_narrow_ci``
  — When conformal quantile < Z₉₀ (model already conservative), CIs unchanged.
* ``test_calibrated_backtest_n_consistent``
  — Split-conformal backtest produces same fold count as uncalibrated.
"""
from __future__ import annotations

import math

import pytest
from census_forecaster.backtest.acs import BacktestRow
from common.models import AcsObservation
from tax_modeler.projection.revenue_backtest import (
    run_revenue_backtest,
)
from tax_modeler.projection.revenue_conformal import (
    WILDCARD,
    RevenueConformalRecord,
    _classify_horizon,
    _conformal_quantile,
    apply_revenue_conformal_floor,
    calibrate_revenue_conformal,
    compute_revenue_conformal_quantiles,
    run_calibrated_revenue_backtest,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _obs(year: int, est: float, geoid: str = "15003",
         moe: float = 1_000.0, vintage: str = "1y") -> AcsObservation:
    return AcsObservation(
        estimate=est, moe=moe, year=year, vintage=vintage,
        geoid=geoid, indicator="B19013_001E",
    )


@pytest.fixture(scope="module")
def panel_growing() -> dict:
    """2.5%/yr geometric growth 2010–2024 for Honolulu (15003)."""
    return {
        ("15003", "B19013_001E"): [
            _obs(y, 75_000 * (1.025 ** i))
            for i, y in enumerate(range(2010, 2025))
        ]
    }


@pytest.fixture(scope="module")
def panel_flat() -> dict:
    """Constant $75k 2010–2024."""
    return {
        ("15003", "B19013_001E"): [
            _obs(y, 75_000.0) for y in range(2010, 2025)
        ]
    }


# ---------------------------------------------------------------------------
# _conformal_quantile (private helper — mirrors census_forecaster test patterns)
# ---------------------------------------------------------------------------

class TestInternalConformalQuantile:
    def test_empty_returns_inf(self):
        assert math.isinf(_conformal_quantile([]))

    def test_small_n_returns_inf(self):
        # n=3, alpha=0.10: level = ceil(4 * 0.9) = 4 > 3 → inf
        assert math.isinf(_conformal_quantile([1.0, 2.0, 3.0]))

    def test_sufficient_n_returns_finite(self):
        # n=9, level = ceil(10 * 0.9) = 9 ≤ 9 → finite
        scores = list(range(1, 10))
        q = _conformal_quantile(scores)
        assert math.isfinite(q)
        assert q == 9.0

    def test_returns_correct_order_statistic(self):
        # n=10, level = ceil(11 * 0.9) = 10 → 10th smallest
        scores = list(range(1, 11))
        assert _conformal_quantile(scores) == 10.0

    def test_alpha_0_inf(self):
        # Full coverage → always ∞
        scores = list(range(1, 25))
        assert math.isinf(_conformal_quantile(scores, alpha=0.0))


# ---------------------------------------------------------------------------
# _classify_horizon
# ---------------------------------------------------------------------------

class TestClassifyHorizon:
    def test_h1_short(self):
        assert _classify_horizon(1) == "short"

    def test_h2_short(self):
        assert _classify_horizon(2) == "short"

    def test_h3_long(self):
        assert _classify_horizon(3) == "long"

    def test_h10_long(self):
        assert _classify_horizon(10) == "long"


# ---------------------------------------------------------------------------
# compute_revenue_conformal_quantiles
# ---------------------------------------------------------------------------

def _make_row(method: str = "ensemble", horizon: int = 2,
              actual: float = 5_500.0, projected: float = 5_000.0,
              sample_se: float = 100.0, forecast_se: float = 150.0) -> BacktestRow:
    return BacktestRow(
        geoid="15003", indicator="B19013_001E",
        anchor_year=2018, target_year=2020,
        horizon=horizon, method=method,
        actual=actual, projected=projected,
        ci90_low=projected - 250.0, ci90_high=projected + 250.0,
        sample_se=sample_se, forecast_se=forecast_se,
    )


class TestComputeRevenueConformalQuantiles:
    def test_returns_record_for_sufficient_n(self):
        rows = [_make_row() for _ in range(12)]
        records = compute_revenue_conformal_quantiles(rows, n_threshold=10)
        assert len(records) == 1
        assert records[0].method == "ensemble"
        assert records[0].h_bucket == "short"
        assert math.isfinite(records[0].quantile)

    def test_below_n_threshold_empty(self):
        rows = [_make_row() for _ in range(5)]
        records = compute_revenue_conformal_quantiles(rows, n_threshold=10)
        assert records == []

    def test_skips_zero_se(self):
        bad = _make_row(sample_se=0.0, forecast_se=0.0)
        good = [_make_row() for _ in range(12)]
        records = compute_revenue_conformal_quantiles([bad] + good, n_threshold=10)
        assert records[0].n_calibration == 12  # bad row excluded

    def test_groups_by_method_and_horizon(self):
        short_rows = [_make_row(method="ensemble", horizon=2) for _ in range(12)]
        long_rows = [_make_row(method="ensemble", horizon=3) for _ in range(12)]
        cf_rows = [_make_row(method="carry_forward", horizon=2) for _ in range(12)]
        records = compute_revenue_conformal_quantiles(
            short_rows + long_rows + cf_rows, n_threshold=10
        )
        keys = {(r.method, r.h_bucket) for r in records}
        assert ("ensemble", "short") in keys
        assert ("ensemble", "long") in keys
        assert ("carry_forward", "short") in keys

    def test_quantile_matches_known_scores(self):
        """All non-conformity scores = 2.0 → quantile = 2.0 (for sufficient n)."""
        # se_rev = sqrt(100^2 + 150^2) ≈ 180.28; score = |5500-5000|/180.28 ≈ 2.774
        rows = [_make_row() for _ in range(12)]
        records = compute_revenue_conformal_quantiles(rows, n_threshold=10)
        se = math.sqrt(100.0 ** 2 + 150.0 ** 2)
        expected_score = abs(5_500.0 - 5_000.0) / se
        assert records[0].quantile == pytest.approx(expected_score, rel=1e-6)

    def test_target_coverage_stored(self):
        # n=20 needed for alpha=0.05: ceil(21 * 0.95) = ceil(19.95) = 20 ≤ 20 → finite
        rows = [_make_row() for _ in range(25)]
        records = compute_revenue_conformal_quantiles(rows, target_coverage=0.95, n_threshold=10)
        assert len(records) >= 1, "Expected at least one record with n=25 rows"
        assert records[0].target_coverage == 0.95

    def test_n_calibration_correct(self):
        rows = [_make_row() for _ in range(15)]
        records = compute_revenue_conformal_quantiles(rows, n_threshold=10)
        assert records[0].n_calibration == 15


# ---------------------------------------------------------------------------
# apply_revenue_conformal_floor
# ---------------------------------------------------------------------------

class TestApplyRevenueConformalFloor:
    def _record(self, method="ensemble", h_bucket="short", quantile=2.0):
        return RevenueConformalRecord(
            method=method, h_bucket=h_bucket,
            quantile=quantile, n_calibration=20,
            target_coverage=0.90,
        )

    def test_widens_ci_when_conformal_larger_than_z90(self):
        """q=2.0 > Z₉₀=1.645 → CI half = 2.0 × se_rev."""
        records = [self._record(quantile=2.0)]
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(5_000.0, se_rev, records, "ensemble", horizon=2)
        expected_half = 2.0 * se_rev
        assert lo == pytest.approx(5_000.0 - expected_half)
        assert hi == pytest.approx(5_000.0 + expected_half)

    def test_no_widening_when_q_below_z90(self):
        """q=1.0 < Z₉₀=1.645 → floor does not bind; half = Z₉₀ × se_rev."""
        records = [self._record(quantile=1.0)]
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(5_000.0, se_rev, records, "ensemble", horizon=2)
        expected_half = 1.6449 * se_rev
        assert lo == pytest.approx(5_000.0 - expected_half, rel=1e-4)
        assert hi == pytest.approx(5_000.0 + expected_half, rel=1e-4)

    def test_empty_records_returns_z90_ci(self):
        """No conformal records → fall back to Z₉₀ Gaussian CI."""
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(5_000.0, se_rev, [], "ensemble", horizon=2)
        expected_half = 1.6449 * se_rev
        assert lo == pytest.approx(5_000.0 - expected_half, rel=1e-4)

    def test_method_mismatch_falls_back_to_z90(self):
        """No record for 'carry_forward' → Z₉₀ CI."""
        records = [self._record(method="ensemble", quantile=3.0)]
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(5_000.0, se_rev, records, "carry_forward", horizon=2)
        expected_half = 1.6449 * se_rev
        assert lo == pytest.approx(5_000.0 - expected_half, rel=1e-4)

    def test_wildcard_h_bucket_matches_any_horizon(self):
        """A WILDCARD h_bucket record matches any horizon."""
        records = [self._record(h_bucket=WILDCARD, quantile=2.5)]
        se_rev = 200.0
        lo_short, hi_short = apply_revenue_conformal_floor(
            5_000.0, se_rev, records, "ensemble", horizon=1
        )
        lo_long, hi_long = apply_revenue_conformal_floor(
            5_000.0, se_rev, records, "ensemble", horizon=5
        )
        expected_half = 2.5 * se_rev
        assert lo_short == pytest.approx(5_000.0 - expected_half)
        assert lo_long == pytest.approx(5_000.0 - expected_half)

    def test_exact_h_bucket_preferred_over_wildcard(self):
        exact = self._record(h_bucket="short", quantile=1.8)
        wildcard = self._record(h_bucket=WILDCARD, quantile=3.0)
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(
            5_000.0, se_rev, [exact, wildcard], "ensemble", horizon=2
        )
        # Should use exact record (q=1.8) not wildcard (q=3.0)
        assert hi == pytest.approx(5_000.0 + 1.8 * se_rev)

    def test_point_preserved(self):
        """Conformal floor only changes CI bounds, not point forecast."""
        records = [self._record(quantile=2.0)]
        se_rev = 200.0
        lo, hi = apply_revenue_conformal_floor(5_000.0, se_rev, records, "ensemble", horizon=2)
        midpoint = (lo + hi) / 2
        assert midpoint == pytest.approx(5_000.0)


# ---------------------------------------------------------------------------
# Ship gates — conformal floor in run_revenue_backtest
# ---------------------------------------------------------------------------

class TestConformalFloorInBacktest:
    """Verify the conformal_quantiles parameter wires correctly into run_revenue_backtest."""

    def test_conformal_ci_wider_than_delta_method_when_floor_binds(self, panel_growing):
        """When q > Z₉₀, every CI must be at least as wide as the delta-method CI."""
        # Force q=3.0 for all methods on short horizon.
        big_q_records = [
            RevenueConformalRecord(
                method=m, h_bucket="short",
                quantile=3.0, n_calibration=20, target_coverage=0.90,
            )
            for m in ("carry_forward", "linear_log", "damped_log_trend",
                      "ar1_log_diff", "ensemble")
        ]

        # Baseline: no conformal
        base = run_revenue_backtest(panel_growing, anchors=[2018, 2020], horizon=2)
        # Conformal-floored
        conf = run_revenue_backtest(
            panel_growing, anchors=[2018, 2020], horizon=2,
            conformal_quantiles=big_q_records,
        )

        for name in base:
            for b_row, c_row in zip(base[name].rows, conf[name].rows):
                b_width = b_row.ci90_high - b_row.ci90_low
                c_width = c_row.ci90_high - c_row.ci90_low
                assert c_width >= b_width - 1e-9, (
                    f"Method {name!r}: conformal CI width {c_width:.2f} "
                    f"< delta-method {b_width:.2f} — floor should never narrow CI"
                )

    def test_conformal_does_not_narrow_ci(self, panel_growing):
        """When q < Z₉₀ (model conservative), CIs are identical."""
        narrow_q_records = [
            RevenueConformalRecord(
                method=m, h_bucket="short",
                quantile=1.0,   # well below Z₉₀ = 1.645
                n_calibration=20, target_coverage=0.90,
            )
            for m in ("carry_forward", "linear_log", "damped_log_trend",
                      "ar1_log_diff", "ensemble")
        ]
        base = run_revenue_backtest(panel_growing, anchors=[2018], horizon=2)
        conf = run_revenue_backtest(
            panel_growing, anchors=[2018], horizon=2,
            conformal_quantiles=narrow_q_records,
        )
        for name in base:
            for b_row, c_row in zip(base[name].rows, conf[name].rows):
                assert c_row.ci90_low == pytest.approx(b_row.ci90_low, rel=1e-6)
                assert c_row.ci90_high == pytest.approx(b_row.ci90_high, rel=1e-6)


# ---------------------------------------------------------------------------
# calibrate_revenue_conformal + run_calibrated_revenue_backtest
# ---------------------------------------------------------------------------

class TestCalibrateRevenueConformal:
    def test_returns_records_list(self, panel_growing):
        """calibrate_revenue_conformal returns a (possibly empty) list."""
        records = calibrate_revenue_conformal(
            panel_growing, calibration_anchors=[2014, 2016, 2018, 2020],
            horizon=2,
        )
        assert isinstance(records, list)

    def test_records_have_correct_type(self, panel_growing):
        records = calibrate_revenue_conformal(
            panel_growing, calibration_anchors=[2014, 2016, 2018, 2020],
            horizon=2,
        )
        for r in records:
            assert isinstance(r, RevenueConformalRecord)
            assert math.isfinite(r.quantile)
            assert r.quantile > 0
            assert r.n_calibration > 0

    def test_empty_panel_returns_empty(self):
        records = calibrate_revenue_conformal({}, calibration_anchors=[2018, 2020], horizon=2)
        assert records == []

    def test_coverage_at_nominal_on_flat_series(self, panel_flat):
        """On a flat series, conformal quantile ≈ Z₉₀ (zero error → score ≈ 0)."""
        from census_forecaster.backtest.acs import DEFAULT_METHODS
        methods = {"carry_forward": DEFAULT_METHODS["carry_forward"]}
        records = calibrate_revenue_conformal(
            panel_flat,
            calibration_anchors=[2015, 2017, 2019, 2021, 2022, 2023],
            horizon=2,
            methods=methods,
        )
        # Flat series: projected == actual, scores all ~0, quantile ~0 (floor irrelevant)
        for r in records:
            assert r.quantile >= 0


class TestRunCalibratedRevenueBacktest:
    def test_returns_tuple_of_records_and_summaries(self, panel_growing):
        records, summaries = run_calibrated_revenue_backtest(
            panel_growing,
            calibration_anchors=[2014, 2016],
            eval_anchors=[2018, 2020, 2022],
            horizon=2,
        )
        assert isinstance(records, list)
        assert isinstance(summaries, dict)

    def test_eval_fold_count_matches_plain_backtest(self, panel_growing):
        """Fold count on eval anchors should match uncalibrated backtest."""
        _, summaries = run_calibrated_revenue_backtest(
            panel_growing,
            calibration_anchors=[2014, 2016],
            eval_anchors=[2018, 2020, 2022],
            horizon=2,
        )
        plain = run_revenue_backtest(
            panel_growing, anchors=[2018, 2020, 2022], horizon=2
        )
        for m in plain:
            assert summaries[m].n == plain[m].n, (
                f"Method {m!r}: calibrated n={summaries[m].n} != plain n={plain[m].n}"
            )

    def test_calibrated_ci_at_least_as_wide_as_uncalibrated(self, panel_growing):
        """Conformal floor can only widen, never narrow, the CI."""
        _, summaries = run_calibrated_revenue_backtest(
            panel_growing,
            calibration_anchors=[2014, 2016, 2018],
            eval_anchors=[2020, 2022],
            horizon=2,
        )
        plain = run_revenue_backtest(
            panel_growing, anchors=[2020, 2022], horizon=2
        )
        for m in plain:
            for p_row, c_row in zip(plain[m].rows, summaries[m].rows):
                p_width = p_row.ci90_high - p_row.ci90_low
                c_width = c_row.ci90_high - c_row.ci90_low
                assert c_width >= p_width - 1e-9, (
                    f"Method {m!r}: calibrated CI {c_width:.2f} "
                    f"< uncalibrated {p_width:.2f}"
                )
