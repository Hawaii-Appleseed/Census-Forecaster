"""Tests for the BEA client and the three bundled BEA anchor sources.

The BeaClient tests use a synthetic JSON cache (no live HTTP). The
anchor source tests verify that the bundled JSONs load through the
existing `AnchorSource.from_file()` machinery and produce sensible
smoothed annual rates.
"""
from __future__ import annotations

import json

import pytest
from census_forecaster.acs.sources import available_sources, load_source
from census_forecaster.bea.client import BeaApiError, BeaClient

# -----------------------------------------------------------------------------
# BeaClient — cache + offline
# -----------------------------------------------------------------------------

class TestBeaClient:
    def test_offline_with_empty_cache_raises(self, tmp_path):
        client = BeaClient(api_key="dummy",
                            cache_path=tmp_path / "missing.json",
                            offline=True)
        with pytest.raises(RuntimeError, match="offline"):
            client.fetch_regional("SAINC1", line_code=3,
                                   geo_fips="15000", year="2024")

    def test_uses_cache_when_present(self, tmp_path):
        cache_path = tmp_path / "bea.json"
        # Pre-populate the cache exactly as the client would have written it
        synthetic_key = "Regional|SAINC1|3|15000|ALL"
        synthetic_value = [
            {"year": 2020, "value": 57030},
            {"year": 2021, "value": 61269},
            {"year": 2024, "value": 71573},
        ]
        cache_path.write_text(json.dumps({synthetic_key: synthetic_value}))

        client = BeaClient(api_key="dummy", cache_path=cache_path, offline=True)
        rows = client.fetch_regional(
            "SAINC1", line_code=3, geo_fips="15000", year="ALL",
        )
        assert len(rows) == 3
        assert rows[0]["year"] == 2020
        assert rows[-1]["value"] == 71573

    def test_no_api_key_fails(self, tmp_path, monkeypatch):
        # Ensure env var is unset so the client really has no key.
        monkeypatch.delenv("BEA_API_KEY", raising=False)
        client = BeaClient(api_key=None, cache_path=tmp_path / "bea.json")
        with pytest.raises(BeaApiError, match="API key"):
            client.fetch_regional("SAINC1", line_code=3,
                                   geo_fips="15000", year="ALL")


# -----------------------------------------------------------------------------
# Bundled BEA anchor JSONs load through AnchorSource
# -----------------------------------------------------------------------------

class TestBeaAnchorSources:
    @pytest.mark.parametrize("name", [
        "bea_hi_percapita_income",
        "bea_honolulu_rpp_all",
        "bea_hi_rpp_housing",
    ])
    def test_anchor_loads(self, name):
        src = load_source(name)
        assert src.name == name
        assert src.publication_lag_years >= 0
        # All three are dollar/index series — values must be positive
        series = src.load_series(end_year=2024)
        assert len(series) > 0
        assert all(value > 0 for _yr, value in series)

    @pytest.mark.parametrize("name", [
        "bea_hi_percapita_income",
        "bea_honolulu_rpp_all",
        "bea_hi_rpp_housing",
    ])
    def test_smoothed_rate_is_finite(self, name):
        src = load_source(name)
        rate = src.smoothed_annual_rate(end_year=2024)
        assert rate is not None
        import math
        assert math.isfinite(rate.log_rate)
        assert math.isfinite(rate.se_log_rate)
        assert rate.se_log_rate > 0  # SE must be positive

    def test_income_indicator_includes_bea_sources(self):
        names = [s.name for s in available_sources("B19013_001E")]
        # Should now include both BEA income-affinity sources
        assert "bea_hi_percapita_income" in names
        assert "bea_honolulu_rpp_all" in names

    def test_rent_indicators_include_bea_housing(self):
        for indicator in ["B25058_001E", "B25064_001E"]:
            names = [s.name for s in available_sources(indicator)]
            assert "bea_hi_rpp_housing" in names

    def test_poverty_indicator_includes_bea_income(self):
        names = [s.name for s in available_sources("S1701_C03_001E")]
        # Per-capita income is the BEA anchor for poverty (income trends
        # correlate with poverty rates)
        assert "bea_hi_percapita_income" in names

    def test_publication_lag_is_one_year(self):
        # All BEA Regional series have a 1-year publication lag
        for name in [
            "bea_hi_percapita_income",
            "bea_honolulu_rpp_all",
            "bea_hi_rpp_housing",
        ]:
            src = load_source(name)
            assert src.publication_lag_years == 1, (
                f"{name} should have publication_lag_years=1, "
                f"got {src.publication_lag_years}"
            )


# -----------------------------------------------------------------------------
# Refresh script: schema validity (no live HTTP)
# -----------------------------------------------------------------------------

class TestBeaRefreshScript:
    def test_anchor_specs_are_well_formed(self):
        from census_forecaster.scripts.refresh_bea_anchors import BEA_ANCHOR_SPECS
        for spec in BEA_ANCHOR_SPECS:
            assert "filename" in spec
            assert spec["filename"].endswith(".json")
            assert "table" in spec and spec["table"]
            assert "line_code" in spec
            assert "geo_fips" in spec
            assert "title" in spec
            assert "units" in spec
            assert isinstance(spec["limitations"], list)
            assert len(spec["limitations"]) >= 1

    def test_three_anchors_specified(self):
        # Sanity check: the v3 plan calls for exactly 3 BEA sources
        from census_forecaster.scripts.refresh_bea_anchors import BEA_ANCHOR_SPECS
        assert len(BEA_ANCHOR_SPECS) == 3

    def test_anchor_filenames_match_registry(self):
        # Each spec's filename must be registered in `_REGISTRY_SPEC`
        from census_forecaster.acs.sources.base import _REGISTRY_SPEC
        from census_forecaster.scripts.refresh_bea_anchors import BEA_ANCHOR_SPECS
        registry_filenames = {entry[0] for entry in _REGISTRY_SPEC}
        for spec in BEA_ANCHOR_SPECS:
            assert spec["filename"] in registry_filenames, (
                f"{spec['filename']} not registered in _REGISTRY_SPEC"
            )
