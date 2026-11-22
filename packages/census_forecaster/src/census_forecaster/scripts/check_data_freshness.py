"""Fail when a bundled data source has gone stale, whatever the refresh steps said.

The monthly ``refresh-data`` job fails when a refresh step crashes, but most steps do not crash when a source fails:
they print ``::warning::``, keep the committed file and exit 0 (by design: one bad source should not cost every
other). A missing API key skips a step the same way. So a source could stop updating for months while every run was
green. This check reads what the data files say about themselves, after the refreshes, and fails on:

* a monthly series whose latest observation is older than its source's usual publication lag plus two missed monthly
  runs (``SERIES_MAX_LAG``);
* a file or series whose own refresh date (``last_refresh``, ``fetch_date``, ``run_date``, national-macro's
  ``series_fetch_date``) is two or more months old: no successful refresh in two monthly runs;
* an annual source missing a year that has been published (``ANNUAL``), including the hand-built ones no job
  refreshes (the ACS calibration panel, the Urban Hawaii CPI anchors), which is the only place they get checked.

Known gaps at the source are listed in ``EXEMPT`` with the reason and a date to look again; an exemption past its date
fails like any other finding.

    python -m census_forecaster.scripts.check_data_freshness            # exit 1 if anything is stale
    python -m census_forecaster.scripts.check_data_freshness --today 2027-01-05
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

#: Files of monthly series ({"series": {name: [{"year", "period": "Mnn", ...}]}}).
SERIES_FILES = ("markets/macro_monthly.json", "markets/prices_panel.json")

#: Months from a series' latest observation to the month of the check, by name prefix (longest match wins): the
#: source's usual lag at the job's run on the 5th, plus two missed runs. Measured 2026-11-21: BLS, FRED, Realtor.com,
#: Zillow, DBEDT and prices 0-2 months; EIA 3-4; BTS 4; the Gulf fuel price 3; HTA's annual archive up to ~18.
SERIES_MAX_LAG = {"": 4, "EIA_": 6, "BTS_": 7, "MJFUEL": 6, "HTA_": 20}

#: Files with a refresh date of their own: (file, key). The month of the date may be at most one month before the
#: check's (a refresh this run or last).
REFRESH_DATES = (
    *((f"anchors/{n}.json", "last_refresh") for n in (
        "bea_hi_percapita_income", "bea_hi_rpp_housing", "bea_honolulu_rpp_all", "bls_laus",
        "bls_national_unemployment", "fred_hi_hpi", "hud_fmr_honolulu", "pce_deflator", "qcew_hawaii_wages",
        "saipe_poverty", "zillow_zhvi", "zillow_zori")),
    ("anchors/calibration.json", "run_date"),
    ("anchors/bls_calibration.json", "run_date"),
    ("bls_panel/cpi_panel.json", "fetch_date"),
    ("dotax_monthly/collections.json", "fetch_date"),
    ("leading_indicators/ui_claims.json", "last_refresh"),
    ("leading_indicators/market_signals.json", "last_refresh"),
    ("markets/hi_disasters.json", "fetch_date"),
)
REFRESH_MAX_AGE_MONTHS = 1
PER_SERIES_DATES = (("leading_indicators/national_macro.json", "series_fetch_date"),)


@dataclass(frozen=True)
class Annual:
    """An annual source must hold ``year - 1`` once the check runs on or after ``(month, day)`` of ``year``, and
    ``year - 2`` before that (when last year's data is not yet out)."""

    file: str
    published: tuple[int, int]
    how: str

    def latest(self, d: dict) -> int:
        if "year_range" in d:
            return int(d["year_range"][1])
        values = d.get("values_by_year") or d.get("values_by_geoid_year")
        years = values.keys() if "values_by_year" in d else {y for v in values.values() for y in v}
        return max(int(y) for y in years)


ANNUAL = (
    Annual("leading_indicators/bps_permits.json", (6, 1),
           "Census posts the county annual file around May; the monthly job's refresh_bps_permits fetches it"),
    Annual("anchors/cpi_honolulu_allitems.json", (3, 1),
           "hand-built (no job refreshes it): add the BLS Urban Hawaii (CUUSS49FSA0) annual average"),
    Annual("anchors/cpi_honolulu_rent.json", (3, 1),
           "hand-built (no job refreshes it): add the BLS Urban Hawaii rent (CUUSS49FSEHA) annual average"),
    Annual("calibration_panel/acs_panel.json", (10, 1),
           "the ACS 1-year release is out each September; rebuild with "
           "census_forecaster.scripts.build_calibration_panel (manual, needs CENSUS_API_KEY; moves forecasts)"),
)

#: Series that stopped at the source: name -> (reason, look again after YYYY-MM).
_MAUI_NA = ("DBEDT's Maui County workbook prints (NA) for visitor statistics from February 2026 on "
            "(labor and jobs continue)", "2027-06")
EXEMPT: dict[str, tuple[str, str]] = {n: _MAUI_NA for n in (
    "DBEDT_ARRIVALS_MAUI", "DBEDT_ARRIVALS_DOM_MAUI", "DBEDT_ARRIVALS_INTL_MAUI", "DBEDT_VISITOR_DAYS_MAUI",
    "DBEDT_VISITOR_DAYS_DOM_MAUI", "DBEDT_VISITOR_DAYS_INTL_MAUI", "DBEDT_VISITOR_SPEND_MAUI")}


def _months(today: datetime.date, year: int, month: int) -> int:
    return (today.year - year) * 12 + (today.month - month)


def _ym(text: str) -> tuple[int, int]:
    y, m = str(text)[:7].split("-")
    return int(y), int(m)


def _max_lag(name: str) -> int:
    return SERIES_MAX_LAG[max((p for p in SERIES_MAX_LAG if name.startswith(p)), key=len)]


def check(today: datetime.date, data: Path = DATA) -> tuple[list[str], list[str]]:
    """``(stale, notes)``: what is stale (each a line saying what and how old), and the exemptions in force."""
    stale: list[str] = []
    notes: list[str] = []

    def load(rel: str) -> dict | None:
        try:
            return json.loads((data / rel).read_text())
        except (OSError, ValueError) as exc:
            stale.append(f"{rel}: cannot be read ({type(exc).__name__}: {exc})")
            return None

    for rel in SERIES_FILES:
        if (d := load(rel)) is None:
            continue
        for name, obs in sorted(d["series"].items()):
            if not obs:
                continue
            last = max(obs, key=lambda o: (int(o["year"]), int(o["period"][1:])))
            lag = _months(today, int(last["year"]), int(last["period"][1:]))
            if lag <= _max_lag(name):
                continue
            where = f"{rel} {name}: latest {last['year']}-{last['period']} is {lag} months old (limit {_max_lag(name)})"
            if name in EXEMPT:
                reason, until = EXEMPT[name]
                if _months(today, *_ym(until)) <= 0:
                    notes.append(f"{where}; exempt until {until}: {reason}")
                    continue
                where += f"; its exemption ran out in {until}: {reason}"
            stale.append(where)

    for rel, key in REFRESH_DATES:
        if (d := load(rel)) is None:
            continue
        if key not in d:
            stale.append(f"{rel}: no {key!r}")
            continue
        age = _months(today, *_ym(d[key]))
        if age > REFRESH_MAX_AGE_MONTHS:
            stale.append(f"{rel}: {key} {d[key]} is {age} months old (no successful refresh in two runs)")

    for rel, key in PER_SERIES_DATES:
        if (d := load(rel)) is None:
            continue
        for name, date in sorted(d.get(key, {}).items()):
            age = _months(today, *_ym(date))
            if age > REFRESH_MAX_AGE_MONTHS:
                stale.append(f"{rel} {name}: fetched {date}, {age} months ago (no successful refresh in two runs)")

    for a in ANNUAL:
        if (d := load(a.file)) is None:
            continue
        want = today.year - 1 if (today.month, today.day) >= a.published else today.year - 2
        if (have := a.latest(d)) < want:
            stale.append(f"{a.file}: latest year {have}, {want} is published ({a.how})")

    return stale, notes


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", type=datetime.date.fromisoformat, default=datetime.date.today())
    args = ap.parse_args(argv)
    stale, notes = check(args.today)
    for n in notes:
        print(f"note: {n}")
    if stale:
        print(f"::error::{len(stale)} bundled data source(s) are stale:")
        for s in stale:
            print(f"  {s}")
        return 1
    print(f"data freshness: every source is current as of {args.today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
