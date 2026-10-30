"""Refresh county-level building permit counts from Census BPS.

Annual county-level totals downloaded from:
  https://www2.census.gov/econ/bps/County/co{year}a.txt

File format (2 header rows, then data):
  Row 0: "Survey,FIPS,FIPS,...,1-unit,,,2-units,,,3-4 units,,,5+ units,..."
  Row 1: "Date,State,County,...,Bldgs,Units,Value,Bldgs,Units,Value,..."
  Data:   year,state_fips_2,county_fips_3,...,bldgs,UNITS,value,bldgs,UNITS,value,...

We sum the Units columns (indices 7, 10, 13, 16) across the four unit types
(1-unit, 2-units, 3-4 units, 5+ units) to get total permitted residential units.

Usage
-----
    python -m census_forecaster.scripts.refresh_bps_permits
    python -m census_forecaster.scripts.refresh_bps_permits --start-year 2008 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data" / "leading_indicators"
_OUT_FILE = _DATA_DIR / "bps_permits.json"
_BPS_URL_TMPL = "https://www2.census.gov/econ/bps/County/co{year}a.txt"

# Column indices for "Units" in each of the four housing-type blocks.
# Layout: ...,Bldgs,Units,Value (repeated 4×, starting at col 6).
# Col 6=1-unit bldgs, Col 7=1-unit units, Col 8=1-unit value, ...
_UNIT_COL_INDICES = (7, 10, 13, 16)


def _panel_geoids() -> list[str]:
    """Return the 90 geoids from the ACS calibration panel."""
    panel_file = (
        Path(__file__).parent.parent / "data" / "calibration_panel" / "acs_panel.json"
    )
    if not panel_file.exists():
        raise FileNotFoundError(f"ACS panel not found at {panel_file}")
    with open(panel_file) as f:
        panel = json.load(f)
    geoids: set[str] = set()
    for item in panel.get("observations", []):
        g = item.get("geoid", "")
        if len(g) == 5:
            geoids.add(g)
    return sorted(geoids)


def _fetch_url(url: str, retries: int = 3, backoff: float = 2.0) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "census-forecaster/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError("unreachable")


def _parse_bps_csv(raw: bytes, geoid_set: set[str]) -> dict[str, int]:
    """Parse one BPS county annual file; return {geoid: total_units}.

    Handles the 2-header-row format used since ≥2004:
      Row 0: multi-row column group headers ("1-unit", "2-units", …)
      Row 1: sub-headers ("State", "County", "Bldgs", "Units", "Value", …)
      Row 2+: data rows (year, 2-digit state FIPS, 3-digit county FIPS, …)
    """
    text = raw.decode("latin-1", errors="replace")
    lines = text.splitlines()

    # Skip blank leading lines; find first line that looks like a header.
    data_lines: list[str] = []
    found_header = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if found_header:
                data_lines.append(line)
            continue
        # Header detection: second header row starts with "Date" or similar
        # non-numeric token; data rows start with the year (4-digit number).
        if not found_header:
            # Look for lines containing state/county FIPS indicator words.
            low = stripped.lower()
            if "state" in low or "date" in low or "survey" in low:
                found_header = True
                data_lines.append(line)  # may be header row 1 or row 0
                continue
        else:
            data_lines.append(line)

    if not data_lines:
        return {}

    reader = csv.reader(io.StringIO("\n".join(data_lines)))
    result: dict[str, int] = {}
    header_rows_seen = 0

    for row in reader:
        if not row:
            continue
        # The first 2 rows are headers; skip them.
        if header_rows_seen < 2:
            header_rows_seen += 1
            continue

        # Data row: col 1 = 2-digit state FIPS, col 2 = 3-digit county FIPS.
        try:
            state = row[1].strip().zfill(2)
            county = row[2].strip().zfill(3)
        except IndexError:
            continue

        if len(state) != 2 or not state.isdigit():
            continue
        if len(county) != 3 or not county.isdigit():
            continue

        geoid = state + county
        if geoid not in geoid_set:
            continue

        total = 0
        for idx in _UNIT_COL_INDICES:
            try:
                val = row[idx].strip().replace(",", "")
                if val:
                    total += int(val)
            except (IndexError, ValueError):
                pass

        result[geoid] = result.get(geoid, 0) + total

    return result


def fetch_bps_year(year: int, geoid_set: set[str]) -> dict[str, int]:
    url = _BPS_URL_TMPL.format(year=year)
    raw = _fetch_url(url)
    return _parse_bps_csv(raw, geoid_set)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh county BPS permit counts from Census BPS CSVs.",
    )
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse but do not write output file.",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out_path: Path = args.out or _OUT_FILE

    try:
        geoids = _panel_geoids()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    geoid_set = set(geoids)
    print(f"[bps] panel covers {len(geoids)} geoids", file=sys.stderr)

    values_by_geoid_year: dict[str, dict[str, int]] = {g: {} for g in geoids}
    years_ok: list[int] = []
    years_fail: list[int] = []

    for year in range(args.start_year, args.end_year + 1):
        try:
            counts = fetch_bps_year(year, geoid_set)
            for geoid, total in counts.items():
                values_by_geoid_year[geoid][str(year)] = total
            years_ok.append(year)
            n_covered = sum(1 for g in geoids if str(year) in values_by_geoid_year[g])
            print(f"[bps] {year}: {n_covered}/{len(geoids)} counties", file=sys.stderr)
        except Exception as exc:
            print(f"[bps] {year}: FAILED — {exc}", file=sys.stderr)
            years_fail.append(year)

    print(
        f"[bps] fetched {len(years_ok)} years "
        f"({min(years_ok) if years_ok else '?'}–{max(years_ok) if years_ok else '?'}), "
        f"{len(years_fail)} failed",
        file=sys.stderr,
    )

    if args.dry_run:
        sample = {
            g: dict(list(d.items())[-3:])
            for g, d in list(values_by_geoid_year.items())[:3]
        }
        print(f"[bps] dry-run sample: {sample}", file=sys.stderr)
        return 0

    payload = {
        "source": "Census Building Permits Survey",
        "series_id": "BPS_County_Annual_TotalUnits",
        "title": "Building Permits Survey — county annual total residential units",
        "frequency": "annual",
        "units": "residential units authorized",
        "geography": "county",
        "notes": (
            "Sum of 1-unit, 2-units, 3-4 units, and 5+ units from Census BPS "
            "county annual files. Used as 18-24 month leading indicator for "
            "vacancy rate, in-migration, and poverty in ML feature set."
        ),
        "values_by_geoid_year": values_by_geoid_year,
    }

    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out_path)
    size_kb = out_path.stat().st_size // 1024
    print(f"[bps] wrote {out_path} ({size_kb} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
