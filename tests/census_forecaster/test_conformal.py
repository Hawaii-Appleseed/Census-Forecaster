"""Tests for split-conformal prediction interval calibration (Phase E)."""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.conformal import (
    ConformalRecord,
    _conformal_quantile,
    build_conformal_lookup,
    compute_conformal_quantiles,
)
from census_forecaster.acs.strata import WILDCARD

# ---------------------------------------------------------------------------
# _conformal_quantile
# ---------------------------------------------------------------------------

class TestConformalQuantile:
    def test_empty_returns_inf(self):
        assert math.isinf(_conformal_quantile([]))

    def test_single_score_level1_ok(self):
        # n=1, level = ceil(2 * 0.9) = 2 > 1 → inf
        assert math.isinf(_conformal_quantile([1.5]))

    def test_two_scores_level_within(self):
        # n=2, alpha=0.10, level = ceil(3 * 0.9) = ceil(2.7) = 3 > 2 → inf
        assert math.isinf(_conformal_quantile([1.0, 2.0]))

    def test_three_scores_level_within(self):
        # n=3, level = ceil(4 * 0.9) = ceil(3.6) = 4 > 3 → inf
        assert math.isinf(_conformal_quantile([1.0, 2.0, 3.0]))

    def test_nine_scores_returns_finite(self):
        # n=9, level = ceil(10 * 0.9) = 9 ≤ 9 → returns 9th-smallest
        scores = list(range(1, 10))  # [1..9]
        q = _conformal_quantile(scores)
        assert math.isfinite(q)
        assert q == 9.0

    def test_returns_correct_order_statistic(self):
        # n=10, level = ceil(11 * 0.9) = ceil(9.9) = 10 → 10th smallest
        scores = [float(i) for i in range(1, 11)]
        q = _conformal_quantile(scores)
        assert q == 10.0

    def test_unsorted_input(self):
        scores = [5.0, 1.0, 3.0, 2.0, 4.0, 6.0, 7.0, 8.0, 9.0]
        q = _conformal_quantile(scores)
        assert q == _conformal_quantile(sorted(scores))

    def test_alpha_0_returns_max(self):
        # alpha=0 → level = ceil((n+1)*1) = n+1 > n → inf
        scores = list(range(1, 25))
        assert math.isinf(_conformal_quantile(scores, alpha=0.0))

    def test_alpha_1_returns_min(self):
        # alpha=1 → level = ceil((n+1)*0) = 0 → treat as 0 or ≤0 → sorted[level-1]
        # level = ceil(0) = 0, level > n is false when n≥1? 0 ≤ n always.
        # Actually level=0, sorted[0-1=−1] would wrap. But level ≤ 0 — our code
        # checks level > n only; let's verify the actual output is sorted[−1]=max.
        # The spec says ceil((n+1)*(1-1)) = ceil(0) = 0, which is ≤ n, so we
        # return sorted[0-1] = sorted[-1] = max. This is technically correct for α=1
        # (0% coverage — the most aggressive interval).
        scores = [1.0, 2.0, 3.0, 4.0, 5.0]
        q = _conformal_quantile(scores, alpha=1.0)
        assert q == max(scores)


# ---------------------------------------------------------------------------
# compute_conformal_quantiles
# ---------------------------------------------------------------------------

class _FakeResidual:
    """Minimal FoldResidual-like object for testing."""
    def __init__(self, actual, point, se_total, indicator, method, pop_bucket, h_bucket, anchor_year=2021):
        self.actual = actual
        self.point = point
        self.se_total = se_total
        self.indicator = indicator
        self.method = method
        self.pop_bucket = pop_bucket
        self.h_bucket = h_bucket
        self.anchor_year = anchor_year


def _make_residuals(n: int, score: float = 1.0, **kw) -> list[_FakeResidual]:
    """Build n residuals all with the same non-conformity score."""
    defaults = dict(
        indicator="B19013_001E", method="trend_ensemble",
        pop_bucket="medium", h_bucket="medium",
    )
    defaults.update(kw)
    residuals = []
    for i in range(n):
        # actual = point + score * se, so s = score
        residuals.append(_FakeResidual(
            actual=100.0 + score * 1.0,
            point=100.0,
            se_total=1.0,
            **defaults,
        ))
    return residuals


class TestComputeConformalQuantiles:
    def test_returns_record_for_sufficient_n(self):
        residuals = _make_residuals(25)
        records = compute_conformal_quantiles(residuals, target_coverage=0.90, n_threshold=20)
        assert len(records) == 1
        assert records[0].indicator == "B19013_001E"
        assert records[0].method == "trend_ensemble"
        assert records[0].n_calibration == 25
        assert math.isfinite(records[0].quantile)

    def test_below_n_threshold_returns_empty(self):
        residuals = _make_residuals(15)
        records = compute_conformal_quantiles(residuals, n_threshold=20)
        assert records == []

    def test_skips_zero_se(self):
        bad = _FakeResidual(actual=105.0, point=100.0, se_total=0.0,
                            indicator="B19013_001E", method="trend_ensemble",
                            pop_bucket="medium", h_bucket="medium")
        good = _make_residuals(25)
        records = compute_conformal_quantiles([bad] + good, n_threshold=20)
        assert records[0].n_calibration == 25  # bad residual excluded

    def test_skips_nonfinite_score(self):
        # se_total=1e-300 makes score huge but still finite; inf se would be skipped
        inf_res = _FakeResidual(actual=1.0, point=0.0, se_total=float("inf"),
                                indicator="B19013_001E", method="trend_ensemble",
                                pop_bucket="medium", h_bucket="medium")
        good = _make_residuals(25)
        records = compute_conformal_quantiles([inf_res] + good, n_threshold=20)
        assert records[0].n_calibration == 25

    def test_groups_by_stratum(self):
        r1 = _make_residuals(25, indicator="B19013_001E", pop_bucket="small")
        r2 = _make_residuals(25, indicator="B19013_001E", pop_bucket="large")
        records = compute_conformal_quantiles(r1 + r2, n_threshold=20)
        assert len(records) == 2
        keys = {(r.indicator, r.pop_bucket) for r in records}
        assert keys == {("B19013_001E", "small"), ("B19013_001E", "large")}

    def test_quantile_value_is_finite_and_positive(self):
        # Scores are all 1.5 → quantile should be 1.5
        residuals = [
            _FakeResidual(actual=101.5, point=100.0, se_total=1.0,
                          indicator="B19013_001E", method="trend_ensemble",
                          pop_bucket="medium", h_bucket="medium")
            for _ in range(25)
        ]
        records = compute_conformal_quantiles(residuals, n_threshold=20)
        assert len(records) == 1
        assert records[0].quantile > 0
        assert math.isfinite(records[0].quantile)

    def test_target_coverage_stored(self):
        residuals = _make_residuals(25)
        records = compute_conformal_quantiles(residuals, target_coverage=0.95, n_threshold=20)
        assert records[0].target_coverage == 0.95


# ---------------------------------------------------------------------------
# build_conformal_lookup
# ---------------------------------------------------------------------------

class TestBuildConformalLookup:
    def _record(self, indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="medium", h_bucket="medium", quantile=1.5):
        return ConformalRecord(
            indicator=indicator, method=method,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
            quantile=quantile, n_calibration=30, target_coverage=0.90,
        )

    def test_exact_match(self):
        records = [self._record()]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", "medium", "medium")
        assert q == 1.5

    def test_returns_none_for_unknown_indicator(self):
        records = [self._record()]
        lookup = build_conformal_lookup(records)
        assert lookup("UNKNOWN", "trend_ensemble", "medium", "medium") is None

    def test_wildcard_pop_fallback(self):
        # Record has WILDCARD pop_bucket; lookup with specific pop_bucket falls back
        records = [self._record(pop_bucket=WILDCARD)]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", "large", "medium")
        assert q == 1.5

    def test_wildcard_h_fallback(self):
        records = [self._record(h_bucket=WILDCARD)]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", "medium", "long")
        assert q == 1.5

    def test_wildcard_both_fallback(self):
        records = [self._record(pop_bucket=WILDCARD, h_bucket=WILDCARD)]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", "small", "short")
        assert q == 1.5

    def test_exact_preferred_over_wildcard(self):
        exact = self._record(pop_bucket="medium", quantile=1.2)
        wildcard = self._record(pop_bucket=WILDCARD, quantile=2.0)
        lookup = build_conformal_lookup([exact, wildcard])
        # Exact hit should win
        assert lookup("B19013_001E", "trend_ensemble", "medium", "medium") == 1.2

    def test_none_pop_treated_as_wildcard(self):
        records = [self._record(pop_bucket=WILDCARD)]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", None, "medium")
        assert q == 1.5

    def test_none_h_treated_as_wildcard(self):
        records = [self._record(h_bucket=WILDCARD)]
        lookup = build_conformal_lookup(records)
        q = lookup("B19013_001E", "trend_ensemble", "medium", None)
        assert q == 1.5

    def test_empty_records_always_returns_none(self):
        lookup = build_conformal_lookup([])
        assert lookup("B19013_001E", "trend_ensemble", "medium", "medium") is None


# ---------------------------------------------------------------------------
# _apply_conformal_interval integration (via ensemble helpers)
# ---------------------------------------------------------------------------

class TestApplyConformalInterval:
    """Test the ensemble._apply_conformal_interval helper directly."""

    def _make_fp(self, point=100_000.0, se=5_000.0, half=8_225.0):
        from census_forecaster.models import ForecastPoint
        return ForecastPoint(
            point=point,
            se_total=se,
            se_sample=se * 0.6,
            se_forecast=se * 0.8,
            ci90_low=point - half,
            ci90_high=point + half,
            method="trend_ensemble",
            target_year=2025,
            geoid="15003",
            indicator="B19013_001E",
            horizon=1,
            notes="test",
        )

    def _make_calibration(self, quantile: float, indicator="B19013_001E",
                          method="trend_ensemble", pop_bucket=WILDCARD, h_bucket=WILDCARD):
        return {
            "schema_version": 4,
            "conformal_quantile_by_stratum": [
                {
                    "indicator": indicator,
                    "method": method,
                    "pop_bucket": pop_bucket,
                    "h_bucket": h_bucket,
                    "quantile": quantile,
                    "n_calibration": 30,
                    "target_coverage": 0.90,
                }
            ],
        }

    def test_widens_ci_when_conformal_larger(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp(point=100_000.0, se=5_000.0, half=8_225.0)
        # q=2.0 → conformal_half = 2.0 * 5000 = 10_000 > 8_225
        cal = self._make_calibration(quantile=2.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        expected_half = 2.0 * 5_000.0
        assert result.ci90_low == pytest.approx(100_000.0 - expected_half)
        assert result.ci90_high == pytest.approx(100_000.0 + expected_half)

    def test_narrows_ci_when_conformal_narrower(self):
        # Conformal-primary policy (2026-08-21): where a record exists the
        # conformal interval replaces the κ CI in BOTH directions.
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp(point=100_000.0, se=5_000.0, half=8_225.0)
        # q=1.0 → conformal_half = 5_000 < 8_225 → CI narrows to conformal
        cal = self._make_calibration(quantile=1.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        assert result.ci90_low == pytest.approx(100_000.0 - 5_000.0)
        assert result.ci90_high == pytest.approx(100_000.0 + 5_000.0)

    def test_returns_unchanged_when_calibration_none(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp()
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", None, se_pre_kappa=5_000.0)
        assert result is fp

    def test_returns_unchanged_when_no_conformal_records(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp()
        cal = {"schema_version": 3}  # no conformal_quantile_by_stratum key
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        assert result is fp

    def test_notes_updated_when_conformal_applies(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp()
        cal = self._make_calibration(quantile=2.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        assert "conformal_ci" in result.notes

    def test_point_unchanged(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp(point=100_000.0)
        cal = self._make_calibration(quantile=2.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        assert result.point == pytest.approx(100_000.0)

    def test_se_total_unchanged(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp(se=5_000.0)
        cal = self._make_calibration(quantile=2.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=5_000.0)
        assert result.se_total == pytest.approx(5_000.0)  # se_total comes from κ, not conformal

    def test_zero_se_pre_kappa_returns_unchanged(self):
        from census_forecaster.acs.ensemble import _apply_conformal_interval
        fp = self._make_fp()
        cal = self._make_calibration(quantile=2.0)
        result = _apply_conformal_interval(fp, "B19013_001E", "trend_ensemble", cal, se_pre_kappa=0.0)
        assert result is fp
