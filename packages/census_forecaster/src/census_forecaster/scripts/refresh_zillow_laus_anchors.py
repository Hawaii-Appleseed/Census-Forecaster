"""Fetch Zillow ZHVI/ZORI and BLS LAUS as county-level anchor JSONs.

Phase A of the v0.3 roadmap adds three direct, free, monthly-cadence
public datasets as macro anchors at *county* granularity:

1. ``zillow_zhvi.json`` — Zillow Home Value Index, per county
   (anchors B25077_001E median home value)
2. ``zillow_zori.json`` — Zillow Observed Rent Index, per county
   (anchors B25058_001E gross rent and B25064_001E contract rent)
3. ``bls_laus.json`` — BLS Local Area Unemployment Statistics, county
   unemployment rate (anchors S2301_C04_001E)

Output schema matches the county-level convention added in Phase A:

::

    {
      "source": "Zillow|BLS",
      "series_id": "ZHVI_AllHomes_..." | "LAUS_unemployment_rate",
      "title": "...",
      "frequency": "monthly_aggregated_to_annual",
      "units": "USD" | "rate",
      "geography": "county",
      "last_refresh": "YYYY-MM",
      "limitations": [...],
      "values_by_geoid_year": {"15003": {"2010": ..., ...}, ...}
    }

Usage
-----

    # Zillow only (no API key needed):
    python -m census_forecaster.scripts.refresh_zillow_laus_anchors --skip-laus

    # LAUS only (requires BLS_API_KEY):
    BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_zillow_laus_anchors \\
        --skip-zillow

    # Everything (default):
    BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_zillow_laus_anchors

    # Dry-run (no network):
    python -m census_forecaster.scripts.refresh_zillow_laus_anchors --dry-run

The geoid scope is read from the bundled calibration panel manifest at
``data/calibration_panel/acs_panel.json`` so the data fetched stays
aligned with the panel used by ``run_acs_calibration``.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import requests

from ..bls.client import fetch_cpi_data

# ---------------------------------------------------------------------------
# Zillow public CSV endpoints
# ---------------------------------------------------------------------------
# Smoothed, seasonally-adjusted, all-homes (SFR + condo) tier 33-67 percentile.
# This is Zillow's flagship ZHVI series. Updated monthly, ~10th of next month.
ZHVI_COUNTY_CSV = (
    "https://files.zillowstatic.com/research/public_csvs/zhvi/"
    "County_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
)

# Smoothed, all-homes-plus-multifamily, county-level. ZORI starts in 2015.
ZORI_COUNTY_CSV = (
    "https://files.zillowstatic.com/research/public_csvs/zori/"
    "County_zori_uc_sfrcondomfr_sm_month.csv"
)


# ---------------------------------------------------------------------------
# BLS LAUS series-ID convention
# ---------------------------------------------------------------------------
# LAUCN{5-digit FIPS}0000000003 — measure code 03 = unemployment rate.
# LAUS releases monthly; M13 is the annual-average period code.
def laus_series_id(geoid: str) -> str:
    """Build the BLS LAUS unemployment-rate series ID for a county."""
    if len(geoid) != 5 or not geoid.isdigit():
        raise ValueError(f"geoid must be 5-digit county FIPS, got {geoid!r}")
    return f"LAUCN{geoid}0000000003"


# ---------------------------------------------------------------------------
# Geoid scope (read from calibration panel manifest)
# ---------------------------------------------------------------------------

def panel_geoids() -> tuple[str, ...]:
    """Return the sorted set of geoids covered by the bundled calibration panel."""
    panel_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "calibration_panel" / "acs_panel.json"
    )
    with open(panel_path) as f:
        panel = json.load(f)
    geoids = sorted({obs["geoid"] for obs in panel.get("observations", [])})
    return tuple(geoids)


# ---------------------------------------------------------------------------
# Zillow ingest
# ---------------------------------------------------------------------------

def _zillow_geoid_from_row(row: dict) -> str | None:
    """Combine Zillow's 2-digit StateCodeFIPS + 3-digit MunicipalCodeFIPS."""
    state_fips = (row.get("StateCodeFIPS") or "").strip()
    muni_fips = (row.get("MunicipalCodeFIPS") or "").strip()
    if not state_fips or not muni_fips:
        return None
    # Zillow encodes both as integers without leading zeros in some files.
    return f"{int(state_fips):02d}{int(muni_fips):03d}"


def _aggregate_monthly_to_annual(
    monthly_values: dict[str, float],
    method: str = "december",
) -> dict[str, float]:
    """Reduce {YYYY-MM-DD: value} to {YYYY: value}.

    `method`:
      - "december": value at YYYY-12 (most stable end-of-year snapshot;
        matches how ACS 1y estimates anchor to year-Y).
      - "annual_mean": mean of all months in YYYY (smoother; less
        sensitive to a single noisy print).
    """
    by_year: dict[str, list[tuple[str, float]]] = {}
    for date_str, val in monthly_values.items():
        if not isinstance(val, (int, float)):
            continue
        if val != val:  # NaN
            continue
        year = date_str[:4]
        if not (year.isdigit() and len(year) == 4):
            continue
        by_year.setdefault(year, []).append((date_str, float(val)))

    out: dict[str, float] = {}
    for year, months in by_year.items():
        months.sort()
        if method == "december":
            dec = next((v for d, v in months if d[5:7] == "12"), None)
            chosen = dec if dec is not None else months[-1][1]
        elif method == "annual_mean":
            chosen = sum(v for _, v in months) / len(months)
        else:
            raise ValueError(f"unknown aggregation method: {method!r}")
        out[year] = chosen
    return out


def fetch_zillow_county_csv(
    url: str, *, timeout: float = 60.0
) -> dict[str, dict[str, float]]:
    """Download a Zillow county-level CSV and return {geoid: {YYYY-MM-DD: value}}.

    The CSV layout: a few descriptor columns (RegionID, RegionName, StateCodeFIPS,
    MunicipalCodeFIPS, …) followed by one column per month (YYYY-MM-DD or YYYY-MM).
    Empty cells are dropped; the rest are coerced to float.
    """
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    descriptor_cols = {
        "RegionID", "SizeRank", "RegionName", "RegionType",
        "StateName", "State", "Metro", "StateCodeFIPS", "MunicipalCodeFIPS",
    }
    out: dict[str, dict[str, float]] = {}
    for row in reader:
        geoid = _zillow_geoid_from_row(row)
        if geoid is None:
            continue
        per_month: dict[str, float] = {}
        for col, val in row.items():
            if col in descriptor_cols or not col:
                continue
            if val is None or val == "":
                continue
            try:
                per_month[col] = float(val)
            except ValueError:
                continue
        if per_month:
            out[geoid] = per_month
    return out


def build_zillow_payload(
    monthly_by_geoid: dict[str, dict[str, float]],
    geoids: Sequence[str],
    *,
    source_label: str,
    series_id: str,
    title: str,
    units: str,
    limitations: Sequence[str],
    aggregation: str = "december",
) -> dict:
    """Assemble the bundled-anchor JSON payload for one Zillow series."""
    values_by_geoid_year: dict[str, dict[str, float]] = {}
    for geoid in geoids:
        monthly = monthly_by_geoid.get(geoid)
        if not monthly:
            continue
        annual = _aggregate_monthly_to_annual(monthly, method=aggregation)
        if annual:
            values_by_geoid_year[geoid] = annual
    return {
        "source": source_label,
        "series_id": series_id,
        "title": title,
        "frequency": "monthly_aggregated_to_annual",
        "units": units,
        "geography": "county",
        "aggregation_method": aggregation,
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": list(limitations),
        "values_by_geoid_year": values_by_geoid_year,
    }


# ---------------------------------------------------------------------------
# BLS LAUS ingest
# ---------------------------------------------------------------------------

def fetch_laus_unemployment_rates(
    geoids: Sequence[str],
    *,
    start_year: int,
    end_year: int,
    api_key: str,
    batch_size: int = 50,
    year_chunk: int = 20,
    sleep_between: float = 1.0,
) -> dict[str, dict[str, float]]:
    """Pull annual-average unemployment rates per county via BLS LAUS.

    Returns {geoid: {YYYY: rate}}. ``rate`` is the unemployment rate in
    percent (e.g. 4.2). Aggregates from monthly observations: when BLS
    publishes M13 (annual average) we use that; otherwise we average
    M01–M12 ourselves so years near the leading edge (only a few months
    available) still produce a partial-year value.
    """
    import time

    series_to_geoid = {laus_series_id(g): g for g in geoids}
    all_series_ids = list(series_to_geoid.keys())

    # Year batching mirrors refresh_bls_panel.py — BLS allows ≤20 years/req.
    year_ranges: list[tuple[int, int]] = []
    s = start_year
    while s <= end_year:
        e = min(s + year_chunk - 1, end_year)
        year_ranges.append((s, e))
        s = e + 1

    raw_by_series: dict[str, list[dict]] = {sid: [] for sid in all_series_ids}
    for ys, ye in year_ranges:
        for i in range(0, len(all_series_ids), batch_size):
            chunk = all_series_ids[i:i + batch_size]
            data = fetch_cpi_data(
                series_ids=chunk,
                start_year=ys,
                end_year=ye,
                api_key=api_key,
            )
            for sid, points in data.items():
                raw_by_series.setdefault(sid, []).extend(points)
            time.sleep(sleep_between)

    out: dict[str, dict[str, float]] = {}
    for sid, points in raw_by_series.items():
        geoid = series_to_geoid[sid]
        annual_avg: dict[str, float] = {}
        monthly_by_year: dict[str, list[float]] = {}
        for p in points:
            year = str(p["year"])
            period = p["period"]
            val = p["value"]
            if period == "M13":  # BLS annual-average period
                annual_avg[year] = float(val)
            elif period.startswith("M") and period != "M13":
                monthly_by_year.setdefault(year, []).append(float(val))
        # Fill any year missing M13 with the mean of its monthly prints.
        for year, monthly_vals in monthly_by_year.items():
            if year not in annual_avg and monthly_vals:
                annual_avg[year] = sum(monthly_vals) / len(monthly_vals)
        if annual_avg:
            out[geoid] = annual_avg
    return out


def build_laus_payload(
    rates_by_geoid: dict[str, dict[str, float]],
) -> dict:
    """Assemble the BLS LAUS bundled-anchor JSON payload."""
    return {
        "source": "BLS",
        "series_id": "LAUS_county_unemployment_rate",
        "title": (
            "BLS Local Area Unemployment Statistics — county-level "
            "unemployment rate (annual average)"
        ),
        "frequency": "monthly_aggregated_to_annual",
        "units": "rate (percent unemployed of labor force)",
        "geography": "county",
        "aggregation_method": "annual_average",
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": [
            "LAUS is a model-based estimate combining CPS, CES, and UI claims; "
            "small-county estimates draw signal from state-level CPS via the "
            "Handbook method.",
            "LAUS unemployment rate is computed against the *labor force*; "
            "ACS S2301_C04 reports it against the population 16+, so the "
            "two are correlated but not identical without a participation-"
            "rate adjustment. The forecaster uses LAUS as a YoY *change* "
            "anchor, which is robust to the level offset.",
            "Subject to annual benchmark revisions in March of year+1.",
        ],
        "values_by_geoid_year": rates_by_geoid,
    }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _default_anchors_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "anchors"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Zillow ZHVI/ZORI and BLS LAUS as county-level anchors.",
    )
    parser.add_argument(
        "--out", type=Path, default=_default_anchors_dir(),
        help="Output directory (default: data/anchors/).",
    )
    parser.add_argument(
        "--start-year", type=int, default=2010,
        help="LAUS start year (Zillow returns its full history regardless).",
    )
    parser.add_argument(
        "--end-year", type=int, default=date.today().year,
        help="LAUS end year (default: current year).",
    )
    parser.add_argument(
        "--skip-zillow", action="store_true", help="Skip ZHVI + ZORI fetch.",
    )
    parser.add_argument(
        "--skip-laus", action="store_true", help="Skip BLS LAUS fetch.",
    )
    parser.add_argument(
        "--aggregation", choices=("december", "annual_mean"),
        default="december",
        help=(
            "How to roll Zillow monthly values to annual. 'december' "
            "(default) picks the Dec snapshot; 'annual_mean' averages "
            "all months in the year."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched and exit (no network).",
    )
    args = parser.parse_args(argv)

    geoids = panel_geoids()
    print(f"[refresh] panel covers {len(geoids)} geoids "
          f"({geoids[0]}…{geoids[-1]})", file=sys.stderr)

    if args.dry_run:
        if not args.skip_zillow:
            print(f"[dry-run] would GET {ZHVI_COUNTY_CSV}", file=sys.stderr)
            print(f"[dry-run] would GET {ZORI_COUNTY_CSV}", file=sys.stderr)
        if not args.skip_laus:
            print(
                f"[dry-run] would issue BLS LAUS series fetch for "
                f"{len(geoids)} counties × ({args.start_year}-{args.end_year})",
                file=sys.stderr,
            )
            sample = laus_series_id(geoids[0])
            print(f"[dry-run] sample series_id: {sample}", file=sys.stderr)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)

    # ---- Zillow ZHVI ----
    if not args.skip_zillow:
        print(f"[zillow] fetching ZHVI from {ZHVI_COUNTY_CSV} …", file=sys.stderr)
        zhvi_monthly = fetch_zillow_county_csv(ZHVI_COUNTY_CSV)
        print(f"[zillow] ZHVI: {len(zhvi_monthly)} counties returned, "
              f"{sum(1 for g in geoids if g in zhvi_monthly)} match panel",
              file=sys.stderr)
        zhvi_payload = build_zillow_payload(
            zhvi_monthly, geoids,
            source_label="Zillow",
            series_id=(
                "ZHVI_AllHomes_SmoothedSeasonallyAdjusted_"
                "tier_0.33_0.67_County"
            ),
            title=(
                "Zillow Home Value Index — county, all homes (SFR+condo), "
                "smoothed & seasonally adjusted, 33–67th percentile tier"
            ),
            units="USD (typical home value)",
            limitations=[
                "ZHVI is a hedonic estimate of typical home value; not a "
                "transaction-weighted price. Differs from ACS B25077 "
                "median *owner-reported* value by ~10-30% level offset; "
                "used here as a YoY *change* anchor where the offset cancels.",
                "Coverage gaps in low-population rural counties; missing "
                "geoids fall back to ACS-only models without anchor support.",
                "Subject to monthly revisions; final value at year+1 release.",
            ],
            aggregation=args.aggregation,
        )
        n_z = len(zhvi_payload["values_by_geoid_year"])
        _atomic_write_json(args.out / "zillow_zhvi.json", zhvi_payload)
        print(f"[zillow] wrote zillow_zhvi.json ({n_z} counties)", file=sys.stderr)

        # ---- Zillow ZORI ----
        print(f"[zillow] fetching ZORI from {ZORI_COUNTY_CSV} …", file=sys.stderr)
        zori_monthly = fetch_zillow_county_csv(ZORI_COUNTY_CSV)
        print(f"[zillow] ZORI: {len(zori_monthly)} counties returned, "
              f"{sum(1 for g in geoids if g in zori_monthly)} match panel",
              file=sys.stderr)
        zori_payload = build_zillow_payload(
            zori_monthly, geoids,
            source_label="Zillow",
            series_id="ZORI_SFRCondoMFR_Smoothed_County",
            title=(
                "Zillow Observed Rent Index — county, all rental types "
                "(SFR+condo+multifamily), smoothed"
            ),
            units="USD (typical asking rent per month)",
            limitations=[
                "ZORI uses asking rents from listings; tilts toward newly-"
                "vacant units, not in-place tenants. ACS B25058/B25064 "
                "captures occupied-unit rents. Used as a YoY *change* anchor.",
                "ZORI history starts 2015; pre-2015 years have no county "
                "values.",
                "Coverage gaps in low-listing-volume rural counties.",
                "Subject to monthly revisions.",
            ],
            aggregation=args.aggregation,
        )
        n_r = len(zori_payload["values_by_geoid_year"])
        _atomic_write_json(args.out / "zillow_zori.json", zori_payload)
        print(f"[zillow] wrote zillow_zori.json ({n_r} counties)", file=sys.stderr)

    # ---- BLS LAUS ----
    if not args.skip_laus:
        api_key = os.environ.get("BLS_API_KEY")
        if not api_key:
            print(
                "ERROR: BLS_API_KEY environment variable is not set. "
                "Get a free key at https://www.bls.gov/developers/.",
                file=sys.stderr,
            )
            return 2
        print(f"[laus] fetching {len(geoids)} counties × "
              f"({args.start_year}-{args.end_year}) from BLS …",
              file=sys.stderr)
        laus_rates = fetch_laus_unemployment_rates(
            geoids,
            start_year=args.start_year,
            end_year=args.end_year,
            api_key=api_key,
        )
        n_l = len(laus_rates)
        print(f"[laus] got annual rates for {n_l} counties", file=sys.stderr)
        laus_payload = build_laus_payload(laus_rates)
        _atomic_write_json(args.out / "bls_laus.json", laus_payload)
        print(f"[laus] wrote bls_laus.json ({n_l} counties)", file=sys.stderr)

    print(f"[refresh] done; output dir: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
