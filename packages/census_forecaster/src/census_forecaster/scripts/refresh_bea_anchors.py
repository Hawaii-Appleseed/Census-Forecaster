"""Fetch the three BEA anchor sources and write them as bundled JSON.

The Census Forecaster uses a small set of macro-anchor JSON files at
`data/anchors/` for ACS multi-source anchoring. This script adds three
new BEA-derived anchors:

1. `bea_hi_percapita_income.json` — Hawaii per-capita personal income
   (BEA Regional SAINC1 LineCode=3, GeoFips=15000)
   Anchors B19013 (median household income), S1701 (poverty rate)

2. `bea_honolulu_rpp_all.json` — Urban Honolulu metro RPP all-items
   (BEA Regional MARPP LineCode=1, GeoFips=46520)
   Anchors B19013 as a Honolulu-specific cost-of-living index

3. `bea_hi_rpp_housing.json` — Hawaii state RPP services: rents
   (BEA Regional SARPP LineCode=3, GeoFips=15000)
   Anchors B25058 / B25064 (rent indicators)

Output schema mirrors `data/anchors/pce_deflator.json` exactly so the
existing `AnchorSource.from_file()` loader works unchanged after the
new files are registered in `acs/sources/base.py::_REGISTRY_SPEC`.

Usage
-----
    BEA_API_KEY=<key> python -m census_forecaster.scripts.refresh_bea_anchors
    BEA_API_KEY=<key> python -m census_forecaster.scripts.refresh_bea_anchors --dry-run
    BEA_API_KEY=<key> python -m census_forecaster.scripts.refresh_bea_anchors --verify

Without `BEA_API_KEY`, the script fails fast.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from ..bea.client import BeaApiError, BeaClient

SourceSpec = dict


# Canonical specifications. To extend the list of BEA anchors, append
# to this tuple — the schema downstream consumers expect is whatever
# `AnchorSource.from_file()` reads (compatible with pce_deflator.json).
BEA_ANCHOR_SPECS: tuple[SourceSpec, ...] = (
    {
        "filename": "bea_hi_percapita_income.json",
        "table": "SAINC1",
        "line_code": 3,
        "geo_fips": "15000",
        "geo_label": "State of Hawaii",
        "title": (
            "Per Capita Personal Income, State of Hawaii "
            "(annual, current dollars)"
        ),
        "units": "USD per capita (nominal)",
        "limitations": [
            "State-level only; intra-state variation (Honolulu vs neighbor "
            "islands) is not captured.",
            "Per-capita metric uses all-state population denominator; excludes "
            "commuter income.",
            "Subject to annual revisions in BEA's April and June releases.",
        ],
    },
    {
        "filename": "bea_honolulu_rpp_all.json",
        "table": "MARPP",
        "line_code": 1,
        "geo_fips": "46520",
        "geo_label": "Urban Honolulu, HI MSA",
        "title": (
            "Regional Price Parities: All Items, Urban Honolulu, HI MSA "
            "(annual, U.S. average = 100)"
        ),
        "units": "index, U.S. average = 100",
        "limitations": [
            "MSA boundaries are subject to OMB redefinition; pre-2020 data "
            "may not be fully comparable to post-2020.",
            "RPP weights expenditure shares; differs from CPI's hedonic "
            "smoothing of housing.",
            "Subject to revisions in subsequent year's release.",
        ],
    },
    {
        "filename": "bea_hi_rpp_housing.json",
        "table": "SARPP",
        "line_code": 3,
        "geo_fips": "15000",
        "geo_label": "State of Hawaii",
        "title": (
            "Regional Price Parities: Services — Rents, State of Hawaii "
            "(annual, U.S. average = 100)"
        ),
        "units": "index, U.S. average = 100",
        "limitations": [
            "RPP services-rents component combines actual and imputed rents; "
            "differs from BLS's CPI rent (actual rents only) and HUD FMR "
            "(small-area assessed rents).",
            "State-level aggregation smooths intra-state variation (Honolulu "
            "vs. neighbor islands).",
            "Subject to revisions.",
        ],
    },
)


def _default_anchors_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "anchors"


def _series_id_label(spec: SourceSpec) -> str:
    return (
        f"BEA Regional {spec['table']} LineCode={spec['line_code']}, "
        f"{spec['geo_label']} ({spec['geo_fips']})"
    )


def fetch_anchor(client: BeaClient, spec: SourceSpec) -> dict:
    """Fetch one anchor from BEA and assemble its bundled-JSON payload."""
    rows = client.fetch_regional(
        table_name=spec["table"],
        line_code=spec["line_code"],
        geo_fips=spec["geo_fips"],
        year="ALL",
    )
    if not rows:
        raise BeaApiError(
            f"No data returned for {spec['table']} L={spec['line_code']} "
            f"@{spec['geo_fips']}"
        )
    values_by_year = {str(r["year"]): r["value"] for r in rows}
    payload = {
        "source": "BEA",
        "series_id": _series_id_label(spec),
        "title": spec["title"],
        "frequency": "annual",
        "units": spec["units"],
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": list(spec["limitations"]),
        "values_by_year": values_by_year,
    }
    return payload


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


def verify_schema(client: BeaClient) -> None:
    """Verify that the LineCodes referenced by `BEA_ANCHOR_SPECS` are
    still valid against the live BEA schema.

    BEA occasionally restructures table layouts; running `--verify`
    before a refresh fails fast if a LineCode no longer exists.
    """
    for spec in BEA_ANCHOR_SPECS:
        values = client.get_parameter_values(
            target_parameter="LineCode", table_name=spec["table"],
        )
        keys = {v.get("Key") for v in values}
        if str(spec["line_code"]) not in keys:
            raise BeaApiError(
                f"LineCode {spec['line_code']} not found in {spec['table']}; "
                f"valid: {sorted(keys)}"
            )
        print(f"  OK: {spec['table']} LineCode={spec['line_code']} "
              f"({values[int(spec['line_code']) - 1].get('Desc', '')})",
              file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the three BEA anchors and write to bundled disk.",
    )
    parser.add_argument(
        "--out", type=Path, default=_default_anchors_dir(),
        help="Output directory (default: data/anchors/).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched and exit.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify LineCodes against live BEA schema before fetching.",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("BEA_API_KEY")
    if not api_key:
        print(
            "ERROR: BEA_API_KEY environment variable is not set. "
            "Get a free key at https://apps.bea.gov/api/signup/.",
            file=sys.stderr,
        )
        return 2

    client = BeaClient(api_key=api_key)

    if args.verify:
        print("[bea] verifying LineCodes against BEA schema …", file=sys.stderr)
        try:
            verify_schema(client)
        except BeaApiError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3

    if args.dry_run:
        print("[bea] --dry-run: would fetch:", file=sys.stderr)
        for spec in BEA_ANCHOR_SPECS:
            print(f"  {spec['filename']}: {_series_id_label(spec)}",
                  file=sys.stderr)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for spec in BEA_ANCHOR_SPECS:
        try:
            payload = fetch_anchor(client, spec)
        except BeaApiError as exc:
            print(f"ERROR: failed to fetch {spec['filename']}: {exc}",
                  file=sys.stderr)
            return 4
        n_years = len(payload["values_by_year"])
        years = sorted(payload["values_by_year"].keys())
        print(f"[bea] {spec['filename']}: {n_years} years "
              f"({years[0]}-{years[-1]})", file=sys.stderr)
        _atomic_write_json(args.out / spec["filename"], payload)

    print(f"[bea] wrote {len(BEA_ANCHOR_SPECS)} anchor files to {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
