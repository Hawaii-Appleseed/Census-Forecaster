"""Hawaiʻi conveyance tax (HRS chapter 247): current law, SB 3028 HD2 (2026)
and two benchmark bills.

Current law, HRS §247-2 (last amended L 2009, c 59), is a *cliff* schedule:
once a price crosses a threshold, one rate applies to the whole price.
Schedule (1) covers every conveyance except (2): condominium or single-family
sales to a purchaser ineligible for a county homeowner exemption.

SB 3028 SD2 HD2 (April 8, 2026; failed in conference) moves residential sales
to *marginal* rates, like income tax brackets, with separate schedules for
owner-occupant and other purchasers and a cap on the total, CPI-indexes the
residential brackets from 2027, and leaves nonresidential property on the
current schedule (1).

No official revenue estimate exists for SB 3028, so two bills that have one
are scored as benchmarks:
  HB 2049 HD3 (2026; passed the House March 10, 2026; Rep. Evslin put the
    increase at about $170M a year) has HD2's structure with steeper rates
    from $600K (above $2M: owner 3.75-6.25%, non-owner 6.5-9.5%), the same
    4% / 6% caps, brackets indexed from 2027, and nonresidential on the
    current schedule (1).
  HB 1410 HD2 (2025; DOTAX's table to House Finance, February 25, 2025,
    implies +$58M in FY2026) makes schedule (1) marginal for owner-occupant
    *and* nonresidential sales, adds a marginal schedule (2) for non-owner
    purchases, has no cap, and indexes the brackets from 2026.
BILLS maps each bill to its tax function and first indexed year.

Both 2026 bills also CPI-index schedule (3), the cliff for property with no
dwelling unit (§247-2(b) names (a)(1)-(3)). Nonresidential is left unindexed
here, as in hd2_tax; on Maui's binned sales that is about $0.04M of $5.8M in
FY2028.

Bill texts (read September 25, 2026):
  https://data.capitol.hawaii.gov/sessions/session2026/bills/SB3028_HD2_.HTM
  https://data.capitol.hawaii.gov/sessions/session2026/bills/HB2049_HD3_.HTM
  https://data.capitol.hawaii.gov/sessions/session2025/bills/HB1410_HD2_.HTM

Categories used here:
  "owner"     residential, purchaser eligible for the homeowner exemption
  "nonowner"  residential (condo / single-family), purchaser ineligible
  "nonres"    everything else (commercial, agricultural, hotel, land, ...)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

CATEGORIES = ("owner", "nonowner", "nonres")

# §247-2 (1) and (2): (upper bound of band, rate on the whole price).
CURRENT_1 = ((600e3, .0010), (1e6, .0020), (2e6, .0030), (4e6, .0050),
             (6e6, .0070), (10e6, .0090), (np.inf, .0100))
CURRENT_2 = ((600e3, .0015), (1e6, .0025), (2e6, .0040), (4e6, .0060),
             (6e6, .0085), (10e6, .0110), (np.inf, .0125))


@dataclass(frozen=True)
class MarginalSchedule:
    """Marginal brackets: (lower bound, rate on the value above it), plus a cap
    on the total as a share of price (np.inf: no cap)."""
    brackets: tuple[tuple[float, float], ...]
    cap: float = np.inf


# SB 3028 SD2 HD2, §247-2 as amended. The bill states each band as a base
# amount plus a rate; the bases are exactly the cumulative marginal tax, e.g.
# owner $26,300 at $3M = 0.10% x 600K + 0.30% x 400K + 0.45% x 1M + 2% x 1M.
HD2_OWNER = MarginalSchedule(
    brackets=((0, .0010), (600e3, .0030), (1e6, .0045), (2e6, .02),
              (3e6, .04), (6e6, .05), (10e6, .06)),
    cap=.04)
HD2_NONOWNER = MarginalSchedule(
    brackets=((0, .0015), (600e3, .0035), (1e6, .0065), (2e6, .05),
              (3e6, .07), (6e6, .08), (10e6, .09)),
    cap=.06)
HD2_FIRST_INDEX_YEAR = 2027   # brackets adjusted annually by Urban Hawaii CPI from 2027

# HB 2049 HD3, §247-2(a)(1) and (2) as amended: HD2's form, steeper from $600K.
# Printed bases: owner $600, $2,000, $8,000, $83,000, $168,000, $378,000;
# non-owner $900, $2,900, $9,400, $139,400, $289,400, $639,400.
# §247-2(c) caps the total at 4% / 6%; (b) indexes "for each taxable year
# beginning after December 31, 2026".
HB2049_HD3_OWNER = MarginalSchedule(
    brackets=((0, .0010), (600e3, .0035), (1e6, .0060), (2e6, .0375),
              (4e6, .0425), (6e6, .0525), (10e6, .0625)),
    cap=.04)
HB2049_HD3_NONOWNER = MarginalSchedule(
    brackets=((0, .0015), (600e3, .0050), (1e6, .0065), (2e6, .0650),
              (4e6, .0750), (6e6, .0875), (10e6, .0950)),
    cap=.06)
HB2049_HD3_FIRST_INDEX_YEAR = 2027

# HB 1410 HD2, §247-2(a) as amended (the rates of SB 3028 as introduced).
# Schedule (1), "except as provided in paragraph (2)", covers owner-occupant
# and nonresidential sales; (2) covers a condominium, single-family residence
# or agricultural land with a dwelling bought by a purchaser ineligible for the
# homeowner exemption. Printed bases: (1) $600, $2,000, $8,000, $25,000,
# $49,000, $119,000; (2) $900, $2,500, $9,000, $49,000, $99,000, $229,000.
# No cap; (b) indexes "for each taxable year beginning after December 31, 2025".
HB1410_HD2_1 = MarginalSchedule(
    brackets=((0, .0010), (600e3, .0035), (1e6, .0060), (2e6, .0085),
              (4e6, .0120), (6e6, .0175), (10e6, .0300)))
HB1410_HD2_2 = MarginalSchedule(
    brackets=((0, .0015), (600e3, .0040), (1e6, .0065), (2e6, .0200),
              (4e6, .0250), (6e6, .0325), (10e6, .0410)))
HB1410_HD2_FIRST_INDEX_YEAR = 2026


def cliff_tax(price: np.ndarray, schedule=CURRENT_1) -> np.ndarray:
    price = np.asarray(price, dtype=float)
    uppers = np.array([u for u, _ in schedule])
    rates = np.array([r for _, r in schedule])
    return price * rates[np.searchsorted(uppers, price, side="right")]


def marginal_tax(price: np.ndarray, schedule: MarginalSchedule, index: float = 1.0) -> np.ndarray:
    """Tax under a marginal schedule whose bracket bounds are scaled by *index*
    (CPI adjustment; upward only in the bills)."""
    price = np.asarray(price, dtype=float)
    lows = np.array([lo for lo, _ in schedule.brackets]) * max(index, 1.0)
    rates = np.array([r for _, r in schedule.brackets])
    highs = np.append(lows[1:], np.inf)
    slices = np.clip(price[..., None] - lows, 0, highs - lows)
    tax = (slices * rates).sum(axis=-1)
    return tax if np.isinf(schedule.cap) else np.minimum(tax, schedule.cap * price)


def current_law_tax(price, category: str) -> np.ndarray:
    return cliff_tax(price, CURRENT_2 if category == "nonowner" else CURRENT_1)


def hd2_tax(price, category: str, index: float = 1.0) -> np.ndarray:
    if category == "owner":
        return marginal_tax(price, HD2_OWNER, index)
    if category == "nonowner":
        return marginal_tax(price, HD2_NONOWNER, index)
    return cliff_tax(price, CURRENT_1)     # nonresidential: unchanged


def hb2049_hd3_tax(price, category: str, index: float = 1.0) -> np.ndarray:
    if category == "owner":
        return marginal_tax(price, HB2049_HD3_OWNER, index)
    if category == "nonowner":
        return marginal_tax(price, HB2049_HD3_NONOWNER, index)
    return cliff_tax(price, CURRENT_1)     # nonresidential: schedule (3), today's rates


def hb1410_hd2_tax(price, category: str, index: float = 1.0) -> np.ndarray:
    if category == "nonowner":
        return marginal_tax(price, HB1410_HD2_2, index)
    return marginal_tax(price, HB1410_HD2_1, index)   # owner and nonresidential


# Bill name -> (tax function with hd2_tax's signature, first indexed taxable year).
BILLS: dict[str, tuple[Callable[..., np.ndarray], int]] = {
    "SB3028 HD2": (hd2_tax, HD2_FIRST_INDEX_YEAR),
    "HB2049 HD3": (hb2049_hd3_tax, HB2049_HD3_FIRST_INDEX_YEAR),
    "HB1410 HD2": (hb1410_hd2_tax, HB1410_HD2_FIRST_INDEX_YEAR),
}


def value_above(tiers: list[tuple[float, float]], t: float, alpha: float | None = None) -> float:
    """Total value above *t* in a property-tax class, from its tier values.

    County tax rolls report, for tiered classes, how much of the class's value
    falls in each band (e.g. Maui non-owner-occupied: the part of each
    parcel's value up to $1M, $1M-$2.5M, above $2.5M). *tiers* is a list of
    (lower bound, value in the band); the value above any bound is the sum of
    the bands above it. Between or beyond bounds the value above t is
    interpolated as a Pareto tail, log-linear in t, which is exact for a
    Pareto distribution. With a single interior bound, *alpha* (the Pareto
    index) must be given.
    """
    bounds = sorted(lo for lo, _ in tiers if lo > 0)
    above = {b: sum(v for lo, v in tiers if lo >= b) for b in bounds}
    if t in above:
        return float(above[t])
    if not bounds:
        raise ValueError("class has no tiers above zero")
    if len(bounds) == 1:
        if alpha is None:
            raise ValueError("one tier bound: pass alpha")
        b1, slope = bounds[0], alpha - 1
    else:
        # the two bounds bracketing t, or the nearest two when t is outside them
        i = int(np.clip(np.searchsorted(bounds, t) - 1, 0, len(bounds) - 2))
        b1, b2 = bounds[i], bounds[i + 1]
        slope = np.log(above[b1] / above[b2]) / np.log(b2 / b1)
    return float(above[b1] * (t / b1) ** -slope)


def pareto_alpha_grouped(bands: list[tuple[float, float, float]], x_min: float) -> float:
    """Maximum-likelihood Pareto index for counts in price bands above *x_min*.

    *bands*: (lower, upper or inf, count), all lower bounds >= x_min.
    """
    grid = np.arange(1.05, 5.0, 0.001)
    ll = np.zeros_like(grid)
    for lo, hi, n in bands:
        p = (lo / x_min) ** -grid - (0.0 if np.isinf(hi) else (hi / x_min) ** -grid)
        ll += n * np.log(np.maximum(p, 1e-300))
    return float(grid[np.argmax(ll)])


def breakeven_price(category: str) -> float:
    """Price below which HD2 owes less than current law (residential), in the
    $2M-$4M band where current law is a flat 0.50% / 0.60%."""
    grid = np.arange(2e6, 4e6, 1e3)
    diff = hd2_tax(grid, category) - current_law_tax(grid, category)
    return float(grid[np.argmax(diff > 0)])
