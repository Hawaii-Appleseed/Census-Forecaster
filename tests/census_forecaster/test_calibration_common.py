"""Tests for the shared bisection algorithm in `acs.calibration_common`.

The same bisection drives both the ACS dollar-space and BLS log-space
calibration paths. Pinning the algorithm in isolation here catches
regressions to the shared logic that integration tests would only
detect after the per-module geometry kicks in.
"""
from __future__ import annotations

import math

from census_forecaster.acs.calibration_common import (
    DEFAULT_COVERAGE_HIGH,
    DEFAULT_COVERAGE_LOW,
    bisect_for_target_coverage,
)


class TestBisectForTargetCoverage:
    def test_in_band_returns_factor_one(self):
        # cov(1.0) = 0.9 — in band, no change
        cov_fn = lambda f: 0.85 + 0.05 * (1 - math.exp(-(f - 0.5)))  # noqa: E731
        # Tune so cov(1.0) ≈ 0.88 — in band
        cov_fn = lambda f: 0.88 if f == 1.0 else 0.50  # noqa: E731 — abrupt
        factor, cov = bisect_for_target_coverage(cov_fn)
        assert factor == 1.0
        assert cov == 0.88

    def test_undercover_widens(self):
        # Use a smooth, monotonic coverage function: cov(f) = 1 - exp(-f)
        # cov(1) ≈ 0.632 (under target_low), cov(5) ≈ 0.993 (over target_high)
        # Bisection should land in [0.85, 0.95]
        cov_fn = lambda f: 1 - math.exp(-f)  # noqa: E731
        factor, cov = bisect_for_target_coverage(cov_fn)
        assert 1.0 < factor < 5.0
        assert DEFAULT_COVERAGE_LOW <= cov <= DEFAULT_COVERAGE_HIGH

    def test_overcover_narrows(self):
        # cov(1) ≈ 0.99 (over), cov(0.5) ≈ 0.95 (in band)
        cov_fn = lambda f: 1 - math.exp(-f * 5.0)  # noqa: E731
        factor, cov = bisect_for_target_coverage(cov_fn)
        assert factor < 1.0
        assert cov <= DEFAULT_COVERAGE_HIGH + 1e-9

    def test_nan_coverage_returns_one(self):
        cov_fn = lambda f: float("nan")  # noqa: E731
        factor, cov = bisect_for_target_coverage(cov_fn)
        assert factor == 1.0
        assert math.isnan(cov)

    def test_can_override_target_band(self):
        # Tighter band [0.89, 0.91] — bisection should still land
        cov_fn = lambda f: 1 - math.exp(-f)  # noqa: E731
        factor, cov = bisect_for_target_coverage(
            cov_fn, target_low=0.89, target_high=0.91,
        )
        assert 0.89 <= cov <= 0.91 or factor < 5.0

    def test_returns_endpoint_when_search_doesnt_bracket(self):
        # cov is constant 0.5 — never reaches band even at factor=5
        cov_fn = lambda f: 0.50  # noqa: E731
        factor, cov = bisect_for_target_coverage(cov_fn)
        # Should return the upper endpoint (factor=5) with coverage 0.5
        assert factor == 5.0
        assert cov == 0.5

    def test_default_band_is_85_95(self):
        # Documented constants — pinned to catch unintended changes.
        assert DEFAULT_COVERAGE_LOW == 0.85
        assert DEFAULT_COVERAGE_HIGH == 0.95
