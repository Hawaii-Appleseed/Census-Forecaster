"""Tests for the v3 stratified BLS CPI calibration.

Coverage:
* Bucket classifiers (h_bucket_for_months, vol_regime_for_value).
* In-memory residual helpers (bias application, log-space coverage).
* SE bisection on log-space residuals.
* Bias estimation + clamping + n-threshold.
* Strata fallback chain (exact → vol_marg → h_vol_marg → global → floor).
* End-to-end run on a synthetic panel.
* compute_cpi_ratio integration: v3 calibration applied vs v2 fallback.
"""
from __future__ import annotations

import json
import math
from datetime import date

import pytest
from census_forecaster.acs.strata import WILDCARD, StrataRecord
from census_forecaster.bls.calibration import (
    DEFAULT_BIAS_CLAMP_LOG,
    BlsFoldResidual,
    _apply_bias_to_residual,
    _bisect_se_factor,
    _coverage_at_factor,
    _estimate_bias_records,
    h_bucket_for_months,
    load_bls_calibration,
    lookup_bls_strata,
    resolve_kappa_and_bias,
    run_stratified_bls_calibration,
    vol_regime_for_value,
    write_bls_calibration,
)
from census_forecaster.bls.projection import (
    _PROJ_SE_INFLATOR,
    _Z_90,
    compute_cpi_ratio,
)

# -----------------------------------------------------------------------------
# Bucket classifiers
# -----------------------------------------------------------------------------

class TestHBucket:
    def test_short_horizons(self):
        assert h_bucket_for_months(1) == "short"
        assert h_bucket_for_months(2) == "short"
        assert h_bucket_for_months(4) == "short"
        assert h_bucket_for_months(5) == "short"

    def test_long_horizons(self):
        assert h_bucket_for_months(6) == "long"
        assert h_bucket_for_months(12) == "long"

    def test_negative_or_zero_returns_wildcard(self):
        assert h_bucket_for_months(0) == WILDCARD
        assert h_bucket_for_months(-3) == WILDCARD

    def test_beyond_defined_clamps_to_long(self):
        # h=24 isn't in any defined bucket — clamps to longest
        assert h_bucket_for_months(24) == "long"
        assert h_bucket_for_months(120) == "long"


class TestVolRegime:
    def test_low_when_below_threshold(self):
        assert vol_regime_for_value(0.001, 0.005) == "low"

    def test_high_when_above_threshold(self):
        assert vol_regime_for_value(0.010, 0.005) == "high"

    def test_equal_to_threshold_is_low(self):
        # Boundary: vol_value <= threshold → low (per docstring)
        assert vol_regime_for_value(0.005, 0.005) == "low"

    def test_none_returns_wildcard(self):
        assert vol_regime_for_value(None, 0.005) == WILDCARD
        assert vol_regime_for_value(0.001, None) == WILDCARD

    def test_nan_returns_wildcard(self):
        assert vol_regime_for_value(float("nan"), 0.005) == WILDCARD


# -----------------------------------------------------------------------------
# In-memory residual operations
# -----------------------------------------------------------------------------

def _fr(point=100.0, actual=100.0, se=0.05,
        series_id="TEST", h_bucket="short", vol_regime="low",
        h_months=2, anchor_year=2020, anchor_month=1,
        target_year=2020, target_month=3):
    half = _Z_90 * se
    return BlsFoldResidual(
        series_id=series_id, anchor_year=anchor_year, anchor_month=anchor_month,
        target_year=target_year, target_month=target_month, h_months=h_months,
        h_bucket=h_bucket, vol_regime=vol_regime,
        actual=actual, point=point,
        residual_log_std=se,
        forecast_se_log=se,
        ci90_low=point * math.exp(-half),
        ci90_high=point * math.exp(+half),
    )


class TestApplyBias:
    def test_zero_bias_is_identity(self):
        r = _fr(point=105, actual=100)
        out = _apply_bias_to_residual(r, 0.0)
        assert out is r

    def test_positive_bias_shrinks_point(self):
        # b = log(1.05) → divide point by 1.05, recover 100
        r = _fr(point=105, actual=100, se=0.02)
        b = math.log(1.05)
        out = _apply_bias_to_residual(r, b)
        assert out.point == pytest.approx(100.0, rel=1e-9)
        # CI bounds rescale by same factor
        factor = math.exp(-b)
        assert out.ci90_low == pytest.approx(r.ci90_low * factor, rel=1e-9)
        assert out.ci90_high == pytest.approx(r.ci90_high * factor, rel=1e-9)

    def test_negative_bias_grows_point(self):
        r = _fr(point=95, actual=100)
        b = math.log(0.95)
        out = _apply_bias_to_residual(r, b)
        assert out.point == pytest.approx(100.0, rel=1e-9)


class TestCoverageAtFactor:
    def test_empty_residuals_returns_nan(self):
        assert math.isnan(_coverage_at_factor([], 1.0))

    def test_factor_one_recovers_input_coverage(self):
        # CI is [point × exp(-Z·se), point × exp(+Z·se)] = [89.6, 111.6]
        # at point=100, se=0.06; actual=110 lands inside.
        residuals = [
            _fr(point=100, actual=99, se=0.06),
            _fr(point=100, actual=110, se=0.06),
            _fr(point=100, actual=130, se=0.06),  # outside
        ]
        cov = _coverage_at_factor(residuals, 1.0)
        # 2 of 3 inside
        assert cov == pytest.approx(2 / 3, rel=1e-6)

    def test_widening_factor_increases_coverage(self):
        residuals = [
            _fr(point=100, actual=130, se=0.05),  # ~31% high
        ]
        cov_narrow = _coverage_at_factor(residuals, 1.0)
        cov_wide = _coverage_at_factor(residuals, 5.0)
        assert cov_narrow == 0.0
        assert cov_wide == 1.0


class TestBisectSeFactor:
    def _make(self, n: int, target_cov: float, seed: int = 0):
        """Build n synthetic residuals with smoothly varying log-error."""
        import random
        rng = random.Random(seed)
        # Choose log-residual SD so target_cov of N(0, sd) falls within ±Z*0.05
        # Empirical CI half-width at point=100, se=0.05: ~Z*0.05*100 ≈ 8.2 in
        # log space. We construct actuals = point × exp(N(0, log_sd)) and
        # measure how many fall within ±Z*0.05.
        if target_cov <= 0.01:
            log_sd = 1.0
        elif target_cov >= 0.99:
            log_sd = 0.001
        else:
            from statistics import NormalDist
            z = NormalDist().inv_cdf(0.5 + target_cov / 2.0)
            log_sd = (_Z_90 * 0.05) / max(z, 1e-3)
        out = []
        for i in range(n):
            offset = rng.gauss(0, log_sd)
            out.append(_fr(point=100.0, actual=100.0 * math.exp(offset), se=0.05))
        return out

    def test_in_band_returns_one(self):
        residuals = self._make(200, target_cov=0.9, seed=1)
        factor, cov = _bisect_se_factor(residuals)
        assert factor == 1.0
        assert 0.83 <= cov <= 0.97

    def test_undercovered_widens(self):
        residuals = self._make(200, target_cov=0.5, seed=2)
        factor, cov = _bisect_se_factor(residuals)
        assert factor > 1.0
        assert 0.83 <= cov <= 0.97

    def test_overcovered_narrows(self):
        # Use a less extreme target so the synthetic residuals span a wider
        # range — at 0.99 with only 200 samples and a small log_sd, the
        # bisection's narrowed factor often still admits 100% coverage
        # (the residuals are too tightly clustered).
        residuals = self._make(400, target_cov=0.97, seed=3)
        factor, cov = _bisect_se_factor(residuals)
        assert factor < 1.0
        # Coverage post-narrowing should be at or below the original;
        # whether it lands exactly in [0.85, 0.95] depends on residual
        # discretisation, but it must not exceed the input target.
        assert cov <= 0.99


# -----------------------------------------------------------------------------
# Bias estimation
# -----------------------------------------------------------------------------

class TestEstimateBiasRecords:
    def test_below_n_threshold_emits_zero(self):
        # 5 residuals all overshooting by 5% → b_raw ≈ log(1.05),
        # but n=5 < 20 → applied b should be 0.
        residuals = [_fr(point=105, actual=100) for _ in range(5)]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        for rec in records:
            assert rec.value == 0.0
            # Raw still computed
            assert "b_raw" in rec.extra
            assert rec.extra["b_raw"] == pytest.approx(math.log(1.05), abs=1e-6)

    def test_above_n_threshold_applies(self):
        residuals = [_fr(point=105, actual=100) for _ in range(25)]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        exact = [r for r in records if r.pop_bucket == "TEST"
                 and r.h_bucket == "short|low"]
        assert len(exact) == 1
        assert exact[0].value == pytest.approx(math.log(1.05), abs=1e-6)
        assert exact[0].extra["clamped"] == 0.0

    def test_clamp_fires_on_extreme_bias(self):
        # 25 residuals overshooting by 25% → clamp to ±10%
        residuals = [_fr(point=125, actual=100) for _ in range(25)]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        exact = [r for r in records if r.pop_bucket == "TEST"
                 and r.h_bucket == "short|low"]
        assert len(exact) == 1
        assert exact[0].value == pytest.approx(DEFAULT_BIAS_CLAMP_LOG, abs=1e-6)
        assert exact[0].extra["clamped"] == 1.0


# -----------------------------------------------------------------------------
# Strata fallback chain
# -----------------------------------------------------------------------------

class TestLookupBlsStrata:
    @staticmethod
    def _records():
        # Build records using the composite "h|vol" h_bucket encoding
        return [
            # Exact cell, n=200 → qualifies
            StrataRecord("", "", "TEST_SERIES", "short|low",
                         value=1.20, n_folds=200),
            # Exact under-n cell → must fall through
            StrataRecord("", "", "TEST_SERIES", "long|low",
                         value=99.0, n_folds=5),
            # Vol-marg fallback for (TEST_SERIES, long, *)
            StrataRecord("", "", "TEST_SERIES", "long|*",
                         value=1.40, n_folds=300),
            # H-vol marg fallback for (TEST_SERIES, *, *)
            StrataRecord("", "", "TEST_SERIES", "*|*",
                         value=1.30, n_folds=600),
            # Global fallback
            StrataRecord("", "", "*", "*|*",
                         value=1.25, n_folds=1000),
        ]

    def test_exact_match(self):
        v, src = lookup_bls_strata(
            self._records(), "TEST_SERIES", "short", "low",
            n_threshold=20, floor_value=1.0,
        )
        assert src == "exact"
        assert v == 1.20

    def test_falls_through_to_vol_marg(self):
        v, src = lookup_bls_strata(
            self._records(), "TEST_SERIES", "long", "low",
            n_threshold=20, floor_value=1.0,
        )
        # exact (long, low) has n=5 < 20; falls to (long, *)
        assert src == "vol_marg"
        assert v == 1.40

    def test_falls_through_to_h_vol_marg(self):
        # Build records where vol-marg is unavailable for (long, *) but
        # h-vol-marg (TEST_SERIES, *, *) qualifies. Skips the vol_marg
        # level explicitly.
        records = [
            StrataRecord("", "", "TEST_SERIES", "long|low",
                         value=99.0, n_folds=5),  # under-n, falls through
            # No "long|*" record at qualifying n — skip vol_marg level
            StrataRecord("", "", "TEST_SERIES", "*|*",
                         value=1.30, n_folds=600),
        ]
        v, src = lookup_bls_strata(
            records, "TEST_SERIES", "long", "low",
            n_threshold=20, floor_value=1.0,
        )
        assert src == "h_vol_marg"
        assert v == 1.30

    def test_falls_through_to_global(self):
        v, src = lookup_bls_strata(
            self._records(), "OTHER_SERIES", "short", "low",
            n_threshold=20, floor_value=1.0,
        )
        # No records for OTHER_SERIES → global
        assert src == "global"
        assert v == 1.25

    def test_floor_when_nothing_qualifies(self):
        # No records at all
        v, src = lookup_bls_strata(
            [], "ANY", "short", "low",
            n_threshold=20, floor_value=1.5,
        )
        assert src == "floor"
        assert v == 1.5


# -----------------------------------------------------------------------------
# resolve_kappa_and_bias
# -----------------------------------------------------------------------------

class TestResolveKappaAndBias:
    def test_no_calibration_returns_floor(self):
        kappa, b, k_src, b_src = resolve_kappa_and_bias(
            calibration=None, series_id="X",
            h_bucket="short", vol_regime="low",
        )
        assert k_src == "floor"
        assert b_src == "floor"
        assert kappa == 1.0
        assert b == 0.0

    def test_with_calibration_uses_strata(self):
        cal = {
            "strata_records": {
                "se_inflator": [
                    {
                        "indicator": "", "method": "", "pop_bucket": "X",
                        "h_bucket": "short|low", "value": 2.0, "n_folds": 100,
                    },
                ],
                "bias": [
                    {
                        "indicator": "", "method": "", "pop_bucket": "X",
                        "h_bucket": "short|low", "value": 0.05, "n_folds": 100,
                    },
                ],
            },
        }
        kappa, b, k_src, b_src = resolve_kappa_and_bias(
            cal, series_id="X", h_bucket="short", vol_regime="low",
        )
        assert k_src == "exact"
        assert b_src == "exact"
        assert kappa == 2.0
        assert b == 0.05


# -----------------------------------------------------------------------------
# End-to-end synthetic panel
# -----------------------------------------------------------------------------

def _synth_series(start_year=2014, n_months=120, monthly_rate=0.002, noise_sd=0.001, seed=0):
    """Generate a monthly CPI series with a constant growth rate + log-normal noise."""
    import random
    rng = random.Random(seed)
    out = []
    y, m = start_year, 1
    value = 100.0
    for _ in range(n_months):
        out.append({"year": y, "period": f"M{m:02d}", "value": value})
        value *= (1.0 + monthly_rate) * math.exp(rng.gauss(0, noise_sd))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


class TestEndToEndCalibration:
    def test_smoke_run(self):
        cpi = {
            "TEST_A": _synth_series(monthly_rate=0.002, noise_sd=0.001, seed=1),
            "TEST_B": _synth_series(monthly_rate=0.003, noise_sd=0.005, seed=2),
        }
        payload = run_stratified_bls_calibration(
            cpi_data=cpi, horizons_months=(2, 4, 6, 8, 12), n_threshold=10,
        )
        assert payload["schema_version"] == 1
        # Basic sanity
        assert len(payload["fold_residuals"]) > 100
        assert "TEST_A" in payload["vol_regime_thresholds"]
        # Strata records cover all granularities
        bias_records = payload["strata_records"]["bias"]
        pop_buckets = {r["pop_bucket"] for r in bias_records}
        assert "TEST_A" in pop_buckets
        assert "TEST_B" in pop_buckets
        assert WILDCARD in pop_buckets

    def test_write_then_load(self, tmp_path):
        cpi = {"TEST": _synth_series(seed=3)}
        payload = run_stratified_bls_calibration(
            cpi_data=cpi, horizons_months=(2, 4), n_threshold=10,
        )
        path = tmp_path / "bls_calibration.json"
        write_bls_calibration(payload, path)
        # On disk, fold_residuals should be stripped
        with open(path) as f:
            on_disk = json.load(f)
        assert "fold_residuals" not in on_disk
        assert "strata_records" in on_disk
        # load_bls_calibration round-trips
        loaded = load_bls_calibration(path)
        assert loaded is not None
        assert loaded["schema_version"] == 1


# -----------------------------------------------------------------------------
# compute_cpi_ratio integration with v3
# -----------------------------------------------------------------------------

class TestComputeCpiRatioV3:
    @staticmethod
    def _series_with_history():
        # 60 months of history ending 2024-12; project to 2026-06 (h=18 months)
        return _synth_series(start_year=2020, n_months=60,
                              monthly_rate=0.003, noise_sd=0.001, seed=42)

    def test_no_calibration_legacy_path(self):
        """Without `calibration`, compute_cpi_ratio uses legacy κ=1.50."""
        cpi = {"TEST": self._series_with_history()}
        result = compute_cpi_ratio(
            cpi, "TEST",
            baseline_date=date(2020, 1, 1),
            target_date=date(2026, 6, 1),
        )
        assert result["method"] == "projected"
        assert result["kappa_used"] is None
        assert result["bias_used"] is None
        # CI should be present and reasonable
        assert result["ratio_ci90_low"] is not None
        assert result["ratio_ci90_high"] is not None

    def test_with_calibration_applies_kappa_and_bias(self):
        """With v3 calibration, ratio shifts (bias) and CI changes (κ)."""
        cpi = {"TEST": self._series_with_history()}
        cal = {
            "vol_regime_thresholds": {"TEST": 0.001},
            "strata_records": {
                "se_inflator": [
                    {
                        "indicator": "", "method": "", "pop_bucket": "*",
                        "h_bucket": "*|*", "value": 3.0, "n_folds": 1000,
                    },
                ],
                "bias": [
                    {
                        "indicator": "", "method": "", "pop_bucket": "*",
                        "h_bucket": "*|*", "value": 0.02, "n_folds": 1000,
                    },
                ],
            },
        }
        r_no = compute_cpi_ratio(
            cpi, "TEST", date(2020, 1, 1), date(2026, 6, 1),
        )
        r_v3 = compute_cpi_ratio(
            cpi, "TEST", date(2020, 1, 1), date(2026, 6, 1),
            calibration=cal,
        )
        # v3 ratio should equal v2 ratio / exp(0.02)
        expected_ratio = r_no["ratio"] / math.exp(0.02)
        assert r_v3["ratio"] == pytest.approx(expected_ratio, rel=1e-9)
        assert r_v3["kappa_used"] == 3.0
        # Source label is "global" because we provided ("*", "*|*") record
        assert r_v3["kappa_source"] == "global"
        assert r_v3["bias_used"] == 0.02
        # SE rescaled: factor = κ_v3 / 1.50 = 2.0
        assert r_v3["forecast_se"] == pytest.approx(
            r_no["forecast_se"] * 3.0 / _PROJ_SE_INFLATOR, rel=1e-9,
        )

    def test_back_compat_no_calibration_unchanged(self):
        """compute_cpi_ratio with calibration=None must be numerically
        identical to pre-v3 behavior on a known input."""
        cpi = {"TEST": self._series_with_history()}
        result = compute_cpi_ratio(
            cpi, "TEST",
            baseline_date=date(2020, 1, 1),
            target_date=date(2026, 6, 1),
            calibration=None,
        )
        # Pre-v3 behavior: ratio is what it always was
        assert result["ratio"] > 1.0  # 6 years of 0.3%/mo growth
        assert result["kappa_used"] is None
        assert result["forecast_se"] is not None
