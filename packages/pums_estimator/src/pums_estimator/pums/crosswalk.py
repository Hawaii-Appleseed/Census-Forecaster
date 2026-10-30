"""PUMA-to-county crosswalk.

Source
------
Census TIGER/Line PUMA-to-county relationship files.
https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html

The crosswalk maps each PUMA to one or more counties with an *allocation
factor* — the fraction of the PUMA's population that falls in that county.
PUMA boundaries were revised for the 2020 Census (effective with 2022 ACS).

Expected CSV files (download separately and place under data/crosswalks/):
  data/crosswalks/puma_county_2020.csv  — for 2022+ ACS PUMS
  data/crosswalks/puma_county_2010.csv  — for 2012-2021 ACS PUMS

CSV column names expected (Census relationship file format):
  PUMA7CE   — 7-char: 2-digit state FIPS + 5-digit PUMA (e.g. "1500100")
  GEOID     — 5-digit county FIPS (e.g. "15003")
  AFACT     — allocation factor (0.0–1.0)

If the crosswalk file is not present, raise FileNotFoundError with a
clear remediation message.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Resolve data/crosswalks/ relative to the repo root.
# File depth from repo root:  packages/pums_estimator/src/pums_estimator/pums/crosswalk.py
# .parent × 6 walks up to the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_CROSSWALK_DIR = _REPO_ROOT / "data" / "crosswalks"


def load_crosswalk(
    vintage_year: int = 2020,
    crosswalk_dir: Path | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Load PUMA-to-county crosswalk from a Census relationship file.

    Parameters
    ----------
    vintage_year : int
        The year of the PUMA vintage to load.  Values ≥ 2022 use the
        2020 PUMA definitions; earlier values use 2010 definitions.
    crosswalk_dir : Path, optional
        Override the default ``data/crosswalks/`` location.

    Returns
    -------
    dict[str, list[tuple[str, float]]]
        Maps 7-character PUMA code (state+PUMA) to a list of
        ``(county_fips_5digit, allocation_factor)`` pairs.
        Allocation factors sum to 1.0 per PUMA.

    Raises
    ------
    FileNotFoundError
        When the crosswalk CSV is not present.  Download the Census
        PUMA-to-county relationship file and place it under
        ``data/crosswalks/``.
    """
    d = crosswalk_dir or _DEFAULT_CROSSWALK_DIR
    suffix = "2020" if vintage_year >= 2022 else "2010"
    path = d / f"puma_county_{suffix}.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Crosswalk file not found: {path}\n"
            "Download the Census PUMA-to-county relationship file and place it at "
            f"{path}.\n"
            "See: https://www.census.gov/geographies/reference-files/time-series/"
            "geo/relationship-files.html"
        )

    result: dict[str, list[tuple[str, float]]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            puma = row["PUMA7CE"]
            county = row["GEOID"]
            try:
                alloc = float(row.get("AFACT", 1.0))
            except (ValueError, TypeError):
                alloc = 1.0
            result.setdefault(puma, []).append((county, alloc))

    return result


def pumas_for_county(
    county_fips: str,
    crosswalk: dict[str, list[tuple[str, float]]],
) -> list[tuple[str, float]]:
    """Return all ``(puma_code, allocation_factor)`` pairs for a county.

    Inverts the PUMA→county mapping from :func:`load_crosswalk`.  Used
    to identify which PUMS records are relevant for a target county and
    what fraction of each PUMA's population belongs to it.

    Parameters
    ----------
    county_fips : str
        5-digit county FIPS code (e.g. "15003" for Honolulu County).
    crosswalk : dict
        Output of :func:`load_crosswalk`.

    Returns
    -------
    list[tuple[str, float]]
        Pairs of ``(puma_7char_code, allocation_factor)``.
        Empty list if no PUMA overlaps the county.
    """
    result: list[tuple[str, float]] = []
    for puma, entries in crosswalk.items():
        for geoid, alloc in entries:
            if geoid == county_fips:
                result.append((puma, alloc))
    return result
