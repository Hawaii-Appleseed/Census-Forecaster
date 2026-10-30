"""Tests for load_crosswalk and pumas_for_county."""
from __future__ import annotations

import pytest
from pums_estimator.pums.crosswalk import load_crosswalk, pumas_for_county


class TestLoadCrosswalk:
    def test_load_crosswalk_reads_csv(self, tmp_crosswalk_csv, fixture_crosswalk):
        cw = load_crosswalk(vintage_year=2022, crosswalk_dir=tmp_crosswalk_csv)
        assert set(cw.keys()) == set(fixture_crosswalk.keys())

    def test_load_crosswalk_allocation_factors(self, tmp_crosswalk_csv):
        cw = load_crosswalk(vintage_year=2022, crosswalk_dir=tmp_crosswalk_csv)
        entries = {geoid: afact for geoid, afact in cw["1500200"]}
        assert entries["15003"] == pytest.approx(0.6)
        assert entries["15009"] == pytest.approx(0.4)

    def test_load_crosswalk_file_not_found_raises_with_message(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="puma_county_2020.csv"):
            load_crosswalk(vintage_year=2022, crosswalk_dir=tmp_path)

    def test_load_crosswalk_uses_2020_suffix_for_vintage_2022(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="puma_county_2020"):
            load_crosswalk(vintage_year=2022, crosswalk_dir=tmp_path)

    def test_load_crosswalk_uses_2020_suffix_for_vintage_2023(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="puma_county_2020"):
            load_crosswalk(vintage_year=2023, crosswalk_dir=tmp_path)

    def test_load_crosswalk_uses_2010_suffix_for_old_vintage(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="puma_county_2010"):
            load_crosswalk(vintage_year=2019, crosswalk_dir=tmp_path)


class TestPumasForCounty:
    def test_pumas_for_county_found(self, fixture_crosswalk):
        result = pumas_for_county("15003", fixture_crosswalk)
        pumas = {p for p, _ in result}
        assert "1500100" in pumas
        assert "1500200" in pumas

    def test_pumas_for_county_unknown_returns_empty(self, fixture_crosswalk):
        assert pumas_for_county("99999", fixture_crosswalk) == []

    def test_pumas_for_county_allocation_factor(self, fixture_crosswalk):
        result = pumas_for_county("15009", fixture_crosswalk)
        assert len(result) == 1
        puma, afact = result[0]
        assert puma == "1500200"
        assert afact == pytest.approx(0.4)
