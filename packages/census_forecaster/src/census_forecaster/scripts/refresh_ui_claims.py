"""Refresh DOL ETA-539 Hawaii weekly UI initial claims (→ monthly means).

Why this exists
---------------
Weekly unemployment-insurance initial claims are administrative
head-counts of new filings — the classic recession canary. They are the
**fastest labour signal in the panel**: the all-states CSV updates
weekly with roughly an 11-day lag, versus ~6 weeks for LAUS/CES. Unlike
LAUS (which is partly modelled), claims are direct counts, so they carry
genuinely independent information about labour-market turning points.

Source
------
``https://oui.doleta.gov/unemploy/csv/ar539.csv`` — keyless, all states,
weekly back to the 1980s (~13 MB). Columns are the ETA-539 report
fields: ``st`` (state), ``c2`` (reflect-week ending date), ``c3``
(state initial claims, excluding the federal programs in c4/c5).

The screen runs on a monthly grid, so weekly values are aggregated to
**calendar-month means of weekly initial claims** (mean, not sum, so
4-week and 5-week months stay comparable). The current partial month is
included — a mean of available weeks is a level, same no-peeking
posture as ``refresh_national_macro.aggregate_to_annual``.

Writes
------
1. ``DOL_HI_INITIAL_CLAIMS`` (monthly mean of weekly IC) into
   ``data/markets/macro_monthly.json``, additively — the causal-screen
   grid.
2. ``data/leading_indicators/ui_claims.json`` — **per-state calendar-year
   mean** weekly initial claims, keyed by 2-digit state FIPS, for the ML
   forecaster's ``STATE_SERIES`` channel. The same all-states CSV already
   in hand covers every county in the calibration panel, so the
   HI_UI_CLAIMS -> HI_UNEMPLOYMENT screen finding can be ablated
   panel-wide with per-state signal rather than a Hawaii-only constant.
   FIPS (not postal) keys so ``ml_features`` can look the series up
   straight off ``geoid[:2]`` with no cross-module abbreviation table.

Usage
-----
    python -m census_forecaster.scripts.refresh_ui_claims --dry-run
    python -m census_forecaster.scripts.refresh_ui_claims
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

import requests

from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"

ETA539_CSV = "https://oui.doleta.gov/unemploy/csv/ar539.csv"
SERIES_ID = "DOL_HI_INITIAL_CLAIMS"
STATE = "HI"

# Per-state annual file (ML STATE_SERIES channel; see module docstring).
STATE_SERIES_ID = "DOL_STATE_INITIAL_CLAIMS"
_PKG_LEADING = _PKG_DATA / "leading_indicators"
UI_CLAIMS_STATE_FILE = _PKG_LEADING / "ui_claims.json"

# ETA-539 keys states by postal abbreviation; county geoids key by FIPS.
# The mapping lives here (at the fetch boundary) so the feature module
# never needs it — the bundled file is written FIPS-keyed.
# 50 states + DC, matching build_calibration_panel.STATE_FIPS; the
# territories ETA-539 also reports (PR/VI/GU) are deliberately absent —
# they have no counties in the calibration panel and different ACS
# coverage, so their rows are dropped rather than mapped.
POSTAL_TO_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
    "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12",
    "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18",
    "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23",
    "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28",
    "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
    "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55",
    "WY": "56",
}


def _parse_week_ending(raw: str) -> Optional[date]:
    """c2 arrives as ISO (2026-07-18) or US (7/18/2026) depending on era."""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_weekly_by_state(text: str) -> dict[str, list[tuple[date, float]]]:
    """ETA-539 CSV text → ``{postal: sorted [(week_ending, claims)]}``.

    One pass over the ~13 MB CSV serves both outputs: the Hawaii monthly
    screen series and the all-states annual ML channel. Rows with an
    unparseable week-ending, a non-numeric ``c3``, or a negative count
    are skipped (they are sparse and always malformed, never meaningful
    zeros).
    """
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        st = (row.get("st") or "").strip()
        if not st:
            continue
        week = _parse_week_ending(row.get("c2", ""))
        if week is None:
            continue
        try:
            ic = float(str(row.get("c3", "")).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if ic < 0:
            continue
        out[st].append((week, ic))
    for weeks in out.values():
        weeks.sort(key=lambda t: t[0])
    return dict(out)


def parse_hi_weekly(text: str, *, state: str = STATE) -> list[tuple[date, float]]:
    """ETA-539 CSV text → sorted ``[(week_ending, initial_claims)]``."""
    return parse_weekly_by_state(text).get(state, [])


def weekly_to_monthly_mean(weekly: Sequence[tuple[date, float]]) -> list[dict]:
    """Mean weekly initial claims per calendar month of the week-ending.

    Mean rather than sum keeps 4-week and 5-week months on the same
    scale; the partial current month is a mean of its available weeks.
    """
    by_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for week, ic in weekly:
        by_month[(week.year, week.month)].append(ic)
    rows = [{"year": y, "period": f"M{m:02d}",
             "value": round(sum(v) / len(v), 2)}
            for (y, m), v in by_month.items()]
    rows.sort(key=lambda r: (r["year"], r["period"]))
    return rows


def weekly_to_annual_mean(
    weekly: Sequence[tuple[date, float]],
) -> dict[int, float]:
    """Mean weekly initial claims per calendar year of the week-ending.

    Same mean-not-sum reasoning as ``weekly_to_monthly_mean``, one grid
    up: 52- and 53-week years stay comparable, and the current partial
    year is the mean of its available weeks — the convention
    ``bls_laus.json`` already uses for the county LAUS annual averages
    this channel sits beside.
    """
    by_year: dict[int, list[float]] = defaultdict(list)
    for week, ic in weekly:
        by_year[week.year].append(ic)
    return {y: round(sum(v) / len(v), 2) for y, v in sorted(by_year.items())}


def build_state_annual(
    by_state: dict[str, list[tuple[date, float]]],
) -> dict[str, dict[int, float]]:
    """``{postal: weekly}`` → ``{state_fips: {year: mean weekly claims}}``.

    Postal codes outside ``POSTAL_TO_FIPS`` (territories, the ``US``
    aggregate row some vintages carry) are dropped, not guessed.
    """
    out: dict[str, dict[int, float]] = {}
    for postal, weekly in by_state.items():
        fips = POSTAL_TO_FIPS.get(postal)
        if fips is None:
            continue
        annual = weekly_to_annual_mean(weekly)
        if annual:
            out[fips] = annual
    return dict(sorted(out.items()))


def build_state_payload(state_annual: dict[str, dict[int, float]]) -> dict:
    """Wrap the per-state annual means in the bundled-file envelope.

    Mirrors ``anchors/bls_laus.json``'s metadata keys so the two county-
    and state-level labour channels document themselves the same way.
    Years are stringified because JSON object keys are strings and the
    loader casts back to int (same as every other bundled panel file).
    """
    return {
        "series_id": STATE_SERIES_ID,
        "title": ("DOL ETA-539 state UI initial claims — calendar-year "
                  "mean of weekly filings"),
        "source": "DOL ETA (oui.doleta.gov)",
        "geography": "state",
        "units": "initial claims per week (mean over the year's weeks)",
        "frequency": "weekly_aggregated_to_annual",
        "aggregation_method": "annual_mean_of_weekly",
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": [
            "Administrative filing counts, not survey estimates: a claim "
            "is a person applying, so the series measures inflow into "
            "unemployment, not the unemployment stock the ACS reports.",
            "Levels scale with state size and are not comparable across "
            "states as-is; the ML channel's scale-free columns "
            "(log-changes, level-vs-own-baseline) carry the cross-state "
            "signal and the raw log level is retained only as a "
            "state-size interaction term.",
            "BLS LAUS — already an ML feature at county level — ingests "
            "UI claims as one of its own inputs, so this channel is "
            "partly collinear with the laus_* columns rather than "
            "independent of them.",
            "The current partial year is a mean of available weeks; "
            "back-weeks are revised by the states.",
            "Coverage is 50 states + DC; ETA-539 territory rows "
            "(PR/VI/GU) are dropped — no calibration-panel counties.",
        ],
        "values_by_state_year": {
            fips: {str(y): v for y, v in sorted(years.items())}
            for fips, years in state_annual.items()
        },
    }


def merge_into_macro_monthly(rows: list[dict],
                             *, path: Path = MACRO_MONTHLY_FILE) -> dict:
    if path.exists():
        with open(path) as f:
            payload = json.load(f)
    else:
        payload = {"version": 1, "series": {}, "sources": {}, "limitations": []}

    payload.setdefault("series", {})[SERIES_ID] = rows
    payload.setdefault("sources", {})["DOL_ETA539"] = (
        f"{ETA539_CSV} — weekly state UI initial claims (c3), "
        "aggregated to calendar-month means"
    )
    payload["fetch_date"] = date.today().isoformat()

    note = (
        "DOL_HI_INITIAL_CLAIMS is the calendar-month MEAN of ETA-539 "
        "weekly state initial claims (c3; excludes the federal-program "
        "counts). Administrative filings, not survey estimates; weekly "
        "source updates with ~11-day lag and back-weeks are revised. "
        "The current partial month is a mean of available weeks."
    )
    lims = payload.setdefault("limitations", [])
    if note not in lims:
        lims.append(note)
    return payload


def _parse_args(argv: Optional[Sequence[str]] = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        resp = requests.get(ETA539_CSV, timeout=180)
        resp.raise_for_status()
        by_state = parse_weekly_by_state(resp.text)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ETA-539 fetch/parse failed: {exc}", file=sys.stderr)
        return 1

    weekly = by_state.get(STATE, [])
    if len(weekly) < 100:
        print(f"ERROR: only {len(weekly)} HI weeks parsed — refusing to "
              "overwrite the bundled series with a fragment.", file=sys.stderr)
        return 1

    state_annual = build_state_annual(by_state)
    if len(state_annual) < 45:
        print(f"ERROR: only {len(state_annual)} states parsed (expected 51) "
              "— refusing to overwrite the bundled per-state file with a "
              "fragment.", file=sys.stderr)
        return 1

    rows = weekly_to_monthly_mean(weekly)
    print(f"  {SERIES_ID}: {len(weekly)} weeks → {len(rows)} months "
          f"({rows[0]['year']}-{rows[0]['period']} → "
          f"{rows[-1]['year']}-{rows[-1]['period']})", flush=True)
    _hi_years = sorted(state_annual[POSTAL_TO_FIPS[STATE]])
    print(f"  {STATE_SERIES_ID}: {len(state_annual)} states, "
          f"{_hi_years[0]}–{_hi_years[-1]} (HI span)", flush=True)

    payload = merge_into_macro_monthly(rows)
    state_payload = build_state_payload(state_annual)
    if args.dry_run:
        print(f"[dry-run] would write {SERIES_ID} to {MACRO_MONTHLY_FILE}")
        print(f"[dry-run] would write {STATE_SERIES_ID} to "
              f"{UI_CLAIMS_STATE_FILE}")
        return 0
    _atomic_write_json(MACRO_MONTHLY_FILE, payload)
    print(f"Wrote {MACRO_MONTHLY_FILE}")
    UI_CLAIMS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(UI_CLAIMS_STATE_FILE, state_payload)
    print(f"Wrote {UI_CLAIMS_STATE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
