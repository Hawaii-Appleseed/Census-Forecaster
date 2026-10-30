"""Refresh Hawaii-specific monthly indicators from FRED and BTS.

Adds three series the pipeline had no equivalent of:

``HIPHCI`` — **Philadelphia Fed Hawaii Coincident Index** (FRED, keyless
    CSV, monthly 1979-, ~1-month lag). A purpose-built composite of
    payroll employment, unemployment rate, average hours in
    manufacturing, and deflated wages — i.e. the Fed's own single answer
    to "how is Hawaii's economy doing right now". Everything else in the
    panel is a single raw series; this is the only composite, and it is
    the most current Hawaii-wide read available (2026-06 as of writing).

``HIBPPRIV`` — **Hawaii housing units authorized by building permits**
    (FRED, keyless CSV, monthly 1988-, current). The ML feature panel's
    BPS permit channel is ANNUAL; this is the same construction signal
    at 12x the cadence. Kept alongside (not instead of)
    ``DBEDT_PERMITS_*``, which measures permit *value* in dollars rather
    than *unit counts* — different quantities, and the screen can decide
    which carries signal.

    NOT ``HONO115BPPRIV``: the Honolulu-MSA series looks like the better
    geographic match but is **discontinued — it ends 2013-12** (same for
    its SA twin). Verified 2026-08-06; the statewide series is the only
    current one of the family.

``BTS_HNL_PASSENGERS`` / ``BTS_HNL_DEPARTURES`` — **BTS T-100 segment
    data for Honolulu (HNL)** (Socrata, keyless, monthly 2014-). Actual
    enplanements and departures, independent of the DBEDT/HTA visitor
    counts: airline operations rather than survey-based visitor
    attribution, so it cross-checks the tourism channel from the supply
    side. NOTE this is origin-airport traffic (departures FROM HNL), not
    total airport throughput.

All three are keyless. The Socrata endpoint requires a browser-like
User-Agent — a bare urllib/curl request is silently rejected — so one is
set explicitly below.

Usage
-----
    python -m census_forecaster.scripts.refresh_hawaii_indicators --dry-run
    python -m census_forecaster.scripts.refresh_hawaii_indicators
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import requests

from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"

FRED_CSV_TMPL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

#: FRED series id → emitted series id (kept identical: FRED ids are the
#: canonical name and grepping for them should find both ends).
FRED_SERIES: dict[str, str] = {
    "HIPHCI": "HIPHCI",
    "HIBPPRIV": "HIBPPRIV",
    # Census Business Formation Statistics — business applications,
    # Hawaii, seasonally adjusted (monthly 2004-07+, ~1-month lag).
    # A filing precedes hiring, leasing and equipment purchase, so
    # formations are a forward-looking read on local activity that no
    # payroll or household survey can give.
    #
    # SA on purpose: the NSA twin is dominated by calendar seasonality
    # (the DBEDT tourism series in this panel lose ~75% of their m/m
    # variance to it), which the screen's log_diff transform cannot
    # remove and which degrades the Granger test for no gain.
    #
    # This is the ONLY route to Hawaii business formation. Hawaii DCCA
    # publishes registrations as HTML search forms only — no CSV, no
    # API, no time series (opendata.hawaii.gov lists 6 DCCA entries,
    # all search UIs; verified 2026-08-06).
    "BABATOTALSAHI": "BABATOTALSAHI",
    # US Gulf Coast kerosene-type jet fuel spot price, FOB $/gal
    # (monthly 1990-04+). NATIONAL, not Hawaii — the exception to this
    # module's name, kept here because it is a keyless FRED CSV like
    # its neighbours. It deliberately does NOT go in
    # refresh_national_macro.py: that script is driven by
    # acs.ml_features.NATIONAL_SERIES, so adding it there would inject
    # an ungated channel into the ACS feature panel. Screen input only.
    #
    # Identity verified 2026-08-06 by value, not by title: all six most
    # recent months match the EIA API's EER_EPJK_PF4_RGC_DPG exactly
    # (3.403, 3.042, 3.943, 3.928, 3.697, 2.262). Worth doing — this
    # repo has shipped a mislabelled FRED series before (CUURS49ASA0
    # was Los Angeles, not Honolulu).
    #
    # NOT the Hawaii-specific jet fuel series: EIA's state-level
    # refiner prices for Hawaii died in 2013-09 (34 non-null
    # observations ever), and the entire refiner-survey jet fuel
    # family — every state and the national total — stops at 2022-03.
    "MJFUELUSGULF": "MJFUELUSGULF",
}

BTS_URL = "https://data.transportation.gov/resource/r495-tyji.json"
BTS_AIRPORT = "HNL"
#: Socrata rejects requests without a browser-like UA (empty body, no error).
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def fetch_fred_monthly(series_id: str, *, timeout: float = 60.0) -> list[dict]:
    """FRED keyless CSV → ``[{year, period, value}]`` (monthly rows only)."""
    url = FRED_CSV_TMPL.format(sid=series_id)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    rows: list[dict] = []
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"{series_id}: unexpected CSV header {header!r}")
    for rec in reader:
        if len(rec) < 2:
            continue
        stamp, raw = rec[0].strip(), rec[1].strip()
        if raw in ("", "."):        # FRED's missing-value marker
            continue
        try:
            year, month = int(stamp[:4]), int(stamp[5:7])
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not 1 <= month <= 12:
            continue
        rows.append({"year": year, "period": f"M{month:02d}",
                     "value": round(value, 4)})
    rows.sort(key=lambda r: (r["year"], r["period"]))
    return rows


def fetch_bts_hnl(*, timeout: float = 60.0) -> dict[str, list[dict]]:
    """BTS T-100 HNL-origin monthly totals → passengers + departures."""
    params = {
        "origin_airport_code": BTS_AIRPORT,
        "$select": "reporting_month,total_passengers,total_departures",
        "$order": "reporting_month ASC",
        "$limit": 5000,
    }
    resp = requests.get(BTS_URL, params=params,
                        headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    pax: list[dict] = []
    dep: list[dict] = []
    for item in resp.json():
        stamp = str(item.get("reporting_month") or "")
        if len(stamp) < 7:
            continue
        try:
            year, month = int(stamp[:4]), int(stamp[5:7])
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue
        period = f"M{month:02d}"
        for key, sink in (("total_passengers", pax), ("total_departures", dep)):
            raw = item.get(key)
            if raw in (None, ""):
                continue
            try:
                sink.append({"year": year, "period": period,
                             "value": float(raw)})
            except (TypeError, ValueError):
                continue
    for rows in (pax, dep):
        rows.sort(key=lambda r: (r["year"], r["period"]))
    return {"BTS_HNL_PASSENGERS": pax, "BTS_HNL_DEPARTURES": dep}


def merge_into_macro_monthly(series_by_id: dict[str, list[dict]],
                             *, path: Path = MACRO_MONTHLY_FILE) -> dict:
    if path.exists():
        with open(path) as f:
            payload = json.load(f)
    else:
        payload = {"version": 1, "series": {}, "sources": {}, "limitations": []}

    payload.setdefault("series", {}).update(series_by_id)
    sources = payload.setdefault("sources", {})
    for sid in FRED_SERIES.values():
        if sid in series_by_id:
            sources[sid] = FRED_CSV_TMPL.format(sid=sid)
    if any(k.startswith("BTS_") for k in series_by_id):
        sources["BTS_T100"] = f"{BTS_URL} (T-100 segment, origin {BTS_AIRPORT})"
    payload["fetch_date"] = date.today().isoformat()

    note = (
        "HIPHCI is the Philadelphia Fed Hawaii coincident index (composite "
        "of payrolls, unemployment, manufacturing hours, deflated wages). "
        "HIBPPRIV is Hawaii housing UNITS authorized (counts) — distinct "
        "from DBEDT_PERMITS_* which is permit VALUE in dollars. "
        "BTS_HNL_* are T-100 origin-airport departures/enplanements FROM "
        "HNL (not total airport throughput), ~4-month lag, and revised. "
        "BABATOTALSAHI is Census BFS business APPLICATIONS (filings, not "
        "confirmed business starts — many never become employers) and is "
        "seasonally adjusted, unlike most series in this file. "
        "MJFUELUSGULF is a NATIONAL Gulf Coast benchmark, not a Hawaii "
        "price: Hawaii-specific jet fuel ended 2013-09 and the whole EIA "
        "refiner-survey jet fuel family ended 2022-03."
    )
    lims = payload.setdefault("limitations", [])
    if note not in lims:
        lims.append(note)
    return payload


def _parse_args(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    series_by_id: dict[str, list[dict]] = {}

    for fred_id, out_id in FRED_SERIES.items():
        try:
            rows = fetch_fred_monthly(fred_id)
        except Exception as exc:  # noqa: BLE001 — degrade per-series
            print(f"::warning:: FRED fetch failed for {fred_id}: {exc}; "
                  "keeping previous", file=sys.stderr)
            continue
        if rows:
            series_by_id[out_id] = rows

    try:
        series_by_id.update({k: v for k, v in fetch_bts_hnl().items() if v})
    except Exception as exc:  # noqa: BLE001
        print(f"::warning:: BTS T-100 fetch failed: {exc}; keeping previous",
              file=sys.stderr)

    if not series_by_id:
        print("ERROR: nothing fetched; leaving macro_monthly.json alone.",
              file=sys.stderr)
        return 1

    for sid, rows in sorted(series_by_id.items()):
        print(f"  {sid}: {len(rows)} months "
              f"({rows[0]['year']}-{rows[0]['period']} → "
              f"{rows[-1]['year']}-{rows[-1]['period']})", flush=True)

    payload = merge_into_macro_monthly(series_by_id)
    if args.dry_run:
        print(f"[dry-run] would write {len(series_by_id)} series to "
              f"{MACRO_MONTHLY_FILE}")
        return 0
    _atomic_write_json(MACRO_MONTHLY_FILE, payload)
    print(f"Wrote {MACRO_MONTHLY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
