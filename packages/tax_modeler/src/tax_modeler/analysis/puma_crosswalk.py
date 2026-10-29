"""Hawaii PUMA → county + legislative district crosswalk.

Hawaii PUMS 2020-geography has 10 PUMAs covering the whole state:

  0100  Maui + Kalawao + Kauai (combined) — split via ``PUMA0100Imputer``
  0200  Hawaii County (Big Island)
  0301  Rural Oahu
  0302  Koolaupoko (Oahu)
  0303  East Honolulu to Kapahulu
  0304  Tantalus to Waikiki
  0305  Nuuanu to Kalihi
  0306  Moanalua to Pearl City
  0307  Central Oahu
  0308  Ewa (Oahu)

Each PUMA spans multiple legislative districts; district assignment uses a
deterministic hash of the row's SERIALNO (or index) to draw reproducibly from
the set of valid (house_district, senate_district) pairs for that PUMA.

Public API
----------
assign_geography(df, split_puma_0100=True, add_districts=True) → df
    Adds ``county``, ``house_district``, ``senate_district`` columns.

load_crosswalk() → pd.DataFrame
    Returns the raw PUMA-to-district table bundled with the package.
"""
from __future__ import annotations

import hashlib
import logging
from functools import cache, lru_cache
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PUMA → county (exact, except PUMA 100 which needs imputation)
# ---------------------------------------------------------------------------

# Canonical 3-digit PUMA codes (leading zeros stripped).
# PUMA 100 is listed as "Maui" here; the imputer overwrites it for Kauai rows.
_PUMA_COUNTY: Dict[int, str] = {
    100: "Maui",
    200: "Hawaii",
    301: "Honolulu",
    302: "Honolulu",
    303: "Honolulu",
    304: "Honolulu",
    305: "Honolulu",
    306: "Honolulu",
    307: "Honolulu",
    308: "Honolulu",
}

_KNOWN_PUMAS = set(_PUMA_COUNTY)


def _normalize_puma(val) -> Optional[int]:
    """Return canonical 3-digit int PUMA code, or None if unrecognised."""
    try:
        code = int(str(val).strip().lstrip("0") or "0")
    except (ValueError, TypeError):
        return None
    return code if code in _KNOWN_PUMAS else None


# ---------------------------------------------------------------------------
# Bundled crosswalk loader
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_crosswalk() -> pd.DataFrame:
    """Load the bundled PUMA-to-district crosswalk table.

    Returns a DataFrame with columns ``PUMA`` (int), ``house_district`` (int),
    ``senate_district`` (int), and ``county`` (str).
    """
    from importlib.resources import files

    data_path = files("tax_modeler") / "data" / "crosswalks" / "hawaii_puma_districts_official_2022.csv"
    df = pd.read_csv(str(data_path))
    df["PUMA"] = df["PUMA"].astype(int)
    df["house_district"] = df["house_district"].astype(int)
    df["senate_district"] = df["senate_district"].astype(int)
    return df


@lru_cache(maxsize=1)
def _puma_district_options() -> Dict[int, List[Tuple[int, int]]]:
    """Return dict mapping PUMA → sorted list of (house_district, senate_district) pairs."""
    xwalk = load_crosswalk()
    result: Dict[int, List[Tuple[int, int]]] = {}
    for puma, grp in xwalk.groupby("PUMA"):
        pairs = sorted(
            set(zip(grp["house_district"], grp["senate_district"]))
        )
        result[int(puma)] = pairs
    return result


# ---------------------------------------------------------------------------
# Deterministic district picker
# ---------------------------------------------------------------------------

@cache
def _pick_district(puma: int, row_key: str) -> Tuple[int, int]:
    """Return a reproducible (house_district, senate_district) pair for a row.

    Uses MD5 of ``"puma_{puma}_{row_key}"`` to index into the sorted list of
    valid pairs for that PUMA, giving stable assignments across runs. Cached:
    every projection assigns the same units again.
    """
    options = _puma_district_options().get(puma, [])
    if not options:
        return (0, 0)
    seed = int(hashlib.md5(f"puma_{puma}_{row_key}".encode()).hexdigest()[:8], 16)
    return options[seed % len(options)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_geography(
    df: pd.DataFrame,
    split_puma_0100: bool = True,
    add_districts: bool = True,
) -> pd.DataFrame:
    """Add ``county``, ``house_district``, and ``senate_district`` to *df*.

    The DataFrame must have a ``PUMA`` column (string or int, with or without
    leading zeros — all formats normalised internally).  If a ``county`` column
    already exists, only null/unrecognised values are filled in.

    Parameters
    ----------
    df:
        Tax-unit DataFrame with a ``PUMA`` column.
    split_puma_0100:
        When ``True`` (default), rows in PUMA 0100 (the Maui + Kauai combined
        PUMA) are probabilistically assigned to Maui or Kauai via
        :class:`~tax_modeler.analysis.puma_imputation.PUMA0100Imputer` using
        a deterministic hash of SERIALNO.  When ``False``, all PUMA 0100 rows
        receive ``county = "Maui"`` (the majority county).
    add_districts:
        When ``True`` (default), ``house_district`` and ``senate_district``
        are added using a deterministic per-row draw from the valid district
        set for each PUMA.

    Returns
    -------
    pd.DataFrame
        Copy of *df* with geographic columns added.  Rows whose PUMA is not
        in the Hawaii panel receive ``county = None`` and ``*_district = 0``.
    """
    if "PUMA" not in df.columns:
        logger.warning("assign_geography: no PUMA column found; returning df unchanged")
        return df.copy()

    df = df.copy()

    # --- Normalise PUMA to 3-digit int in a helper series -----------------
    puma_int = df["PUMA"].map(_normalize_puma)  # int or NaN

    # --- County assignment -------------------------------------------------
    has_county = "county" in df.columns
    if not has_county:
        df["county"] = None

    # Fill county for rows where it is currently null
    needs_county = df["county"].isna()

    for code, county_name in _PUMA_COUNTY.items():
        if code == 100:
            continue  # handled separately below
        mask = needs_county & (puma_int == code)
        if mask.any():
            df.loc[mask, "county"] = county_name

    # PUMA 0100: Maui/Kauai imputation (or simple Maui default)
    puma_100_mask = needs_county & (puma_int == 100)
    if puma_100_mask.any():
        if split_puma_0100:
            from tax_modeler.analysis.puma_imputation import PUMA0100Imputer
            imputer = PUMA0100Imputer()
            subset = df[puma_100_mask]
            imputed = imputer.impute_counties(subset)
            df.loc[puma_100_mask, "county"] = imputed["county"].values
        else:
            df.loc[puma_100_mask, "county"] = "Maui"

    # Rows with unknown PUMA
    unknown_mask = needs_county & puma_int.isna()
    if unknown_mask.any():
        logger.warning(
            "assign_geography: %d rows have unrecognised PUMA codes and will have county=None",
            unknown_mask.sum(),
        )

    # --- District assignment -----------------------------------------------
    if add_districts:
        if "house_district" not in df.columns:
            df["house_district"] = 0
        if "senate_district" not in df.columns:
            df["senate_district"] = 0

        needs_district = (df["house_district"] == 0) | df["house_district"].isna()

        # Build a row-key series: prefer SERIALNO, fall back to string index.
        if "SERIALNO" in df.columns:
            row_keys = df["SERIALNO"].astype(str)
        else:
            row_keys = df.index.astype(str)

        for code in _KNOWN_PUMAS:
            mask = needs_district & (puma_int == code)
            if not mask.any():
                continue
            idx = df.index[mask]
            keys = row_keys.loc[idx]
            pairs = [_pick_district(code, k) for k in keys]
            df.loc[idx, "house_district"] = [p[0] for p in pairs]
            df.loc[idx, "senate_district"] = [p[1] for p in pairs]

    return df
