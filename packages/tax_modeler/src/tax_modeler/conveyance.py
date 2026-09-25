"""Hawaii conveyance tax (HRS chapter 247): current law and SB 3028 HD2 (2026).

Current law, HRS §247-2 (last amended L 2009, c 59), is a *cliff* schedule:
once a price crosses a threshold, one rate applies to the whole price.
Schedule (1) covers every conveyance except (2): condominium or single-family
sales to a purchaser ineligible for a county homeowner exemption.

SB 3028 SD2 HD2 (April 8, 2026; failed in conference) moves residential sales
to *marginal* rates, like income tax brackets, with separate schedules for
owner-occupant and other purchasers and a cap on the total, CPI-indexes the
residential brackets from 2027, and leaves nonresidential property on the
current schedule (1).

Categories used here:
  "owner"     residential, purchaser eligible for the homeowner exemption
  "nonowner"  residential (condo / single-family), purchaser ineligible
  "nonres"    everything else (commercial, agricultural, hotel, land, ...)
"""
from __future__ import annotations

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
    on the total as a share of price."""
    brackets: tuple[tuple[float, float], ...]
    cap: float


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


def cliff_tax(price: np.ndarray, schedule=CURRENT_1) -> np.ndarray:
    price = np.asarray(price, dtype=float)
    uppers = np.array([u for u, _ in schedule])
    rates = np.array([r for _, r in schedule])
    return price * rates[np.searchsorted(uppers, price, side="right")]


def marginal_tax(price: np.ndarray, schedule: MarginalSchedule, index: float = 1.0) -> np.ndarray:
    """Tax under a marginal schedule whose bracket bounds are scaled by *index*
    (CPI adjustment; upward only in the bill)."""
    price = np.asarray(price, dtype=float)
    lows = np.array([lo for lo, _ in schedule.brackets]) * max(index, 1.0)
    rates = np.array([r for _, r in schedule.brackets])
    highs = np.append(lows[1:], np.inf)
    slices = np.clip(price[..., None] - lows, 0, highs - lows)
    return np.minimum((slices * rates).sum(axis=-1), schedule.cap * price)


def current_law_tax(price, category: str) -> np.ndarray:
    return cliff_tax(price, CURRENT_2 if category == "nonowner" else CURRENT_1)


def hd2_tax(price, category: str, index: float = 1.0) -> np.ndarray:
    if category == "owner":
        return marginal_tax(price, HD2_OWNER, index)
    if category == "nonowner":
        return marginal_tax(price, HD2_NONOWNER, index)
    return cliff_tax(price, CURRENT_1)     # nonresidential: unchanged


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
