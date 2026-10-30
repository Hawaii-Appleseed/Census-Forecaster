"""Integration tests for estimate_county_distribution."""
from __future__ import annotations

import pytest
from pums_estimator.estimation.synthetic import estimate_county_distribution
from pums_estimator.models import CountyControls, SyntheticEstimatePoint


def _controls(geoid: str = "15003", ceiling: bool = False) -> CountyControls:
    if ceiling:
        margins = {"VEH": {"0": 400.0, "1": 800.0, "2+": 800.0}}
    else:
        margins = {"VEH": {"0": 400.0, "1": 800.0, "2": 500.0, "3": 300.0}}
    return CountyControls(geoid=geoid, population=10000, margins=margins)


class TestEstimateCountyDistribution:
    def test_estimate_returns_list_of_points(
        self, fixture_pums_records, fixture_crosswalk
    ):
        results = estimate_county_distribution(
            fixture_pums_records, _controls(), "VEH", fixture_crosswalk
        )
        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(r, SyntheticEstimatePoint) for r in results)

    def test_estimate_sum_matches_controls(
        self, fixture_pums_records, fixture_crosswalk
    ):
        controls = _controls()
        results = estimate_county_distribution(
            fixture_pums_records, controls, "VEH", fixture_crosswalk
        )
        total_estimate = sum(r.estimate for r in results)
        total_target = sum(controls.margins["VEH"].values())
        assert total_estimate == pytest.approx(total_target, rel=1e-2)

    def test_estimate_proportion_mode_sums_to_one(
        self, fixture_pums_records, fixture_crosswalk
    ):
        results = estimate_county_distribution(
            fixture_pums_records, _controls(), "VEH", fixture_crosswalk,
            output_mode="proportion",
        )
        assert sum(r.estimate for r in results) == pytest.approx(1.0, abs=1e-6)

    def test_estimate_empty_when_no_puma_overlap(
        self, fixture_pums_records, fixture_crosswalk
    ):
        # County 15007 (Kalawao) is not in the fixture crosswalk.
        controls = CountyControls(
            geoid="15007", population=10000,
            margins={"VEH": {"0": 100.0, "1": 900.0}},
        )
        results = estimate_county_distribution(
            fixture_pums_records, controls, "VEH", fixture_crosswalk
        )
        assert results == []

    def test_estimate_notes_contain_n_eff(
        self, fixture_pums_records, fixture_crosswalk
    ):
        results = estimate_county_distribution(
            fixture_pums_records, _controls(), "VEH", fixture_crosswalk
        )
        assert all("n_eff" in r.notes for r in results)

    def test_estimate_categories_match_margin_categories(
        self, fixture_pums_records, fixture_crosswalk
    ):
        """Regression test: output must use '2+' when margins specify it.

        Before the synthetic.py step-4 fix, str(int(val)) produced raw integer
        strings ("2", "3") rather than the margin key "2+".
        """
        results = estimate_county_distribution(
            fixture_pums_records, _controls(ceiling=True), "VEH", fixture_crosswalk
        )
        output_cats = {r.category for r in results}
        assert "2+" in output_cats
        assert "2" not in output_cats
        assert "3" not in output_cats
