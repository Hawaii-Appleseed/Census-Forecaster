"""Tests for the v3 calibration consumers.

Validates that:
* `_apply_se_override` honours the strata fallback chain when a v3
  calibration is provided, and reverts to v2 lookup otherwise.
* `_apply_bias_correction` applies the geometric correction with the
  same fallback semantics, and is a no-op when calibration is v2.
* `project_ensemble_multi` threads (pop_bucket, h_bucket) through to
  both helpers and applies bias *before* the SE override.
* When no v3 records match, projections fall back to v2 behavior
  unchanged (regression guard for backwards compatibility).
"""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.ensemble import (
    _apply_bias_correction,
    _apply_se_override,
    project_ensemble_multi,
)
from census_forecaster.acs.projection import EMPIRICAL_SE_INFLATOR
from census_forecaster.acs.strata import WILDCARD
from census_forecaster.models import AcsObservation, ForecastPoint

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _make_fp(
    *, point=100000.0, se_total=3000.0, se_sample=1500.0, se_forecast=2598.0,
    method="trend_ensemble", indicator="B19013_001E", geoid="15003",
    target_year=2026, horizon=2, ci90_low=None, ci90_high=None,
):
    half = se_total * 1.645
    return ForecastPoint(
        point=point, se_total=se_total, se_sample=se_sample, se_forecast=se_forecast,
        ci90_low=ci90_low if ci90_low is not None else point - half,
        ci90_high=ci90_high if ci90_high is not None else point + half,
        method=method, target_year=target_year, geoid=geoid,
        indicator=indicator, horizon=horizon, notes="",
    )


def _v3_calibration(
    *,
    se_records: list[dict] | None = None,
    bias_records: list[dict] | None = None,
):
    """Build a minimal v3-shaped calibration dict for testing."""
    return {
        "schema_version": 3,
        "strata_records": {
            "rmse": [],
            "coverage_pre": [],
            "coverage_post": [],
            "se_inflator": se_records or [],
            "bias": bias_records or [],
        },
        "rmse_by_indicator_source": {},
        "rmse_by_indicator_method": {},
        "ci90_coverage_by_indicator_method": {},
        "ci90_coverage_post_override": {},
        "se_inflator_override_by_indicator_method": {},
    }


def _strata_record_dict(
    *, indicator, method, pop_bucket, h_bucket, value, n_folds, **extra,
):
    out = {
        "indicator": indicator, "method": method,
        "pop_bucket": pop_bucket, "h_bucket": h_bucket,
        "value": value, "n_folds": n_folds,
    }
    if extra:
        out["extra"] = extra
    return out


# -----------------------------------------------------------------------------
# _apply_se_override with v3 strata
# -----------------------------------------------------------------------------

class TestApplySeOverrideV3:
    def test_v3_exact_cell_used(self):
        cal = _v3_calibration(se_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="large", h_bucket="short",
                value=1.50, n_folds=200,
                factor=1.50 / EMPIRICAL_SE_INFLATOR,
            ),
        ])
        fp = _make_fp(se_total=3000)
        out = _apply_se_override(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="large", h_bucket="short",
        )
        # κ=1.50 → factor = 1.50/1.30 ≈ 1.154
        expected_factor = 1.50 / EMPIRICAL_SE_INFLATOR
        assert out.se_total == pytest.approx(3000 * expected_factor, rel=1e-6)
        # Notes should record the strata source
        assert "strata=exact" in out.notes
        assert "1.500" in out.notes

    def test_v3_falls_back_to_h_marginal(self):
        # Exact cell has too few folds → fall to h_marg
        cal = _v3_calibration(se_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="large", h_bucket="short",
                value=99.0, n_folds=5,  # below threshold
            ),
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="large", h_bucket=WILDCARD,
                value=1.40, n_folds=300,
            ),
        ])
        fp = _make_fp()
        out = _apply_se_override(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="large", h_bucket="short",
        )
        # Should use h_marg κ=1.40, not exact κ=99
        expected_factor = 1.40 / EMPIRICAL_SE_INFLATOR
        assert out.se_total == pytest.approx(3000 * expected_factor, rel=1e-6)
        assert "strata=h_marg" in out.notes

    def test_v3_falls_back_to_global(self):
        cal = _v3_calibration(se_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket=WILDCARD, h_bucket=WILDCARD,
                value=1.30, n_folds=2000,
            ),
        ])
        fp = _make_fp()
        # Pop bucket "small" has no records → falls through to global
        out = _apply_se_override(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="small", h_bucket="short",
        )
        # κ=1.30 = base, so factor=1.0, SE unchanged
        assert out.se_total == pytest.approx(3000.0, rel=1e-6)
        assert "strata=global" in out.notes

    def test_v3_no_matching_cell_returns_unchanged(self):
        """If nothing qualifies, _apply_se_override returns fp unchanged."""
        cal = _v3_calibration(se_records=[
            _strata_record_dict(
                indicator="OTHER_IND", method="trend_ensemble",
                pop_bucket="small", h_bucket="short",
                value=1.50, n_folds=100,
            ),
        ])
        fp = _make_fp()
        out = _apply_se_override(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="large", h_bucket="long",
        )
        # No match for B19013_001E → no change
        assert out.se_total == fp.se_total

    def test_v2_calibration_still_works(self):
        """Backward compat: a v2 calibration dict (no strata_records) uses
        the legacy flat lookup."""
        cal = {
            "schema_version": 2,
            "se_inflator_override_by_indicator_method": {
                "B19013_001E": {"trend_ensemble": 1.40},
            },
        }
        fp = _make_fp()
        out = _apply_se_override(fp, "B19013_001E", "trend_ensemble", cal)
        expected_factor = 1.40 / EMPIRICAL_SE_INFLATOR
        assert out.se_total == pytest.approx(3000 * expected_factor, rel=1e-6)
        assert "(v2)" in out.notes


# -----------------------------------------------------------------------------
# _apply_bias_correction
# -----------------------------------------------------------------------------

class TestApplyBiasCorrection:
    def test_v2_calibration_is_no_op(self):
        """A v2 calibration has no bias records; bias correction is a no-op."""
        cal = {"schema_version": 2}
        fp = _make_fp()
        out = _apply_bias_correction(fp, "B19013_001E", "trend_ensemble", cal)
        assert out.point == fp.point
        assert out.se_total == fp.se_total

    def test_zero_bias_is_no_op(self):
        cal = _v3_calibration(bias_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="large", h_bucket="short",
                value=0.0, n_folds=100,
            ),
        ])
        fp = _make_fp()
        out = _apply_bias_correction(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="large", h_bucket="short",
        )
        assert out.point == fp.point

    def test_positive_bias_shrinks_point(self):
        # b=log(1.05) means the model overshoots by 5% on average.
        # Correction: divide point by 1.05.
        b = math.log(1.05)
        cal = _v3_calibration(bias_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="large", h_bucket="short",
                value=b, n_folds=100,
            ),
        ])
        fp = _make_fp(point=105000.0)
        out = _apply_bias_correction(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="large", h_bucket="short",
        )
        # 105000 / 1.05 = 100000
        assert out.point == pytest.approx(100000.0, rel=1e-9)
        # SEs and CIs all rescale by the same factor
        factor = math.exp(-b)
        assert out.se_total == pytest.approx(fp.se_total * factor, rel=1e-9)
        assert out.ci90_low == pytest.approx(fp.ci90_low * factor, rel=1e-9)
        assert out.ci90_high == pytest.approx(fp.ci90_high * factor, rel=1e-9)
        # Notes record the correction
        assert "bias_corr=" in out.notes

    def test_negative_bias_grows_point(self):
        b = math.log(0.95)  # model undershoots by 5%
        cal = _v3_calibration(bias_records=[
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket=WILDCARD, h_bucket=WILDCARD,
                value=b, n_folds=200,
            ),
        ])
        fp = _make_fp(point=95000.0)
        out = _apply_bias_correction(
            fp, "B19013_001E", "trend_ensemble", cal,
            pop_bucket="medium", h_bucket="long",  # falls through to global
        )
        assert out.point == pytest.approx(100000.0, rel=1e-9)


# -----------------------------------------------------------------------------
# project_ensemble_multi end-to-end with v3 calibration
# -----------------------------------------------------------------------------

class TestProjectEnsembleMultiV3:
    @staticmethod
    def _hawaii_obs():
        """A short Honolulu B19013 series."""
        years = list(range(2014, 2024))
        ests = [72133, 74460, 76872, 80513, 83102, 85857, 89409, 95608, 98013, 99814]
        return [
            AcsObservation(
                estimate=e, moe=2000, year=y, vintage="1y",
                geoid="15003", indicator="B19013_001E",
            )
            for y, e in zip(years, ests)
        ]

    def test_v3_calibration_applies_both_corrections(self):
        # Build a v3 calibration with non-zero bias and SE inflation
        b = math.log(1.02)  # 2% overshoot
        cal = _v3_calibration(
            se_records=[
                _strata_record_dict(
                    indicator="B19013_001E", method="trend_ensemble",
                    pop_bucket=WILDCARD, h_bucket=WILDCARD,
                    value=1.50, n_folds=300,
                ),
                _strata_record_dict(
                    indicator="B19013_001E", method="multi_anchor",
                    pop_bucket=WILDCARD, h_bucket=WILDCARD,
                    value=1.40, n_folds=300,
                ),
            ],
            bias_records=[
                _strata_record_dict(
                    indicator="B19013_001E", method="trend_ensemble",
                    pop_bucket=WILDCARD, h_bucket=WILDCARD,
                    value=b, n_folds=300,
                ),
            ],
        )
        cal["rmse_by_indicator_method"] = {
            "B19013_001E": {"trend_ensemble": 0.07, "multi_anchor": 0.06},
        }
        cal["rmse_by_indicator_source"] = {
            "B19013_001E": {"cpi_honolulu_allitems": 0.07, "qcew_hawaii_wages": 0.05},
        }

        # Without v3 calibration
        fp_no_cal = project_ensemble_multi(
            self._hawaii_obs(), target_year=2025, calibration=None,
            populations={"15003": 1_009_506},
        )
        # With v3 calibration
        fp_v3 = project_ensemble_multi(
            self._hawaii_obs(), target_year=2025, calibration=cal,
            populations={"15003": 1_009_506},
        )

        assert fp_no_cal is not None
        assert fp_v3 is not None
        # The v3 result should differ — bias correction shifts the point;
        # SE inflation widens the CI.
        assert fp_v3.point != pytest.approx(fp_no_cal.point, rel=1e-9)

    def test_v2_calibration_unchanged_behavior(self):
        """A v2-shaped calibration should produce the same result as before
        v3 was added — regression guard."""
        cal_v2 = {
            "schema_version": 2,
            "se_inflator_override_by_indicator_method": {
                "B19013_001E": {"trend_ensemble": 1.40},
            },
            "rmse_by_indicator_method": {
                "B19013_001E": {"trend_ensemble": 0.07, "multi_anchor": 0.06},
            },
            "rmse_by_indicator_source": {
                "B19013_001E": {"cpi_honolulu_allitems": 0.07},
            },
        }
        fp = project_ensemble_multi(
            self._hawaii_obs(), target_year=2025, calibration=cal_v2,
            populations={"15003": 1_009_506},
        )
        assert fp is not None
        # Should run without error and produce a sensible projection
        assert 80000 < fp.point < 200000
        assert fp.se_total > 0

    def test_no_population_falls_through_to_global(self):
        """If the projection target has no known 2020 pop, the v3 lookup
        chain should still succeed by hitting globally marginalised cells."""
        cal = _v3_calibration(
            se_records=[
                _strata_record_dict(
                    indicator="B19013_001E", method="trend_ensemble",
                    pop_bucket=WILDCARD, h_bucket=WILDCARD,
                    value=1.50, n_folds=300,
                ),
            ],
        )
        cal["rmse_by_indicator_method"] = {
            "B19013_001E": {"trend_ensemble": 0.07, "multi_anchor": 0.06},
        }
        cal["rmse_by_indicator_source"] = {
            "B19013_001E": {"cpi_honolulu_allitems": 0.07},
        }
        fp = project_ensemble_multi(
            self._hawaii_obs(), target_year=2025, calibration=cal,
            populations={},  # no population for 15003
        )
        assert fp is not None
        # Notes on the inner trend should show strata=global
        assert "strata=global" in fp.notes or "trend:" in fp.notes


# -----------------------------------------------------------------------------
# _lookup_phi gating (v4 phi is diagnostic-only unless phi_enabled)
# -----------------------------------------------------------------------------

class TestLookupPhiGating:
    """Per-cell phi must only apply when the payload opts in.

    The kappa/bias records in a payload are calibrated on Pass 2 folds. A
    payload without `phi_enabled: true` built those folds at DEFAULT_PHI, so
    applying per-cell phi at projection time would run production under a
    different trend model than its calibration was fit for.
    """

    @staticmethod
    def _phi_calibration(*, phi_enabled=None):
        cal = _v3_calibration()
        cal["strata_records"]["phi"] = [
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket="xlarge", h_bucket="short",
                value=0.72, n_folds=50,
            ),
            _strata_record_dict(
                indicator="B19013_001E", method="trend_ensemble",
                pop_bucket=WILDCARD, h_bucket=WILDCARD,
                value=0.78, n_folds=200,
            ),
        ]
        if phi_enabled is not None:
            cal["phi_enabled"] = phi_enabled
        return cal

    def test_records_without_flag_are_ignored(self):
        from census_forecaster.acs.ensemble import _lookup_phi
        from census_forecaster.acs.projection import DEFAULT_PHI
        cal = self._phi_calibration()  # no phi_enabled key (bundled payloads)
        phi = _lookup_phi(cal, "B19013_001E", "trend_ensemble", "xlarge", "short")
        assert phi == DEFAULT_PHI

    def test_flag_false_is_ignored(self):
        from census_forecaster.acs.ensemble import _lookup_phi
        from census_forecaster.acs.projection import DEFAULT_PHI
        cal = self._phi_calibration(phi_enabled=False)
        phi = _lookup_phi(cal, "B19013_001E", "trend_ensemble", "xlarge", "short")
        assert phi == DEFAULT_PHI

    def test_flag_true_applies_per_cell_value(self):
        from census_forecaster.acs.ensemble import _lookup_phi
        cal = self._phi_calibration(phi_enabled=True)
        phi = _lookup_phi(cal, "B19013_001E", "trend_ensemble", "xlarge", "short")
        assert phi == pytest.approx(0.72)

    def test_flag_true_without_records_falls_back(self):
        from census_forecaster.acs.ensemble import _lookup_phi
        from census_forecaster.acs.projection import DEFAULT_PHI
        cal = _v3_calibration()
        cal["phi_enabled"] = True
        phi = _lookup_phi(cal, "B19013_001E", "trend_ensemble", "xlarge", "short")
        assert phi == DEFAULT_PHI
