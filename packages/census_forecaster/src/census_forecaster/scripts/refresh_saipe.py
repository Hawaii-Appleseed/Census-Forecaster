"""Fetch Census SAIPE county poverty rates as an anchor for S1701_C03_001E.

The Census Small Area Income and Poverty Estimates (SAIPE) program produces
annual model-based estimates of poverty for every county and school district
in the U.S.  SAIPE is the *official* small-area county-level poverty
estimator: it combines the American Community Survey, IRS tax records, SNAP
participation, and decennial Census data via a hierarchical Bayesian model.

For our purposes SAIPE is a near-direct anchor for ACS S1701_C03_001E
(poverty rate of population for whom poverty status is determined): both
report the same conceptual quantity, but SAIPE's 1-year cadence and admin-
data smoothing track inflection years more sharply than the 5-year ACS
rolling window that S1701 is sometimes derived from.

Publication schedule
--------------------
The SAIPE December release of year T+1 publishes estimates for year T.
We treat ``publication_lag_years=0`` because year-T values are visible by
end-of-year T+1, before any ACS forecast for year T+1 would be made.

Output schema matches the county-level convention added in v0.3 Phase A:

::

    {
      "source": "Census",
      "series_id": "SAIPE_county_poverty_rate_all_ages",
      "title": "...",
      "frequency": "annual",
      "units": "rate (percent in poverty)",
      "geography": "county",
      "last_refresh": "YYYY-MM",
      "limitations": [...],
      "values_by_geoid_year": {"15003": {"2010": 9.5, ...}, ...}
    }

Usage
-----

    python -m census_forecaster.scripts.refresh_saipe
    python -m census_forecaster.scripts.refresh_saipe --start-year 2010
    CENSUS_API_KEY=<key> python -m census_forecaster.scripts.refresh_saipe

The geoid scope is read from the bundled calibration panel manifest at
``data/calibration_panel/acs_panel.json`` so the data fetched stays
aligned with the panel used by ``run_acs_calibration``.
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

SAIPE_API_BASE = "https://api.census.gov/data/timeseries/poverty/saipe"


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
# SAIPE fetch
# ---------------------------------------------------------------------------

def fetch_saipe_poverty_rates(
    start_year: int,
    end_year: int,
    geoids: Sequence[str] | None = None,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> dict[str, dict[str, float]]:
    """Fetch annual county poverty rates from the Census SAIPE API.

    Pulls the entire national county panel for [start_year, end_year] and
    optionally filters to the supplied geoid set.  SAIPE's timeseries
    endpoint accepts a multi-year range in one request, so this is a single
    HTTP call rather than per-year fan-out.

    Parameters
    ----------
    start_year, end_year : int
        Inclusive year range.  SAIPE coverage starts in 1995.
    geoids : sequence of str, optional
        5-digit county FIPS codes to retain.  If None, all counties returned
        by the API are kept.
    api_key : str, optional
        Census API key.  Falls back to ``CENSUS_API_KEY`` env var.

    Returns
    -------
    dict[str, dict[str, float]]
        ``{geoid: {year_str: rate, ...}, ...}`` — exactly the shape expected
        for ``values_by_geoid_year``.
    """
    key = api_key or os.environ.get("CENSUS_API_KEY", "")
    # The SAIPE timeseries endpoint expects `time=from YYYY to YYYY` with
    # literal spaces (URL-encoded as `+`).  requests would escape a literal
    # `+` as `%2B` and the API rejects that, so we hand it spaces directly.
    params: dict[str, str] = {
        "get": "SAEPOVRTALL_PT,STATE,COUNTY",
        "for": "county:*",
        "time": f"from {start_year} to {end_year}",
    }
    if key:
        params["key"] = key

    resp = requests.get(SAIPE_API_BASE, params=params, timeout=timeout)
    resp.raise_for_status()
    # The Census API returns HTTP 200 with an HTML error page (not JSON) for
    # certain failures — most commonly a missing/invalid key, which Census now
    # REQUIRES for this endpoint. Detect that and raise an actionable error
    # rather than an opaque JSONDecodeError on resp.json().
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype.lower():
        body = resp.text[:200].strip().replace("\n", " ")
        if "key" in resp.text.lower():
            raise RuntimeError(
                "Census SAIPE API requires an API key (got an HTML 'Missing "
                "Key' page). Set CENSUS_API_KEY (free signup at "
                "https://api.census.gov/data/key_signup.html). "
                f"Response: {body!r}"
            )
        raise RuntimeError(
            f"Census SAIPE API returned non-JSON ({ctype or 'unknown'}): {body!r}"
        )
    rows = resp.json()
    if not rows or len(rows) < 2:
        return {}

    headers = [h.upper() for h in rows[0]]
    idx = {h: i for i, h in enumerate(headers)}

    keep: set[str] | None = set(geoids) if geoids is not None else None
    out: dict[str, dict[str, float]] = {}

    for row in rows[1:]:
        try:
            state = row[idx["STATE"]]
            county = row[idx["COUNTY"]]
            time_val = row[idx["TIME"]]
            rate_raw = row[idx["SAEPOVRTALL_PT"]]
        except (KeyError, IndexError):
            continue
        if rate_raw is None or rate_raw == "":
            continue
        try:
            rate = float(rate_raw)
        except (TypeError, ValueError):
            continue
        if rate <= 0 or rate > 100:
            continue

        geoid = f"{state}{county}".zfill(5)
        if keep is not None and geoid not in keep:
            continue
        year_str = str(int(time_val))
        out.setdefault(geoid, {})[year_str] = rate

    return out


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def build_saipe_payload(
    rates_by_geoid: dict[str, dict[str, float]],
) -> dict:
    """Assemble the SAIPE bundled-anchor JSON payload."""
    return {
        "source": "Census",
        "series_id": "SAIPE_county_poverty_rate_all_ages",
        "title": (
            "Census Small Area Income and Poverty Estimates — county-level "
            "poverty rate, all ages (annual)"
        ),
        "frequency": "annual",
        "units": "rate (percent in poverty, all ages)",
        "geography": "county",
        "aggregation_method": "annual_point_estimate",
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": [
            "SAIPE is a model-based estimate combining ACS, IRS records, "
            "SNAP participation, and decennial Census via hierarchical "
            "Bayesian shrinkage; small-county estimates draw signal from "
            "state-level patterns.",
            "SAIPE poverty rate is for the population for whom poverty "
            "status is determined (excludes group quarters, college "
            "students living in dorms in non-family settings, etc.) — "
            "the same population concept as ACS S1701_C03_001E.",
            "Subject to annual revisions: year-T estimates are released in "
            "December of year T+1 and re-estimated in subsequent vintages.",
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
        description="Fetch Census SAIPE county poverty rates as an anchor.",
    )
    parser.add_argument(
        "--out", type=Path, default=_default_anchors_dir(),
        help="Output directory (default: data/anchors/).",
    )
    parser.add_argument(
        "--start-year", type=int, default=2010,
        help=(
            "SAIPE start year (default: 2010, matches the calibration "
            "panel.  Earlier years are available back to 1995, but the "
            "Census API row limit caps a single fetch at ~15 years × all "
            "U.S. counties; pull older years separately if needed."
        ),
    )
    parser.add_argument(
        "--end-year", type=int, default=date.today().year - 2,
        help=(
            "SAIPE end year (default: current year - 2). The December "
            "release of year T+1 publishes estimates for year T, so on "
            "any date before mid-December the latest available year is "
            "current-year - 2."
        ),
    )
    parser.add_argument(
        "--all-counties", action="store_true",
        help=(
            "Fetch all U.S. counties (default: filter to the bundled "
            "calibration panel's geoid set)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched and exit (no network).",
    )
    args = parser.parse_args(argv)

    geoids = None if args.all_counties else panel_geoids()
    if geoids is not None:
        print(
            f"[saipe] panel covers {len(geoids)} geoids "
            f"({geoids[0]}…{geoids[-1]})",
            file=sys.stderr,
        )
    else:
        print("[saipe] fetching all U.S. counties (no panel filter)",
              file=sys.stderr)

    if args.dry_run:
        print(
            f"[dry-run] would GET {SAIPE_API_BASE} for years "
            f"{args.start_year}–{args.end_year}",
            file=sys.stderr,
        )
        return 0

    args.out.mkdir(parents=True, exist_ok=True)

    print(
        f"[saipe] fetching {args.start_year}–{args.end_year} from "
        f"Census SAIPE API …",
        file=sys.stderr,
    )
    rates = fetch_saipe_poverty_rates(
        start_year=args.start_year,
        end_year=args.end_year,
        geoids=geoids,
    )
    n = len(rates)
    if n == 0:
        print("ERROR: SAIPE returned zero counties; check API access.",
              file=sys.stderr)
        return 1

    sample_geoid = next(iter(rates))
    sample_years = sorted(rates[sample_geoid].keys())
    print(
        f"[saipe] got annual rates for {n} counties; "
        f"sample {sample_geoid}: {len(sample_years)} years "
        f"({sample_years[0]}–{sample_years[-1]})",
        file=sys.stderr,
    )

    payload = build_saipe_payload(rates)
    _atomic_write_json(args.out / "saipe_poverty.json", payload)
    print(
        f"[saipe] wrote {args.out / 'saipe_poverty.json'} ({n} counties)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
