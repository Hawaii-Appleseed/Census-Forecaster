"""Empirical timing for PUMS replicate-weight fetches.

Answers the v4 calibration plan's #1 risk: is fetching 80 replicate weights
per state-year feasible within a reasonable wall-clock budget?

Tests three regimes:
  A. Single-call: all 80 replicate weights + WGTP + SERIALNO + PUMA in one request
  B. Two-call chunked: WGTP1-49 in call 1, WGTP50-80 in call 2, join by SERIALNO
  C. Single-call with just main weight (baseline — what fetch_pums does today)

Then extrapolates to 50 states x 15 years.

Run:  python scripts/timing_pums_replicates.py
"""
from __future__ import annotations

import os
import time

import requests

CENSUS_API_BASE = "https://api.census.gov/data"


def _build_url(year: int, vintage: str) -> str:
    return f"{CENSUS_API_BASE}/{year}/acs/acs{vintage}/pums"


def _fetch(
    url: str,
    get_vars: list[str],
    state_fips: str,
    api_key: str | None,
    timeout: int = 120,
) -> tuple[float, int, int]:
    """Return (elapsed_seconds, n_rows, payload_bytes)."""
    params: dict = {
        "get": ",".join(get_vars),
        "for": "public use microdata area:*",
        "in": f"state:{state_fips}",
    }
    if api_key:
        params["key"] = api_key

    t0 = time.perf_counter()
    resp = requests.get(url, params=params, timeout=timeout)
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    n_bytes = len(resp.content)
    rows = resp.json()
    return elapsed, len(rows) - 1, n_bytes  # subtract header row


def regime_a_single_call(state_fips: str, year: int, vintage: str, api_key: str | None) -> None:
    """All 80 housing-unit replicate weights in one request."""
    url = _build_url(year, vintage)
    base = ["SERIALNO", "PUMA", "WGTP"]
    replicates = [f"WGTP{i}" for i in range(1, 81)]
    get_vars = base + replicates
    print(f"\n[A] Single-call ({len(get_vars)} vars) for state {state_fips}, {year} ACS{vintage}y")
    try:
        elapsed, n_rows, n_bytes = _fetch(url, get_vars, state_fips, api_key)
        print(f"    OK  : {elapsed:.2f}s, {n_rows:,} rows, {n_bytes/1024:.1f} KB")
    except requests.HTTPError as e:
        print(f"    FAIL: {e.response.status_code} — {e.response.text[:200]}")


def regime_b_two_calls(state_fips: str, year: int, vintage: str, api_key: str | None) -> None:
    """Split into two calls; join by SERIALNO."""
    url = _build_url(year, vintage)
    base = ["SERIALNO", "PUMA", "WGTP"]
    chunk1 = base + [f"WGTP{i}" for i in range(1, 41)]
    chunk2 = ["SERIALNO"] + [f"WGTP{i}" for i in range(41, 81)]
    print(f"\n[B] Two-call ({len(chunk1)}+{len(chunk2)} vars) for state {state_fips}, {year} ACS{vintage}y")
    try:
        e1, n1, b1 = _fetch(url, chunk1, state_fips, api_key)
        e2, n2, b2 = _fetch(url, chunk2, state_fips, api_key)
        print(f"    OK  : {e1:.2f}s + {e2:.2f}s = {e1+e2:.2f}s, {n1:,}/{n2:,} rows, "
              f"{(b1+b2)/1024:.1f} KB")
    except requests.HTTPError as e:
        print(f"    FAIL: {e.response.status_code} — {e.response.text[:200]}")


def regime_c_baseline(state_fips: str, year: int, vintage: str, api_key: str | None) -> None:
    """Baseline — just the main weight (what current fetch_pums does)."""
    url = _build_url(year, vintage)
    get_vars = ["SERIALNO", "PUMA", "WGTP"]
    print(f"\n[C] Baseline ({len(get_vars)} vars) for state {state_fips}, {year} ACS{vintage}y")
    try:
        elapsed, n_rows, n_bytes = _fetch(url, get_vars, state_fips, api_key)
        print(f"    OK  : {elapsed:.2f}s, {n_rows:,} rows, {n_bytes/1024:.1f} KB")
    except requests.HTTPError as e:
        print(f"    FAIL: {e.response.status_code} — {e.response.text[:200]}")


def extrapolate(seconds_per_state_year: float) -> None:
    """Project to full nationwide calibration panel."""
    states = 50
    years = 15
    total_calls = states * years
    seconds_total = total_calls * seconds_per_state_year
    print("\n=== Extrapolation ===")
    print(f"At {seconds_per_state_year:.2f}s per state-year:")
    print(f"  50 states x 15 years = {total_calls} state-years")
    print(f"  Total wall-clock: {seconds_total:.0f}s "
          f"= {seconds_total/60:.1f}min = {seconds_total/3600:.2f}h")


if __name__ == "__main__":
    api_key = os.environ.get("CENSUS_API_KEY")
    if not api_key:
        print("WARNING: No CENSUS_API_KEY in env. Rate-limited to ~500 req/day.")

    state_fips = "15"  # Hawaii
    year = 2022
    vintage = "1"

    regime_c_baseline(state_fips, year, vintage, api_key)
    regime_a_single_call(state_fips, year, vintage, api_key)
    regime_b_two_calls(state_fips, year, vintage, api_key)

    # Try a bigger state too — California (06) — to see if size scales linearly
    print("\n--- Repeating regime A for California to test scaling ---")
    regime_a_single_call("06", year, vintage, api_key)
