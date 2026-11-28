"""Fetch Hawaiʻi retail prices for the per-unit excises into ``data/retail_prices.json``.

A per-unit tax (cents a gallon, cents a cigarette) needs the price with tax to
turn an elasticity into a change in the quantity sold.

- **Gasoline and diesel**: DBEDT's "Monthly Energy Data" workbook, sheet
  ``State``, rows "Gasoline - Regular, State" and "Diesel, State", dollars a
  gallon by month from January 2006. The workbook's link changes each month;
  it is found on https://dbedt.hawaii.gov/economic/energy-trends-2/.
- **Cigarettes**: CDC's "The Tax Burden on Tobacco" dataset (Orzechowski and
  Walker), "Average Cost per pack" for Hawaiʻi by year, through its last
  year (2019). https://data.cdc.gov/resource/7nwe-3aj9.json

    python -m state_revenue.scripts.refresh_retail_prices --cache /tmp/prices
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import sys
import tempfile
from pathlib import Path

from state_revenue.scripts.refresh_annual_collections import _fetch

DBEDT_PAGE = "https://dbedt.hawaii.gov/economic/energy-trends-2/"
DBEDT_LINK = re.compile(r'href="([^"]*Monthly_Energy_Data[^"]*\.xlsx)"', re.I)
CDC_URL = "https://data.cdc.gov/resource/7nwe-3aj9.json?locationabbr=HI&$limit=5000"
OUT = Path(__file__).resolve().parent.parent / "data" / "retail_prices.json"
FUEL_ROWS = {"gasoline - regular, state": "gasoline", "diesel, state": "diesel"}


def parse_energy(content: bytes) -> dict[str, dict[str, float]]:
    """``{"gasoline" | "diesel": {"YYYY-MM": dollars a gallon}}`` from the State sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    rows = list(wb["State"].iter_rows(values_only=True))
    header = next(r for r in rows if r and r[0] == "Series")
    months = [h.strftime("%Y-%m") if isinstance(h, (dt.datetime, dt.date)) else None for h in header]
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        key = FUEL_ROWS.get(re.sub(r"\s+", " ", str(r[0] or "")).strip().lower())
        if key:
            out[key] = {m: round(float(v), 4) for m, v in zip(months, r, strict=False)
                        if m and isinstance(v, (int, float))}
    missing = set(FUEL_ROWS.values()) - set(out)
    if missing or any(len(v) < 12 for v in out.values()):
        raise ValueError(f"energy workbook: rows not found or too short: {sorted(missing)}")
    return out


def parse_cigarettes(rows: list[dict]) -> dict[int, float]:
    """``{year: average cost of a pack, dollars}`` from the CDC dataset's Hawaiʻi rows."""
    out = {int(r["year"]): float(r["data_value"]) for r in rows
           if r.get("submeasuredesc") == "Average Cost per pack" and r.get("locationabbr") == "HI"
           and r.get("data_value") not in (None, "")}
    if not out:
        raise ValueError("CDC dataset: no 'Average Cost per pack' rows for Hawaii")
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path)
    args = ap.parse_args(argv)
    cache = args.cache or Path(tempfile.mkdtemp(prefix="prices_"))
    cache.mkdir(parents=True, exist_ok=True)
    links = DBEDT_LINK.findall(_fetch(DBEDT_PAGE).decode("utf-8", "replace"))
    if not links:
        print(f"ERROR: no Monthly_Energy_Data workbook on {DBEDT_PAGE}", file=sys.stderr)
        return 1
    url = links[0]
    f = cache / "Monthly_Energy_Data.xlsx"
    f.write_bytes(_fetch(url))
    fuel = parse_energy(f.read_bytes())
    cigarettes = parse_cigarettes(json.loads(_fetch(CDC_URL)))
    payload = {
        "source": "DBEDT Monthly Energy Data (State sheet: regular gasoline and diesel, $ a gallon); "
                  "CDC, The Tax Burden on Tobacco (average cost per pack, Hawaii)",
        "fetch_date": dt.date.today().isoformat(),
        "reports": [{"url": url}, {"url": CDC_URL}],
        "fuel": fuel,
        "cigarettes_per_pack": {str(y): v for y, v in cigarettes.items()},
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"[prices] gasoline {min(fuel['gasoline'])} to {max(fuel['gasoline'])}; cigarettes "
          f"{min(cigarettes)}-{max(cigarettes)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
