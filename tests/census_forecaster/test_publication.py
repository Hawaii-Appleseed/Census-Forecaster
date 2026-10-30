"""Tests for publication.py release-date helpers and AcsObservation.publication_date."""
from __future__ import annotations

from datetime import date

import pytest
from census_forecaster.models import AcsObservation
from census_forecaster.publication import acs_1y_release_date, acs_5y_release_date

# ---------------------------------------------------------------------------
# publication.py helpers — verify historical dates
# ---------------------------------------------------------------------------

class TestAcs1yReleaseDates:
    @pytest.mark.parametrize("year,expected", [
        (2023, date(2024, 9, 12)),
        (2022, date(2023, 9, 14)),
        (2021, date(2022, 9, 8)),   # 2nd Thursday of Sept 2022
        (2019, date(2020, 9, 10)),  # 2nd Thursday of Sept 2020
        (2018, date(2019, 9, 12)),
    ])
    def test_known_dates(self, year, expected):
        assert acs_1y_release_date(year) == expected

    def test_returns_thursday(self):
        for year in range(2015, 2028):
            d = acs_1y_release_date(year)
            assert d.weekday() == 3, f"Expected Thursday for year {year}, got {d.strftime('%A')} {d}"

    def test_falls_in_september(self):
        for year in range(2010, 2030):
            d = acs_1y_release_date(year)
            assert d.month == 9
            assert d.year == year + 1

    def test_is_second_week_of_sept(self):
        for year in range(2010, 2030):
            d = acs_1y_release_date(year)
            assert 8 <= d.day <= 14


class TestAcs5yReleaseDates:
    @pytest.mark.parametrize("year,expected", [
        (2022, date(2023, 12, 7)),
        (2021, date(2022, 12, 1)),  # 1st Thursday of Dec 2022
        (2020, date(2021, 12, 2)),  # 1st Thursday of Dec 2021
        (2019, date(2020, 12, 3)),
    ])
    def test_known_dates(self, year, expected):
        assert acs_5y_release_date(year) == expected

    def test_returns_thursday(self):
        for year in range(2015, 2028):
            d = acs_5y_release_date(year)
            assert d.weekday() == 3

    def test_falls_in_december(self):
        for year in range(2010, 2030):
            d = acs_5y_release_date(year)
            assert d.month == 12
            assert d.year == year + 1

    def test_1y_precedes_5y_same_data_year(self):
        for year in range(2010, 2025):
            assert acs_1y_release_date(year) < acs_5y_release_date(year)


# ---------------------------------------------------------------------------
# AcsObservation.publication_date property
# ---------------------------------------------------------------------------

class TestObservationPublicationDate:
    def _obs(self, year: int, vintage: str) -> AcsObservation:
        return AcsObservation(
            estimate=100.0, moe=5.0, year=year, vintage=vintage,
            geoid="15003", indicator="B25077_001E",
        )

    def test_1y_delegates_to_acs_1y_release_date(self):
        obs = self._obs(2022, "1y")
        assert obs.publication_date == acs_1y_release_date(2022)

    def test_5y_delegates_to_acs_5y_release_date(self):
        obs = self._obs(2022, "5y")
        assert obs.publication_date == acs_5y_release_date(2022)

    def test_1y_releases_before_5y_same_year(self):
        obs_1y = self._obs(2022, "1y")
        obs_5y = self._obs(2022, "5y")
        assert obs_1y.publication_date < obs_5y.publication_date

    def test_year_2023_1y_released_in_2024(self):
        obs = self._obs(2023, "1y")
        assert obs.publication_date.year == 2024


# ---------------------------------------------------------------------------
# truncate_to_anchor with as_of_date
# ---------------------------------------------------------------------------

class TestTruncateWithAsOfDate:
    def _obs(self, year: int, vintage: str = "1y") -> AcsObservation:
        return AcsObservation(
            estimate=float(year * 1000), moe=10.0, year=year, vintage=vintage,
            geoid="15003", indicator="B19013_001E",
        )

    def test_instant_mode_includes_anchor_year(self):
        from census_forecaster.backtest.acs import truncate_to_anchor
        series = [self._obs(y) for y in range(2015, 2024)]
        result = truncate_to_anchor(series, anchor_year=2020)
        assert max(o.year for o in result) == 2020

    def test_publication_mode_excludes_unpublished_5y(self):
        from census_forecaster.backtest.acs import truncate_to_anchor
        # 5-year ACS end_year=2020 is published Dec 2021; as_of=Sept 2021 excludes it.
        obs_1y_2020 = self._obs(2020, "1y")    # pub: Sept 2021
        obs_5y_2020 = self._obs(2020, "5y")    # pub: Dec 2021
        obs_1y_2019 = self._obs(2019, "1y")    # pub: Sept 2020 ← before as_of
        series = [obs_1y_2019, obs_1y_2020, obs_5y_2020]
        as_of = acs_1y_release_date(2020)       # ~Sept 10, 2021
        result = truncate_to_anchor(series, anchor_year=2020, as_of_date=as_of)
        vintages = {(o.year, o.vintage) for o in result}
        assert (2020, "1y") in vintages         # 1-year 2020 published on as_of ✓
        assert (2019, "1y") in vintages         # 2019 clearly before as_of ✓
        assert (2020, "5y") not in vintages     # 5-year 2020 published after as_of ✗

    def test_no_as_of_date_is_backwards_compat(self):
        from census_forecaster.backtest.acs import truncate_to_anchor
        series = [self._obs(y) for y in range(2015, 2023)]
        # Should behave identically to the pre-Phase-B signature.
        result_old = [o for o in series if o.year <= 2020]
        result_new = truncate_to_anchor(series, anchor_year=2020)
        assert result_old == result_new
