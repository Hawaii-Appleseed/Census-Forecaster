"""BLS public API client.

Wraps the BLS Public Data API (v2) for time-series fetches. Returns the
raw {year, period, value} dicts that the projection module consumes.

Caching is the caller's responsibility — this module just does HTTP +
parse. For an opinionated cache see `census_forecaster.bls.cache`.

API reference
-------------
https://www.bls.gov/developers/api_signature_v2.htm
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def fetch_cpi_data(
    series_ids: list[str],
    start_year: int | None = None,
    end_year: int | None = None,
    api_key: str | None = None,
    timeout: float = 30,
) -> dict:
    """Fetch one or more BLS time series from the public API.

    Returns dict mapping series_id -> list of {year, period, value} dicts,
    sorted chronologically (BLS returns newest-first).

    `period` follows BLS conventions: "M01"-"M12" for monthly series,
    "S01"/"S02" for semi-annual, "A01" for annual, "Q01"-"Q05" for
    quarterly. The `value` is float; missing prints (BLS sentinel "-")
    are dropped.

    `api_key` falls back to `BLS_API_KEY` env var. Without a key the
    public API allows ~25 requests/day; with a free registered key, 500.
    """
    if api_key is None:
        api_key = os.environ.get("BLS_API_KEY", "")

    if start_year is None:
        start_year = date.today().year - 1
    if end_year is None:
        end_year = date.today().year

    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    if api_key:
        payload["registrationkey"] = api_key

    headers = {"Content-Type": "application/json"}
    resp = requests.post(BLS_API_URL, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API error: {data.get('message', data)}")

    results = {}
    for series in data.get("Results", {}).get("series", []):
        sid = series["seriesID"]
        points = []
        for obs in series.get("data", []):
            raw_val = obs.get("value", "-")
            if raw_val == "-":
                continue  # BLS sentinel for missing/preliminary
            points.append({
                "year": int(obs["year"]),
                "period": obs["period"],
                "value": float(raw_val),
            })
        points.sort(key=lambda p: (p["year"], p["period"]))
        results[sid] = points

    return results


# ---------------------------------------------------------------------------
# Period helpers (cadence-agnostic)
# ---------------------------------------------------------------------------

def get_value(cpi_data: dict, series_id: str, year: int, period: str) -> float | None:
    """Look up a specific (year, period) in fetched data."""
    points = cpi_data.get(series_id, [])
    for p in points:
        if p["year"] == year and p["period"] == period:
            return p["value"]
    return None


def get_latest(cpi_data: dict, series_id: str) -> dict | None:
    """Most recent observation for a series."""
    points = cpi_data.get(series_id, [])
    return points[-1] if points else None


def date_to_bls_period(d: date) -> tuple[int, str]:
    """Convert a date to BLS year + monthly period (M01-M12)."""
    return d.year, f"M{d.month:02d}"


def find_nearest_periods(
    cpi_data: dict, series_id: str, target_year: int, target_month: int,
) -> tuple[dict | None, dict | None]:
    """Locate observations bracketing a target month.

    Returns (before, after) such that:
      - If an exact (target_year, M{target_month:02d}) print exists,
        returns (that_point, None).
      - Else returns the most recent point ≤ target and the earliest
        point > target. Either may be None at the series boundaries.

    Useful for both interpolation (when both are present) and forward
    projection (when only `before` exists).
    """
    points = cpi_data.get(series_id, [])
    if not points:
        return None, None

    target_period = f"M{target_month:02d}"
    target_val = target_year * 12 + target_month

    # Exact match short-circuit.
    for p in points:
        if p["year"] == target_year and p["period"] == target_period:
            return p, None

    before, after = None, None
    for p in points:
        if not p["period"].startswith("M"):
            continue
        try:
            p_month = int(p["period"][1:])
        except ValueError:
            continue
        p_val = p["year"] * 12 + p_month
        if p_val <= target_val:
            if before is None or p_val > before["year"] * 12 + int(before["period"][1:]):
                before = p
        if p_val >= target_val:
            if after is None or p_val < after["year"] * 12 + int(after["period"][1:]):
                after = p
    return before, after


# ---------------------------------------------------------------------------
# Honolulu bimonthly cadence helpers (kept here because the Hawaii repo
# uses them; harmless for other consumers since they default to today).
# ---------------------------------------------------------------------------

# Honolulu (S49A) CPI is bimonthly. Data periods are odd months (Jan, Mar,
# May, Jul, Sep, Nov) and the release lands the following even month around
# the 15th — e.g. Mar-2026 data is published around Apr-15, 2026.
HONOLULU_DATA_MONTHS = {1, 3, 5, 7, 9, 11}
BLS_RELEASE_DAY = 15


def expected_latest_period_bimonthly(today: date | None = None) -> tuple[int, int]:
    """Expected latest *bimonthly* CPI data period, given today's date.

    Tailored to Honolulu (and any other BLS area on the same bimonthly
    cadence). Other-area consumers should write their own helper that
    matches their release schedule.
    """
    if today is None:
        today = date.today()
    y, m = today.year, today.month
    for _ in range(12):
        if m in HONOLULU_DATA_MONTHS:
            release_year = y if m < 12 else y + 1
            release_month = m + 1 if m < 12 else 1
            if (today.year, today.month, today.day) >= (
                release_year, release_month, BLS_RELEASE_DAY
            ):
                return (y, m)
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return (today.year - 1, today.month)


def cache_has_period(
    cpi_data: dict, series_ids: list[str], year: int, month: int,
) -> bool:
    """True iff every series in *series_ids* has a print for (year, M{month:02d})."""
    period = f"M{month:02d}"
    for sid in series_ids:
        points = cpi_data.get(sid, [])
        if not any(p["year"] == year and p["period"] == period for p in points):
            return False
    return True


# ---------------------------------------------------------------------------
# Disk caching helpers
# ---------------------------------------------------------------------------

def _default_cache_dir() -> Path:
    base = os.environ.get("CENSUS_FORECASTER_CACHE_DIR")
    if base:
        return Path(base) / "bls"
    return Path.home() / ".cache" / "census-forecaster" / "bls"


def fetch_and_cache(
    series_ids: list[str],
    start_year: int | None = None,
    end_year: int | None = None,
    api_key: str | None = None,
    cache_dir: Path | None = None,
) -> dict:
    """Fetch series and write a daily snapshot to disk."""
    cache_dir = cache_dir or _default_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    data = fetch_cpi_data(series_ids, start_year, end_year, api_key)

    cache_file = cache_dir / f"cpi_{date.today().isoformat()}.json"
    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    return data


def load_cached_cpi(cache_dir: Path | None = None) -> dict | None:
    """Most recent cached snapshot from `cache_dir`."""
    cache_dir = cache_dir or _default_cache_dir()
    if not cache_dir.exists():
        return None
    cache_files = sorted(cache_dir.glob("cpi_*.json"), reverse=True)
    if not cache_files:
        return None
    with open(cache_files[0]) as f:
        return json.load(f)


def fetch_if_stale(
    series_ids: list[str],
    start_year: int | None = None,
    api_key: str | None = None,
    cache_dir: Path | None = None,
    expected_latest_period_fn=None,
) -> tuple[dict, bool]:
    """Return cached data if it's already current; else fetch fresh.

    `expected_latest_period_fn() -> (year, month)` decides whether the
    cache is current. Defaults to the Honolulu bimonthly schedule;
    callers on a different cadence should pass their own.

    Returns *(cpi_data, did_fetch)*. Falls back to cached data on
    network error.
    """
    cached = load_cached_cpi(cache_dir) or {}
    fn = expected_latest_period_fn or expected_latest_period_bimonthly
    exp_year, exp_month = fn()

    if cached and cache_has_period(cached, series_ids, exp_year, exp_month):
        return cached, False

    try:
        fresh = fetch_and_cache(series_ids, start_year=start_year,
                                api_key=api_key, cache_dir=cache_dir)
        return fresh, True
    except Exception:
        return cached, False
