"""Refresh the annual ACS anchors no other refresh script owns.

Three bundled anchor files in ``data/anchors/``, each fetched in full from its
source and rewritten whole:

1. ``pce_deflator.json``       — BEA PCE chain-type price index, annual, 2017=100
   (FRED ``DPCERG3A086NBEA``, a keyless mirror of NIPA Table 2.3.4 line 1).
   Anchors B19013 as a national inflation proxy.
2. ``fred_hi_hpi.json``        — FHFA All-Transactions House Price Index for
   Hawaii (FRED ``HISTHPI``, quarterly), annual mean of the four quarters,
   complete years only. Anchors B25077.
3. ``qcew_hawaii_wages.json``  — BLS QCEW Hawaii statewide average annual pay,
   all industries, total covered (BLS API ``ENU1500050010``, period A01).
   Anchors B19013 and B20002.

Why this exists: until 2026-10 these were hand-compiled files, stale since
January 2026 with no refresh step, and they do not reproduce from the sources
they name. Against the live series, QCEW agreed within 1% through 2019 and then
ran 2-5% low (2020 pay +3.9% in the file, +10.0% in QCEW; 2024 $64,956 against
$67,366); the HPI differed from the true annual mean by up to 11% (2021: 786.9
against 709.8) with no consistent rule; the PCE file's 2017 value was 97.0 in a
file titled 2017=100 and its year-over-year changes were off by up to 0.3 pp.
Its ``series_id`` for QCEW (``ENU1500010010``) is monthly employment, not pay.
``hud_fmr_honolulu.json`` has the same problem and is NOT handled here: its only
keyless source is a HUD workbook, and its API needs a token.

Failure posture mirrors ``refresh_national_macro``: each anchor is independent;
a failed fetch warns and leaves the committed file (and its ``last_refresh``, so
the staleness warning keeps telling the truth); a fetch that lacks a committed
year is treated as partial and refused; a layout change (missing quarter,
implausible value) raises rather than writing a misaligned series.

FRED requests carry NO custom User-Agent: see ``refresh_national_macro.fetch_fred_csv``.

Usage
-----
    python -m census_forecaster.scripts.refresh_annual_anchors --dry-run
    BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_annual_anchors
    python -m census_forecaster.scripts.refresh_annual_anchors --strict   # exit 1 on any failure
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Optional, Sequence

from ..bls.client import fetch_cpi_data
from .refresh_national_macro import fetch_fred_csv
from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
ANCHORS_DIR = _PKG_DATA / "anchors"

#: First year kept; matches the files this replaces (2010 onward).
FIRST_YEAR = 2010

#: Plausible value ranges per anchor: a value outside raises (layout change).
_RANGES = {
    "pce_deflator.json": (50.0, 400.0),
    "fred_hi_hpi.json": (100.0, 5000.0),
    "qcew_hawaii_wages.json": (20_000.0, 250_000.0),
}

ANCHOR_SPECS: tuple[dict, ...] = (
    {
        "filename": "pce_deflator.json",
        "kind": "fred_annual",
        "fred_id": "DPCERG3A086NBEA",
        "source": "BEA",
        "series_id": ("BEA NIPA Table 2.3.4 line 1 (Personal consumption "
                      "expenditures price index), via FRED DPCERG3A086NBEA"),
        "title": ("Personal Consumption Expenditures: Chain-type Price Index "
                  "(annual, 2017=100)"),
        "frequency": "annual",
        "units": "index, 2017=100",
        "limitations": [
            "National-level price index; Hawaii cost-of-living deviates "
            "persistently above the national average so the *level* differs "
            "from Honolulu CPI but YoY rates are tightly correlated.",
            "PCE deflator weights expenditure shares differently from CPI "
            "(chain-weighted Fisher index, broader scope including imputed "
            "services).",
            "Often revised in subsequent annual GDP releases (typically "
            "<0.2 pp on YoY rate).",
            "Replaced in full on 2026-10 from the live series: the January "
            "2026 file was not on the 2017=100 base it was titled with.",
        ],
    },
    {
        "filename": "fred_hi_hpi.json",
        "kind": "fred_quarterly_mean",
        "fred_id": "HISTHPI",
        "source": "FRED",
        "series_id": ("HISTHPI (FHFA All-Transactions House Price Index for "
                      "Hawaii)"),
        "title": ("All-Transactions House Price Index for Hawaii (annual mean "
                  "of the four quarters, 1980Q1=100)"),
        "frequency": "quarterly (annual mean of Q1-Q4, complete years only)",
        "units": "index, 1980Q1=100",
        "limitations": [
            "Repeat-sales index; new construction is excluded by construction.",
            "State-level only; intra-state divergence (Honolulu vs Maui vs Big "
            "Island) is not captured.",
            "FHFA HPI tracks home-price *appreciation* across all "
            "transactions, including those with conforming GSE financing — its "
            "sample skews against jumbo/cash-only segments common in luxury "
            "Honolulu.",
            "Annual values are the mean of the four quarterly readings (an ACS "
            "1-year value reflects responses across the whole survey year); a "
            "year is written only once all four quarters exist.",
            "Replaced in full on 2026-10 from the live series: the January "
            "2026 file matched the true annual mean in only 3 of 15 years and "
            "differed by up to 11% in the rest.",
        ],
    },
    {
        "filename": "qcew_hawaii_wages.json",
        "kind": "bls_annual",
        "bls_id": "ENU1500050010",
        "source": "BLS QCEW",
        "series_id": ("ENU1500050010 (Hawaii statewide, all industries, total "
                      "covered, average annual pay)"),
        "title": ("Quarterly Census of Employment and Wages — Hawaii statewide "
                  "annual average pay (all ownership, all industries)"),
        "frequency": "annual",
        "units": "USD per covered employee per year (nominal)",
        "limitations": [
            "Covers UI-covered employment only; excludes self-employed, "
            "federal civilian, and certain agricultural workers.",
            "State-level series; county-level QCEW exists (ENU15001*, "
            "ENU15003*, etc.) but is noisier on small-county wage means.",
            "Average pay is total wages / employment — top-coding effects from "
            "a thin tail of high earners can move the mean by 1-2% in any year.",
            "Reported on calendar year basis; trailing revisions through Q3 of "
            "year+1 are common (typically <0.5%).",
            "QCEW is *nominal* wages; real wage growth is the relevant "
            "comparison for ACS B19013 (also nominal).",
            "Replaced in full on 2026-10 from the BLS API: the January 2026 "
            "file agreed within 1% through 2019, then ran 2-5% low (2020 "
            "growth +3.9% against QCEW's +10.0%, a pandemic composition "
            "effect), and its series id was monthly employment.",
        ],
    },
)


# ---------------------------------------------------------------------------
# Value builders (pure; network-free)
# ---------------------------------------------------------------------------

def annual_from_fred_annual(rows: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """FRED annual rows ``[{date, value}]`` → ``{year: value}`` from ``first_year``."""
    out: dict[int, float] = {}
    for r in rows:
        y = int(r["date"][:4])
        if y >= first_year:
            out[y] = round(float(r["value"]), 3)
    return dict(sorted(out.items()))


def annual_from_fred_quarterly(rows: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """FRED quarterly rows → annual mean of Q1-Q4, complete years only."""
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for r in rows:
        by_year[int(r["date"][:4])][int(r["date"][5:7])] = float(r["value"])
    out: dict[int, float] = {}
    for y, q in sorted(by_year.items()):
        if y < first_year:
            continue
        if set(q) == {1, 4, 7, 10}:
            out[y] = round(mean(q.values()), 3)
        elif len(q) == 4:
            raise ValueError(f"{y}: quarterly readings fall in months {sorted(q)}, "
                             "expected 1/4/7/10: layout change?")
    return out


def annual_from_bls(points: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """BLS API points ``[{year, period, value}]`` → ``{year: value}`` for period A01."""
    out = {int(p["year"]): float(p["value"]) for p in points
           if p["period"] == "A01" and int(p["year"]) >= first_year}
    return dict(sorted(out.items()))


def check_plausible(filename: str, values: dict[int, float]) -> None:
    lo, hi = _RANGES[filename]
    bad = {y: v for y, v in values.items() if not lo <= v <= hi}
    if bad:
        raise ValueError(f"{filename}: values outside [{lo:g}, {hi:g}]: {bad} "
                         "(wrong series or a units change?)")


def years_lost(previous: dict[int, float], new: dict[int, float]) -> list[int]:
    """Committed years the fetch lacks: a partial fetch, never a refresh."""
    return sorted(set(previous) - set(new))


def build_payload(spec: dict, values: dict[int, float], today: Optional[date] = None) -> dict:
    today = today or date.today()
    return {
        "source": spec["source"],
        "series_id": spec["series_id"],
        "title": spec["title"],
        "frequency": spec["frequency"],
        "units": spec["units"],
        "last_refresh": today.strftime("%Y-%m"),
        "limitations": list(spec["limitations"]),
        "values_by_year": {str(y): v for y, v in sorted(values.items())},
    }


# ---------------------------------------------------------------------------
# Fetchers (network)
# ---------------------------------------------------------------------------

def fetch_bls_annual(series_id: str, api_key: Optional[str], *, first_year: int = FIRST_YEAR,
                     last_year: Optional[int] = None) -> list[dict]:
    """Chunked BLS fetch (10-year windows keyless, 20 with a key) → raw points."""
    last_year = last_year or date.today().year
    window = 20 if api_key else 10
    points: list[dict] = []
    start = first_year
    while start <= last_year:
        end = min(start + window - 1, last_year)
        raw = fetch_cpi_data(series_ids=[series_id], start_year=start, end_year=end,
                             api_key=api_key)
        points.extend(raw.get(series_id, []))
        start = end + 1
    return points


def fetch_values(spec: dict, api_key: Optional[str], first_year: int = FIRST_YEAR) -> dict[int, float]:
    kind = spec["kind"]
    if kind == "fred_annual":
        values = annual_from_fred_annual(fetch_fred_csv(spec["fred_id"]), first_year)
    elif kind == "fred_quarterly_mean":
        values = annual_from_fred_quarterly(fetch_fred_csv(spec["fred_id"]), first_year)
    elif kind == "bls_annual":
        values = annual_from_bls(fetch_bls_annual(spec["bls_id"], api_key, first_year=first_year),
                                 first_year)
    else:  # pragma: no cover - spec typo
        raise ValueError(f"unknown anchor kind {kind!r}")
    if not values:
        raise ValueError(f"{spec['filename']}: the source returned no usable years")
    check_plausible(spec["filename"], values)
    return values


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_previous(path: Path) -> dict[int, float]:
    if not path.exists():
        return {}
    with open(path) as f:
        return {int(y): float(v) for y, v in json.load(f).get("values_by_year", {}).items()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the PCE, FHFA Hawaii HPI and QCEW Hawaii anchors.")
    parser.add_argument("--out", type=Path, default=ANCHORS_DIR,
                        help="Output directory (default: data/anchors/).")
    parser.add_argument("--first-year", type=int, default=FIRST_YEAR)
    parser.add_argument("--dry-run", action="store_true", help="List what would be fetched; write nothing.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any anchor fails (default: warn and keep the committed file).")
    args = parser.parse_args(argv)

    if args.dry_run:
        for spec in ANCHOR_SPECS:
            src = spec.get("fred_id") or spec.get("bls_id")
            print(f"[dry-run] {spec['filename']}: {spec['kind']} {src}", file=sys.stderr)
        return 0

    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        print("::warning::BLS_API_KEY not set; fetching QCEW keylessly (25 req/day)", file=sys.stderr)

    failed: list[str] = []
    for spec in ANCHOR_SPECS:
        name = spec["filename"]
        path = args.out / name
        try:
            values = fetch_values(spec, api_key, args.first_year)
        except Exception as exc:  # noqa: BLE001 - degrade per anchor, don't fail CI
            print(f"::warning::{name}: fetch failed ({exc}); keeping the committed file "
                  "and its last_refresh", file=sys.stderr)
            failed.append(name)
            continue
        previous = _read_previous(path)
        lost = years_lost(previous, values)
        if lost:
            print(f"::warning::{name}: fetch lacks {len(lost)} committed year(s) "
                  f"({lost[0]}-{lost[-1]}); treating it as a partial fetch, keeping the "
                  "committed file", file=sys.stderr)
            failed.append(name)
            continue
        _atomic_write_json(path, build_payload(spec, values))
        ys = sorted(values)
        print(f"[annual-anchors] {name}: {len(ys)} years ({ys[0]}-{ys[-1]}), last {values[ys[-1]]:g}",
              file=sys.stderr)

    if failed:
        print(f"[annual-anchors] {len(failed)} of {len(ANCHOR_SPECS)} anchors not refreshed: "
              + ", ".join(failed), file=sys.stderr)
        return 1 if args.strict else 0
    print("[annual-anchors] done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
