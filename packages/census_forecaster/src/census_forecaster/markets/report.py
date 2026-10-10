"""Tracker CLI: per-ticker status table + CSV output.

Usage
-----
    python -m census_forecaster.markets.report
    python -m census_forecaster.markets.report --csv-dir reports/market_signals/

Reads the committed prices panel (no network) and prints per ticker:
last close, 3/6/12-month momentum, trailing-36-month annualised vol,
data span, and fetch provenance. ``--csv-dir`` also writes
``tracker_status.csv`` there.

``--forecast`` adds damped-drift 3/6/12-month forecasts with
walk-forward-calibrated 90% bands (see ``markets/trend.py`` — tracker
context, not trading advice). ``--screen-summary`` prints the current
``selected_signals.json`` if a causal screen has been run.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

from .panel import PricesPanel, load_prices_panel
from .universe import TICKERS


def _pct(logret: Optional[float]) -> str:
    if logret is None:
        return "     —"
    return f"{(math.exp(logret) - 1) * 100:+6.1f}%"


def build_status_rows(panel: PricesPanel) -> list[dict]:
    """One status dict per universe ticker present in the panel."""
    rows: list[dict] = []
    for spec in TICKERS:
        if spec.symbol not in panel.series:
            continue
        bars = panel.bars(spec.symbol)
        if not bars:
            continue
        last = bars[-1]
        vol = panel.annualized_vol(spec.symbol)
        rows.append({
            "symbol": spec.symbol,
            "name": spec.name,
            "tier": spec.tier,
            "last_month": f"{last.year}-{last.month:02d}",
            "last_close": round(last.adj_close, 2),
            "mom_3m": panel.momentum(spec.symbol, 3),
            "mom_6m": panel.momentum(spec.symbol, 6),
            "mom_12m": panel.momentum(spec.symbol, 12),
            "vol_36m_ann": round(vol, 4) if vol is not None else None,
            "first_month": f"{bars[0].year}-{bars[0].month:02d}",
            "n_obs": len(bars),
            "source": panel.provenance.get(spec.symbol, "?"),
        })
    return rows


def print_status_table(rows: Sequence[dict], out=sys.stdout) -> None:
    print(
        f"{'symbol':<7}{'tier':<8}{'last':>9}{'close':>10}"
        f"{'3m':>8}{'6m':>8}{'12m':>8}{'vol':>7}{'since':>9}  src",
        file=out,
    )
    for r in rows:
        vol = f"{r['vol_36m_ann']:.2f}" if r["vol_36m_ann"] is not None else "—"
        print(
            f"{r['symbol']:<7}{r['tier']:<8}{r['last_month']:>9}"
            f"{r['last_close']:>10.2f}"
            f"{_pct(r['mom_3m']):>8}{_pct(r['mom_6m']):>8}"
            f"{_pct(r['mom_12m']):>8}{vol:>7}{r['first_month']:>9}"
            f"  {r['source']}",
            file=out,
        )


def write_status_csv(rows: Sequence[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol", "name", "tier", "last_month", "last_close",
        "mom_3m", "mom_6m", "mom_12m", "vol_36m_ann",
        "first_month", "n_obs", "source",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            out = dict(r)
            for k in ("mom_3m", "mom_6m", "mom_12m"):
                out[k] = round(out[k], 6) if out[k] is not None else ""
            writer.writerow(out)


def build_forecast_rows(
    panel: PricesPanel, horizons: Sequence[int] = (3, 6, 12),
) -> list[dict]:
    """Per-ticker damped-drift forecasts with calibrated 90% bands."""
    from datetime import date

    from .trend import calibrate_band_multiplier, forecast_ticker

    rows: list[dict] = []
    for spec in TICKERS:
        if spec.symbol not in panel.series:
            continue
        bars = panel.bars(spec.symbol)
        if len(bars) < 24:
            continue
        z = calibrate_band_multiplier(bars)
        last = bars[-1]
        for h in horizons:
            total = last.year * 12 + (last.month - 1) + h
            target = date(total // 12, total % 12 + 1, 28)
            fc = forecast_ticker(bars, target, band_multiplier=z)
            rows.append({
                "symbol": spec.symbol,
                "horizon_months": h,
                "target_month": f"{target.year}-{target.month:02d}",
                "point": round(fc.value, 2),
                "lo90": round(fc.lo90, 2),
                "hi90": round(fc.hi90, 2),
                "monthly_vol": round(fc.monthly_vol, 4),
                "band_multiplier": round(fc.band_multiplier, 3),
                "band_calibrated": z is not None,
                "cap_hit": fc.cap_hit,
            })
    return rows


def write_forecast_csv(rows: Sequence[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["symbol", "horizon_months", "target_month", "point",
              "lo90", "hi90", "monthly_vol", "band_multiplier",
              "band_calibrated", "cap_hit"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_forecast_table(rows: Sequence[dict], out=sys.stdout) -> None:
    print("\nDamped-drift forecasts (φ=0.92/mo), calibrated 90% bands — "
          "tracker context, not trading advice:", file=out)
    print(f"{'symbol':<7}{'h':>4}{'target':>9}{'point':>10}"
          f"{'lo90':>10}{'hi90':>10}{'z':>7}", file=out)
    for r in rows:
        print(f"{r['symbol']:<7}{r['horizon_months']:>4}"
              f"{r['target_month']:>9}{r['point']:>10.2f}"
              f"{r['lo90']:>10.2f}{r['hi90']:>10.2f}"
              f"{r['band_multiplier']:>7.2f}", file=out)


def print_screen_summary(out=sys.stdout) -> int:
    import json

    from .panel import _DATA_DIR
    path = _DATA_DIR / "selected_signals.json"
    if not path.exists():
        print("[tracker] no selected_signals.json yet — run "
              "`python -m census_forecaster.scripts.run_market_screen`",
              file=sys.stderr)
        return 2
    with open(path) as f:
        sel = json.load(f)
    print(f"Screen of {sel['generated']}: {len(sel['signals'])} BH "
          f"survivor(s) of {sel['candidates_tested']} tests "
          f"(q={sel['q_fdr']}):", file=out)
    for s in sel["signals"]:
        robust = "robust" if s["robust_to_2020_exclusion"] else "NOT robust"
        print(f"  {s['name']}: p={s['granger_p']:.4f} "
              f"xcorr={s['best_xcorr_r']:+.3f}@{s['best_xcorr_lead_months']}m "
              f"({robust} to 2020 exclusion)", file=out)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Market-signals tracker: per-ticker status from the "
                    "committed prices panel (offline).",
    )
    parser.add_argument(
        "--panel", type=Path, default=None,
        help="Path to prices_panel.json (default: bundled panel).",
    )
    parser.add_argument(
        "--csv-dir", type=Path, default=None,
        help="Also write tracker_status.csv (and forecasts.csv with "
             "--forecast) into this directory.",
    )
    parser.add_argument(
        "--forecast", action="store_true",
        help="Add damped-drift 3/6/12-month forecasts with calibrated "
             "90%% bands.",
    )
    parser.add_argument(
        "--screen-summary", action="store_true",
        help="Print the current selected_signals.json summary and exit.",
    )
    args = parser.parse_args(argv)

    if args.screen_summary:
        return print_screen_summary()

    try:
        panel = load_prices_panel(args.panel)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rows = build_status_rows(panel)
    if not rows:
        print("ERROR: panel contains no universe tickers", file=sys.stderr)
        return 2

    print(f"[tracker] panel fetched {panel.fetch_date}; "
          f"{len(rows)} tickers\n", file=sys.stderr)
    print_status_table(rows)

    if args.csv_dir is not None:
        csv_path = args.csv_dir / "tracker_status.csv"
        write_status_csv(rows, csv_path)
        print(f"\n[tracker] wrote {csv_path}", file=sys.stderr)

    if args.forecast:
        fc_rows = build_forecast_rows(panel)
        print_forecast_table(fc_rows)
        if args.csv_dir is not None:
            fc_path = args.csv_dir / "forecasts.csv"
            write_forecast_csv(fc_rows, fc_path)
            print(f"[tracker] wrote {fc_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
