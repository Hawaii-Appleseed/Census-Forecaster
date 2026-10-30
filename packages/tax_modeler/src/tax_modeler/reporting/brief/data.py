"""Data loading, constants, and formatting utilities for the poverty-impact brief."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Repo root, for locating reports/ and packages/data/raw/ in a checkout.
# This file lives at
#   packages/tax_modeler/src/tax_modeler/reporting/brief/data.py
# so the repo root is 6 levels up (brief → reporting → tax_modeler → src →
# tax_modeler → packages). test_brief_paths.py pins this, so a future move
# fails loudly instead of silently resolving to the wrong directory.
REPO_ROOT = Path(__file__).resolve().parents[6]

# Brand palette (Hawaii Appleseed) — single-sourced in reporting.palette.
from tax_modeler.reporting.palette import (  # noqa: E402, F401 - re-exported: charts.py imports these from here
    CHARCOAL,
    GOLD,
    LIGHT_GRAY,
    LIGHT_TEAL,
    SLATE,
    TEAL,
    WHITE,
)

TIER_PREFERENCE = [
    "poverty_impact_2024_tier4_spm",
    "poverty_impact_2024_tier3",
    "poverty_impact_2024_tier2",
    "poverty_impact_2024_review",
    "poverty_impact_2024_tier1",
    "poverty_impact_2024",
]

DATA_SOURCE_CITATION = (
    "Source: U.S. Census Bureau 5-Year ACS PUMS 2018-2022; Census-Forecaster "
    "tax simulation model, Hawaiʻi Appleseed 2025"
)


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _fmt_int(n: float | int | None) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "n/a"
    return f"{int(round(float(n))):,}"


def _fmt_money_m(n: float | int | None) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "n/a"
    return f"${float(n) / 1e6:,.0f}M"


def _fmt_pct(rate: float | None, digits: int = 1) -> str:
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        return "n/a"
    return f"{float(rate) * 100:.{digits}f}%"


# ---------------------------------------------------------------------------
# BriefData dataclass
# ---------------------------------------------------------------------------

@dataclass
class BriefData:
    tax_year: int
    data_dir: Path
    state: pd.Series
    counties: pd.DataFrame
    house_districts: pd.DataFrame
    senate_districts: pd.DataFrame
    household_types: pd.DataFrame | None = None
    racial_stats: pd.DataFrame | None = None
    rxkids_state: pd.Series | None = None
    rxkids_dir: Path | None = None


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------

def _resolve_data_dir(explicit: Path | None, tax_year: int) -> Path:
    if explicit is not None:
        if not (explicit / "by_state.csv").exists():
            raise FileNotFoundError(
                f"--data-dir does not contain by_state.csv: {explicit}"
            )
        return explicit
    candidates = [REPO_ROOT / "reports" / name for name in TIER_PREFERENCE]
    for cand in candidates:
        if (cand / "by_state.csv").exists():
            return cand
    raise FileNotFoundError(
        "No poverty_impact_* directory with by_state.csv found under reports/. "
        f"Tried: {[c.name for c in candidates]}"
    )


def _resolve_rxkids_dir(explicit: Path | None, tax_year: int) -> Path | None:
    if explicit is not None:
        return explicit if (explicit / "by_state.csv").exists() else None
    cand = REPO_ROOT / "reports" / f"rxkids_impact_{tax_year}"
    return cand if (cand / "by_state.csv").exists() else None


# ---------------------------------------------------------------------------
# Demographic statistics
# ---------------------------------------------------------------------------

def _compute_racial_stats(pums_dir: Path) -> pd.DataFrame | None:
    """Compute poverty rate and median personal income by race from ACS PUMS.

    Prefers ACS 2024 1-year data; falls back to 2018-2022 5-year if absent.
    """
    acs2024_dir = pums_dir / "pums_2024_1yr"
    if (acs2024_dir / "psam_p15.parquet").exists():
        persons_path = acs2024_dir / "psam_p15.parquet"
        vintage = "ACS 2024 1-Year"
    elif (pums_dir / "psam_p15.parquet").exists():
        persons_path = pums_dir / "psam_p15.parquet"
        vintage = "ACS 2018-2022 5-Year"
    else:
        return None

    import numpy as np

    persons = pd.read_parquet(persons_path)
    race_groups = [
        ("White alone", persons["RAC1P"] == 1),
        ("Asian alone", persons["RAC1P"] == 6),
        ("Native Hawaiian\n(alone or in combo)", persons["RACNH"] == 1),
        ("Pacific Islander\n(NHPI alone)", persons["RAC1P"] == 7),
        ("Two or more races", persons["RAC1P"] == 9),
        ("Black alone", persons["RAC1P"] == 2),
    ]
    rows = []
    for label, mask in race_groups:
        sub = persons[mask]
        tot = sub["PWGTP"].sum()
        if tot < 10000:
            continue
        pov = sub[sub["POVPIP"] < 100]["PWGTP"].sum()
        earners = sub[(sub["PERNP"] > 0) & sub["PERNP"].notna()].copy()
        earners["adj"] = earners["PERNP"] * earners["ADJINC"] / 1e6
        earners = earners.sort_values("adj")
        cum = earners["PWGTP"].cumsum()
        half = earners["PWGTP"].sum() / 2
        med_row = earners[cum >= half]
        med_inc = float(med_row["adj"].iloc[0]) if len(med_row) > 0 else np.nan
        rows.append({
            "race": label,
            "poverty_rate": pov / tot,
            "median_income": med_inc,
            "n": int(tot),
            "vintage": vintage,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main data loader — public alias: load_brief_data
# ---------------------------------------------------------------------------

def _load_data(data_dir: Path, tax_year: int, rxkids_dir: Path | None) -> BriefData:
    state = pd.read_csv(data_dir / "by_state.csv").iloc[0]
    counties = pd.read_csv(data_dir / "by_county.csv")
    hd = pd.read_csv(data_dir / "by_house_district.csv")
    sd = pd.read_csv(data_dir / "by_senate_district.csv")
    hht_path = data_dir / "by_household_type.csv"
    household_types = pd.read_csv(hht_path) if hht_path.exists() else None
    rxkids_state = None
    if rxkids_dir is not None:
        rxkids_state = pd.read_csv(rxkids_dir / "by_state.csv").iloc[0]
    pums_dir = REPO_ROOT / "packages" / "data" / "raw"
    racial_stats = _compute_racial_stats(pums_dir)
    return BriefData(
        tax_year=tax_year,
        data_dir=data_dir,
        state=state,
        counties=counties,
        house_districts=hd,
        senate_districts=sd,
        household_types=household_types,
        racial_stats=racial_stats,
        rxkids_state=rxkids_state,
        rxkids_dir=rxkids_dir,
    )


# Public alias
load_brief_data = _load_data
