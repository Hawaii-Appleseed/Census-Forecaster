"""Refresh FEMA major-disaster declarations for Hawaii.

This is NOT a predictor. Nothing here is screened against anything —
it is a **control**, the same kind of thing ``exclude_2020`` already is.

The screen re-runs itself with 2020 removed because one shock moved
every series at once, manufacturing correlations that are really a
single event seen many times. Hawaii has smaller versions of that shock
on a regular cadence: the 2023 Lahaina wildfire, the 2018 Kīlauea
eruption, Hurricane Lane, repeated flood declarations. Each one moves
arrivals, payrolls and prices together for a few months. Having the
dates in the repo makes it possible to ask whether a finding survives
their removal, instead of hoping it does.

Two design decisions that matter more than they look:

**Biological declarations are dropped by default.** Hawaii's COVID
declaration (DR-4510) has an incident window of 2020-01-20 →
2023-05-11 — 41 months. Excluding every declared month would delete
three and a half years of panel, which is not a robustness check, it is
demolition. The 2020 gate already handles that regime.

**Long incident windows are capped** (``MAX_SPAN_MONTHS``). A
declaration's window is an administrative artifact as much as a
physical one: DR-4201 (Puʻu ʻŌʻō) runs 2014-09 → 2015-03 because lava
kept advancing, but the economic shock is concentrated at the start.
Capping keeps a single long declaration from quietly dominating the
excluded set. Both defaults are arguments, not hardcoded.

Usage
-----
    python -m census_forecaster.scripts.refresh_fema_disasters --dry-run
    python -m census_forecaster.scripts.refresh_fema_disasters
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

import requests

from .refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
DISASTERS_FILE = _MARKETS_DIR / "hi_disasters.json"

FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
_STATE = "HI"

#: "DR" = major disaster declaration. "EM" (emergency) and "FM" (fire
#: management assistance) are lower bars and far more numerous; a
#: robustness control wants the events big enough to move a state
#: economy, not every federal cost-share.
_DECLARATION_TYPE = "DR"

#: COVID arrives as a 41-month "Biological" declaration — see module doc.
DEFAULT_EXCLUDED_TYPES: tuple[str, ...] = ("Biological",)

#: Cap on how many months one declaration may contribute.
MAX_SPAN_MONTHS = 6


def fetch_hi_disasters(*, timeout: float = 60.0) -> list[dict]:
    """Hawaii major-disaster declarations, deduped to one row each.

    FEMA returns one row per designated area (county), so a statewide
    event appears several times; we keep the first and record how many
    areas it covered, which is a rough severity proxy.
    """
    params = {
        "$filter": f"state eq '{_STATE}' and declarationType eq "
                   f"'{_DECLARATION_TYPE}'",
        "$select": ("disasterNumber,declarationDate,incidentType,"
                    "declarationTitle,incidentBeginDate,incidentEndDate,"
                    "designatedArea"),
        "$top": 1000,
    }
    resp = requests.get(FEMA_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json().get("DisasterDeclarationsSummaries", [])
    if not rows:
        raise ValueError("FEMA returned no Hawaii declarations")

    by_number: dict[int, dict] = {}
    for r in rows:
        num = r.get("disasterNumber")
        if num is None:
            continue
        rec = by_number.setdefault(num, {
            "disaster_number": num,
            "declaration_date": (r.get("declarationDate") or "")[:10],
            "incident_type": r.get("incidentType") or "",
            "title": r.get("declarationTitle") or "",
            "incident_begin": (r.get("incidentBeginDate") or "")[:10],
            "incident_end": (r.get("incidentEndDate") or "")[:10],
            "designated_areas": 0,
        })
        rec["designated_areas"] += 1

    out = sorted(by_number.values(), key=lambda d: d["incident_begin"])
    for rec in out:
        rec["months"] = _span_months(rec["incident_begin"],
                                     rec["incident_end"])
    return out


def _span_months(begin: str, end: str) -> list[str]:
    """Inclusive YYYY-MM list spanned by an incident window.

    An open-ended incident (no end date) counts as its begin month only:
    an unclosed declaration is an administrative state, not evidence
    that the shock is still running.
    """
    if len(begin) < 7:
        return []
    by, bm = int(begin[:4]), int(begin[5:7])
    if len(end) < 7:
        return [f"{by:04d}-{bm:02d}"]
    ey, em = int(end[:4]), int(end[5:7])
    start, stop = by * 12 + (bm - 1), ey * 12 + (em - 1)
    if stop < start:
        return [f"{by:04d}-{bm:02d}"]
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(start, stop + 1)]


def disaster_months(
    disasters: Iterable[dict],
    *,
    excluded_types: Sequence[str] = DEFAULT_EXCLUDED_TYPES,
    max_span_months: int = MAX_SPAN_MONTHS,
) -> list[str]:
    """Sorted YYYY-MM months a robustness re-run could drop.

    Both filters are defaults, not policy: pass ``excluded_types=()``
    and ``max_span_months=999`` to get every declared month, and see
    the module docstring for why you probably do not want to.
    """
    banned = {t.lower() for t in excluded_types}
    months: set[str] = set()
    for d in disasters:
        if (d.get("incident_type") or "").lower() in banned:
            continue
        months.update((d.get("months") or [])[:max_span_months])
    return sorted(months)


def build_payload(disasters: list[dict]) -> dict:
    months = disaster_months(disasters)
    return {
        "version": 1,
        "fetch_date": date.today().isoformat(),
        "source": f"{FEMA_URL} (state={_STATE}, declarationType="
                  f"{_DECLARATION_TYPE})",
        "disasters": disasters,
        "excluded_types": list(DEFAULT_EXCLUDED_TYPES),
        "max_span_months": MAX_SPAN_MONTHS,
        "candidate_exclusion_months": months,
        "limitations": [
            "NOT a predictor — a robustness control, like exclude_2020. "
            "Nothing here is screened against anything.",
            "Major-disaster (DR) declarations only; emergency (EM) and "
            "fire-management (FM) declarations are a lower bar and far "
            "more numerous.",
            "Biological declarations are excluded by default: Hawaii's "
            "COVID declaration spans 2020-01 to 2023-05 (41 months), and "
            "dropping all of them would delete three and a half years of "
            "panel rather than test anything.",
            f"One declaration contributes at most {MAX_SPAN_MONTHS} "
            "months; incident windows are administrative as much as "
            "physical and the economic shock concentrates at the start.",
            "Declarations are state-level here. FEMA designates areas by "
            "county, so a Maui-only event still marks the month for a "
            "statewide series — appropriate for statewide targets, "
            "conservative for county ones.",
        ],
    }


def _parse_args(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description="Refresh FEMA HI disasters")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        disasters = fetch_hi_disasters()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: FEMA fetch failed: {exc}; leaving "
              f"{DISASTERS_FILE.name} alone.", file=sys.stderr)
        return 1

    payload = build_payload(disasters)
    months = payload["candidate_exclusion_months"]
    print(f"  {len(disasters)} Hawaii major-disaster declarations "
          f"({disasters[0]['incident_begin']} → "
          f"{disasters[-1]['incident_begin']})")
    print(f"  {len(months)} candidate exclusion months after filters")
    for d in disasters[-5:]:
        print(f"    DR-{d['disaster_number']}  {d['incident_begin']}  "
              f"{d['incident_type']:18} {d['title'][:40]}")

    if args.dry_run:
        print(f"[dry-run] would write {DISASTERS_FILE}")
        return 0
    _atomic_write_json(DISASTERS_FILE, payload)
    print(f"Wrote {DISASTERS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
