"""Refresh EIA Hawaii monthly energy-price series.

Why this exists
---------------
``METHODOLOGY.md`` (Market signals → Known limitations) records that
CPI-directed hypotheses in the causal screen are stuck at
xcorr-descriptive "until a monthly Hawaii price proxy exists": the
genuine Urban Hawaii CPI (``CUURS49FSA0``) is **bimonthly**, and the
Granger test's all-lags-present rule needs a monthly grid. The screen
had been passing on ``CUURS49ASA0``, which the 2026-07-27 identity audit
proved is **Los Angeles**, not Honolulu — so those passes described LA.

EIA's state-level retail electricity price is a genuine **monthly,
Hawaii-specific price series** (2001-01 →, ~3-month publication lag), so
it is the missing proxy. Hawaii imports ~80% of its energy, which is the
same channel the XLE ticker hypothesis was written around — but measured
directly instead of inferred from an energy-sector equity price.

Scope note: EIA's gasoline series (``petroleum/pri/gnd``) covers only 29
major metro/PADD areas and does **not** include Hawaii, so electricity is
the only Hawaii-specific monthly price this source can provide.

API key
-------
EIA requires a free key. Resolution order (never committed to the repo):

1. ``$EIA_API_KEY``
2. ``~/.eia_api_key`` (same convention as ``~/.census_api_key``)

Writes
------
Merges ``EIA_HI_ELEC_<SECTOR>`` monthly series into
``data/markets/macro_monthly.json`` — the file the causal screen reads
for its monthly targets — leaving every other series untouched.

Usage
-----
    python -m census_forecaster.scripts.refresh_eia_hawaii --dry-run
    python -m census_forecaster.scripts.refresh_eia_hawaii
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import requests

from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"

EIA_BASE = "https://api.eia.gov/v2/electricity/retail-sales/data/"

#: Sectors to pull. ALL is the headline all-sector average price;
#: RES is the household-facing one that maps to the CPI energy channel.
SECTORS: tuple[str, ...] = ("ALL", "RES")

#: Screen/series id prefix. Kept explicit so a reader grepping
#: macro_monthly.json can tell EIA rows from BLS/FRED rows at a glance.
SERIES_PREFIX = "EIA_HI_ELEC_"

#: Consumption (million kWh) alongside price, from the same endpoint.
#: Price is a COST channel — imported fuel reaching household bills.
#: Sales are a VOLUME channel: how much electricity Hawaii actually
#: used, which tracks activity (hotels running air conditioning,
#: commercial floorspace in use) rather than what it cost. Different
#: quantities that can move in opposite directions, so both are kept.
SALES_PREFIX = "EIA_HI_ELEC_SALES_"

_STATE = "HI"


def resolve_api_key(explicit: str | None = None) -> str | None:
    """$EIA_API_KEY, then ~/.eia_api_key. Never read from the repo."""
    if explicit:
        return explicit.strip()
    env = os.environ.get("EIA_API_KEY")
    if env:
        return env.strip()
    path = Path.home() / ".eia_api_key"
    if path.exists():
        key = path.read_text().strip()
        if key:
            return key
    return None


def fetch_hawaii_electricity(
    api_key: str,
    sector: str,
    *,
    metric: str = "price",
    timeout: float = 30.0,
) -> list[dict]:
    """Monthly HI retail electricity ``metric`` → ``[{year, period, value}]``.

    ``metric`` is "price" (cents/kWh) or "sales" (million kWh).

    Paginates because EIA caps a JSON response at 5000 rows; a single
    state-sector pull is ~300 rows today, but the loop keeps this honest
    if the query is ever widened.
    """
    rows: list[dict] = []
    offset, page = 0, 5000
    while True:
        params = {
            "api_key": api_key,
            "frequency": "monthly",
            "data[0]": metric,
            "facets[stateid][]": _STATE,
            "facets[sectorid][]": sector,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": page,
        }
        resp = requests.get(EIA_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json().get("response", {})
        data = payload.get("data", [])
        for item in data:
            period = item.get("period", "")      # "YYYY-MM"
            value = item.get(metric)
            if value is None or len(period) != 7:
                continue
            try:
                year, month = int(period[:4]), int(period[5:7])
                val = float(value)
            except (TypeError, ValueError):
                continue
            if not 1 <= month <= 12:
                continue
            rows.append({"year": year, "period": f"M{month:02d}",
                         "value": round(val, 4)})
        if len(data) < page:
            break
        offset += page
    rows.sort(key=lambda r: (r["year"], r["period"]))
    return rows


def merge_into_macro_monthly(
    series_by_id: dict[str, list[dict]],
    *,
    path: Path = MACRO_MONTHLY_FILE,
) -> dict:
    """Merge EIA series into macro_monthly.json, preserving the rest."""
    if path.exists():
        with open(path) as f:
            payload = json.load(f)
    else:
        payload = {"version": 1, "series": {}, "sources": {},
                   "limitations": []}

    payload.setdefault("series", {}).update(series_by_id)
    payload.setdefault("sources", {})["EIA"] = (
        "https://api.eia.gov/v2/electricity/retail-sales — state monthly "
        "retail electricity price (cents/kWh), Hawaii"
    )
    payload["fetch_date"] = date.today().isoformat()

    note = (
        "EIA_HI_ELEC_* are Hawaii monthly retail electricity prices "
        "(cents/kWh, nominal). Genuine monthly Hawaii-specific price "
        "series — added as the monthly price proxy the causal screen "
        "needed (the genuine Urban Hawaii CPI is bimonthly). Published "
        "with roughly a 3-month lag and revised. EIA_HI_ELEC_SALES_* "
        "are retail SALES VOLUME (million kWh, NOT seasonally adjusted) "
        "from the same endpoint — an activity proxy, distinct from and "
        "not comparable with the price series."
    )
    lims = payload.setdefault("limitations", [])
    if note not in lims:
        lims.append(note)
    return payload


def _parse_args(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-key", default=None,
                   help="EIA key (default: $EIA_API_KEY, then ~/.eia_api_key)")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and report, but do not write.")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print("ERROR: no EIA API key. Set $EIA_API_KEY or write "
              "~/.eia_api_key (free key: https://www.eia.gov/opendata/).",
              file=sys.stderr)
        return 2

    series_by_id: dict[str, list[dict]] = {}
    for metric, prefix in (("price", SERIES_PREFIX), ("sales", SALES_PREFIX)):
        for sector in SECTORS:
            try:
                rows = fetch_hawaii_electricity(api_key, sector, metric=metric)
            except Exception as exc:  # noqa: BLE001 — degrade, don't crash
                print(f"::warning:: EIA {metric} fetch failed for sector "
                      f"{sector}: {exc}", file=sys.stderr)
                continue
            if not rows:
                print(f"::warning:: EIA returned no {metric} rows for sector "
                      f"{sector}", file=sys.stderr)
                continue
            sid = f"{prefix}{sector}"
            series_by_id[sid] = rows
            print(f"  {sid}: {len(rows)} months "
                  f"({rows[0]['year']}-{rows[0]['period']} → "
                  f"{rows[-1]['year']}-{rows[-1]['period']})", flush=True)

    if not series_by_id:
        print("ERROR: nothing fetched; leaving macro_monthly.json alone.",
              file=sys.stderr)
        return 1

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
