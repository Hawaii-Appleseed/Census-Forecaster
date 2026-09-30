"""Refresh national-macro predictor series (Census/BLS/BEA/FRED).

Registry-driven by ``acs.ml_features.NATIONAL_SERIES`` — the single source
of truth shared with the feature columns, so fetch and features never
drift. Writes two artifacts:

1. ``data/leading_indicators/national_macro.json`` — annual calendar-year
   mean level per series ``{name: {year: level}}``. This is the ACS
   forecaster channel (read by ``load_national_macro_data``); the
   log-change / diff / level transforms are applied at row-build time.
2. Merges the monthly national series (BLS monthly + FRED weekly/daily
   resampled to monthly) into ``data/markets/macro_monthly.json`` for the
   stock causal screen. Quarterly series (HVS vacancy/homeownership) and
   the CPI subindexes (already in the BLS panel) are NOT written here.

Sources, all keyless:
- ``CPI_PANEL``  — read national CPI subindexes straight from the bundled
  ``data/bls_panel/cpi_panel.json`` (already refreshed by the BLS panel job).
- ``BLS_FETCH``  — BLS v2 public API via ``bls.client.fetch_cpi_data``
  (chunked, keyless; 25 req/day).
- ``FRED``       — ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>``
  (keyless CSV; the Census Housing Vacancy Survey rides the FRED mirror).

Failure posture mirrors ``refresh_market_panel``: the write MERGES into
the committed ``national_macro.json``, never replaces it. ``main()`` seeds
every registry series from the existing file, each successful fetch
overwrites its own series, and a failed or empty fetch emits
``::warning`` and leaves the previous series in place. A final
``::warning`` names every series carried over unrefreshed, and
``series_fetch_date`` records when each series was last actually
fetched. The write aborts (exit 2) only if the merged result would be
empty — no committed file and nothing fetched.

This used to be false. ``main()`` started from an empty dict and wrote
only what fetched, so the "keeping previous" warning lied: the monthly
CI refreshes of 2026-08-06 and 2026-09-23 each hit a FRED outage and
deleted ``mortgage30``, ``dgs10``, ``rental_vacancy`` and
``homeownership`` from the bundle (7 of the 22 ``natl_*`` feature
columns went all-NaN). ``ef96a9f`` hand-restored the file once without
fixing the script. Pinned by the merge tests in
``tests/census_forecaster/test_refresh_national_macro.py`` and by
``test_national_macro_features.test_bundled_national_macro_loads``.

Usage
-----
    python -m census_forecaster.scripts.refresh_national_macro --dry-run
    python -m census_forecaster.scripts.refresh_national_macro
    python -m census_forecaster.scripts.refresh_national_macro --skip-fred
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import requests

from ..acs.ml_features import NATIONAL_SERIES, NationalSeriesSpec
from ..bls.client import fetch_cpi_data
from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_LEADING_DIR = _PKG_DATA / "leading_indicators"
_MARKETS_DIR = _PKG_DATA / "markets"
_CPI_PANEL_FILE = _PKG_DATA / "bls_panel" / "cpi_panel.json"

NATIONAL_MACRO_FILE = _LEADING_DIR / "national_macro.json"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"

FRED_CSV_TMPL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# Cadences whose monthly form is meaningful for the stock screen.
_SCREEN_CADENCES = {"monthly", "weekly", "daily"}


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_bls_monthly(
    series_ids: Sequence[str],
    api_key: Optional[str],
    *,
    start_year: int,
    end_year: int,
) -> dict[str, list[dict]]:
    """Keyless chunked BLS fetch → ``{sid: [{year, period, value}]}`` (M13 dropped).

    Generalizes ``refresh_market_panel.fetch_unemployment_monthly`` to an
    arbitrary series list.
    """
    window = 20 if api_key else 10
    chunks: list[tuple[int, int]] = []
    s = start_year
    while s <= end_year:
        e = min(s + window - 1, end_year)
        chunks.append((s, e))
        s = e + 1

    merged: dict[str, list[dict]] = defaultdict(list)
    for ys, ye in chunks:
        raw = fetch_cpi_data(series_ids=list(series_ids),
                             start_year=ys, end_year=ye, api_key=api_key)
        for sid, points in raw.items():
            merged[sid].extend(points)

    # BLS occasionally drops a series from a batched keyless response
    # without erroring; retry any empty series individually before giving
    # up on it (observed with LNS14000000, 2026-07-15).
    for sid in series_ids:
        if merged.get(sid):
            continue
        print(f"::warning::BLS batch returned no rows for {sid}; "
              "retrying individually", file=sys.stderr)
        try:
            for ys, ye in chunks:
                raw = fetch_cpi_data(series_ids=[sid], start_year=ys,
                                     end_year=ye, api_key=api_key)
                merged[sid].extend(raw.get(sid, []))
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::individual retry failed for {sid} ({exc})",
                  file=sys.stderr)

    out: dict[str, list[dict]] = {}
    for sid, points in merged.items():
        monthly = [p for p in points
                   if p["period"].startswith("M") and p["period"] != "M13"]
        monthly.sort(key=lambda p: (p["year"], p["period"]))
        out[sid] = monthly
    return out


def fetch_fred_csv(series_id: str, *, timeout: float = 30.0) -> list[dict]:
    """Download a FRED series CSV → ``[{date: 'YYYY-MM-DD', value: float}]``.

    Missing observations (FRED sentinel ``.``) are dropped.
    """
    url = FRED_CSV_TMPL.format(sid=series_id)
    resp = requests.get(url, timeout=timeout,
                        headers={"User-Agent": "census-forecaster/1.0"})
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    # FRED CSV columns: observation_date,<SERIES_ID>
    date_col = "observation_date"
    val_col = series_id
    out: list[dict] = []
    for row in reader:
        d = (row.get(date_col) or "").strip()
        raw = (row.get(val_col) or "").strip()
        if len(d) < 7 or raw in ("", "."):
            continue
        try:
            out.append({"date": d, "value": float(raw)})
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _year_of(row: dict) -> Optional[int]:
    if "year" in row:
        return int(row["year"])
    d = row.get("date", "")
    return int(d[:4]) if d[:4].isdigit() else None


def _month_of(row: dict) -> Optional[int]:
    if "period" in row and str(row["period"]).startswith("M"):
        return int(str(row["period"])[1:])
    d = row.get("date", "")
    return int(d[5:7]) if len(d) >= 7 and d[5:7].isdigit() else None


def aggregate_to_annual(rows: Sequence[dict]) -> dict[int, float]:
    """Calendar-year mean of all in-year prints (any cadence).

    Partial current year → mean of available prints (a level, in the
    series' native units); no-peeking holds because the year-Y mean is
    complete by year-end, well before the ACS target at Y+h (h≥1).
    """
    by_year: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        y = _year_of(r)
        if y is not None:
            by_year[y].append(float(r["value"]))
    return {y: round(sum(v) / len(v), 4) for y, v in sorted(by_year.items())}


def resample_monthly(rows: Sequence[dict]) -> list[dict]:
    """Reduce any-cadence rows to one value per month → screen rows.

    Weekly/daily prints in a month are averaged; BLS monthly rows pass
    through. Output: sorted ``[{year, period:"Mxx", value}]``.
    """
    by_ym: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        y, m = _year_of(r), _month_of(r)
        if y is not None and m is not None and 1 <= m <= 12:
            by_ym[(y, m)].append(float(r["value"]))
    out = [{"year": y, "period": f"M{m:02d}",
            "value": round(sum(v) / len(v), 4)}
           for (y, m), v in by_ym.items()]
    out.sort(key=lambda p: (p["year"], p["period"]))
    return out


# ---------------------------------------------------------------------------
# CPI-panel reader (Tier 0, no network)
# ---------------------------------------------------------------------------

def read_cpi_panel_series(series_id: str) -> list[dict]:
    """Read one national CPI subindex's monthly rows from the bundled panel."""
    if not _CPI_PANEL_FILE.exists():
        return []
    with open(_CPI_PANEL_FILE) as f:
        panel = json.load(f)
    return panel.get("series", {}).get(series_id, [])


# ---------------------------------------------------------------------------
# Previous-bundle reader (merge seed)
# ---------------------------------------------------------------------------

def read_previous_national_macro(
    path: Path,
) -> tuple[dict[str, dict[int, float]], dict[str, str], dict[str, str]]:
    """Seed for ``main()``: the committed file's registry series.

    Returns ``(annual_by_name, sources, series_fetch_date)`` with int year
    keys, restricted to names in ``NATIONAL_SERIES`` — a series dropped
    from the registry is dropped from the file rather than carried
    forever. A series with no per-series date (files written before
    ``series_fetch_date`` existed) takes the file's top-level
    ``fetch_date``. Absent file → three empty dicts.
    """
    if not path.exists():
        return {}, {}, {}
    with open(path) as f:
        payload = json.load(f)
    registry = {s.name for s in NATIONAL_SERIES}
    file_date = payload.get("fetch_date", "")
    prev_dates = payload.get("series_fetch_date", {})
    prev_sources = payload.get("sources", {})

    annual_by_name: dict[str, dict[int, float]] = {}
    sources: dict[str, str] = {}
    fetched_on: dict[str, str] = {}
    for name, by_year in payload.get("series", {}).items():
        if name not in registry:
            print(f"[national-macro] dropping {name}: no longer in "
                  "NATIONAL_SERIES", file=sys.stderr)
            continue
        vals = {int(y): float(v) for y, v in by_year.items() if v is not None}
        if not vals:
            continue
        annual_by_name[name] = vals
        if name in prev_sources:
            sources[name] = prev_sources[name]
        fetched_on[name] = prev_dates.get(name, file_date)
    return annual_by_name, sources, fetched_on


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def build_national_macro_payload(
    annual_by_name: dict[str, dict[int, float]],
    sources: dict[str, str],
    series_fetch_date: Optional[dict[str, str]] = None,
) -> dict:
    today = date.today().isoformat()
    fetched_on = series_fetch_date or {}
    return {
        "version": 1,
        "fetch_date": today,
        "series": {
            name: {str(y): v for y, v in sorted(vals.items())}
            for name, vals in sorted(annual_by_name.items())
        },
        "sources": sources,
        "series_fetch_date": {
            name: fetched_on.get(name, today)
            for name in sorted(annual_by_name)
        },
        "limitations": [
            "The refresh MERGES into this file: a series whose fetch fails "
            "keeps its previously committed values rather than being "
            "dropped. fetch_date is when the file was last written; "
            "series_fetch_date is when each series was last actually "
            "fetched, and an older date there means that series is stale.",
            "Each value is the CALENDAR-YEAR MEAN level in the series' "
            "native units (index level, rate %, or yield %). The forecaster "
            "applies the log-change / diff / level transform at row-build "
            "time (see acs/ml_features NATIONAL_SERIES col_policy).",
            "No-peeking: the year-Y mean is complete by year-end, well "
            "before the ACS target at Y+h (h>=1). The current partial year "
            "is a mean of available prints.",
            "National series are geoid-constant leading indicators (feature "
            "channel), NOT anchors and NOT county-specific.",
            "FRED-sourced series (mortgage, 10yr Treasury, HVS vacancy & "
            "homeownership) are national and public; quarterly HVS is meaned "
            "over available quarters.",
        ],
    }


def _merge_macro_monthly(monthly_by_key: dict[str, list[dict]]) -> None:
    """Read-modify-write the new monthly national series into macro_monthly.json,
    preserving existing keys (unemployment, Zillow)."""
    if MACRO_MONTHLY_FILE.exists():
        with open(MACRO_MONTHLY_FILE) as f:
            payload = json.load(f)
    else:
        payload = {"version": 1, "fetch_date": date.today().isoformat(),
                   "series": {}, "sources": {}, "limitations": []}
    payload.setdefault("series", {})
    payload.setdefault("sources", {})
    for key, rows in monthly_by_key.items():
        payload["series"][key] = rows
        payload["sources"][key] = "national-macro refresh"
    payload["fetch_date"] = date.today().isoformat()
    note = ("National macro predictors (BLS AHE/LFPR/emp-pop/JOLTS monthly; "
            "FRED mortgage & 10yr resampled to monthly means) added for the "
            "causal screen; rate-series log-returns are pp-change proxies.")
    if note not in payload.get("limitations", []):
        payload.setdefault("limitations", []).append(note)
    _atomic_write_json(MACRO_MONTHLY_FILE, payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh national-macro predictor series "
                    "(CPI panel + BLS + FRED), registry-driven.",
    )
    parser.add_argument("--start-year", type=int, default=2005)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--skip-fred", action="store_true",
                        help="Skip FRED series (mortgage/10yr/HVS); their "
                             "previously committed values are kept.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be fetched; write nothing.")
    args = parser.parse_args(argv)

    bls_specs = [s for s in NATIONAL_SERIES if s.source == "BLS_FETCH"]
    fred_specs = [s for s in NATIONAL_SERIES if s.source == "FRED"]
    cpi_specs = [s for s in NATIONAL_SERIES if s.source == "CPI_PANEL"]

    if args.dry_run:
        print(f"[dry-run] {len(cpi_specs)} CPI-panel series (no fetch): "
              f"{', '.join(s.series_id for s in cpi_specs)}", file=sys.stderr)
        print(f"[dry-run] {len(bls_specs)} BLS keyless fetches: "
              f"{', '.join(s.series_id for s in bls_specs)}", file=sys.stderr)
        if args.skip_fred:
            print("[dry-run] FRED skipped (--skip-fred)", file=sys.stderr)
        else:
            for s in fred_specs:
                print(f"[dry-run] would GET {FRED_CSV_TMPL.format(sid=s.series_id)}",
                      file=sys.stderr)
        print(f"[dry-run] outputs: {NATIONAL_MACRO_FILE} (merge), "
              f"{MACRO_MONTHLY_FILE} (merge)", file=sys.stderr)
        return 0

    # Seed from the committed file so a failed fetch leaves its series
    # in place; each successful fetch below overwrites its own entry.
    annual_by_name, sources, fetched_on = read_previous_national_macro(
        NATIONAL_MACRO_FILE)
    previous = set(annual_by_name)
    refreshed: set[str] = set()
    monthly_for_screen: dict[str, list[dict]] = {}
    today = date.today().isoformat()

    def _refresh(name: str, rows: Sequence[dict], source: str) -> None:
        annual_by_name[name] = aggregate_to_annual(rows)
        sources[name] = source
        fetched_on[name] = today
        refreshed.add(name)

    # ---- Tier 0: CPI panel (no network) ----
    for s in cpi_specs:
        rows = read_cpi_panel_series(s.series_id)
        if rows:
            _refresh(s.name, rows, f"BLS CPI panel ({s.series_id})")
            print(f"[cpi] {s.name}: {len(annual_by_name[s.name])} years "
                  f"from panel", file=sys.stderr)
        else:
            print(f"::warning::CPI panel missing {s.series_id}; keeping "
                  f"previous {s.name}", file=sys.stderr)

    # ---- Tier 1: BLS keyless ----
    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        print("::warning::BLS_API_KEY not set; fetching keylessly "
              "(25 req/day)", file=sys.stderr)
    try:
        bls_monthly = fetch_bls_monthly(
            [s.series_id for s in bls_specs], api_key,
            start_year=args.start_year, end_year=args.end_year)
        for s in bls_specs:
            rows = bls_monthly.get(s.series_id, [])
            if rows:
                _refresh(s.name, rows, f"BLS API ({s.series_id})")
                if s.cadence in _SCREEN_CADENCES:
                    monthly_for_screen[s.series_id] = resample_monthly(rows)
                print(f"[bls] {s.name}: {len(rows)} monthly prints",
                      file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — degrade, don't fail CI
        print(f"::warning::BLS fetch failed ({exc}); keeping previous BLS "
              "national-macro series", file=sys.stderr)

    # ---- Tier 1: FRED keyless CSV ----
    if not args.skip_fred:
        for s in fred_specs:
            try:
                rows = fetch_fred_csv(s.series_id)
            except Exception as exc:  # noqa: BLE001
                print(f"::warning::FRED fetch failed for {s.series_id} "
                      f"({exc}); keeping previous {s.name}", file=sys.stderr)
                continue
            if not rows:
                print(f"::warning::FRED returned no rows for {s.series_id}; "
                      f"keeping previous {s.name}", file=sys.stderr)
                continue
            _refresh(s.name, rows, f"FRED ({s.series_id})")
            if s.cadence in _SCREEN_CADENCES:
                monthly_for_screen[s.series_id] = resample_monthly(rows)
            print(f"[fred] {s.name}: {len(rows)} prints → "
                  f"{len(annual_by_name[s.name])} years", file=sys.stderr)

    if not annual_by_name:
        print("ERROR: nothing fetched and no previous national_macro.json; "
              "refusing to write an empty file", file=sys.stderr)
        return 2

    carried = sorted(previous - refreshed)
    if carried:
        print("::warning::national_macro.json carried over unrefreshed: "
              + ", ".join(f"{n} (last fetched {fetched_on.get(n) or '?'})"
                          for n in carried), file=sys.stderr)
    missing = [s.name for s in NATIONAL_SERIES if s.name not in annual_by_name]
    if missing:
        print(f"::warning::national_macro.json has no data for "
              f"{', '.join(missing)} (never fetched); their natl_* feature "
              "columns will be all-NaN", file=sys.stderr)

    _atomic_write_json(
        NATIONAL_MACRO_FILE,
        build_national_macro_payload(annual_by_name, sources, fetched_on))
    print(f"[national-macro] wrote national_macro.json "
          f"({len(annual_by_name)} series: {len(refreshed)} refreshed, "
          f"{len(carried)} carried over)", file=sys.stderr)

    if monthly_for_screen:
        _merge_macro_monthly(monthly_for_screen)
        print(f"[national-macro] merged {len(monthly_for_screen)} monthly "
              f"series into macro_monthly.json", file=sys.stderr)

    print("[national-macro] done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
