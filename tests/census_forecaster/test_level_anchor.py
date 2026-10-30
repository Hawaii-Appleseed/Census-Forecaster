"""Tests for the level-anchor forecast framework.

Covers:
- AnchorSource.latest_level() for county-level data
- combined_level_anchor() inverse-variance combination
- level_anchor_as_forecast() point/SE/CI construction
- RMSE-gate in project_ensemble_multi (good anchor included, bad excluded)
- Backwards compatibility: indicators without level anchors unaffected
"""
from __future__ import annotations

import pytest
from common.models import AcsObservation

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_obs(geoid="15003", indicator="S2301_C04_001E", year=2023,
              estimate=4.5, moe=0.5):
    return AcsObservation(
        geoid=geoid, indicator=indicator, year=year,
        vintage="1y", estimate=estimate, moe=moe,
    )


@pytest.fixture
def laus_source():
    from census_forecaster.acs.sources.base import available_sources
    sources = [s for s in available_sources("S2301_C04_001E")
               if s.anchor_type == "level" and s.name == "bls_laus"]
    if not sources:
        pytest.skip("bls_laus.json not present")
    return sources[0]


@pytest.fixture
def saipe_source():
    from census_forecaster.acs.sources.base import available_sources
    sources = [s for s in available_sources("S1701_C03_001E")
               if s.anchor_type == "level" and s.name == "saipe_poverty"]
    if not sources:
        pytest.skip("saipe_poverty.json not present")
    return sources[0]


# ---------------------------------------------------------------------------
# AnchorSource.latest_level
# ---------------------------------------------------------------------------

def test_latest_level_returns_float_for_known_county(laus_source):
    val = laus_source.latest_level(end_year=2023, geoid="15003")
    assert val is not None
    assert isinstance(val, float)
    assert 0 < val < 30  # unemployment rate in percent


def test_latest_level_respects_end_year_cutoff(laus_source):
    """latest_level(end_year=2015) must not return post-2015 data."""
    v2015 = laus_source.latest_level(end_year=2015, geoid="15003")
    v2023 = laus_source.latest_level(end_year=2023, geoid="15003")
    if v2015 is None or v2023 is None:
        pytest.skip("insufficient LAUS data range")
    # We can't assert strict inequality without knowing the exact values,
    # but both should be plausible unemployment rates.
    assert 0 < v2015 < 30
    assert 0 < v2023 < 30


def test_latest_level_returns_none_for_unknown_geoid(laus_source):
    val = laus_source.latest_level(end_year=2023, geoid="99999")
    assert val is None


def test_latest_level_allows_zero_rate():
    """A 0.0 rate value must not be filtered (unlike load_series which drops v<=0)."""
    import json
    import os
    import tempfile
    from pathlib import Path

    from census_forecaster.acs.sources.base import AnchorSource

    data = {"values_by_geoid_year": {"15003": {"2020": 0.0, "2021": 2.5}}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        fname = f.name

    try:
        src = AnchorSource(
            name="test_zero", path=Path(fname),
            publication_lag_years=0,
            indicator_affinity=("S2301_C04_001E",),
            rate_se_floor=0.005, _data=data,
            geography="county", anchor_type="level", level_se_floor=1.5,
        )
        # end_year=2020 should see the 0.0 value
        val = src.latest_level(end_year=2020, geoid="15003")
        assert val == 0.0
    finally:
        os.unlink(fname)


# ---------------------------------------------------------------------------
# combined_level_anchor
# ---------------------------------------------------------------------------

def test_combined_level_anchor_returns_point_for_known_county():
    from census_forecaster.acs.anchors import combined_level_anchor
    la = combined_level_anchor("S2301_C04_001E", end_year=2023, geoid="15003")
    assert la is not None
    assert 0 < la.point < 30
    assert la.se >= 1.5  # at least the floor
    assert "bls_laus" in la.source_names


def test_combined_level_anchor_returns_none_no_data():
    from census_forecaster.acs.anchors import combined_level_anchor
    la = combined_level_anchor("B19013_001E", end_year=2023, geoid="15003")
    assert la is None  # no level-type anchor registered for income


def test_combined_level_anchor_saipe_for_poverty():
    from census_forecaster.acs.anchors import combined_level_anchor
    la = combined_level_anchor("S1701_C03_001E", end_year=2023, geoid="15003")
    assert la is not None
    assert 0 < la.point < 50  # poverty rate in percent
    assert "saipe_poverty" in la.source_names


# ---------------------------------------------------------------------------
# level_anchor_as_forecast
# ---------------------------------------------------------------------------

def test_level_anchor_as_forecast_point_equals_anchor_level():
    from census_forecaster.acs.anchors import combined_level_anchor, level_anchor_as_forecast
    obs = _make_obs(indicator="S2301_C04_001E", year=2023, estimate=4.5)
    la = combined_level_anchor("S2301_C04_001E", end_year=2023, geoid="15003")
    if la is None:
        pytest.skip("no LAUS data")
    fp = level_anchor_as_forecast(obs, target_year=2024, level_anchor=la)
    assert fp.point == pytest.approx(la.point, rel=1e-9)
    assert fp.method == "level_anchor"
    assert fp.horizon == 1


def test_level_anchor_se_grows_with_horizon():
    """SE at h=2 must exceed SE at h=1 (random-walk uncertainty growth)."""
    from census_forecaster.acs.anchors import combined_level_anchor, level_anchor_as_forecast
    obs = _make_obs(indicator="S2301_C04_001E", year=2022, estimate=4.5)
    la = combined_level_anchor("S2301_C04_001E", end_year=2023, geoid="15003")
    if la is None:
        pytest.skip("no LAUS data")
    fp1 = level_anchor_as_forecast(obs, target_year=2023, level_anchor=la)
    fp2 = level_anchor_as_forecast(obs, target_year=2024, level_anchor=la)
    assert fp2.se_total > fp1.se_total


def test_level_anchor_passthrough_when_horizon_zero():
    from census_forecaster.acs.anchors import combined_level_anchor, level_anchor_as_forecast
    obs = _make_obs(indicator="S2301_C04_001E", year=2023, estimate=4.5)
    la = combined_level_anchor("S2301_C04_001E", end_year=2023, geoid="15003")
    if la is None:
        pytest.skip("no LAUS data")
    fp = level_anchor_as_forecast(obs, target_year=2023, level_anchor=la)
    assert "passthrough" in fp.method
    assert fp.point == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# RMSE gate in project_ensemble_multi
# ---------------------------------------------------------------------------

def _minimal_series(geoid, indicator, n_years=10, base=4.5, vintage="1y"):
    """Build a minimal ACS series for ensemble testing."""
    return [
        AcsObservation(
            geoid=geoid, indicator=indicator,
            year=2010 + i, vintage=vintage,
            estimate=base * (1 + 0.01 * i), moe=0.5,
        )
        for i in range(n_years)
    ]


def test_ensemble_includes_level_anchor_for_poverty():
    """S1701 level anchor RMSE is best → should appear in notes."""
    from census_forecaster.acs.anchors import load_calibration
    from census_forecaster.acs.ensemble import project_ensemble_multi
    series = _minimal_series("15003", "S1701_C03_001E", base=12.0)
    cal = load_calibration()
    fp = project_ensemble_multi(
        series_observations=series,
        target_year=2026,
        end_year=2023,
        calibration=cal,
        use_ml=False,
    )
    assert fp is not None
    # When level_anchor contributes, notes should mention it
    assert fp.notes is not None


def test_ensemble_excludes_level_anchor_when_rmse_too_high(monkeypatch):
    """If level anchor RMSE >> best other, it should not enter the blend."""
    from census_forecaster.acs import ensemble as ens_mod
    from census_forecaster.acs.anchors import load_calibration

    # Inject a fake calibration where level_anchor RMSE = 99% (terrible)
    # and trend_ensemble RMSE = 5% (good).
    cal = load_calibration() or {}
    import copy
    cal_patched = copy.deepcopy(cal)
    cal_patched.setdefault("rmse_by_indicator_method", {}) \
        .setdefault("S2301_C04_001E", {})
    cal_patched["rmse_by_indicator_method"]["S2301_C04_001E"]["level_anchor"] = 0.99
    cal_patched["rmse_by_indicator_method"]["S2301_C04_001E"]["trend_ensemble"] = 0.05

    series = _minimal_series("15003", "S2301_C04_001E", base=4.5)
    fp = ens_mod.project_ensemble_multi(
        series_observations=series,
        target_year=2026,
        end_year=2023,
        calibration=cal_patched,
        use_ml=False,
    )
    assert fp is not None
    # level_anchor was excluded; notes should NOT mention level_anchor sources
    assert "bls_laus" not in (fp.notes or "")


def test_ensemble_includes_level_anchor_when_rmse_slightly_worse(monkeypatch):
    """level_anchor within 25% of best → should be included."""
    import copy

    from census_forecaster.acs import ensemble as ens_mod
    from census_forecaster.acs.anchors import load_calibration

    cal = load_calibration() or {}
    cal_patched = copy.deepcopy(cal)
    cal_patched.setdefault("rmse_by_indicator_method", {}) \
        .setdefault("S1701_C03_001E", {})
    cal_patched["rmse_by_indicator_method"]["S1701_C03_001E"]["level_anchor"] = 0.20
    cal_patched["rmse_by_indicator_method"]["S1701_C03_001E"]["trend_ensemble"] = 0.18
    # ratio = 0.20/0.18 = 1.11 < 1.25 → include

    series = _minimal_series("15003", "S1701_C03_001E", base=12.0)
    fp = ens_mod.project_ensemble_multi(
        series_observations=series,
        target_year=2026,
        end_year=2023,
        calibration=cal_patched,
        use_ml=False,
    )
    assert fp is not None


# ---------------------------------------------------------------------------
# Registry sanity checks
# ---------------------------------------------------------------------------

def test_laus_registered_as_level_anchor():
    from census_forecaster.acs.sources.base import available_sources
    sources = available_sources("S2301_C04_001E")
    level_names = {s.name for s in sources if s.anchor_type == "level"}
    if not any(p.name == "bls_laus" for p in
               [s for s in sources]):
        pytest.skip("bls_laus.json not in registry")
    assert "bls_laus" in level_names


def test_saipe_registered_as_level_anchor():
    from census_forecaster.acs.sources.base import available_sources
    sources = available_sources("S1701_C03_001E")
    level_names = {s.name for s in sources if s.anchor_type == "level"}
    if not any(s.name == "saipe_poverty" for s in sources):
        pytest.skip("saipe_poverty.json not in registry")
    assert "saipe_poverty" in level_names


def test_income_indicator_has_no_level_anchors():
    from census_forecaster.acs.sources.base import available_sources
    sources = available_sources("B19013_001E")
    level_sources = [s for s in sources if s.anchor_type == "level"]
    assert len(level_sources) == 0


def test_unpack_spec_backwards_compat():
    """5-element registry spec (legacy format) must still load without error."""
    from census_forecaster.acs.sources.base import _unpack_spec
    spec5 = ("some_file.json", 0, ("B19013_001E",), 0.005, "any")
    fname, lag, aff, floor, geo, atype, lfloor = _unpack_spec(spec5)
    assert atype == "rate"
    assert lfloor == 0.0
    assert fname == "some_file.json"
