"""Fetch the v3 multi-MSA BLS CPI panel and write it to bundled disk.

Cross-product of `BLS_PANEL_AREAS × BLS_PANEL_SERIES = 11 × 5 = 55 series`,
spanning 2010 to the current year. Output is a single JSON file that
the calibration generator (`bls/calibration.py`) reads at run time.

Why a one-shot bundled file vs lazy fetching:
* The full panel is ~9,000 observations totalling <1 MB on disk —
  trivially shippable inside the package.
* Calibration runs need every (series, anchor, h) fold; lazy fetching
  would issue ~2,000 calls during one calibration. The BLS API rate-
  limits at 500/day with a key, 25 without — bundled is the only
  feasible path for CI auto-refresh.
* Determinism: a calibration regenerated against an offline checkpoint
  produces identical κ values, which matters for reproducibility.

Usage
-----
    BLS_API_KEY=<your_key> python -m census_forecaster.scripts.refresh_bls_panel
    BLS_API_KEY=<your_key> python -m census_forecaster.scripts.refresh_bls_panel \
        --start-year 2010 --end-year 2025

Without `BLS_API_KEY`, the script fails fast — ~55 series across 15
years requires ~110 API calls (BLS allows max 50 series and 20-year
range per request, so we batch). At 25 calls/day unauthenticated, this
would take 4-5 days. With a key (500/day), it runs in ~15 seconds.

Output
------
    src/census_forecaster/data/bls_panel/cpi_panel.json — flat JSON of
    {series_id: [{year, period, value}, …], …}.
    src/census_forecaster/data/bls_panel/manifest.json — fetch timestamps,
    series→area metadata, ranges per series.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from ..bls.client import fetch_cpi_data
from ..bls.panel import (
    BLS_PANEL_AREAS,
    BLS_PANEL_SERIES,
    all_panel_series_ids,
    parse_series_id,
)

# BLS API limits per request:
#   - Up to 50 series IDs
#   - Up to 20 years of history
# We batch series and split year ranges if needed.
MAX_SERIES_PER_BATCH: int = 50
MAX_YEARS_PER_BATCH: int = 20
SLEEP_BETWEEN_BATCHES: float = 1.0  # seconds — well below BLS rate limit


def _default_panel_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "bls_panel"


def fetch_full_panel(
    api_key: str,
    start_year: int,
    end_year: int,
    series_ids: Sequence[str] | None = None,
    verbose: bool = True,
) -> dict[str, list[dict]]:
    """Batch-fetch the full panel, splitting by series count and year span."""
    if series_ids is None:
        series_ids = all_panel_series_ids()
    series_ids = list(series_ids)

    out: dict[str, list[dict]] = {}

    # Year batching: BLS allows ≤20 years per call. Most calibration runs
    # span 2010–current = 16 years, so usually 1 batch; if expanded we
    # split into chunks of 20.
    year_chunks: list[tuple[int, int]] = []
    cursor = start_year
    while cursor <= end_year:
        chunk_end = min(cursor + MAX_YEARS_PER_BATCH - 1, end_year)
        year_chunks.append((cursor, chunk_end))
        cursor = chunk_end + 1

    total_calls = (
        ((len(series_ids) + MAX_SERIES_PER_BATCH - 1) // MAX_SERIES_PER_BATCH)
        * len(year_chunks)
    )
    call_idx = 0

    for ystart, yend in year_chunks:
        for batch_start in range(0, len(series_ids), MAX_SERIES_PER_BATCH):
            batch = series_ids[batch_start: batch_start + MAX_SERIES_PER_BATCH]
            call_idx += 1
            if verbose:
                print(f"[panel] [{call_idx}/{total_calls}] {len(batch)} series, "
                      f"{ystart}-{yend}", file=sys.stderr)
            data = fetch_cpi_data(
                series_ids=batch,
                start_year=ystart,
                end_year=yend,
                api_key=api_key,
            )
            for sid, points in data.items():
                if sid not in out:
                    out[sid] = []
                out[sid].extend(points)
            time.sleep(SLEEP_BETWEEN_BATCHES)

    # Dedupe + sort each series
    for sid, points in out.items():
        seen: set[tuple[int, str]] = set()
        deduped: list[dict] = []
        for p in points:
            key = (p["year"], p["period"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(p)
        deduped.sort(key=lambda p: (p["year"], p["period"]))
        out[sid] = deduped

    return out


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


def write_panel_files(
    panel_dir: Path,
    panel_data: dict[str, list[dict]],
    start_year: int,
    end_year: int,
) -> None:
    """Persist the panel JSON + a per-series manifest to `panel_dir`."""
    panel_path = panel_dir / "cpi_panel.json"
    _atomic_write_json(panel_path, {
        "version": 1,
        "fetch_date": date.today().isoformat(),
        "year_range": [start_year, end_year],
        "n_series": len(panel_data),
        "series": panel_data,
    })

    manifest = {
        "version": 1,
        "fetch_date": date.today().isoformat(),
        "year_range": [start_year, end_year],
        "areas": [
            {
                "code": a.code, "name": a.name,
                "region": a.region, "size_class": a.size_class,
                "headline_frequency": a.headline_frequency,
            }
            for a in BLS_PANEL_AREAS
        ],
        "series_kinds": [
            {
                "suffix": k.suffix, "label": k.label,
                "description": k.description,
            }
            for k in BLS_PANEL_SERIES
        ],
        "series_summary": {
            sid: {
                "n_points": len(pts),
                "first": (pts[0]["year"], pts[0]["period"]) if pts else None,
                "last": (pts[-1]["year"], pts[-1]["period"]) if pts else None,
                "label": (
                    f"{parse_series_id(sid)[0].name} — {parse_series_id(sid)[1].label}"
                    if parse_series_id(sid) is not None else "unparsable"
                ),
            }
            for sid, pts in panel_data.items()
        },
    }
    _atomic_write_json(panel_dir / "manifest.json", manifest)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the multi-MSA BLS CPI panel and write to bundled disk.",
    )
    parser.add_argument("--out", type=Path, default=_default_panel_dir())
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve series list and exit without fetching.")
    parser.add_argument("--single-area", type=str, default=None,
                        help="Restrict the fetch to one area code (e.g. S49A).")
    args = parser.parse_args(argv)

    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        print(
            "ERROR: BLS_API_KEY environment variable is not set. "
            "A BLS API key is required (~110 calls). Get one free at "
            "https://www.bls.gov/developers/api_signature_v2.htm",
            file=sys.stderr,
        )
        return 2

    if args.single_area:
        from ..bls.panel import panel_series_for_area
        series_ids = panel_series_for_area(args.single_area)
    else:
        series_ids = all_panel_series_ids()

    print(f"[main] panel build: {len(series_ids)} series, "
          f"{args.start_year}-{args.end_year}", file=sys.stderr)

    if args.dry_run:
        print("[main] --dry-run: resolved series:", file=sys.stderr)
        for sid in series_ids:
            print(f"  {sid}", file=sys.stderr)
        return 0

    panel_data = fetch_full_panel(
        api_key=api_key,
        start_year=args.start_year,
        end_year=args.end_year,
        series_ids=series_ids,
    )
    print(f"[main] fetched {sum(len(p) for p in panel_data.values())} "
          f"observations across {len(panel_data)} series", file=sys.stderr)

    write_panel_files(
        panel_dir=args.out,
        panel_data=panel_data,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    print(f"[main] wrote panel to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
