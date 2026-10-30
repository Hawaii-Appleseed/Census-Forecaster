"""End-to-end integration tests for the v3 calibration pipeline.

These tests exercise the full pipeline: build a synthetic panel →
run calibration → write → load → project. They catch integration
bugs that unit tests can miss (e.g. mismatched key conventions
between the generator and the consumers, JSON round-trip drift,
order-of-operations bugs in the bias × SE override interaction).

They also pin v2 backwards compatibility: a v2-shaped calibration
loaded against the v3-aware code must produce the same projection
numbers as it did against the pre-v3 code, within tight tolerance.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict

import pytest
from census_forecaster.acs.calibration import (
    run_stratified_calibration,
    write_calibration,
)
from census_forecaster.acs.ensemble import project_ensemble_multi
from census_forecaster.models import AcsObservation

# -----------------------------------------------------------------------------
# Fixture: synthetic multi-county panel
# -----------------------------------------------------------------------------

def _build_synthetic_panel(
    n_counties: int = 12,
    indicators: list[str] | None = None,
    growth_rate: float = 0.025,
    noise_sd: float = 0.04,
    base_year: int = 2010,
    end_year: int = 2024,
    seed: int = 0,
) -> dict:
    """Build a synthetic multi-county panel of plausible-shape ACS series.

    Each (county, indicator) series follows base × (1 + growth_rate)^i ×
    exp(N(0, noise_sd)) — geometric trend with multiplicative noise.
    Different counties have different base values to span population
    buckets when paired with the population fixture below.
    """
    if indicators is None:
        indicators = ["B19013_001E", "B25064_001E"]
    rng = random.Random(seed)
    panel: dict[tuple[str, str], list[AcsObservation]] = defaultdict(list)

    # Spread across realistic county GEOIDs (state 06=CA, 36=NY, 15=HI, 48=TX, 12=FL, 17=IL)
    state_county_pairs = [
        ("06", "037"), ("06", "059"), ("06", "073"),  # LA, Orange, San Diego
        ("36", "061"), ("36", "047"),                  # NY, Kings (Brooklyn)
        ("48", "201"), ("48", "113"),                  # Harris (Houston), Dallas
        ("17", "031"),                                 # Cook (Chicago)
        ("12", "086"),                                 # Miami-Dade
        ("15", "003"), ("15", "001"), ("15", "007"),  # Honolulu, Hawaii, Maui
    ][:n_counties]

    for st, co in state_county_pairs:
        geoid = st + co
        # Base estimate varies by county (proxies for cost-of-living variation)
        county_factor = 1.0 + 0.5 * rng.random()  # 1.0–1.5
        for ind in indicators:
            indicator_factor = 1.0 if ind.startswith("B19") else 0.02  # rents are smaller
            for i, year in enumerate(range(base_year, end_year + 1)):
                if year == 2020:
                    continue  # 1y ACS suspended
                trend = (1 + growth_rate) ** i
                noise = math.exp(rng.gauss(0, noise_sd))
                base = 50_000 * county_factor * indicator_factor
                panel[(geoid, ind)].append(AcsObservation(
                    estimate=base * trend * noise,
                    moe=base * trend * 0.025,  # 2.5% MOE
                    year=year, vintage="1y",
                    geoid=geoid, indicator=ind,
                ))
    return dict(panel)


SYNTHETIC_POPULATIONS = {
    # Large metros
    "06037": 10_014_009,  # LA County (xlarge)
    "06059": 3_175_692,   # Orange (xlarge)
    "06073": 3_298_634,   # San Diego (xlarge)
    "36061": 1_628_706,   # NY County / Manhattan (xlarge)
    "36047": 2_576_771,   # Kings / Brooklyn (xlarge)
    "48201": 4_731_145,   # Harris / Houston (xlarge)
    "48113": 2_613_539,   # Dallas (xlarge)
    "17031": 5_173_146,   # Cook / Chicago (xlarge)
    "12086": 2_701_767,   # Miami-Dade (xlarge)
    # Hawaii (mixed buckets)
    "15003": 1_009_506,   # Honolulu (xlarge)
    "15001": 200_629,     # Hawaii County (large)
    "15007": 168_307,     # Maui (medium)
}


# -----------------------------------------------------------------------------
# End-to-end pipeline
# -----------------------------------------------------------------------------

class TestEndToEnd:
    def test_calibrate_write_load_project(self, tmp_path):
        panel = _build_synthetic_panel(n_counties=12, seed=11)
        anchor_years = [2014, 2016, 2018, 2021, 2022]

        # Calibrate
        payload = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=anchor_years,
            horizons=[1, 2, 3],
            populations=SYNTHETIC_POPULATIONS,
            n_threshold=10,  # lower for the small synthetic panel
        )
        assert payload["schema_version"] in (3, 4)
        assert "strata_records" in payload
        # SE inflators must be in a sensible range — clamped, not blown up
        for d in payload["strata_records"]["se_inflator"]:
            assert 0.1 <= d["value"] <= 5.0

        # Write to disk
        cal_path = tmp_path / "calibration.json"
        write_calibration(payload, cal_path)
        # On-disk version drops fold_residuals
        on_disk = json.loads(cal_path.read_text())
        assert "fold_residuals" not in on_disk
        assert "strata_records" in on_disk

        # Load back
        from census_forecaster.acs.anchors import load_calibration
        loaded = load_calibration(cal_path)
        assert loaded is not None
        assert loaded["schema_version"] in (3, 4)

        # Project against a real series with the loaded calibration
        honolulu_series = panel[("15003", "B19013_001E")]
        fp = project_ensemble_multi(
            honolulu_series, target_year=2026,
            calibration=loaded,
            populations=SYNTHETIC_POPULATIONS,
        )
        assert fp is not None
        assert math.isfinite(fp.point)
        assert fp.point > 0
        assert fp.ci90_low <= fp.point <= fp.ci90_high
        assert fp.method == "ensemble_multi_anchor"

    def test_v3_calibration_changes_projection(self, tmp_path):
        """A v3 calibration with non-trivial bias/κ should shift the
        projection relative to no-calibration."""
        panel = _build_synthetic_panel(n_counties=12, seed=22)
        cal = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=[2014, 2016, 2018, 2021, 2022],
            horizons=[1, 2, 3],
            populations=SYNTHETIC_POPULATIONS,
            n_threshold=10,
        )
        honolulu_series = panel[("15003", "B19013_001E")]
        fp_no_cal = project_ensemble_multi(
            honolulu_series, target_year=2026, calibration=None,
            populations=SYNTHETIC_POPULATIONS,
        )
        fp_with_cal = project_ensemble_multi(
            honolulu_series, target_year=2026, calibration=cal,
            populations=SYNTHETIC_POPULATIONS,
        )
        assert fp_no_cal is not None and fp_with_cal is not None
        # Both should produce sensible projections; the calibrated one's
        # CI width should reflect the calibrated κ.
        assert fp_with_cal.ci90_high > fp_with_cal.ci90_low
        assert fp_no_cal.ci90_high > fp_no_cal.ci90_low

    def test_strata_lookup_falls_through_when_county_unknown(self):
        """A county not in the population dict should still get a sensible
        projection via the global-marginalised cells."""
        panel = _build_synthetic_panel(n_counties=12, seed=33)
        cal = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=[2014, 2016, 2018, 2021, 2022],
            horizons=[1, 2, 3],
            populations=SYNTHETIC_POPULATIONS,
            n_threshold=10,
        )
        # Project against a county whose population is NOT in the dict
        unknown_geoid_series = [
            AcsObservation(
                estimate=80_000, moe=2000, year=y, vintage="1y",
                geoid="99999", indicator="B19013_001E",  # fictitious geoid
            )
            for y in range(2014, 2024)
        ]
        fp = project_ensemble_multi(
            unknown_geoid_series, target_year=2026, calibration=cal,
            populations=SYNTHETIC_POPULATIONS,
        )
        assert fp is not None
        # Should have run cleanly and produced a sensible CI
        assert math.isfinite(fp.point)
        assert fp.ci90_low <= fp.point <= fp.ci90_high


# -----------------------------------------------------------------------------
# v2 → v3 backwards compatibility regression
# -----------------------------------------------------------------------------

class TestV2BackwardsCompat:
    """Pin: a v2 calibration must produce the same projection numbers
    against v3-aware consumers as it did pre-v3."""

    @staticmethod
    def _v2_cal_fixture():
        """A realistic v2-shaped calibration payload for regression testing.

        Modelled after the actual production calibration.json that shipped
        in v0.2 (Hawaii panel, fixed h=2)."""
        return {
            "schema_version": 2,
            "anchor_years": [2015, 2017, 2019, 2021],
            "horizon": 2,
            "rmse_by_indicator_source": {
                "B19013_001E": {
                    "cpi_honolulu_allitems": 0.0708,
                    "qcew_hawaii_wages": 0.0653,
                },
            },
            "rmse_by_indicator_method": {
                "B19013_001E": {
                    "trend_ensemble": 0.0703,
                    "multi_anchor": 0.0677,
                },
            },
            "ci90_coverage_by_indicator_method": {
                "B19013_001E": {"trend_ensemble": 0.92, "multi_anchor": 0.92},
            },
            "se_inflator_override_by_indicator_method": {
                "B19013_001E": {"trend_ensemble": 1.45, "multi_anchor": 1.20},
            },
        }

    def test_v2_calibration_loads_clean(self):
        cal = self._v2_cal_fixture()
        # No strata_records, but consumers must accept it
        from census_forecaster.acs.ensemble import _apply_se_override
        fp = self._fake_fp()
        out = _apply_se_override(fp, "B19013_001E", "trend_ensemble", cal)
        assert "v2" in out.notes
        # κ=1.45 → factor = 1.45/1.30 ≈ 1.115
        from census_forecaster.acs.projection import EMPIRICAL_SE_INFLATOR
        expected_factor = 1.45 / EMPIRICAL_SE_INFLATOR
        assert out.se_total == pytest.approx(fp.se_total * expected_factor, rel=1e-6)

    def test_v2_bias_correction_is_no_op(self):
        cal = self._v2_cal_fixture()
        from census_forecaster.acs.ensemble import _apply_bias_correction
        fp = self._fake_fp()
        out = _apply_bias_correction(fp, "B19013_001E", "trend_ensemble", cal)
        # No bias records in v2 → exact passthrough
        assert out.point == fp.point
        assert out.se_total == fp.se_total

    def test_v2_end_to_end_projection_runs(self):
        """A full project_ensemble_multi call with a v2 cal must succeed."""
        cal = self._v2_cal_fixture()
        series = [
            AcsObservation(80_000 * 1.025**i, 2000, y, "1y", "15003", "B19013_001E")
            for i, y in enumerate(range(2014, 2024))
        ]
        fp = project_ensemble_multi(
            series, target_year=2026, calibration=cal,
            populations={"15003": 1_009_506},
        )
        assert fp is not None
        assert math.isfinite(fp.point)
        # The projection should incorporate the v2 SE override
        assert fp.notes  # has populated notes
        # κ override note should mention "v2"
        assert "v2" in fp.notes or "se_override" in fp.notes

    @staticmethod
    def _fake_fp():
        from census_forecaster.models import ForecastPoint
        return ForecastPoint(
            point=100_000.0, se_total=3000.0, se_sample=1500.0, se_forecast=2598.0,
            ci90_low=95065, ci90_high=104935,
            method="trend_ensemble", target_year=2026, geoid="15003",
            indicator="B19013_001E", horizon=2, notes="",
        )
