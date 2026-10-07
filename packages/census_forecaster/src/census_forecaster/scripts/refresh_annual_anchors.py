"""Refresh the annual ACS anchors no other refresh script owns.

Four bundled anchor files in ``data/anchors/``, each fetched in full from its
source and rewritten whole:

1. ``pce_deflator.json``       — BEA PCE chain-type price index, annual, 2017=100
   (FRED ``DPCERG3A086NBEA``, a keyless mirror of NIPA Table 2.3.4 line 1).
   Anchors B19013 as a national inflation proxy.
2. ``fred_hi_hpi.json``        — FHFA All-Transactions House Price Index for
   Hawaii (FRED ``HISTHPI``, quarterly), annual mean of the four quarters,
   complete years only. Anchors B25077.
3. ``qcew_hawaii_wages.json``  — BLS QCEW Hawaii statewide average annual pay,
   all industries, total covered (BLS API ``ENU1500050010``, period A01).
   Anchors B19013 and B20002.
4. ``hud_fmr_honolulu.json``   — HUD Fair Market Rent, 2-bedroom, Urban Honolulu
   HI MSA (Honolulu County), by HUD fiscal year, from HUD's combined history
   workbook ``FMR_2Bed_1983_<year>.xlsx`` (keyless; needs openpyxl, the
   ``dotax`` extra). Anchors B25058 and B25064 (rent).

Why this exists: until 2026-10 these were hand-compiled files, stale since
January 2026 with no refresh step, and they do not reproduce from the sources
they name. Against the live series, QCEW agreed within 1% through 2019 and then
ran 2-5% low (2020 pay +3.9% in the file, +10.0% in QCEW; 2024 $64,956 against
$67,366); the HPI differed from the true annual mean by up to 11% (2021: 786.9
against 709.8) with no consistent rule; the PCE file's 2017 value was 97.0 in a
file titled 2017=100 and its year-over-year changes were off by up to 0.3 pp.
Its ``series_id`` for QCEW (``ENU1500010010``) is monthly employment, not pay.
The HUD file matched HUD's own 2-bedroom FMR in no year (2021 $2,376 against
$2,073, 2024 $2,599 against $2,388; its 2020-21 change was +5.1% against HUD's
-4.0%) and stopped at FY2024 although FY2025-FY2027 are published. HUD's API
needs a registered token, so this reads the workbook, which does not.

Failure posture mirrors ``refresh_national_macro``: each anchor is independent;
a failed fetch warns and leaves the committed file (and its ``last_refresh``, so
the staleness warning keeps telling the truth); a fetch that lacks a committed
year is treated as partial and refused; a layout change (missing quarter,
implausible value) raises rather than writing a misaligned series.

FRED requests carry NO custom User-Agent: see ``refresh_national_macro.fetch_fred_csv``.
HUD is the opposite: huduser.gov answers a request without a browser-like
User-Agent with HTTP 202 and an empty body, so ``fetch_hud_workbook`` sends one
and treats anything but a 200 holding a zip as an error.

Usage
-----
    python -m census_forecaster.scripts.refresh_annual_anchors --dry-run
    BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_annual_anchors
    python -m census_forecaster.scripts.refresh_annual_anchors --strict   # exit 1 on any failure
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Optional, Sequence

import requests

from ..bls.client import fetch_cpi_data
from .refresh_national_macro import fetch_fred_csv
from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
ANCHORS_DIR = _PKG_DATA / "anchors"

#: First year kept; matches the files this replaces (2010 onward).
FIRST_YEAR = 2010

#: HUD publishes one combined 2-bedroom history workbook; the end year in its
#: name moves each autumn (``FMR_2Bed_1983_2027.xlsx`` as of 2026-10).
HUD_FMR_URL_TMPL = "https://www.huduser.gov/portal/datasets/FMR/FMR_2Bed_1983_{year}.xlsx"

#: huduser.gov needs a browser-like User-Agent: requests' default and curl's
#: both get HTTP 202 and an empty body. Runner probe, 2026-10-07: this UA 5/5
#: HTTP 200 with the full 2,471,963-byte file in about 0.9 s (curl and requests);
#: the default UA 4/4 HTTP 202, 0 bytes. The opposite of FRED's rule.
HUD_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

#: Honolulu County's whole-county row in the workbook (state 15 + county 003 +
#: county-subdivision 99999). Its HUD FMR area is the Urban Honolulu MSA: coded
#: METRO26180M26180 through FY2015 and METRO46520M46520 from FY2016.
HUD_HONOLULU_FIPS = "1500399999"
_HUD_AREA_CODES = frozenset({"METRO26180M26180", "METRO46520M46520"})

#: Plausible value ranges per anchor: a value outside raises (layout change).
_RANGES = {
    "pce_deflator.json": (50.0, 400.0),
    "fred_hi_hpi.json": (100.0, 5000.0),
    "qcew_hawaii_wages.json": (20_000.0, 250_000.0),
    "hud_fmr_honolulu.json": (500.0, 10_000.0),    # USD/month; a percentile (40/50) would fail
}

ANCHOR_SPECS: tuple[dict, ...] = (
    {
        "filename": "pce_deflator.json",
        "kind": "fred_annual",
        "fred_id": "DPCERG3A086NBEA",
        "source": "BEA",
        "series_id": ("BEA NIPA Table 2.3.4 line 1 (Personal consumption "
                      "expenditures price index), via FRED DPCERG3A086NBEA"),
        "title": ("Personal Consumption Expenditures: Chain-type Price Index "
                  "(annual, 2017=100)"),
        "frequency": "annual",
        "units": "index, 2017=100",
        "limitations": [
            "National-level price index; Hawaii cost-of-living deviates "
            "persistently above the national average so the *level* differs "
            "from Honolulu CPI but YoY rates are tightly correlated.",
            "PCE deflator weights expenditure shares differently from CPI "
            "(chain-weighted Fisher index, broader scope including imputed "
            "services).",
            "Often revised in subsequent annual GDP releases (typically "
            "<0.2 pp on YoY rate).",
            "Replaced in full on 2026-10 from the live series: the January "
            "2026 file was not on the 2017=100 base it was titled with.",
        ],
    },
    {
        "filename": "fred_hi_hpi.json",
        "kind": "fred_quarterly_mean",
        "fred_id": "HISTHPI",
        "source": "FRED",
        "series_id": ("HISTHPI (FHFA All-Transactions House Price Index for "
                      "Hawaii)"),
        "title": ("All-Transactions House Price Index for Hawaii (annual mean "
                  "of the four quarters, 1980Q1=100)"),
        "frequency": "quarterly (annual mean of Q1-Q4, complete years only)",
        "units": "index, 1980Q1=100",
        "limitations": [
            "Repeat-sales index; new construction is excluded by construction.",
            "State-level only; intra-state divergence (Honolulu vs Maui vs Big "
            "Island) is not captured.",
            "FHFA HPI tracks home-price *appreciation* across all "
            "transactions, including those with conforming GSE financing — its "
            "sample skews against jumbo/cash-only segments common in luxury "
            "Honolulu.",
            "Annual values are the mean of the four quarterly readings (an ACS "
            "1-year value reflects responses across the whole survey year); a "
            "year is written only once all four quarters exist.",
            "Replaced in full on 2026-10 from the live series: the January "
            "2026 file matched the true annual mean in only 3 of 15 years and "
            "differed by up to 11% in the rest.",
        ],
    },
    {
        "filename": "qcew_hawaii_wages.json",
        "kind": "bls_annual",
        "bls_id": "ENU1500050010",
        "source": "BLS QCEW",
        "series_id": ("ENU1500050010 (Hawaii statewide, all industries, total "
                      "covered, average annual pay)"),
        "title": ("Quarterly Census of Employment and Wages — Hawaii statewide "
                  "annual average pay (all ownership, all industries)"),
        "frequency": "annual",
        "units": "USD per covered employee per year (nominal)",
        "limitations": [
            "Covers UI-covered employment only; excludes self-employed, "
            "federal civilian, and certain agricultural workers.",
            "State-level series; county-level QCEW exists (ENU15001*, "
            "ENU15003*, etc.) but is noisier on small-county wage means.",
            "Average pay is total wages / employment — top-coding effects from "
            "a thin tail of high earners can move the mean by 1-2% in any year.",
            "Reported on calendar year basis; trailing revisions through Q3 of "
            "year+1 are common (typically <0.5%).",
            "QCEW is *nominal* wages; real wage growth is the relevant "
            "comparison for ACS B19013 (also nominal).",
            "Replaced in full on 2026-10 from the BLS API: the January 2026 "
            "file agreed within 1% through 2019, then ran 2-5% low (2020 "
            "growth +3.9% against QCEW's +10.0%, a pandemic composition "
            "effect), and its series id was monthly employment.",
        ],
    },
    {
        "filename": "hud_fmr_honolulu.json",
        "kind": "hud_fmr_workbook",
        "hud_fips": HUD_HONOLULU_FIPS,
        "source": "HUD",
        "series_id": ("HUD Fair Market Rents — Urban Honolulu, HI MSA (Honolulu "
                      "County; METRO46520M46520, METRO26180M26180 before FY2016), "
                      "2BR, from the FMR_2Bed_1983_<year> workbook"),
        "title": ("Fair Market Rent, Honolulu MSA, 2-bedroom, by HUD fiscal year "
                  "(Oct 1 prior year - Sep 30)"),
        "frequency": "annual (FY-aligned)",
        "units": "USD per month, gross rent (includes utility allowances)",
        "limitations": [
            "FMRs are administrative: a percentile of recent-mover gross rents, "
            "not a direct rent estimate. HUD set Honolulu's at the 40th "
            "percentile in FY2010-FY2011 and from FY2018, and at the 50th in "
            "FY2012-FY2017, so the series has level shifts at those boundaries "
            "that are methodology, not rent.",
            "Lag: FMRs are built from ACS data a few years old plus HUD's trend "
            "adjustments, so their rate of change lags spot rent by 1-2 years.",
            "Honolulu only; neighbor-island county FMRs (Hawaii, Kauai, Maui) "
            "are published separately.",
            "Enters the rent blend as one of four anchors (with Zillow ZORI, "
            "CPI rent and BEA RPP) at a weight set by back-test. It is a "
            "lagging administrative series, not a forecast of contract rent.",
            "Replaced in full on 2026-10 from HUD's published workbook: the "
            "January 2026 file matched HUD's 2-bedroom FMR in no year (up to "
            "15% off; its 2020-21 change was +5.1% against HUD's -4.0%) and "
            "stopped at FY2024. FY2025-FY2027 are included.",
        ],
    },
)


# ---------------------------------------------------------------------------
# Value builders (pure; network-free)
# ---------------------------------------------------------------------------

def annual_from_fred_annual(rows: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """FRED annual rows ``[{date, value}]`` → ``{year: value}`` from ``first_year``."""
    out: dict[int, float] = {}
    for r in rows:
        y = int(r["date"][:4])
        if y >= first_year:
            out[y] = round(float(r["value"]), 3)
    return dict(sorted(out.items()))


def annual_from_fred_quarterly(rows: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """FRED quarterly rows → annual mean of Q1-Q4, complete years only."""
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for r in rows:
        by_year[int(r["date"][:4])][int(r["date"][5:7])] = float(r["value"])
    out: dict[int, float] = {}
    for y, q in sorted(by_year.items()):
        if y < first_year:
            continue
        if set(q) == {1, 4, 7, 10}:
            out[y] = round(mean(q.values()), 3)
        elif len(q) == 4:
            raise ValueError(f"{y}: quarterly readings fall in months {sorted(q)}, "
                             "expected 1/4/7/10: layout change?")
    return out


def annual_from_bls(points: Sequence[dict], first_year: int = FIRST_YEAR) -> dict[int, float]:
    """BLS API points ``[{year, period, value}]`` → ``{year: value}`` for period A01."""
    out = {int(p["year"]): float(p["value"]) for p in points
           if p["period"] == "A01" and int(p["year"]) >= first_year}
    return dict(sorted(out.items()))


def _hud_fmr_year(column: object) -> Optional[int]:
    """``'fmr24_2'`` -> 2024 and ``'fmr99_2'`` -> 1999; None for any other column.

    ``fmrYY_2`` is the 2-bedroom FMR for fiscal year YY. The workbook spans 1983
    onward, so 00-50 reads as 20xx and 51-99 as 19xx.
    """
    m = re.fullmatch(r"fmr(\d{2})_2", str(column or ""))
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy if yy <= 50 else 1900 + yy


def _strip_core_dates(content: bytes) -> bytes:
    """Drop ``dcterms:created`` / ``modified`` from a workbook's core properties.

    HUD has shipped workbooks whose dates openpyxl cannot read (it raises
    ``ValueError`` or ``TypeError`` on load). They are metadata; nothing here
    uses them.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as zin, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = re.sub(rb"<dcterms:(created|modified)\b[^>]*?(?:/>|>.*?</dcterms:\1>)",
                              b"", data, flags=re.S)
            zout.writestr(item.filename, data)
    return out.getvalue()


def _open_hud_workbook(content: bytes):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - the dotax extra ships it
        raise RuntimeError("openpyxl is required for the HUD FMR anchor "
                           "(pip install 'census-forecaster[dotax]')") from exc
    opts = {"read_only": True, "data_only": True}
    try:
        return openpyxl.load_workbook(io.BytesIO(content), **opts)
    except (ValueError, TypeError):
        return openpyxl.load_workbook(io.BytesIO(_strip_core_dates(content)), **opts)


def annual_from_hud_workbook(content: bytes, fips: str = HUD_HONOLULU_FIPS,
                             first_year: int = FIRST_YEAR) -> dict[int, int]:
    """HUD combined 2BR workbook -> ``{fiscal year: 2-bedroom FMR in dollars}`` for one area.

    One row per FMR area, keyed by ``fips`` (state + county + county-subdivision),
    and per fiscal year YY three columns: ``msaYY`` (the FMR area code),
    ``fmrYY_2`` (the 2-bedroom FMR, the value taken) and ``fmrYY`` (the percentile
    it was set at, 40 or 50; not a rent). Raises rather than write a misaligned
    series: no ``fips`` column, not exactly one row for the area, a fractional
    dollar value, an FMR area code other than the two Honolulu has had, or a gap
    in the years.
    """
    wb = _open_hud_workbook(content)
    try:
        rows = wb.active.iter_rows(values_only=True)
        index = {name: i for i, name in enumerate(next(rows, None) or ()) if name}
        if "fips" not in index:
            raise ValueError("HUD workbook has no 'fips' column in its header row: layout change?")
        matches = [r for r in rows if str(r[index["fips"]]).strip() == fips]
    finally:
        wb.close()
    if len(matches) != 1:
        raise ValueError(f"HUD workbook has {len(matches)} rows for fips {fips}, expected 1: "
                         "layout change?")
    row = matches[0]

    def cell(name: str):
        i = index.get(name)
        return row[i] if i is not None and i < len(row) else None

    out: dict[int, int] = {}
    for name in index:
        year = _hud_fmr_year(name)
        if year is None or year < first_year or cell(name) in (None, ""):
            continue
        area = cell(f"msa{name[3:5]}")
        if area not in _HUD_AREA_CODES:
            raise ValueError(f"FY{year}: FMR area code {area!r} for fips {fips} is not one of "
                             f"{sorted(_HUD_AREA_CODES)}: did HUD re-delineate the area?")
        dollars = float(cell(name))
        if dollars != round(dollars):
            raise ValueError(f"FY{year}: 2BR FMR {cell(name)!r} is not whole dollars: layout change?")
        out[year] = int(round(dollars))
    years = sorted(out)
    if not years or years != list(range(years[0], years[-1] + 1)):
        raise ValueError(f"HUD workbook fiscal years {years} are not one contiguous run: "
                         "layout change?")
    return dict(sorted(out.items()))


def check_plausible(filename: str, values: dict[int, float]) -> None:
    lo, hi = _RANGES[filename]
    bad = {y: v for y, v in values.items() if not lo <= v <= hi}
    if bad:
        raise ValueError(f"{filename}: values outside [{lo:g}, {hi:g}]: {bad} "
                         "(wrong series or a units change?)")


def years_lost(previous: dict[int, float], new: dict[int, float]) -> list[int]:
    """Committed years the fetch lacks: a partial fetch, never a refresh."""
    return sorted(set(previous) - set(new))


def build_payload(spec: dict, values: dict[int, float], today: Optional[date] = None) -> dict:
    today = today or date.today()
    return {
        "source": spec["source"],
        "series_id": spec["series_id"],
        "title": spec["title"],
        "frequency": spec["frequency"],
        "units": spec["units"],
        "last_refresh": today.strftime("%Y-%m"),
        "limitations": list(spec["limitations"]),
        "values_by_year": {str(y): v for y, v in sorted(values.items())},
    }


# ---------------------------------------------------------------------------
# Fetchers (network)
# ---------------------------------------------------------------------------

def fetch_bls_annual(series_id: str, api_key: Optional[str], *, first_year: int = FIRST_YEAR,
                     last_year: Optional[int] = None) -> list[dict]:
    """Chunked BLS fetch (10-year windows keyless, 20 with a key) → raw points."""
    last_year = last_year or date.today().year
    window = 20 if api_key else 10
    points: list[dict] = []
    start = first_year
    while start <= last_year:
        end = min(start + window - 1, last_year)
        raw = fetch_cpi_data(series_ids=[series_id], start_year=start, end_year=end,
                             api_key=api_key)
        points.extend(raw.get(series_id, []))
        start = end + 1
    return points


def fetch_hud_workbook(*, today: Optional[date] = None, timeout: float = 90.0) -> bytes:
    """Newest ``FMR_2Bed_1983_<year>.xlsx`` as bytes.

    The end year in the name moves each autumn, so try next year, this year and
    last year in turn; HUD answers 404 for a name it has not published. A request
    it dislikes gets HTTP 202 and an empty body, which a status-only check would
    take for success, so anything but a 200 holding a zip raises.
    """
    year = (today or date.today()).year
    for end_year in (year + 1, year, year - 1):
        url = HUD_FMR_URL_TMPL.format(year=end_year)
        resp = requests.get(url, headers={"User-Agent": HUD_USER_AGENT}, timeout=timeout)
        if resp.status_code == 404:
            continue
        if resp.status_code != 200 or resp.content[:2] != b"PK":
            raise RuntimeError(f"{url}: HTTP {resp.status_code}, {len(resp.content)} bytes, "
                               "not a workbook (bot challenge?)")
        return resp.content
    raise RuntimeError(f"no FMR_2Bed_1983_<year>.xlsx published for {year - 1}-{year + 1}")


def fetch_values(spec: dict, api_key: Optional[str], first_year: int = FIRST_YEAR) -> dict[int, float]:
    kind = spec["kind"]
    if kind == "fred_annual":
        values = annual_from_fred_annual(fetch_fred_csv(spec["fred_id"]), first_year)
    elif kind == "fred_quarterly_mean":
        values = annual_from_fred_quarterly(fetch_fred_csv(spec["fred_id"]), first_year)
    elif kind == "bls_annual":
        values = annual_from_bls(fetch_bls_annual(spec["bls_id"], api_key, first_year=first_year),
                                 first_year)
    elif kind == "hud_fmr_workbook":
        values = annual_from_hud_workbook(fetch_hud_workbook(), spec["hud_fips"], first_year)
    else:  # pragma: no cover - spec typo
        raise ValueError(f"unknown anchor kind {kind!r}")
    if not values:
        raise ValueError(f"{spec['filename']}: the source returned no usable years")
    check_plausible(spec["filename"], values)
    return values


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_previous(path: Path) -> dict[int, float]:
    if not path.exists():
        return {}
    with open(path) as f:
        return {int(y): float(v) for y, v in json.load(f).get("values_by_year", {}).items()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the PCE, FHFA Hawaii HPI, QCEW Hawaii wage and HUD FMR anchors.")
    parser.add_argument("--out", type=Path, default=ANCHORS_DIR,
                        help="Output directory (default: data/anchors/).")
    parser.add_argument("--first-year", type=int, default=FIRST_YEAR)
    parser.add_argument("--dry-run", action="store_true", help="List what would be fetched; write nothing.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any anchor fails (default: warn and keep the committed file).")
    args = parser.parse_args(argv)

    if args.dry_run:
        for spec in ANCHOR_SPECS:
            src = spec.get("fred_id") or spec.get("bls_id") or spec.get("hud_fips")
            print(f"[dry-run] {spec['filename']}: {spec['kind']} {src}", file=sys.stderr)
        return 0

    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        print("::warning::BLS_API_KEY not set; fetching QCEW keylessly (25 req/day)", file=sys.stderr)

    failed: list[str] = []
    for spec in ANCHOR_SPECS:
        name = spec["filename"]
        path = args.out / name
        try:
            values = fetch_values(spec, api_key, args.first_year)
        except Exception as exc:  # noqa: BLE001 - degrade per anchor, don't fail CI
            print(f"::warning::{name}: fetch failed ({exc}); keeping the committed file "
                  "and its last_refresh", file=sys.stderr)
            failed.append(name)
            continue
        previous = _read_previous(path)
        lost = years_lost(previous, values)
        if lost:
            print(f"::warning::{name}: fetch lacks {len(lost)} committed year(s) "
                  f"({lost[0]}-{lost[-1]}); treating it as a partial fetch, keeping the "
                  "committed file", file=sys.stderr)
            failed.append(name)
            continue
        _atomic_write_json(path, build_payload(spec, values))
        ys = sorted(values)
        print(f"[annual-anchors] {name}: {len(ys)} years ({ys[0]}-{ys[-1]}), last {values[ys[-1]]:g}",
              file=sys.stderr)

    if failed:
        print(f"[annual-anchors] {len(failed)} of {len(ANCHOR_SPECS)} anchors not refreshed: "
              + ", ".join(failed), file=sys.stderr)
        return 1 if args.strict else 0
    print("[annual-anchors] done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
