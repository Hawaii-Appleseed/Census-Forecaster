"""Refresh Realtor.com county listing-market metrics for Hawaii.

Why this source
---------------
Every housing series already in the panel measures the **sale** side —
DBEDT resale counts and median prices, Zillow's ZHVI valuation index,
FRED permit units. All of them are recorded at or after closing, which
in Hawaii lands 30-60 days after the price was actually agreed.

Realtor.com's Residential Listings database measures the **listing**
side: how long homes sit, how many sellers cut their asking price, how
much is for sale, how much is under contract. Those move while a deal
is being negotiated rather than after it settles, which is the whole
point of adding them — they are candidate *leading* indicators for
``HONOLULU_ZHVI``, not another way of measuring the same closings.

Coverage: 4 Hawaii counties, monthly, 2016-07 → current month (~1-month
lag; the file was refreshed 2026-08-04 with 2026-07 data as of writing,
making it the freshest housing series in the panel). Keyless.

Kalawao County (15005) is absent from the source — population ~80, no
listing market to speak of. The other four counties are complete: 121/121
months on every metric taken here, verified 2026-08-06.

NOT Redfin
----------
Redfin's Data Center county tracker was the first choice: it reaches back
to 2012 (vs 2016 here) and carries genuine sale-side extras this file has
no equivalent of — ``months_of_supply`` and ``avg_sale_to_list``. It was
rejected because **it is frozen**. Every export in that bucket (national,
metro, county, state, zip) carries an identical ``Last-Modified`` of
2026-06-02, with data stopping at 2026-05 — no update in over two months,
checked 2026-08-06. A feed that has stopped advancing cannot serve the
nowcast role this intake exists for. Its Honolulu single-family sale
counts do agree with DBEDT's to within ~2% where they overlap, so it
remains a sound archival cross-check if anyone ever wants the 2012-2016
backfill; it is simply not worth a 241 MB monthly download to re-fetch a
series that no longer moves.

``quality_flag``
----------------
The source marks some months as lower-confidence (thin volume, or
pandemic-era disruption — for Honolulu the flagged months cluster from
2020-03). Those rows are kept, not dropped: silently removing months
would change the series composition in a way no downstream consumer
could audit, and the screen already re-runs itself with 2020 excluded.
The flag is recorded in the limitations note instead.

Usage
-----
    python -m census_forecaster.scripts.refresh_realtor_inventory --dry-run
    python -m census_forecaster.scripts.refresh_realtor_inventory
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

import requests

from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"

HISTORY_URL = (
    "https://econdata.s3-us-west-2.amazonaws.com/Reports/Core/"
    "RDC_Inventory_Core_Metrics_County_History.csv"
)

#: county FIPS → geography suffix. 15005 (Kalawao) is not published.
GEOS: dict[str, str] = {
    "15001": "HAWAII",
    "15003": "HONOLULU",
    "15007": "KAUAI",
    "15009": "MAUI",
}

#: source column → emitted series prefix. Deliberately excludes the
#: ``_mm``/``_yy`` derivative columns (recomputable, and the screen does
#: its own differencing) and the sale-side-free extras.
METRICS: dict[str, str] = {
    # Duration: how long the typical listing sits before going pending.
    "median_days_on_market": "RDC_DOM_",
    # Stock of homes for sale.
    "active_listing_count": "RDC_ACTIVE_",
    # Flow of homes newly offered.
    "new_listing_count": "RDC_NEW_LISTINGS_",
    # Stock under contract but not closed — the closings of 1-2 months out.
    "pending_listing_count": "RDC_PENDING_",
    # pending / active: a tightness ratio robust to market size.
    "pending_ratio": "RDC_PENDING_RATIO_",
    # Share of active listings that cut their asking price = seller
    # capitulation, visible before it reaches any closed-sale index.
    "price_reduced_share": "RDC_PRICE_CUTS_",
    "price_increased_share": "RDC_PRICE_HIKES_",
    # Asking prices. See the circularity note in screen.py before
    # screening these against ZHVI.
    "median_listing_price": "RDC_LIST_PRICE_",
    "median_listing_price_per_square_foot": "RDC_LIST_PPSF_",
}


def parse_history_rows(lines: Iterable[str]) -> dict[str, list[dict]]:
    """Filter the national county history CSV down to Hawaii series.

    ``lines`` is any iterable of CSV text lines (the live fetch streams
    them so the 100 MB file never lands on disk). Returns
    ``{series_id: [{year, period, value}]}``.
    """
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        raise ValueError("empty CSV: no header row")
    missing = ({"month_date_yyyymm", "county_fips"} | set(METRICS)) - set(
        reader.fieldnames)
    if missing:
        raise ValueError(f"source is missing expected columns: {sorted(missing)}")

    out: dict[str, list[dict]] = {}
    for row in reader:
        geo = GEOS.get((row.get("county_fips") or "").strip())
        if geo is None:
            continue
        stamp = (row.get("month_date_yyyymm") or "").strip()
        if len(stamp) != 6:
            continue
        try:
            year, month = int(stamp[:4]), int(stamp[4:6])
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue
        period = f"M{month:02d}"
        for column, prefix in METRICS.items():
            raw = (row.get(column) or "").strip()
            if raw in ("", "NA"):
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            out.setdefault(prefix + geo, []).append(
                {"year": year, "period": period, "value": round(value, 4)})

    for rows in out.values():
        rows.sort(key=lambda r: (r["year"], r["period"]))
    if not out:
        raise ValueError("no Hawaii counties matched; FIPS scheme may have changed")
    return out


def fetch_hawaii_listings(*, timeout: float = 300.0) -> dict[str, list[dict]]:
    """Stream the county history file, keeping only Hawaii rows."""
    with requests.get(HISTORY_URL, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return parse_history_rows(resp.iter_lines(decode_unicode=True))


def merge_into_macro_monthly(series_by_id: dict[str, list[dict]],
                             *, path: Path = MACRO_MONTHLY_FILE) -> dict:
    """Read-modify-write: this file has seven contributing scripts."""
    if path.exists():
        with open(path) as f:
            payload = json.load(f)
    else:
        payload = {"version": 1, "series": {}, "sources": {}, "limitations": []}

    payload.setdefault("series", {}).update(series_by_id)
    payload.setdefault("sources", {})["REALTOR_RDC"] = HISTORY_URL
    payload["fetch_date"] = date.today().isoformat()

    note = (
        "RDC_* are Realtor.com residential LISTING metrics (asking prices, "
        "days on market, active/pending counts), not closed-sale data — "
        "they describe homes offered for sale, so they neither replace nor "
        "reconcile with DBEDT resale counts or Zillow ZHVI. Coverage is "
        "2016-07+, 4 counties (Kalawao/15005 unpublished). Some months "
        "carry the source's quality_flag=1 (thin volume or pandemic-era "
        "disruption; for Honolulu these cluster from 2020-03) and are "
        "retained rather than dropped."
    )
    lims = payload.setdefault("limitations", [])
    if note not in lims:
        lims.append(note)
    return payload


def _parse_args(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description="Refresh Realtor.com Hawaii listings")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        series_by_id = fetch_hawaii_listings()
    except Exception as exc:  # noqa: BLE001 — never wipe good data on a blip
        print(f"ERROR: Realtor.com fetch failed: {exc}; "
              "leaving macro_monthly.json alone.", file=sys.stderr)
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
