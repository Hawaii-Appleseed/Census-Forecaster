"""Filer counts by age band for the Phase 1 forward rake (demographic aging).

Population entered the forward targets only as a uniform 0.5%/yr scalar on
the filer total (``forward_targets.population_growth_rate``), so a TY2031
frame kept TY2023's filer age mix. Hawaiʻi's age structure is moving: the
65+ share of residents was 19.8% in 2020 and DBEDT projects 23–24% by 2030.
Filers 65+ hold most of the pension income Hawaiʻi exempts from tax
(HRS §235-7(a)(2)-(3)), so the age mix moves the taxable share of income,
which a scalar on the total cannot do.

This module turns a population-by-age projection into **filer counts by
age band** for the target year: the base-year PUMS filer age mix (weighted
``primary_agep``) is moved band by band with the population projection's
growth in that band, then normalized to the forward filer total —

    N_b(Y) = N(Y) · s_b(base) · P_b(Y)/P_b(base) / Σ_b' s_b'(base) · P_b'(Y)/P_b'(base)

so the filer total is still the forward target's and only the age mix
changes. ``phase1_reweight`` rakes to these counts as one more margin.

Data
----
``data/external/hi_population_by_age.json`` — resident population by band
and year, with vintage and provenance (``README_DATA_SOURCES.md``). The
loader interpolates between the years in the file and holds the last year
flat beyond it.
"""
from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

AGE_BANDS: Tuple[Tuple[str, int, int], ...] = (
    ("16-24", 16, 24),
    ("25-44", 25, 44),
    ("45-64", 45, 64),
    ("65+", 65, 200),
)
AGE_BAND_LABELS: Tuple[str, ...] = tuple(b[0] for b in AGE_BANDS)
AGE_COL = "primary_agep"

_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "external" / "hi_population_by_age.json"


def age_band(ages: np.ndarray) -> np.ndarray:
    """Band label per age; ``""`` for NaN / below 16 (not in any margin cell)."""
    a = np.asarray(ages, dtype=float)
    out = np.full(a.shape, "", dtype=object)
    for label, lo, hi in AGE_BANDS:
        out[(a >= lo) & (a <= hi)] = label
    return out


@cache
def load_population_by_age(path: Optional[str] = None) -> Dict:
    """The bundled projection: ``{"vintage", "source", "series": {year: {band: pop}}}``."""
    p = Path(path) if path else _DATA_FILE
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["series"] = {int(y): {b: float(v) for b, v in row.items()} for y, row in d["series"].items()}
    return d


def population_by_band(year: int, table: Optional[Dict] = None) -> Dict[str, float]:
    """Population per band in ``year``, linearly interpolated between the
    file's years; flat beyond its ends."""
    t = table if table is not None else load_population_by_age()
    series = t["series"]
    years = sorted(series)
    if year <= years[0]:
        return dict(series[years[0]])
    if year >= years[-1]:
        return dict(series[years[-1]])
    for y0, y1 in zip(years, years[1:]):
        if y0 <= year <= y1:
            f = (year - y0) / (y1 - y0)
            return {b: series[y0][b] + f * (series[y1][b] - series[y0][b]) for b in AGE_BAND_LABELS}
    raise ValueError(year)


def base_age_shares(
    units: pd.DataFrame, *, age_col: str = AGE_COL, weight_col: str = "weight",
) -> Dict[str, float]:
    """Weighted share of filers in each band (units with a usable age)."""
    if age_col not in units.columns:
        raise KeyError(f"{age_col!r} not in units")
    bands = age_band(units[age_col].to_numpy(dtype=float))
    w = units[weight_col].to_numpy(dtype=float)
    ok = bands != ""
    total = float(w[ok].sum())
    return {b: float(w[ok & (bands == b)].sum() / total) if total > 0 else 0.0
            for b in AGE_BAND_LABELS}


def build_age_targets(
    year: int,
    base_shares: Dict[str, float],
    total_filers: int,
    *,
    base_year: int,
    table: Optional[Dict] = None,
) -> Tuple[Dict[str, int], str]:
    """Filer counts by band for ``year`` (module doc), and the provenance string."""
    t = table if table is not None else load_population_by_age()
    p0 = population_by_band(base_year, t)
    p1 = population_by_band(year, t)
    raw = {b: base_shares.get(b, 0.0) * (p1[b] / p0[b] if p0[b] > 0 else 1.0) for b in AGE_BAND_LABELS}
    denom = sum(raw.values())
    shares = {b: v / denom for b, v in raw.items()} if denom > 0 else dict(base_shares)
    counts = {b: int(round(shares[b] * total_filers)) for b in AGE_BAND_LABELS}
    # largest-remainder fix so the bands sum to the total exactly
    diff = int(total_filers) - sum(counts.values())
    if diff:
        order = sorted(AGE_BAND_LABELS, key=lambda b: shares[b] * total_filers - counts[b], reverse=diff > 0)
        for b in order[:abs(diff)]:
            counts[b] += 1 if diff > 0 else -1
    src = f"{t.get('source', 'population by age')} (vintage {t.get('vintage', '?')})"
    return counts, src


__all__ = [
    "AGE_BANDS", "AGE_BAND_LABELS", "AGE_COL", "age_band", "base_age_shares",
    "build_age_targets", "load_population_by_age", "population_by_band",
]
