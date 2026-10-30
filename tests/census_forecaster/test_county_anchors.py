"""Tests for county-level macro-anchor support (Phase A geography lift).

Verifies that:
1. AnchorSource(geography="county") reads from `values_by_geoid_year`.
2. Existing state/national/any-geography sources are unaffected — geoid
   is silently ignored, preserving backwards-compat with all v0.2 callers.
3. combined_anchor_rate threads geoid through the source loop, so
   different counties can produce different anchor rates from the same
   source registration.
4. covered_geoids reports the per-county series available for audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from census_forecaster.acs.anchors import combined_anchor_rate
from census_forecaster.acs.sources import AnchorSource, load_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_county_source(
    tmp_path: Path,
    name: str = "test_county.json",
    *,
    affinity: tuple[str, ...] = ("B25077_001E",),
    values_by_geoid_year: dict | None = None,
    values_by_year: dict | None = None,
) -> Path:
    """Materialise a county-geography anchor JSON for testing."""
    payload = {
        "source": "TestSrc",
        "series_id": "TEST_COUNTY",
        "title": "Synthetic county-level test source",
        "frequency": "annual",
        "units": "index",
        "geography": "county",
        "values_by_geoid_year": values_by_geoid_year or {},
    }
    if values_by_year is not None:
        payload["values_by_year"] = values_by_year
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def _county_source(path: Path, **kwargs) -> AnchorSource:
    """Build an AnchorSource directly so we don't depend on registry mutation."""
    return AnchorSource.from_file(
        path=path,
        publication_lag_years=kwargs.get("publication_lag_years", 0),
        indicator_affinity=kwargs.get("indicator_affinity", ("B25077_001E",)),
        rate_se_floor=kwargs.get("rate_se_floor", 0.005),
        geography="county",
    )


# ---------------------------------------------------------------------------
# County-level loading
# ---------------------------------------------------------------------------

class TestCountySourceLoading:
    def test_county_source_returns_per_geoid_series(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={
                "15003": {"2018": 600000, "2019": 615000, "2020": 640000,
                          "2021": 700000, "2022": 760000, "2023": 800000},
                "15001": {"2018": 350000, "2019": 360000, "2020": 380000,
                          "2021": 410000, "2022": 445000, "2023": 470000},
            },
        )
        src = _county_source(path)

        honolulu = dict(src.load_series(geoid="15003"))
        hawaii_county = dict(src.load_series(geoid="15001"))
        assert honolulu[2023] == 800000
        assert hawaii_county[2023] == 470000
        # Different counties produce distinct YoY trajectories.
        assert honolulu != hawaii_county

    def test_missing_geoid_falls_back_to_state_aggregate(self, tmp_path):
        # Hybrid source: per-county for the named counties, state-level
        # fallback in `values_by_year` for unlisted geoids.
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={"15003": {"2022": 100, "2023": 110}},
            values_by_year={"2022": 90, "2023": 95},
        )
        src = _county_source(path)
        unlisted = dict(src.load_series(geoid="48999"))  # nonexistent county
        assert unlisted[2023] == 95  # state fallback

    def test_missing_geoid_with_no_fallback_returns_empty(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={"15003": {"2022": 100, "2023": 110}},
        )
        src = _county_source(path)
        assert src.load_series(geoid="48999") == []

    def test_county_smoothed_rate_is_geoid_specific(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={
                # Honolulu: ~5%/yr growth
                "15003": {"2019": 100, "2020": 105, "2021": 110.25,
                          "2022": 115.76, "2023": 121.55},
                # Hawaii County: ~1%/yr growth
                "15001": {"2019": 100, "2020": 101, "2021": 102.01,
                          "2022": 103.03, "2023": 104.06},
            },
        )
        src = _county_source(path)
        r_hon = src.smoothed_annual_rate(end_year=2023, geoid="15003")
        r_haw = src.smoothed_annual_rate(end_year=2023, geoid="15001")
        assert r_hon is not None and r_haw is not None
        assert r_hon.log_rate > r_haw.log_rate  # 5% > 1% in log space
        # Sanity bounds (log(1.05) ≈ 0.0488; log(1.01) ≈ 0.00995)
        assert 0.04 < r_hon.log_rate < 0.06
        assert 0.005 < r_haw.log_rate < 0.015

    def test_publication_lag_still_applies(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={"15003": {"2020": 100, "2021": 105,
                                            "2022": 110, "2023": 115}},
        )
        # Force a 1-year publication lag.
        src = AnchorSource.from_file(
            path=path,
            publication_lag_years=1,
            indicator_affinity=("B25077_001E",),
            rate_se_floor=0.005,
            geography="county",
        )
        years = [y for y, _ in src.load_series(end_year=2022, geoid="15003")]
        # With lag=1, year 2022 data is not yet visible at end_year=2022.
        assert max(years) <= 2021

    def test_covered_geoids_listing(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            values_by_geoid_year={"15003": {"2023": 1.0}, "15001": {"2023": 1.0}},
        )
        src = _county_source(path)
        assert src.covered_geoids == ("15001", "15003")  # sorted


# ---------------------------------------------------------------------------
# Backwards-compat: state/national sources ignore geoid
# ---------------------------------------------------------------------------

class TestNonCountySourcesIgnoreGeoid:
    def test_existing_source_unchanged_with_geoid(self):
        # CPI Honolulu is geography="any"; geoid kwarg should not change it.
        src = load_source("cpi_honolulu_allitems")
        without = src.load_series(end_year=2024)
        with_geoid = src.load_series(end_year=2024, geoid="15003")
        assert without == with_geoid

    def test_smoothed_rate_unchanged_with_geoid(self):
        src = load_source("qcew_hawaii_wages")
        without = src.smoothed_annual_rate(end_year=2024)
        with_geoid = src.smoothed_annual_rate(end_year=2024, geoid="15003")
        assert without is not None and with_geoid is not None
        assert without.log_rate == pytest.approx(with_geoid.log_rate)
        assert without.se_log_rate == pytest.approx(with_geoid.se_log_rate)

    def test_metadata_includes_geography(self):
        src = load_source("pce_deflator")
        assert src.metadata["geography"] == "national"

    def test_non_county_source_has_no_covered_geoids(self):
        src = load_source("cpi_honolulu_allitems")
        assert src.covered_geoids == ()


# ---------------------------------------------------------------------------
# combined_anchor_rate threads geoid through
# ---------------------------------------------------------------------------

class TestCombinedAnchorRateGeoidPlumbing:
    def test_geoid_param_changes_county_source_contribution(self, tmp_path):
        path = _write_county_source(
            tmp_path,
            affinity=("TEST_INDICATOR",),
            values_by_geoid_year={
                "15003": {"2019": 100, "2020": 105, "2021": 110.25,
                          "2022": 115.76, "2023": 121.55},
                "15001": {"2019": 100, "2020": 101, "2021": 102.01,
                          "2022": 103.03, "2023": 104.06},
            },
        )
        src = _county_source(path, indicator_affinity=("TEST_INDICATOR",))
        r_hon = combined_anchor_rate(
            indicator="TEST_INDICATOR", end_year=2023,
            sources=[src], geoid="15003",
        )
        r_haw = combined_anchor_rate(
            indicator="TEST_INDICATOR", end_year=2023,
            sources=[src], geoid="15001",
        )
        assert r_hon is not None and r_haw is not None
        assert r_hon.point_log_rate > r_haw.point_log_rate

    def test_geoid_none_with_county_source_returns_no_rate(self, tmp_path):
        # No geoid passed → county source has no series to load → combiner
        # gets no rates and returns None (graceful fallback).
        path = _write_county_source(
            tmp_path,
            affinity=("TEST_IND_2",),
            values_by_geoid_year={"15003": {"2022": 100, "2023": 110}},
        )
        src = _county_source(path, indicator_affinity=("TEST_IND_2",))
        rate = combined_anchor_rate(
            indicator="TEST_IND_2", end_year=2023, sources=[src], geoid=None,
        )
        assert rate is None

    def test_existing_indicator_unaffected_by_geoid_kwarg(self):
        # B19013 anchors are state/national → result must be identical
        # whether geoid is passed or omitted.
        without = combined_anchor_rate(indicator="B19013_001E", end_year=2024)
        with_geoid = combined_anchor_rate(
            indicator="B19013_001E", end_year=2024, geoid="15003",
        )
        assert without is not None and with_geoid is not None
        assert without.point_log_rate == pytest.approx(with_geoid.point_log_rate)
        assert without.se_log_rate == pytest.approx(with_geoid.se_log_rate)


# ---------------------------------------------------------------------------
# Market-signals Phase 3: national unemployment rate anchor
# ---------------------------------------------------------------------------

class TestNationalUnemploymentAnchor:
    """The national-unemployment RATE anchor was tried and FAILED the
    Phase-3 ship gate: blended-ensemble RMSE regression on S2301 plus a
    rate-band violation (|log-rate| 0.47 in 2009 vs the 0.30 invariant).
    See the registry comment in sources/base.py and
    backtests/results/market_ml_ablation_2026-07-14.md. These tests pin
    the *removal* so the failed experiment doesn't silently come back."""

    def test_not_registered(self):
        from census_forecaster.acs.sources.base import _REGISTRY_SPEC
        assert not any(
            s[0] == "bls_national_unemployment.json" for s in _REGISTRY_SPEC
        ), "failed anchor experiment re-registered without new evidence"

    def test_never_served_for_s2301(self):
        from census_forecaster.acs.sources.base import available_sources
        assert not any(
            src.name == "bls_national_unemployment"
            for src in available_sources("S2301_C04_001E")
        )
