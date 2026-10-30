"""Smoke tests for pums_estimator dataclasses."""
from __future__ import annotations

import pytest
from pums_estimator.models import CountyControls, PumsRecord, SyntheticEstimatePoint


class TestPumsRecord:
    def test_construct_minimal(self):
        r = PumsRecord(serial="HI001", puma="1500100", weight=87.5)
        assert r.serial == "HI001"
        assert r.puma == "1500100"
        assert r.weight == 87.5
        assert r.variables == {}

    def test_construct_with_variables(self):
        r = PumsRecord(serial="HI002", puma="1500100", weight=42.0,
                       variables={"VEH": 2.0, "HINCP": 75000.0})
        assert r.variables["VEH"] == 2.0

    def test_frozen(self):
        r = PumsRecord(serial="X", puma="1500100", weight=1.0)
        with pytest.raises((AttributeError, TypeError)):
            r.weight = 999.0  # type: ignore[misc]


class TestCountyControls:
    def test_construct(self):
        cc = CountyControls(geoid="15003", population=1_000_000)
        assert cc.geoid == "15003"
        assert cc.population == 1_000_000
        assert cc.margins == {}

    def test_with_margins(self):
        cc = CountyControls(
            geoid="15003", population=1_000_000,
            margins={"VEH": {"0": 4500, "1": 12000, "2+": 8000}},
        )
        assert cc.margins["VEH"]["1"] == 12000


class TestSyntheticEstimatePoint:
    def test_construct(self):
        ep = SyntheticEstimatePoint(
            geoid="15009", variable="VEH", category="1",
            estimate=22000.0, se=1200.0, n_pums_records=450,
            method="ipf_raking",
        )
        assert ep.geoid == "15009"
        assert ep.estimate == 22000.0
        assert ep.notes == ""

    def test_with_notes(self):
        ep = SyntheticEstimatePoint(
            geoid="15009", variable="VEH", category="0",
            estimate=3000.0, se=400.0, n_pums_records=80,
            method="ipf_raking", notes="n_eff=65.0",
        )
        assert "n_eff" in ep.notes
