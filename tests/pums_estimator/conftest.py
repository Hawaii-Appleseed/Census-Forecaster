"""Shared pytest fixtures for pums_estimator tests.

All fixtures produce in-memory data only — no network access required.
"""
from __future__ import annotations

import csv

import pytest
from pums_estimator.models import PumsRecord

# ---------------------------------------------------------------------------
# Crosswalk fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_crosswalk() -> dict[str, list[tuple[str, float]]]:
    """In-memory PUMA→county crosswalk with two Hawaii PUMAs.

    PUMA 1500100 belongs entirely to Honolulu County (15003), AFACT=1.0.
    PUMA 1500200 is split between Honolulu (15003) 60% and Maui (15009) 40%.
    """
    return {
        "1500100": [("15003", 1.0)],
        "1500200": [("15003", 0.6), ("15009", 0.4)],
    }


@pytest.fixture
def tmp_crosswalk_csv(tmp_path, fixture_crosswalk):
    """Write fixture_crosswalk to a real CSV file; return the directory path."""
    crosswalk_dir = tmp_path / "crosswalks"
    crosswalk_dir.mkdir()
    csv_path = crosswalk_dir / "puma_county_2020.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["PUMA7CE", "GEOID", "AFACT"])
        writer.writeheader()
        for puma, entries in fixture_crosswalk.items():
            for geoid, afact in entries:
                writer.writerow({"PUMA7CE": puma, "GEOID": geoid, "AFACT": afact})
    return crosswalk_dir


# ---------------------------------------------------------------------------
# PUMS record fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_pums_records() -> list[PumsRecord]:
    """20 synthetic PumsRecord objects with VEH variable.

    Records are split across two PUMAs: 12 in 1500100, 8 in 1500200.
    VEH distribution: 4×0.0, 8×1.0, 5×2.0, 3×3.0.
    All weights are 100.0 for predictable arithmetic.
    """
    veh_values = [0.0] * 4 + [1.0] * 8 + [2.0] * 5 + [3.0] * 3
    pumas = ["1500100"] * 12 + ["1500200"] * 8
    return [
        PumsRecord(
            serial=f"HI{i:04d}",
            puma=puma,
            weight=100.0,
            variables={"VEH": veh},
        )
        for i, (puma, veh) in enumerate(zip(pumas, veh_values))
    ]
