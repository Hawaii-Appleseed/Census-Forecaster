"""Refresh the market prices panel + monthly macro series + national anchor.

Fetches, per the pre-registered universe in ``markets/universe.py``:

1. ``data/markets/prices_panel.json`` + ``manifest.json`` — monthly
   adjusted closes per ticker (yfinance primary when installed, Stooq
   fallback; both free/keyless).
2. ``data/markets/macro_monthly.json`` — monthly national + Hawaii macro
   series for the Phase-2 causal screen: national unemployment
   (LNS14000000), Hawaii statewide unemployment (LASST150000000000003),
   and monthly Honolulu-county Zillow ZHVI/ZORI. Needs ``BLS_API_KEY``
   for the unemployment block (Zillow is keyless); skipped with a
   warning when absent.
3. ``data/anchors/bls_national_unemployment.json`` — annual-average
   national unemployment rate in the standard anchor schema (registered
   in ``_REGISTRY_SPEC`` in Phase 3; inert until then).

Failure posture (this runs in the monthly refresh workflow):
``--tolerate-failures`` keeps the previously committed series for any
ticker whose fetch fails (both sources) and emits a ``::warning``; the
panel write is aborted only when EVERY ticker fails.

Usage
-----
    python -m census_forecaster.scripts.refresh_market_panel --dry-run
    python -m census_forecaster.scripts.refresh_market_panel --skip-macro
    BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_market_panel \\
        --tolerate-failures
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from ..bls.client import fetch_cpi_data
from ..markets.client import MarketDataError, fetch_monthly_history
from ..markets.panel import PricesPanel
from ..markets.universe import TICKERS
from .refresh_zillow_laus_anchors import (
    ZHVI_COUNTY_CSV,
    ZORI_COUNTY_CSV,
    _atomic_write_json,
    fetch_zillow_county_csv,
)

_PKG_DATA = Path(__file__).resolve().parent.parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
_ANCHORS_DIR = _PKG_DATA / "anchors"

PRICES_PANEL_FILE = _MARKETS_DIR / "prices_panel.json"
MANIFEST_FILE = _MARKETS_DIR / "manifest.json"
MACRO_MONTHLY_FILE = _MARKETS_DIR / "macro_monthly.json"
NATIONAL_UNEMPLOYMENT_ANCHOR = _ANCHORS_DIR / "bls_national_unemployment.json"

# BLS series: CPS national unemployment rate (SA) and LAUS Hawaii
# statewide unemployment rate (SA), both monthly.
NATIONAL_UNEMP_SID = "LNS14000000"
HAWAII_UNEMP_SID = "LASST150000000000003"
# CES total nonfarm payrolls, Hawaii (establishment survey) — the
# hiring-side complement to LAUS's household survey. Added 2026-08-05;
# fetched in the same keyless BLS call as the unemployment series.
HAWAII_PAYROLLS_SID = "SMS15000000000000001"

HONOLULU_GEOID = "15003"


# ---------------------------------------------------------------------------
# Prices panel
# ---------------------------------------------------------------------------

def _load_previous_panel(path: Path) -> Optional[PricesPanel]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return PricesPanel.from_payload(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[markets] previous panel unreadable ({exc}); ignoring",
              file=sys.stderr)
        return None


def build_prices_panel(
    *,
    start_year: int,
    tolerate_failures: bool,
    previous: Optional[PricesPanel],
    source: str = "auto",
) -> PricesPanel:
    """Fetch every universe ticker; fall back to previous data on failure.

    Raises ``MarketDataError`` if every ticker fails and no previous
    series can stand in (a wholly-failed refresh must not write an
    empty panel).
    """
    series: dict[str, list] = {}
    provenance: dict[str, str] = {}
    n_fetched = 0

    for spec in TICKERS:
        try:
            bars, prov = fetch_monthly_history(
                spec.symbol, spec.stooq_symbol,
                start_year=start_year, source=source,
            )
            series[spec.symbol] = bars
            provenance[spec.symbol] = prov
            n_fetched += 1
            print(f"[markets] {spec.symbol}: {len(bars)} months via {prov}",
                  file=sys.stderr)
        except MarketDataError as exc:
            if not tolerate_failures:
                raise
            prev_bars = previous.series.get(spec.symbol) if previous else None
            if prev_bars:
                series[spec.symbol] = prev_bars
                provenance[spec.symbol] = (
                    previous.provenance.get(spec.symbol, "?") + "+stale"
                )
                print(f"::warning::market fetch failed for {spec.symbol} "
                      f"({exc}); keeping previously committed series "
                      f"({len(prev_bars)} months)", file=sys.stderr)
            else:
                print(f"::warning::market fetch failed for {spec.symbol} "
                      f"({exc}); no previous series to keep", file=sys.stderr)

    if n_fetched == 0:
        raise MarketDataError(
            "every ticker fetch failed; refusing to write the panel"
        )

    return PricesPanel(
        series=series,
        provenance=provenance,
        fetch_date=date.today().isoformat(),
        start_year=start_year,
    )


def build_manifest(panel: PricesPanel) -> dict:
    tickers = []
    for spec in TICKERS:
        bars = panel.series.get(spec.symbol)
        if not bars:
            continue
        tickers.append({
            "symbol": spec.symbol,
            "name": spec.name,
            "tier": spec.tier,
            "hypothesis": spec.hypothesis,
            "affinity_indicators": list(spec.affinity_indicators),
            "first_month": f"{bars[0].year}-{bars[0].month:02d}",
            "last_month": f"{bars[-1].year}-{bars[-1].month:02d}",
            "n_obs": len(bars),
            "source": panel.provenance.get(spec.symbol, "?"),
        })
    return {
        "version": 1,
        "fetch_date": panel.fetch_date,
        "n_tickers": len(tickers),
        "tickers": tickers,
    }


# ---------------------------------------------------------------------------
# Monthly macro series (Phase-2 screen targets)
# ---------------------------------------------------------------------------

def fetch_unemployment_monthly(
    api_key: Optional[str], *, start_year: int, end_year: int,
) -> dict[str, list[dict]]:
    """Fetch national + Hawaii monthly unemployment via the BLS API.

    Works keylessly: without ``api_key`` the BLS v2 API still serves
    requests (25/day, ≤10-year window), so the year range is chunked to
    10-year windows. With a key a single 20-year request suffices.

    Returns ``{sid: [{year, period, value}]}`` with M13 (annual-average)
    rows dropped — this file is strictly monthly cadence.
    """
    window = 20 if api_key else 10
    chunks: list[tuple[int, int]] = []
    s = start_year
    while s <= end_year:
        e = min(s + window - 1, end_year)
        chunks.append((s, e))
        s = e + 1

    merged: dict[str, list[dict]] = {}
    for ys, ye in chunks:
        raw = fetch_cpi_data(
            series_ids=[NATIONAL_UNEMP_SID, HAWAII_UNEMP_SID,
                        HAWAII_PAYROLLS_SID],
            start_year=ys,
            end_year=ye,
            api_key=api_key,
        )
        for sid, points in raw.items():
            merged.setdefault(sid, []).extend(points)

    out: dict[str, list[dict]] = {}
    for sid, points in merged.items():
        monthly = [p for p in points
                   if p["period"].startswith("M") and p["period"] != "M13"]
        monthly.sort(key=lambda p: (p["year"], p["period"]))
        out[sid] = monthly
    return out


def zillow_monthly_series(monthly_by_geoid: dict[str, dict[str, float]],
                          geoid: str) -> list[dict]:
    """One county's Zillow columns as sorted ``[{year, period, value}]``."""
    per_month = monthly_by_geoid.get(geoid, {})
    rows = []
    for date_str, val in per_month.items():
        year, month = date_str[:4], date_str[5:7]
        if not (year.isdigit() and month.isdigit()):
            continue
        rows.append({"year": int(year), "period": f"M{int(month):02d}",
                     "value": float(val)})
    rows.sort(key=lambda p: (p["year"], p["period"]))
    return rows


_MACRO_LIMITATIONS = [
    "Monthly cadence throughout; BLS unemployment is seasonally "
    "adjusted, Zillow indexes are smoothed + seasonally adjusted "
    "(ZHVI) / smoothed (ZORI).",
    "Zillow monthly values are subject to revision; final value "
    "settles ~1 year after first print.",
    "ZORI history starts 2015.",
]


def build_macro_payload(series: dict[str, list[dict]],
                        sources: dict[str, str],
                        existing_path: Optional[Path] = None) -> dict:
    """Merge this run's series into the existing macro_monthly.json.

    MUST merge, never replace. This function used to return a payload
    containing only the series THIS script fetched, which made the write
    a full overwrite of a file that five other refresh scripts also
    contribute to (national-macro, EIA, DBEDT, HTA, UI-claims). Because
    this script runs FIRST in the workflow, every downstream script was
    re-adding its own series to an emptied file — and any series whose
    downstream fetch failed that run was silently deleted from committed
    history rather than merely going stale.

    That is not hypothetical: the 2026-08-06 run lost MORTGAGE30US and
    DGS10 from this file when their FRED fetches timed out. (The same
    outage deleted mortgage30, dgs10, rental_vacancy and homeownership
    from national_macro.json, through the same replace-not-merge defect
    in refresh_national_macro, fixed 2026-09-29.) Their handler logs "keeping previous", which was true of its
    own merge step but false in effect, because this write had already
    removed the previous values. Merging here makes that message honest.
    """
    payload: dict = {"version": 1, "series": {}, "sources": {},
                     "limitations": []}
    if existing_path is not None and existing_path.exists():
        with open(existing_path) as f:
            payload = json.load(f)
        payload.setdefault("series", {})
        payload.setdefault("sources", {})
        payload.setdefault("limitations", [])

    payload["series"].update(series)
    payload["sources"].update(sources)
    payload["fetch_date"] = date.today().isoformat()
    for note in _MACRO_LIMITATIONS:
        if note not in payload["limitations"]:
            payload["limitations"].append(note)
    return payload


# ---------------------------------------------------------------------------
# National unemployment anchor (annual, standard anchor schema)
# ---------------------------------------------------------------------------

def build_national_unemployment_anchor(monthly: list[dict]) -> dict:
    """Annual-average national unemployment in the anchor JSON schema.

    Partial years use the mean of available months (mirrors the LAUS
    county treatment in ``refresh_zillow_laus_anchors``).
    """
    by_year: dict[str, list[float]] = {}
    for p in monthly:
        by_year.setdefault(str(p["year"]), []).append(float(p["value"]))
    values_by_year = {
        year: round(sum(vals) / len(vals), 3)
        for year, vals in sorted(by_year.items())
    }
    return {
        "source": "BLS",
        "series_id": NATIONAL_UNEMP_SID,
        "title": "National unemployment rate (CPS, seasonally adjusted, "
                 "annual average of monthly prints)",
        "frequency": "monthly_aggregated_to_annual",
        "units": "rate (percent unemployed of labor force)",
        "geography": "national",
        "aggregation_method": "annual_average",
        "last_refresh": date.today().strftime("%Y-%m"),
        "limitations": [
            "National level does not transfer to county level; registered "
            "(Phase 3) as a RATE anchor so only the YoY direction is used.",
            "CPS-based; conceptually closer to ACS S2301 than LAUS models "
            "but still a labor-force denominator, not population 16+.",
            "Current-year value is a partial-year mean until December.",
        ],
        "values_by_year": values_by_year,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the market prices panel, monthly macro series, "
                    "and the national-unemployment anchor.",
    )
    parser.add_argument("--start-year", type=int, default=2005)
    parser.add_argument(
        "--end-year", type=int, default=date.today().year,
        help="Macro-series end year (tickers always fetch to present).",
    )
    parser.add_argument(
        "--out", type=Path, default=_MARKETS_DIR,
        help="Markets output directory (default: data/markets/).",
    )
    parser.add_argument(
        "--anchors-out", type=Path, default=_ANCHORS_DIR,
        help="Anchors output directory (default: data/anchors/).",
    )
    parser.add_argument(
        "--source", choices=("auto", "yfinance", "yahoo_chart", "stooq"),
        default="auto",
        help="Price source ('auto' tries yfinance, then the Yahoo chart "
             "API via stdlib, then Stooq).",
    )
    parser.add_argument(
        "--skip-macro", action="store_true",
        help="Skip the monthly macro block (BLS unemployment + Zillow).",
    )
    parser.add_argument(
        "--tolerate-failures", action="store_true",
        help="Keep previously committed series for tickers whose fetch "
             "fails; abort only if ALL tickers fail. Use in CI.",
    )
    parser.add_argument(
        "--derive-signals", action="store_true",
        help="Also derive June-cutoff annual signals into "
             "data/leading_indicators/market_signals.json. Requires a "
             "prior causal-screen run (selected_signals.json); skipped "
             "with a warning otherwise.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched and exit (no network, no writes).",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        print(f"[dry-run] would fetch {len(TICKERS)} tickers "
              f"(source={args.source}, start={args.start_year}): "
              f"{', '.join(s.symbol for s in TICKERS)}", file=sys.stderr)
        if not args.skip_macro:
            print(f"[dry-run] would fetch BLS {NATIONAL_UNEMP_SID} + "
                  f"{HAWAII_UNEMP_SID} ({args.start_year}-{args.end_year})"
                  f"{' [BLS_API_KEY set]' if os.environ.get('BLS_API_KEY') else ' [keyless: chunked 10-yr windows]'}",
                  file=sys.stderr)
            print(f"[dry-run] would GET {ZHVI_COUNTY_CSV}", file=sys.stderr)
            print(f"[dry-run] would GET {ZORI_COUNTY_CSV}", file=sys.stderr)
        print(f"[dry-run] outputs: {args.out / 'prices_panel.json'}, "
              f"{args.out / 'manifest.json'}, "
              f"{args.out / 'macro_monthly.json'}, "
              f"{args.anchors_out / 'bls_national_unemployment.json'}",
              file=sys.stderr)
        return 0

    # ---- prices panel ----
    previous = _load_previous_panel(args.out / "prices_panel.json")
    try:
        panel = build_prices_panel(
            start_year=args.start_year,
            tolerate_failures=args.tolerate_failures,
            previous=previous,
            source=args.source,
        )
    except MarketDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _atomic_write_json(args.out / "prices_panel.json", panel.to_payload())
    _atomic_write_json(args.out / "manifest.json", build_manifest(panel))
    print(f"[markets] wrote prices_panel.json ({len(panel.series)} tickers) "
          f"+ manifest.json", file=sys.stderr)

    # ---- monthly macro block ----
    if not args.skip_macro:
        macro_series: dict[str, list[dict]] = {}
        macro_sources: dict[str, str] = {}

        api_key = os.environ.get("BLS_API_KEY")
        if not api_key:
            print("::warning::BLS_API_KEY not set; fetching unemployment "
                  "keylessly (25 req/day BLS limit — fine for this script, "
                  "set the key if other BLS refreshes run the same day)",
                  file=sys.stderr)
        print(f"[macro] fetching {NATIONAL_UNEMP_SID} + "
              f"{HAWAII_UNEMP_SID} + {HAWAII_PAYROLLS_SID} from BLS …",
              file=sys.stderr)
        try:
            unemp = fetch_unemployment_monthly(
                api_key, start_year=args.start_year, end_year=args.end_year,
            )
            for sid, rows in unemp.items():
                macro_series[sid] = rows
                macro_sources[sid] = "BLS API"
                print(f"[macro] {sid}: {len(rows)} monthly prints",
                      file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail CI
            print(f"::warning::BLS unemployment fetch failed ({exc}); "
                  "macro_monthly.json written without unemployment block",
                  file=sys.stderr)

        print("[macro] fetching monthly Honolulu ZHVI/ZORI …", file=sys.stderr)
        try:
            zhvi = fetch_zillow_county_csv(ZHVI_COUNTY_CSV)
            zori = fetch_zillow_county_csv(ZORI_COUNTY_CSV)
            macro_series["ZHVI_HONOLULU_MONTHLY"] = zillow_monthly_series(
                zhvi, HONOLULU_GEOID)
            macro_series["ZORI_HONOLULU_MONTHLY"] = zillow_monthly_series(
                zori, HONOLULU_GEOID)
            macro_sources["ZHVI_HONOLULU_MONTHLY"] = ZHVI_COUNTY_CSV
            macro_sources["ZORI_HONOLULU_MONTHLY"] = ZORI_COUNTY_CSV
            print(f"[macro] ZHVI: "
                  f"{len(macro_series['ZHVI_HONOLULU_MONTHLY'])} months; "
                  f"ZORI: {len(macro_series['ZORI_HONOLULU_MONTHLY'])} months",
                  file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail CI
            print(f"::warning::Zillow monthly fetch failed ({exc}); "
                  "macro_monthly.json written without Zillow block",
                  file=sys.stderr)

        if macro_series:
            _atomic_write_json(
                args.out / "macro_monthly.json",
                build_macro_payload(macro_series, macro_sources,
                                    existing_path=args.out / "macro_monthly.json"),
            )
            print(f"[macro] wrote macro_monthly.json "
                  f"({len(macro_series)} series)", file=sys.stderr)
        else:
            print("::warning::no macro series fetched; macro_monthly.json "
                  "not written", file=sys.stderr)

        # ---- national unemployment anchor ----
        national = macro_series.get(NATIONAL_UNEMP_SID)
        if national:
            anchor = build_national_unemployment_anchor(national)
            _atomic_write_json(
                args.anchors_out / "bls_national_unemployment.json", anchor,
            )
            print(f"[anchor] wrote bls_national_unemployment.json "
                  f"({len(anchor['values_by_year'])} years)", file=sys.stderr)

    # ---- derived annual signals (Phase 3, screen-gated) ----
    if args.derive_signals:
        from datetime import date as _date

        from ..markets.signals import (
            build_signals_payload,
            derive_annual_signals,
        )
        selected_path = args.out / "selected_signals.json"
        if not selected_path.exists():
            print("::warning::--derive-signals requested but "
                  f"{selected_path} is absent; run "
                  "run_market_screen first — skipping", file=sys.stderr)
        else:
            with open(selected_path) as f:
                selected = json.load(f)
            signals = derive_annual_signals(panel, selected)
            if not signals:
                print("::warning::no screen-gated channels active; "
                      "market_signals.json not written", file=sys.stderr)
            else:
                out_path = (_PKG_DATA / "leading_indicators"
                            / "market_signals.json")
                payload = build_signals_payload(
                    signals, selected,
                    last_refresh=_date.today().strftime("%Y-%m"),
                )
                _atomic_write_json(out_path, payload)
                spans = {n: f"{min(v)}–{max(v)}" for n, v in signals.items()}
                print(f"[signals] wrote market_signals.json: {spans}",
                      file=sys.stderr)

    print("[markets] done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
