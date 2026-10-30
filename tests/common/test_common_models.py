"""Verify common.models directly (not via census_forecaster shim)."""
from __future__ import annotations

from common.models import AcsObservation, ForecastPoint, GeographySeries


class TestAcsObservationDirect:
    def test_construct(self):
        obs = AcsObservation(estimate=63488, moe=812, year=2022, vintage="1y",
                             geoid="15003", indicator="B19013_001E")
        assert obs.estimate == 63488
        assert obs.geoid == "15003"

    def test_publication_date_1y(self):
        obs = AcsObservation(estimate=100, moe=10, year=2022, vintage="1y",
                             geoid="15003", indicator="B01001_001E")
        d = obs.publication_date
        assert d.year == 2023

    def test_publication_date_5y(self):
        obs = AcsObservation(estimate=100, moe=10, year=2022, vintage="5y",
                             geoid="15003", indicator="B01001_001E")
        d = obs.publication_date
        assert d.year == 2023


class TestForecastPointDirect:
    def test_construct(self):
        fp = ForecastPoint(
            point=70000.0, se_total=3000.0, se_sample=2000.0, se_forecast=2236.0,
            ci90_low=65065.0, ci90_high=74935.0,
            method="trend_ensemble", target_year=2026,
            geoid="15003", indicator="B19013_001E", horizon=4,
        )
        assert fp.point == 70000.0
        assert fp.method == "trend_ensemble"


class TestGeographySeriesDirect:
    def test_from_observations(self):
        obs = [
            AcsObservation(estimate=60000, moe=800, year=2020, vintage="1y",
                           geoid="15003", indicator="B19013_001E"),
            AcsObservation(estimate=63000, moe=820, year=2021, vintage="1y",
                           geoid="15003", indicator="B19013_001E"),
        ]
        gs = GeographySeries.from_observations("15003", "B19013_001E", obs)
        assert len(gs.observations) == 2
        assert gs.geoid == "15003"
