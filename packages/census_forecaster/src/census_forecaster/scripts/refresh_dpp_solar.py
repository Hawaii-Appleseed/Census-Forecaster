"""Fetch Honolulu DPP solar permit aggregates and write them to bundled disk.

Source: City & County of Honolulu open-data portal (Socrata), dataset
``4vab-c87q`` — "Building Permits" master table, 1999→present, refreshed
by DPP. Queries are server-side SoQL aggregations (a few KB transferred),
keyless and anonymous.

Why this exists: the SB 3125 REEC forecast projects residential
renewable-energy credit demand as ``base_2023 × income_growth ×
demand_factor``, where the demand factors are *scenario assumptions*
derived from SEIA national forecasts. DPP solar permits are the physical
Oʻahu counterpart — an empirical check on those assumptions that still
beats the ~2-year DOTAX claim lag, though not by as much as the portal's
update cadence suggests: as of the 2026-07 fetch the dataset was updated
days earlier yet its content window still ends 2025-06-30, i.e. the
observed *publication lag is ~12 months*, not ~1.

Flag choice (verified against the portal 2026-07-30):

* ``solar = 'Y'`` — the maintained flag; ~6-7k residential permits/yr
  recently. Includes solar water heating, which §235-12.5 also credits,
  so it matches the REEC base *better* than a PV-only flag.
* ``solarvpinstallation = 'Y'`` — appears abandoned after ~2017 (the
  portal's dedicated "PV permits" dataset also ends June 2017); carried
  as a secondary column for the historical PV boom shape only.

Usage
-----
    python -m census_forecaster.scripts.refresh_dpp_solar
    python -m census_forecaster.scripts.refresh_dpp_solar --start-year 2005

Output
------
    src/census_forecaster/data/dpp_permits/solar_permits.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import date
from pathlib import Path

SOCRATA_BASE = "https://data.honolulu.gov/resource/4vab-c87q.json"
DATASET_ID = "4vab-c87q"

LIMITATIONS = [
    "Permit issuance is a *leading* proxy for REEC claims: systems are "
    "typically installed within months of permit issuance and credited "
    "in the installation tax year, but slippage and abandonment are not "
    "observed.",
    "estimatedvalueofwork is the contractor-declared job value, not the "
    "credit basis; use year-over-year *ratios*, never levels, and note "
    "declared values are not audited.",
    "Oʻahu only. Honolulu County is ~65-70% of state population and the "
    "dominant share of residential REEC claims, but neighbor-island "
    "demand is invisible here.",
    "The solar='Y' flag includes solar water heating (also REEC-"
    "creditable). The PV-specific solarvpinstallation flag appears "
    "unmaintained after ~2017 and is carried for history only.",
    "Commercial solar is lumpy (single utility-scale permits dominate "
    "years) — mirrors the corporate-REEC volatility noted in the DOTAX "
    "actuals; do not trend it.",
    "The current year is partial through max_issuedate; use the h1 "
    "block for same-window year-over-year comparisons.",
    "Observed publication lag is ~12 months: the portal row-updates "
    "frequently, but the content window trails far behind (2026-07 "
    "fetch → max issuedate 2025-07-01). Check max_issuedate, not "
    "fetch_date, before trusting recency.",
]


def _soql(params: dict) -> list[dict]:
    url = SOCRATA_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_aggregates(start_year: int) -> dict:
    """Run the server-side aggregations and assemble the payload dict."""
    max_row = _soql({"$select": "max(issuedate)"})
    max_issuedate = (max_row[0].get("max_issuedate") or "")[:10]

    # Annual counts + declared value, solar='Y', split res/com.
    annual: dict[str, dict] = {}
    rows = _soql({
        "$select": ("date_extract_y(issuedate) as yr, commercialresidential "
                    "as cls, count(*) as n, sum(estimatedvalueofwork) as val"),
        "$where": f"solar='Y' AND issuedate>='{start_year}-01-01'",
        "$group": "yr, cls",
        "$order": "yr",
        "$limit": "500",
    })
    for r in rows:
        yr = r["yr"]
        cls = (r.get("cls") or "").lower()
        if cls not in ("residential", "commercial"):
            continue
        rec = annual.setdefault(yr, {})
        rec[f"{cls[:3]}_n"] = int(r["n"])
        rec[f"{cls[:3]}_val"] = round(float(r.get("val") or 0.0), 2)

    # Secondary: PV-specific flag, residential only (historical shape).
    rows = _soql({
        "$select": ("date_extract_y(issuedate) as yr, count(*) as n, "
                    "sum(estimatedvalueofwork) as val"),
        "$where": ("solarvpinstallation='Y' AND "
                   "commercialresidential='Residential' AND "
                   f"issuedate>='{start_year}-01-01'"),
        "$group": "yr",
        "$order": "yr",
        "$limit": "500",
    })
    for r in rows:
        rec = annual.setdefault(r["yr"], {})
        rec["pv_res_n"] = int(r["n"])
        rec["pv_res_val"] = round(float(r.get("val") or 0.0), 2)

    # H1 (Jan-Jun) residential series for partial-year comparisons.
    h1: dict[str, dict] = {}
    rows = _soql({
        "$select": ("date_extract_y(issuedate) as yr, count(*) as n, "
                    "sum(estimatedvalueofwork) as val"),
        "$where": ("solar='Y' AND commercialresidential='Residential' AND "
                   f"issuedate>='{start_year}-01-01' AND "
                   "date_extract_m(issuedate) between 1 and 6"),
        "$group": "yr",
        "$order": "yr",
        "$limit": "500",
    })
    for r in rows:
        h1[r["yr"]] = {
            "res_n": int(r["n"]),
            "res_val": round(float(r.get("val") or 0.0), 2),
        }

    return {
        "source": "Honolulu DPP building permits (data.honolulu.gov)",
        "dataset_id": DATASET_ID,
        "fetch_date": date.today().isoformat(),
        "max_issuedate": max_issuedate,
        "units": {"*_n": "permits issued", "*_val": "USD, declared job value"},
        "limitations": LIMITATIONS,
        "annual": {y: annual[y] for y in sorted(annual)},
        "h1_residential": {y: h1[y] for y in sorted(h1)},
    }


def _default_out() -> Path:
    return (Path(__file__).resolve().parent.parent
            / "data" / "dpp_permits" / "solar_permits.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Honolulu DPP solar permit aggregates.")
    parser.add_argument("--out", type=Path, default=_default_out())
    parser.add_argument("--start-year", type=int, default=2005)
    args = parser.parse_args(argv)

    payload = fetch_aggregates(args.start_year)
    n_years = len(payload["annual"])
    if n_years < 10:
        print(f"ERROR: only {n_years} years returned — refusing to overwrite "
              "the bundled file with a suspiciously small payload.",
              file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(args.out)
    print(f"[dpp] wrote {n_years} years (max issuedate "
          f"{payload['max_issuedate']}) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
