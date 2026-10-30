"""The ticker forecast board — the markets subpackage as a stock forecaster.

Builds per-ticker, multi-horizon forecasts from the bundled prices
panel: damped-drift point (φ=0.92/mo, §2.3.1) + a 90% band whose σ is
the EWMA(λ=0.97) monthly vol and whose multiplier is walk-forward
calibrated per ticker *under that same σ*. EWMA is the default here —
not in ``forecast_ticker``, which keeps its original rolling-SD default
for back-compatibility — because a 2026-07 bake-off (3,806 pooled
walk-forward forecasts, sequentially calibrated multipliers) ranked it
first on interval score at identical coverage; GARCH(1,1) ranked last
and its dependency was rejected.

What the board deliberately is NOT:

* **Not fundamentals-driven.** The reverse-direction experiment
  (``fundamentals.py``) was a clean EMH null — census/BLS/Zillow
  signals made return forecasts *worse* out of sample — so no such
  signal touches the point forecast. The board instead carries the
  evidence as annotations: each row reports whether the ticker is
  itself a surviving *leading indicator* for Hawaii (the arrow that
  does work) and a volatility-regime flag (EWMA vs long-window σ),
  which is where macro state legitimately shows up.
* **Not trading advice.** Tracker context, per the standing repo line.

CLI::

    python -m lab.markets.forecaster
    python -m lab.markets.forecaster --horizons 1 3 6 12 \
        --vol ewma --json forecasts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from .panel import PricesPanel, load_prices_panel
from .trend import _monthly_vol, calibrate_band_multiplier, forecast_ticker
from .universe import TICKERS

DEFAULT_HORIZONS = (1, 3, 6, 12)
DEFAULT_VOL_METHOD = "ewma"

# EWMA-vs-rolling ratio thresholds for the regime flag: outside this
# band, recent vol has moved materially away from its 3-year norm.
_REGIME_HI = 1.15
_REGIME_LO = 0.85

DISCLAIMER = ("Damped-drift point + calibrated 90% band. Tracker context, "
              "not trading advice. Fundamentals-driven return signals were "
              "tested and rejected (EMH null — see markets/fundamentals.py).")


@dataclass(frozen=True)
class BoardRow:
    """One ticker × horizon forecast with its diagnostics."""
    symbol: str
    name: str
    tier: str
    horizon_months: int
    target_month: str
    last_close: float
    point: float
    lo90: float
    hi90: float
    monthly_vol: float
    vol_method: str
    band_multiplier: float
    band_calibrated: bool
    cap_hit: bool
    mom12: Optional[float]          # trailing 12m log change
    vol_regime: str                 # 'elevated' | 'normal' | 'calm'
    leading_indicator: bool         # ticker survives the forward screen


def _surviving_indicator_symbols() -> set[str]:
    """Tickers that are robust screen survivors (the forward direction)."""
    from importlib.resources import files
    try:
        payload = json.loads(
            (files("census_forecaster") / "data" / "markets"
             / "selected_signals.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {s["ticker"] for s in payload.get("signals", [])
            if s.get("robust_to_2020_exclusion")}


def _vol_regime(bars, vol_method: str) -> str:
    recent = _monthly_vol(bars, method=vol_method)
    base = _monthly_vol(bars, method="rolling")
    if not recent or not base:
        return "normal"
    ratio = recent / base
    if ratio > _REGIME_HI:
        return "elevated"
    if ratio < _REGIME_LO:
        return "calm"
    return "normal"


def forecast_board(
    panel: Optional[PricesPanel] = None,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    vol_method: str = DEFAULT_VOL_METHOD,
) -> list[BoardRow]:
    """Full forecast board over the pre-registered ticker universe."""
    if panel is None:
        panel = load_prices_panel()
    survivors = _surviving_indicator_symbols()
    rows: list[BoardRow] = []
    for spec in TICKERS:
        if spec.symbol not in panel.series:
            continue
        bars = panel.bars(spec.symbol)
        if len(bars) < 24:
            continue
        z = calibrate_band_multiplier(bars, vol_method=vol_method)
        mom12 = panel.momentum(spec.symbol, 12)
        regime = _vol_regime(bars, vol_method)
        last = bars[-1]
        for h in horizons:
            total = last.year * 12 + (last.month - 1) + h
            target = date(total // 12, total % 12 + 1, 28)
            fc = forecast_ticker(bars, target, band_multiplier=z,
                                 vol_method=vol_method)
            rows.append(BoardRow(
                symbol=spec.symbol,
                name=spec.name,
                tier=spec.tier,
                horizon_months=h,
                target_month=f"{target.year}-{target.month:02d}",
                last_close=round(last.adj_close, 2),
                point=round(fc.value, 2),
                lo90=round(fc.lo90, 2),
                hi90=round(fc.hi90, 2),
                monthly_vol=round(fc.monthly_vol, 4),
                vol_method=vol_method,
                band_multiplier=round(fc.band_multiplier, 3),
                band_calibrated=z is not None,
                cap_hit=fc.cap_hit,
                mom12=round(mom12, 4) if mom12 is not None else None,
                vol_regime=regime,
                leading_indicator=spec.symbol in survivors,
            ))
    return rows


def print_board(rows: Sequence[BoardRow], out=sys.stdout) -> None:
    print(f"\n{DISCLAIMER}\n", file=out)
    print(f"{'symbol':<7}{'tier':<8}{'h':>3}{'target':>9}{'last':>9}"
          f"{'point':>9}{'lo90':>9}{'hi90':>9}{'z':>6}"
          f"{'regime':>10}{'lead':>6}", file=out)
    for r in rows:
        print(f"{r.symbol:<7}{r.tier:<8}{r.horizon_months:>3}"
              f"{r.target_month:>9}{r.last_close:>9.2f}{r.point:>9.2f}"
              f"{r.lo90:>9.2f}{r.hi90:>9.2f}{r.band_multiplier:>6.2f}"
              f"{r.vol_regime:>10}{'*' if r.leading_indicator else '':>6}",
              file=out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-ticker damped-drift forecast board (offline, "
                    "bundled panel). Not trading advice.")
    parser.add_argument("--panel", type=Path, default=None)
    parser.add_argument("--horizons", type=int, nargs="+",
                        default=list(DEFAULT_HORIZONS))
    parser.add_argument("--vol", choices=("ewma", "rolling"),
                        default=DEFAULT_VOL_METHOD)
    parser.add_argument("--json", type=Path, default=None,
                        help="Also write the board as JSON.")
    args = parser.parse_args(argv)

    try:
        panel = load_prices_panel(args.panel)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    rows = forecast_board(panel, horizons=tuple(args.horizons),
                          vol_method=args.vol)
    if not rows:
        print("ERROR: no forecastable tickers in panel", file=sys.stderr)
        return 2
    print(f"[forecaster] panel fetched {panel.fetch_date}; "
          f"{len({r.symbol for r in rows})} tickers × "
          f"{len(args.horizons)} horizons, vol={args.vol}", file=sys.stderr)
    print_board(rows)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_from_panel": panel.fetch_date,
            "vol_method": args.vol,
            "disclaimer": DISCLAIMER,
            "rows": [asdict(r) for r in rows],
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[forecaster] wrote {args.json}", file=sys.stderr)
    return 0


__all__ = ["BoardRow", "forecast_board", "print_board", "main",
           "DEFAULT_HORIZONS", "DEFAULT_VOL_METHOD", "DISCLAIMER"]

if __name__ == "__main__":
    raise SystemExit(main())
