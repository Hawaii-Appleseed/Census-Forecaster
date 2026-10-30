"""Tests for the calibration panel build/load round-trip and selection logic.

These tests don't hit the live Census API. The selection logic is tested
against a synthetic population dict; the persistence tests use a tempdir
to verify the JSON round-trip.
"""
from __future__ import annotations

import pytest
from census_forecaster.models import AcsObservation
from census_forecaster.scripts.build_calibration_panel import (
    DEFAULT_SAMPLE_TARGETS,
    HAWAII_COUNTIES,
    INDICATORS,
    PANEL_YEARS,
    compute_median_cv,
    select_counties,
    write_panel_files,
)
from census_forecaster.scripts.load_calibration_panel import (
    PanelMissingError,
    load_panel,
    load_populations,
    panel_dir_exists,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def synthetic_populations():
    """A toy 200-county population distribution covering all four buckets."""
    pops: dict[str, int] = {}
    # 5 xlarge (>1M)
    for i, p in enumerate([10_000_000, 5_000_000, 3_000_000, 2_000_000, 1_500_000]):
        pops[f"06{i:03d}"] = p
    # 50 large (200K-1M)
    for i, p in enumerate(range(900_000, 200_000, -14_000)):
        pops[f"36{i:03d}"] = p
    # 60 medium (50K-200K)
    for i, p in enumerate(range(190_000, 50_000, -2_400)):
        pops[f"48{i:03d}"] = p
    # 80 small (<50K)
    for i, p in enumerate(range(49_000, 1_000, -600)):
        pops[f"12{i:03d}"] = p
    # Hawaii counties at realistic 2020 populations
    pops["15003"] = 1_009_506  # Honolulu — large
    pops["15001"] = 200_629    # Hawaii County — large
    pops["15007"] = 168_307    # Maui — medium
    pops["15009"] = 73_135     # Kauai — medium
    return pops


@pytest.fixture
def synthetic_observations():
    """A handful of ACS observations for round-trip testing."""
    obs = []
    for year in [2018, 2019, 2021, 2022, 2023]:
        for ind in INDICATORS:
            for geoid in ["15003", "06001", "36001"]:
                obs.append(AcsObservation(
                    estimate=50000.0 + hash((year, ind, geoid)) % 50000,
                    moe=1500.0,
                    year=year,
                    vintage="1y",
                    geoid=geoid,
                    indicator=ind,
                ))
    return obs


# -----------------------------------------------------------------------------
# Selection logic
# -----------------------------------------------------------------------------

class TestSelectCounties:
    def test_must_include_always_picked(self, synthetic_populations):
        selected = select_counties(
            synthetic_populations,
            targets={"xlarge": 5, "medium": 5, "small": 5},
            must_include=HAWAII_COUNTIES,
            seed=42,
        )
        # Hawaii counties must appear regardless of target
        all_picked = set()
        for gs in selected.values():
            all_picked.update(gs)
        for hi in HAWAII_COUNTIES:
            assert hi in all_picked, f"{hi} missing from selection"

    def test_xlarge_picks_top_n_by_population(self, synthetic_populations):
        selected = select_counties(
            synthetic_populations,
            targets={"xlarge": 3},
            must_include=(),
            seed=42,
        )
        # Should be the 3 highest pops
        xlarge = selected["xlarge"]
        sorted_pops = sorted(
            synthetic_populations.items(), key=lambda kv: kv[1], reverse=True
        )
        expected_top3 = {g for g, _ in sorted_pops[:3]}
        assert set(xlarge) == expected_top3

    def test_seed_determinism(self, synthetic_populations):
        a = select_counties(synthetic_populations, seed=42)
        b = select_counties(synthetic_populations, seed=42)
        assert a == b

    def test_different_seeds_different_picks(self, synthetic_populations):
        a = select_counties(synthetic_populations, targets={"medium": 10}, seed=1)
        b = select_counties(synthetic_populations, targets={"medium": 10}, seed=2)
        # Highly likely (60 candidates choose 10) — sets should differ
        assert set(a["medium"]) != set(b["medium"])

    def test_target_count_respected(self, synthetic_populations):
        targets = {"xlarge": 4, "medium": 8, "small": 12}
        selected = select_counties(
            synthetic_populations, targets=targets,
            must_include=(), seed=42,
        )
        for bucket, target in targets.items():
            assert len(selected[bucket]) == target


# -----------------------------------------------------------------------------
# Persistence round-trip
# -----------------------------------------------------------------------------

class TestPanelRoundTrip:
    def test_write_then_load(self, tmp_path, synthetic_populations, synthetic_observations):
        selected = {"xlarge": ["06001"], "medium": ["48001"], "small": ["12001"]}
        cv = compute_median_cv(synthetic_observations)
        write_panel_files(
            panel_dir=tmp_path,
            populations=synthetic_populations,
            selected=selected,
            observations=synthetic_observations,
            median_cv=cv,
            seed=42,
            targets={"xlarge": 1, "medium": 1, "small": 1},
        )
        # All three files should exist
        assert (tmp_path / "acs_panel.json").exists()
        assert (tmp_path / "county_population_2020.json").exists()
        assert (tmp_path / "manifest.json").exists()
        assert panel_dir_exists(tmp_path)

        # Load round-trip
        series_by_key, populations, manifest = load_panel(tmp_path)
        # Series keys are (geoid, indicator); should cover the unique pairs
        # in the synthetic observations.
        unique_pairs = {(o.geoid, o.indicator) for o in synthetic_observations}
        assert set(series_by_key.keys()) == unique_pairs

        # Each series is sorted ascending by year
        for key, obs_list in series_by_key.items():
            years = [o.year for o in obs_list]
            assert years == sorted(years)

        # Populations cover only selected geoids
        selected_geoids = {"06001", "48001", "12001"}
        assert set(populations.keys()) == selected_geoids

        # Manifest sanity
        assert manifest["selection_seed"] == 42
        assert manifest["totals"]["counties"] == 3
        assert manifest["version"] == 1

    def test_panel_missing_error(self, tmp_path):
        # Empty directory → PanelMissingError
        assert not panel_dir_exists(tmp_path)
        with pytest.raises(PanelMissingError):
            load_panel(tmp_path)

    def test_load_populations_only(self, tmp_path, synthetic_populations, synthetic_observations):
        write_panel_files(
            panel_dir=tmp_path,
            populations=synthetic_populations,
            selected={"xlarge": ["06001"]},
            observations=[],
            median_cv={},
            seed=42,
            targets={"xlarge": 1},
        )
        pops = load_populations(tmp_path)
        assert pops == {"06001": synthetic_populations["06001"]}


# -----------------------------------------------------------------------------
# Median CV computation
# -----------------------------------------------------------------------------

class TestMedianCv:
    def test_handles_empty_input(self):
        assert compute_median_cv([]) == {}

    def test_skips_invalid(self):
        obs = [
            # Valid
            AcsObservation(50000, 1500, 2020, "1y", "06001", "B19013_001E"),
            # Invalid: zero estimate
            AcsObservation(0, 1500, 2021, "1y", "06001", "B19013_001E"),
            # Invalid: negative MOE (Census suppression)
            AcsObservation(50000, -555555555, 2022, "1y", "06001", "B19013_001E"),
        ]
        cv = compute_median_cv(obs)
        # Only the one valid obs counts
        assert "06001" in cv
        assert cv["06001"]["B19013_001E"] == pytest.approx(
            (1500 / 1.645) / 50000, rel=1e-6
        )

    def test_median_across_years(self):
        # Two observations for the same (geoid, indicator) — result should
        # be the median of the two CVs.
        obs = [
            AcsObservation(40000, 1000, 2020, "1y", "06001", "B19013_001E"),
            AcsObservation(60000, 3000, 2021, "1y", "06001", "B19013_001E"),
        ]
        cv = compute_median_cv(obs)
        cv1 = (1000 / 1.645) / 40000
        cv2 = (3000 / 1.645) / 60000
        # Median of two values = mean
        expected = (cv1 + cv2) / 2
        assert cv["06001"]["B19013_001E"] == pytest.approx(expected, rel=1e-6)


# -----------------------------------------------------------------------------
# Schema sanity
# -----------------------------------------------------------------------------

class TestPanelSchema:
    def test_panel_year_range(self):
        # Sanity check: the year range shouldn't span 2020 cancellation
        # silently — the script should still include 2020 in PANEL_YEARS,
        # downstream filters drop it.
        assert 2020 in PANEL_YEARS
        assert min(PANEL_YEARS) == 2010
        assert max(PANEL_YEARS) == 2024

    def test_indicators_include_v2_set(self):
        # The original v2 dollar-denominated indicators must remain in
        # INDICATORS so the marginalised v3-→v2 fallback tables stay
        # coherent for legacy consumers.
        v2_set = {"B19013_001E", "B25058_001E", "B25064_001E", "B25077_001E"}
        assert v2_set.issubset(set(INDICATORS))

    def test_indicators_include_v3_additions(self):
        # v0.3 additions: educational attainment, poverty, rent burden.
        v3_additions = {
            "S1501_C02_014E",  # HS+
            "S1501_C02_015E",  # BA+
            "S1701_C03_001E",  # poverty
            "B25071_001E",     # median rent as % of HH income
        }
        assert v3_additions.issubset(set(INDICATORS))

    def test_default_targets_total_150(self):
        # The plan calls for ~150 counties. Confirm the defaults still match
        # before any v1.5 expansion.
        assert sum(DEFAULT_SAMPLE_TARGETS.values()) == 150
