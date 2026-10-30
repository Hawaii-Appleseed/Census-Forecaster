"""PUMS microdata fetcher via the Census Data API.

Endpoint
--------
    https://api.census.gov/data/{year}/acs/acs{vintage}/pums

Documentation
-------------
    https://www.census.gov/data/developers/data-sets/acs-1year/
    https://www.census.gov/data/developers/data-sets/acs-5year/

Variables commonly requested
-----------------------------
    SERIALNO  — housing-unit serial number (always fetched)
    PUMA      — Public Use Microdata Area code (always fetched)
    WGTP      — housing-unit weight (always fetched)
    VEH       — vehicles available (0-6)
    HINCP     — household income past 12 months
    TEN       — tenure (owned/rented)
    BDSP      — number of bedrooms
"""
from __future__ import annotations

import os

import requests

from ..models import PumsRecord

CENSUS_API_BASE = "https://api.census.gov/data"


def fetch_pums(
    state_fips: str,
    variables: list[str],
    year: int = 2022,
    vintage: str = "1",
    api_key: str | None = None,
    timeout: int = 60,
) -> list[PumsRecord]:
    """Fetch PUMS microdata records for a state.

    Parameters
    ----------
    state_fips : str
        2-digit state FIPS code (e.g. "15" for Hawaii).
    variables : list[str]
        PUMS variable names to fetch in addition to SERIALNO, PUMA, WGTP
        which are always included.  Example: ["VEH", "TEN"].
    year : int
        ACS data year (e.g. 2022).
    vintage : str
        "1" for 1-year PUMS, "5" for 5-year PUMS.
    api_key : str, optional
        Census API key.  Falls back to the ``CENSUS_API_KEY`` environment
        variable.  Without a key, requests are rate-limited to ~500/day.
    timeout : int
        HTTP request timeout in seconds (default 60).

    Returns
    -------
    list[PumsRecord]
        One ``PumsRecord`` per housing unit.  Records with weight ≤ 0 or
        a missing PUMA are silently excluded.

    Raises
    ------
    requests.HTTPError
        On non-200 responses from the Census API.
    """
    key = api_key or os.environ.get("CENSUS_API_KEY", "")
    url = f"{CENSUS_API_BASE}/{year}/acs/acs{vintage}/pums"

    always_fetch = ["SERIALNO", "PUMA", "WGTP"]
    get_vars = list(dict.fromkeys(always_fetch + variables))

    params: dict = {
        "get": ",".join(get_vars),
        "for": "public use microdata area:*",
        "in": f"state:{state_fips}",
    }
    if key:
        params["key"] = key

    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()

    rows = resp.json()
    headers = rows[0]
    records: list[PumsRecord] = []

    for row in rows[1:]:
        d = dict(zip(headers, row, strict=False))

        # PUMA may appear as "PUMA" or as the geo field "public use microdata area"
        puma_raw = d.get("PUMA") or d.get("public use microdata area", "")
        if not puma_raw:
            continue

        puma = f"{state_fips}{puma_raw.zfill(5)}"

        weight_raw = d.get("WGTP", "0") or "0"
        try:
            weight = float(weight_raw)
        except ValueError:
            weight = 0.0
        if weight <= 0:
            continue

        var_values: dict[str, float] = {}
        for v in variables:
            raw = d.get(v)
            if raw is not None:
                try:
                    var_values[v] = float(raw)
                except ValueError:
                    pass

        records.append(PumsRecord(
            serial=d.get("SERIALNO", ""),
            puma=puma,
            weight=weight,
            variables=var_values,
        ))

    return records


def fetch_pums_with_replicates(
    state_fips: str,
    variables: list[str],
    year: int = 2022,
    vintage: str = "1",
    api_key: str | None = None,
    timeout: int = 120,
    weight_type: str = "housing",
) -> tuple[list[PumsRecord], list[list[float]]]:
    """Fetch PUMS records with all 80 replicate weights for variance estimation.

    The Census API caps requests at 50 variables, so replicate weights are
    fetched in two chunked calls and joined by SERIALNO. The returned
    ``PumsRecord`` list has the same shape as ``fetch_pums`` — replicate
    weights are returned separately to keep ``PumsRecord`` lightweight.

    Parameters
    ----------
    state_fips : str
        2-digit state FIPS code (e.g. "15" for Hawaii).
    variables : list[str]
        Survey variables to include in addition to always-fetched fields.
    year : int
        ACS data year.
    vintage : str
        "1" for 1-year PUMS, "5" for 5-year PUMS.
    api_key : str, optional
        Census API key. Falls back to ``CENSUS_API_KEY`` env var.
    timeout : int
        Per-request timeout in seconds (default 120 — replicate fetches are
        larger than plain PUMS calls).
    weight_type : str
        "housing" to fetch WGTP / WGTP1-80 (default);
        "person" to fetch PWGTP / PWGTP1-80.

    Returns
    -------
    records : list[PumsRecord]
        One record per housing/person unit (weight > 0, PUMA present).
        ``variables`` dict includes the requested survey variables only —
        not the replicate weights.
    replicate_matrix : list[list[float]]
        Parallel list to ``records``. Each entry is an 80-element list of
        replicate weights [WGTP1 … WGTP80] for that record.

    Raises
    ------
    requests.HTTPError
        On non-200 API responses.
    ValueError
        If ``weight_type`` is not "housing" or "person".
    """
    if weight_type not in ("housing", "person"):
        raise ValueError(f"weight_type must be 'housing' or 'person', got {weight_type!r}")

    key = api_key or os.environ.get("CENSUS_API_KEY", "")
    url = f"{CENSUS_API_BASE}/{year}/acs/acs{vintage}/pums"
    wgt_prefix = "WGTP" if weight_type == "housing" else "PWGTP"
    main_weight = wgt_prefix

    def _get(get_vars: list[str]) -> list[dict]:
        params: dict = {
            "get": ",".join(get_vars),
            "for": "public use microdata area:*",
            "in": f"state:{state_fips}",
        }
        if key:
            params["key"] = key
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        rows = resp.json()
        headers = rows[0]
        return [dict(zip(headers, row, strict=False)) for row in rows[1:]]

    # Chunk 1: base fields + replicates 1-40 (43 vars total, under 50-var cap)
    chunk1_vars = ["SERIALNO", "PUMA", main_weight] + list(variables) + [
        f"{wgt_prefix}{i}" for i in range(1, 41)
    ]
    # Chunk 2: SERIALNO as join key + replicates 41-80 (42 vars total)
    chunk2_vars = ["SERIALNO"] + [f"{wgt_prefix}{i}" for i in range(41, 81)]

    rows1 = _get(chunk1_vars)
    rows2 = _get(chunk2_vars)

    # Index chunk2 by SERIALNO for O(n) join
    replicates_41_80: dict[str, list[float]] = {}
    for d in rows2:
        serial = d.get("SERIALNO", "")
        replicates_41_80[serial] = [
            _safe_float(d.get(f"{wgt_prefix}{i}")) for i in range(41, 81)
        ]

    records: list[PumsRecord] = []
    replicate_matrix: list[list[float]] = []

    for d in rows1:
        puma_raw = d.get("PUMA") or d.get("public use microdata area", "")
        if not puma_raw:
            continue
        puma = f"{state_fips}{puma_raw.zfill(5)}"

        weight = _safe_float(d.get(main_weight, "0"))
        if weight <= 0:
            continue

        serial = d.get("SERIALNO", "")
        rep_1_40 = [_safe_float(d.get(f"{wgt_prefix}{i}")) for i in range(1, 41)]
        rep_41_80 = replicates_41_80.get(serial, [0.0] * 40)
        all_replicates = rep_1_40 + rep_41_80

        var_values: dict[str, float] = {}
        for v in variables:
            raw = d.get(v)
            if raw is not None:
                try:
                    var_values[v] = float(raw)
                except ValueError:
                    pass

        records.append(PumsRecord(
            serial=serial,
            puma=puma,
            weight=weight,
            variables=var_values,
        ))
        replicate_matrix.append(all_replicates)

    return records, replicate_matrix


def _safe_float(val: str | None, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default
