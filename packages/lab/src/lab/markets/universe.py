"""The pre-registered ticker universe.

Each ticker carries an explicit *hypothesis* — the economic mechanism by
which its price plausibly leads a Hawaii indicator — and the ACS
indicator cells that hypothesis maps to (``affinity_indicators``). The
Phase-2 causal screen tests ONLY these pre-registered ticker→target
pairs. Restricting the pair list up front is deliberate: it caps the
multiple-testing burden and prevents the screen from data-mining its way
into spurious "discoveries" (twelve tickers × every target × 18 leads
would be hundreds of hypotheses).

Three tiers:

* ``broad``  — national market benchmarks (macro wealth / financial
  conditions channel).
* ``sector`` — sector ETFs whose industry maps onto a Hawaii-dominant
  channel (tourism, real estate, energy costs, financial conditions).
* ``hawaii`` — companies listed on US exchanges whose revenue base is
  substantially Hawaii (local credit, utilities, shipping, real estate).

Stooq symbol convention: lowercase + ``.us`` suffix (e.g. ``spy.us``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickerSpec:
    """One pre-registered ticker with its Hawaii-lead hypothesis."""
    symbol: str                # canonical symbol, e.g. "SPY"
    name: str
    tier: str                  # "broad" | "sector" | "hawaii"
    stooq_symbol: str          # e.g. "spy.us"
    hypothesis: str            # economic mechanism, human-readable
    affinity_indicators: tuple[str, ...]  # ACS cells this may lead


# ACS indicator cells (matching acs/sources/base.py conventions):
#   B19013_001E — median household income
#   B25077_001E — median home value
#   B25058_001E / B25064_001E — median contract / gross rent
#   S2301_C04_001E — unemployment rate
#   S1701_C03_001E — poverty rate

TICKERS: tuple[TickerSpec, ...] = (
    # ----- broad market -----
    TickerSpec(
        "SPY", "SPDR S&P 500", "broad", "spy.us",
        "Aggregate equity wealth and financial conditions lead household "
        "income and top-quintile spending with a 6-18 month lag.",
        ("B19013_001E",),
    ),
    TickerSpec(
        "QQQ", "Invesco Nasdaq-100", "broad", "qqq.us",
        "Growth-equity wealth effect; sharper cycle amplitude than SPY "
        "gives earlier turning-point signal for income.",
        ("B19013_001E",),
    ),
    TickerSpec(
        "VTI", "Vanguard Total Market", "broad", "vti.us",
        "Broadest wealth-effect proxy; redundancy check against SPY.",
        ("B19013_001E",),
    ),
    # ----- sector -----
    TickerSpec(
        "JETS", "US Global Jets ETF", "sector", "jets.us",
        "Airline equity prices embed forward bookings; Hawaii tourism "
        "employment follows visitor arrivals, so JETS should lead the "
        "unemployment rate in tourism-dependent counties.",
        ("S2301_C04_001E",),
    ),
    TickerSpec(
        "XLRE", "Real Estate Select Sector", "sector", "xlre.us",
        "REIT pricing embeds forward rent growth and cap-rate moves; "
        "leads measured home values and rents.",
        ("B25077_001E", "B25058_001E", "B25064_001E"),
    ),
    TickerSpec(
        "VNQ", "Vanguard Real Estate", "sector", "vnq.us",
        "Broader REIT universe than XLRE (longer history, pre-2015); "
        "same forward-rent channel.",
        ("B25077_001E", "B25058_001E", "B25064_001E"),
    ),
    TickerSpec(
        "XLF", "Financial Select Sector", "sector", "xlf.us",
        "Bank equity prices lead credit availability, which leads "
        "household income and home purchases.",
        ("B19013_001E",),
    ),
    TickerSpec(
        "XLE", "Energy Select Sector", "sector", "xle.us",
        "Energy equity prices track oil; Hawaii imports ~80% of its "
        "energy, so XLE leads Honolulu CPI (electricity, gasoline, "
        "shipping surcharges).",
        (),  # CPI target is monthly-only; no ACS cell affinity
    ),
    # ----- hawaii-listed -----
    TickerSpec(
        "BOH", "Bank of Hawaii", "hawaii", "boh.us",
        "Local bank equity embeds expected Hawaii credit quality and "
        "loan demand; leads household income and poverty.",
        ("B19013_001E", "S1701_C03_001E"),
    ),
    TickerSpec(
        "FHB", "First Hawaiian Inc", "hawaii", "fhb.us",
        "Second local-credit signal; IPO 2016 so history is short — "
        "confirmatory only.",
        ("B19013_001E", "S1701_C03_001E"),
    ),
    TickerSpec(
        "HE", "Hawaiian Electric", "hawaii", "he.us",
        "Regulated utility with ~95% Hawaii revenue; equity shocks are "
        "Hawaii-specific shocks (2023 Maui-fire crash is a real local "
        "event signature, not market beta).",
        ("B19013_001E", "S2301_C04_001E"),
    ),
    TickerSpec(
        "MATX", "Matson Inc", "hawaii", "matx.us",
        "Dominant Hawaii ocean-freight carrier; shipping volumes/rates "
        "lead goods prices in Honolulu CPI and construction activity.",
        ("B25077_001E",),
    ),
    # Alexander & Baldwin (ALEX) — Hawaii commercial-real-estate REIT —
    # would have been the strongest B25077 candidate, but it was acquired
    # by a Blackstone/MW Group/DivcoWest JV and delisted 2026-03-12; free
    # sources purge delisted histories, so it cannot be fetched keylessly.
    # XLRE/VNQ + MATX cover the real-estate channel instead.
)


def ticker_by_symbol(symbol: str) -> TickerSpec:
    for spec in TICKERS:
        if spec.symbol == symbol:
            return spec
    raise KeyError(f"unknown ticker symbol: {symbol!r}")


__all__ = ["TickerSpec", "TICKERS", "ticker_by_symbol"]
