"""Derive annual leading-indicator signals from the causal-screen survivors.

The bridge between Phase 2 (screen) and the forecaster: turns the
monthly prices panel into **annual, June-cutoff** signal values that the
ML feature builder (``acs/ml_features.py``) can consume, written to
``data/leading_indicators/market_signals.json``.

No-peeking discipline
---------------------
The value for anchor year Y uses ONLY bars from June of year Y or
earlier (``as_of_month=6``). ACS 1-year estimates describe interviews
centred mid-year, so a June cutoff aligns the signal with what was
knowable when the ACS reference period was still in progress — the same
discipline ``backtest.acs.truncate_to_anchor`` enforces for anchors. A
July market shock must never move year-Y's signal (tested).

Channels
--------
Signals are per-channel 12-month momenta (June-to-June log change), not
per-ticker, so the feature count stays small (the values are
geoid-constant and act as year-effects in the pooled panel — overfit
risk grows fast with column count):

* ``mkt_energy_mom``   — XLE  (imported-energy → Honolulu CPI channel)
* ``mkt_shipping_mom`` — MATX (ocean freight → Honolulu CPI channel)
* ``mkt_reit_mom``     — mean of surviving REIT tickers (XLRE/VNQ →
  ZHVI/ZORI channel)

A channel is emitted only when ``selected_signals.json`` contains a
BH-passing signal for one of its tickers that is ALSO robust to the
2020-exclusion re-run (``require_robust=True`` default) — one-event
artifacts don't get to become features.
"""
from __future__ import annotations

import math
from typing import Mapping, Optional, Sequence

from .panel import PricesPanel

# channel name → the tickers whose screen survival activates it
CHANNELS: dict[str, tuple[str, ...]] = {
    "mkt_energy_mom": ("XLE",),
    "mkt_shipping_mom": ("MATX",),
    "mkt_reit_mom": ("XLRE", "VNQ"),
}

AS_OF_MONTH_DEFAULT = 6  # June — ACS mid-year alignment


def surviving_tickers(
    selected: Mapping,
    *,
    require_robust: bool = True,
) -> set[str]:
    """Tickers with at least one qualifying signal in selected_signals."""
    out: set[str] = set()
    for sig in selected.get("signals", []):
        if require_robust and not sig.get("robust_to_2020_exclusion"):
            continue
        out.add(sig["ticker"])
    return out


def _june_momentum(
    panel: PricesPanel, symbol: str, year: int, *, as_of_month: int,
) -> Optional[float]:
    """12-month log change ending at (year, as_of_month), exact endpoints."""
    return panel.momentum(symbol, 12, as_of=(year, as_of_month))


def derive_annual_signals(
    panel: PricesPanel,
    selected: Mapping,
    *,
    as_of_month: int = AS_OF_MONTH_DEFAULT,
    require_robust: bool = True,
    channels: Mapping[str, Sequence[str]] = CHANNELS,
) -> dict[str, dict[int, float]]:
    """Return {signal_name: {year: value}} for the active channels.

    Per channel and year: the mean June-to-June 12-month log momentum
    over the channel's *surviving* tickers with data at both endpoints.
    Years are emitted only when at least one ticker contributes.
    """
    survivors = surviving_tickers(selected, require_robust=require_robust)
    years = sorted({b.year for bars in panel.series.values() for b in bars})
    out: dict[str, dict[int, float]] = {}

    for name, tickers in channels.items():
        active = [t for t in tickers if t in survivors and t in panel.series]
        if not active:
            continue
        by_year: dict[int, float] = {}
        for year in years:
            vals = []
            for sym in active:
                m = _june_momentum(panel, sym, year, as_of_month=as_of_month)
                if m is not None and math.isfinite(m):
                    vals.append(m)
            if vals:
                by_year[year] = sum(vals) / len(vals)
        if by_year:
            out[name] = by_year
    return out


def build_signals_payload(
    signals: Mapping[str, Mapping[int, float]],
    selected: Mapping,
    *,
    as_of_month: int = AS_OF_MONTH_DEFAULT,
    last_refresh: str = "",
) -> dict:
    """Assemble the leading-indicator JSON payload."""
    return {
        "source": "markets",
        "series_id": "MARKET_SIGNALS_ANNUAL",
        "title": "Market leading-indicator signals (June-cutoff annual "
                 "12-month momenta, screen-gated channels)",
        "frequency": "annual",
        "geography": "national",
        "as_of_month": as_of_month,
        "screen_generated": selected.get("generated", ""),
        "last_refresh": last_refresh,
        "limitations": [
            "Values for year Y use only prices through month "
            f"{as_of_month} of year Y (no-peeking, ACS mid-year "
            "alignment).",
            "Channels are gated on Granger-screen survivors robust to "
            "2020 exclusion; Granger != causation — the forecaster "
            "ablation is the final gate.",
            "Geoid-constant (national/sector prices): acts as a "
            "year-effect in the pooled ML panel.",
        ],
        "signals": {
            name: {str(y): round(v, 6) for y, v in sorted(vals.items())}
            for name, vals in sorted(signals.items())
        },
    }


__all__ = [
    "CHANNELS",
    "AS_OF_MONTH_DEFAULT",
    "surviving_tickers",
    "derive_annual_signals",
    "build_signals_payload",
]
