"""Hawaiʻi's alternative tax on net long-term capital gains, HRS §235-51(f).

When a filer has a net capital gain, the tax is the lower of the regular
bracket tax on taxable income and the sum of

1. the bracket tax on the greater of (A) taxable income less the net
   capital gain and (B) the taxable income taxed at a rate below the
   alternative rate, and
2. the alternative rate on the rest of taxable income.

Here (B) is the floor of the first bracket whose rate reaches the
alternative rate (:func:`cap_floor`): with a schedule whose rates fall as
income rises, the first such bracket is the one used. With rates that never
fall this is exactly the statute's (B). Where a rate falls back below the
alternative rate after one reaches it, the literal sum of the income in
below-rate brackets can give more or less tax, so it is a reading, and the
simulator page says so (``plan.js``'s ``falling_cg`` warning). A rate at or above
every bracket rate leaves nothing below (B)'s floor to tax differently, so
the tax is the regular tax.

This is the one implementation for ``TaxCalculator`` (systems with
``cg_alt_tax="statute"``) and the capital-gains page
(``forecast_cg_rate_options.Scorer``). ``site/assets/simulator/kernel.js``
mirrors it. Registry systems keep the legacy "stacked" shortcut,
``min(bracket tax on the gains, 7.25% x gains)``, so the published Act 24
and Act 46 runs reproduce.
"""
from __future__ import annotations

import numpy as np


def cap_floor(floors: np.ndarray, rates: np.ndarray, cap: float) -> float:
    """The floor of the first bracket whose rate reaches ``cap`` (decimal),
    or infinity when none does."""
    at_or_above = rates >= cap - 1e-12
    return floors[np.argmax(at_or_above)] if at_or_above.any() else np.inf


def _bracket(ti: np.ndarray, floors: np.ndarray, rates: np.ndarray,
             cum: np.ndarray) -> np.ndarray:
    # the same operations as TaxCalculator._vectorized_bracket_tax
    idx = np.clip(np.searchsorted(floors, ti, side="right") - 1, 0, len(floors) - 1)
    return cum[idx] + (ti - floors[idx]) * rates[idx]


def alternative_tax(ti: np.ndarray, gains: np.ndarray, floors: np.ndarray, rates: np.ndarray,
                    cum: np.ndarray, cap: float | None) -> np.ndarray:
    """Tax on taxable income ``ti`` with net long-term capital gains
    ``gains``, under one bracket schedule (``TaxCalculator._bracket_schedule``:
    floors, decimal rates, tax owed at each floor) and alternative rate
    ``cap`` (decimal; None: no alternative tax, gains are ordinary income).

    Gains above taxable income count only up to it. With zero gains the
    result is exactly the regular bracket tax.
    """
    regular = _bracket(ti, floors, rates, cum)
    if cap is None:
        return regular
    ncg = np.clip(gains, 0.0, ti)
    base = np.maximum(ti - ncg, np.minimum(ti, cap_floor(floors, rates, cap)))
    alt = _bracket(base, floors, rates, cum) + cap * (ti - base)
    return np.minimum(regular, alt)
