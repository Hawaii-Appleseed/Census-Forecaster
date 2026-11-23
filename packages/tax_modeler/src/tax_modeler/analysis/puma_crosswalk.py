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

Each PUMA spans several legislative districts. District assignment draws one
(house_district, senate_district) piece of the row's PUMA, within its county for
the split PUMA 100, with probability equal to the piece's share of the PUMA's
2020 census population (:func:`population_crosswalk`, all 51 House and 25 Senate
districts of the 2022 plan). The draw is a deterministic hash of the row's
SERIALNO (or index), so a household's members land together and every run agrees.
For district totals that do not depend on one draw per household, apportion PUMA
totals instead (:func:`apportion`).

Public API
----------
assign_geography(df, split_puma_0100=True, add_districts=True) → df
    Adds ``county``, ``house_district``, ``senate_district`` columns.

load_crosswalk() → pd.DataFrame
    The PUMA-to-district pieces with their populations (:func:`population_crosswalk`).

apportion(by_puma, level) → pd.DataFrame
    PUMA totals spread over counties or districts by population.
"""
from __future__ import annotations

import hashlib
import logging
from functools import cache, lru_cache

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PUMA → county (exact, except PUMA 100 which needs imputation)
# ---------------------------------------------------------------------------

# Canonical 3-digit PUMA codes (leading zeros stripped).
# PUMA 100 is listed as "Maui" here; the imputer overwrites it for Kauai rows.
_PUMA_COUNTY: dict[int, str] = {
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


def _normalize_puma(val) -> int | None:
    """Return canonical 3-digit int PUMA code, or None if unrecognised."""
    try:
        code = int(str(val).strip().lstrip("0") or "0")
    except (ValueError, TypeError):
        return None
    return code if code in _KNOWN_PUMAS else None


# ---------------------------------------------------------------------------
# Bundled crosswalk loader
# ---------------------------------------------------------------------------

def load_crosswalk() -> pd.DataFrame:
    """The PUMA-to-district crosswalk: columns ``PUMA``, ``county``, ``house_district``, ``senate_district`` (ints
    for the codes), ``population`` and ``share_of_puma``. Until November 2026 this read
    ``hawaii_puma_districts_official_2022.csv``, which listed 35 House and 18 Senate districts and no populations;
    it is now :func:`population_crosswalk`."""
    return population_crosswalk().copy()


@cache
def _district_pieces(puma: int, county: str | None) -> tuple[tuple[tuple[int, int], ...], tuple[float, ...]]:
    """The (house_district, senate_district) pieces of ``puma`` (within ``county`` when given and present) and their
    cumulative population shares, ending at 1."""
    x = population_crosswalk()
    x = x[x["PUMA"] == puma]
    if county is not None and (x["county"] == county).any():
        x = x[x["county"] == county]
    g = x.groupby(["house_district", "senate_district"], sort=True)["population"].sum()
    if g.empty or g.sum() <= 0:
        return (), ()
    cum = (g.cumsum() / g.sum()).to_numpy().copy()
    cum[-1] = 1.0
    return tuple((int(h), int(d)) for h, d in g.index), tuple(float(c) for c in cum)


# ---------------------------------------------------------------------------
# Deterministic district picker
# ---------------------------------------------------------------------------

@cache
def _pick_district(puma: int, row_key: str, county: str | None = None) -> tuple[int, int]:
    """Return a reproducible (house_district, senate_district) pair for a row.

    MD5 of ``"puma_{puma}_{row_key}"`` gives a number in [0, 1); the pair is the piece of the PUMA (within
    ``county`` for PUMA 100) whose cumulative population share first exceeds it, so each district is drawn in
    proportion to its share of the PUMA's people. Cached: every projection assigns the same units again.
    """
    pairs, cum = _district_pieces(puma, county)
    if not pairs:
        return (0, 0)
    u = int(hashlib.md5(f"puma_{puma}_{row_key}".encode()).hexdigest()[:8], 16) / 0x100000000
    for pair, c in zip(pairs, cum, strict=True):
        if u < c:
            return pair
    return pairs[-1]


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
            # PUMA 100 spans Maui and Kauaʻi: draw within the county already assigned.
            counties = df.loc[idx, "county"] if code == 100 else pd.Series(None, index=idx, dtype=object)
            pairs = [_pick_district(code, k, None if pd.isna(c) else str(c)) for k, c in zip(keys, counties, strict=True)]
            df.loc[idx, "house_district"] = [p[0] for p in pairs]
            df.loc[idx, "senate_district"] = [p[1] for p in pairs]

    return df


# ---------------------------------------------------------------------------
# Population-weighted apportionment (all 51 House and 25 Senate districts)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def population_crosswalk() -> pd.DataFrame:
    """Pieces of each 2020 PUMA by county, House and Senate district (2022 plan), with 2020 census population.

    Columns ``PUMA``, ``county``, ``house_district``, ``senate_district``, ``population``, ``share_of_puma``. Built
    from census blocks by ``scripts/build_puma_district_crosswalk.py``; every district, each weighted by its
    population. :func:`assign_geography` draws from it.
    """
    from importlib.resources import files

    path = files("tax_modeler") / "data" / "crosswalks" / "hawaii_puma_district_population_2022.csv"
    return pd.read_csv(str(path))


def apportionment_matrix(level: str) -> pd.DataFrame:
    """PUMA × geography shares: row ``p`` says how PUMA ``p``'s population divides among the ``level`` areas
    (``"county"``, ``"house_district"`` or ``"senate_district"``). Rows sum to 1."""
    x = population_crosswalk()
    if level not in ("county", "house_district", "senate_district"):
        raise ValueError(f"unknown level {level!r}")
    return x.pivot_table(index="PUMA", columns=level, values="share_of_puma", aggfunc="sum", fill_value=0.0)


def apportion(by_puma: pd.DataFrame, level: str) -> pd.DataFrame:
    """Spread PUMA totals (rows indexed by PUMA code, any number of columns) over ``level`` areas by population.

    An area's figure is the sum over the PUMAs it touches of the PUMA's total times the PUMA's share of
    population in the area: the ACS has no sample of its own below the PUMA, so each part of a PUMA is given the
    PUMA's average. Totals are kept. Replicate totals apportioned the same way give the areas' sampling errors.
    """
    a = apportionment_matrix(level)
    missing = set(by_puma.index) - set(a.index)
    if missing:
        raise ValueError(f"PUMAs not in the crosswalk: {sorted(missing)}")
    a = a.loc[by_puma.index]
    return pd.DataFrame(a.to_numpy().T @ by_puma.to_numpy(dtype=float), index=a.columns, columns=by_puma.columns)


def normalize_puma(values) -> pd.Series:
    """PUMA codes as the crosswalk's ints (``"00301"``, ``301`` and ``"301"`` alike); unknown codes are NaN."""
    return pd.Series(values).map(_normalize_puma)
