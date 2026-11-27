"""Scorers for the transient accommodations tax and the per-unit excises.

Each tax is a base times a rate. The base in a fiscal year is the phase 2
baseline's revenue over the rate in force (``rate_in``), or, where DOTAX
publishes it, the base itself grown with that baseline (fuel gallons by type,
cigarettes from tobacco collections by product). A change is the new rate
minus the old, times that base, for a whole fiscal year.

Rates come from DOTAX documents, not from memory:

- Transient accommodations: 10.25% from January 1, 2018 through December 31,
  2030, 9.25% before and after; Act 96, SLH 2025 adds 0.75 points from January
  1, 2026 through December 31, 2030 and taxes cruise fares (DOTAX Annual
  Report FY2025, §1.5.1 and §1.5.3).
- Rental motor vehicle surcharge: $6.00 a day, $6.50 from January 1, 2024,
  $7.00 from January 1, 2025 (§1.7.1). Tour vehicles and car sharing are
  taxed otherwise and are taken as part of the same base (they are small).
- Cigarettes: 16 cents each from July 1, 2011 (§1.8.1).
- Fuel per gallon by type, and the environmental response tax per barrel and
  per MMBtu: each type's state tax over its gallons in DOTAX's calendar-year
  fuel report (``data/excise_bases.json``; the schedule has been unchanged
  since July 1, 2020).

Static by default. ``elasticity`` (of the taxed quantity to the tax-inclusive
price) applies where the price is known: the transient accommodations tax,
an ad valorem rate. Liquor, motor vehicle and the rest take
``score_proportional``: a rate change of x% moves the line's revenue x%.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache

from state_revenue.general_excise import DATA, Score, burden_by_purchaser, shares
from state_revenue.ledger import load_ledger

#: (first day, rate). Each holds until the next one's first day.
SCHEDULES: dict[str, tuple[tuple[date, float], ...]] = {
    "transient_accommodations": (
        (date(2010, 7, 1), 0.0925),
        (date(2018, 1, 1), 0.1025),
        (date(2026, 1, 1), 0.1100),            # Act 96, SLH 2025: +0.75 points
        (date(2031, 1, 1), 0.0925),            # both increases end December 31, 2030
    ),
    "rental_vehicle": (                        # dollars a day
        (date(2000, 1, 1), 6.00),
        (date(2024, 1, 1), 6.50),
        (date(2025, 1, 1), 7.00),
    ),
    "cigarettes": ((date(2011, 7, 1), 0.16),),  # dollars a cigarette
}


def rate_in(tax: str, fiscal_year: int) -> float:
    """The rate in force over a fiscal year (July to June), weighted by days."""
    steps = SCHEDULES[tax]
    start, end = date(fiscal_year - 1, 7, 1), date(fiscal_year, 7, 1)
    total = 0.0
    for i, (first, rate) in enumerate(steps):
        until = steps[i + 1][0] if i + 1 < len(steps) else date.max
        lo, hi = max(first, start), min(until, end)
        if hi > lo:
            total += rate * (hi - lo).days
    days = (end - start).days
    if total == 0.0 and steps[0][0] >= end:
        raise ValueError(f"no {tax} rate before FY{fiscal_year}")
    return total / days


@lru_cache(maxsize=1)
def load_bases() -> dict:
    return json.loads((DATA / "excise_bases.json").read_text())


def _line(line: str, fiscal_year: int, baseline: dict[int, float] | None) -> float:
    """A line's revenue in a fiscal year, $ thousands: actual if reported, else baseline."""
    ledger = load_ledger("all")
    if fiscal_year in ledger.years and line in ledger[fiscal_year].lines:
        return float(ledger[fiscal_year].lines[line])
    if baseline is None:
        from state_revenue.baseline import build_baseline
        baseline = build_baseline("all").line(line)
    return baseline[fiscal_year]


def _growth(line: str, fiscal_year: int, data_fy: int, baseline: dict[int, float] | None) -> float:
    return _line(line, fiscal_year, baseline) / _line(line, data_fy, baseline)


# --- transient accommodations ---------------------------------------------------

def score_tat(rate: float, fiscal_year: int, *, elasticity: float = 0.0,
              baseline: dict[int, float] | None = None) -> Score:
    """Set the transient accommodations tax rate for a whole fiscal year.

    Base: the baseline's collections over the rate in force. ``elasticity``
    is room nights' response to the price with tax (``-0.5``: a 1% higher
    price, 0.5% fewer nights). Who bears it: the input-output table's
    accommodation tax by final purchaser (visitors about 70%).
    """
    old = rate_in("transient_accommodations", fiscal_year)
    revenue = _line("transient_accommodations", fiscal_year, baseline)
    base = revenue / old
    quantity = 1.0 + elasticity * ((1 + rate) / (1 + old) - 1)
    delta = rate * base * quantity - revenue
    return Score(fiscal_year, delta, 0.0, shares(burden_by_purchaser(tax="state_tat")),
                 {"base": base, "rate_in_force": old})


# --- fuel and the environmental response tax --------------------------------------

def fuel_rates() -> dict[str, float]:
    """State tax per unit by fuel type, from the calendar-year report."""
    return {k: v["state_tax"] / v["quantity"] for k, v in load_bases()["fuel"].items()
            if v["quantity"] > 0}


def score_fuel(new_rates: dict[str, float], fiscal_year: int, *,
               baseline_fuel: dict[int, float] | None = None,
               baseline_ert: dict[int, float] | None = None) -> Score:
    """Set state rates per unit by fuel type (``{"gasoline": 0.20}``, dollars a
    gallon; ``ert_barrel`` dollars a barrel, ``ert_mmbtu`` per MMBtu).

    Quantities: the calendar-year report's, grown with the baseline of the
    line that holds them (fuel, or environmental response for ``ert_*``);
    the calendar year is read as the fiscal year that ends in the next June.
    County fuel taxes are unchanged.
    """
    bases = load_bases()
    data_fy = bases["calendar_year"] + 1
    current = fuel_rates()
    detail = {}
    for fuel, rate in new_rates.items():
        if fuel not in current:
            raise KeyError(f"unknown fuel {fuel!r}; the report has {sorted(current)}")
        line = "environmental_response" if fuel.startswith("ert_") else "fuel"
        g = _growth(line, fiscal_year, data_fy,
                    baseline_ert if fuel.startswith("ert_") else baseline_fuel)
        quantity = bases["fuel"][fuel]["quantity"] * g
        detail[fuel] = (rate - current[fuel]) * quantity / 1e3
    return Score(fiscal_year, sum(detail.values()), 0.0, {}, detail)


# --- cigarettes ---------------------------------------------------------------------

def cigarettes_sold(fiscal_year: int, *, baseline: dict[int, float] | None = None) -> float:
    """Cigarettes taxed in a fiscal year: the calendar-year collections over the
    rate, grown with the tobacco line's baseline."""
    bases = load_bases()
    data_fy = bases["calendar_year"] + 1
    sticks = bases["tobacco"]["cigarettes"] / SCHEDULES["cigarettes"][-1][1]
    return sticks * _growth("tobacco", fiscal_year, data_fy, baseline)


def score_cigarettes(per_cigarette: float, fiscal_year: int, *, elasticity: float = 0.0,
                     pack_price: float | None = None,
                     baseline: dict[int, float] | None = None) -> Score:
    """Set the tax per cigarette (dollars; 0.16 now) for a whole fiscal year.

    With ``elasticity`` and ``pack_price`` (the price of 20 with tax, dollars),
    sales respond to the change in the pack's price.
    """
    old = rate_in("cigarettes", fiscal_year)
    sticks = cigarettes_sold(fiscal_year, baseline=baseline)
    response = 1.0
    if elasticity and pack_price:
        response = 1.0 + elasticity * (20 * (per_cigarette - old) / pack_price)
    delta = (per_cigarette * sticks * response - old * sticks) / 1e3
    return Score(fiscal_year, delta, 0.0, {}, {"cigarettes": sticks * response, "rate_in_force": old})


# --- rental vehicles ----------------------------------------------------------------

def score_rental_vehicle(per_day: float, fiscal_year: int, *,
                         baseline: dict[int, float] | None = None) -> Score:
    """Set the rental motor vehicle surcharge per day for a whole fiscal year."""
    old = rate_in("rental_vehicle", fiscal_year)
    revenue = _line("rental_vehicle", fiscal_year, baseline)
    days = revenue * 1e3 / old
    return Score(fiscal_year, (per_day - old) * days / 1e3, 0.0, {},
                 {"vehicle_days": days, "rate_in_force": old})


# --- everything else ----------------------------------------------------------------

def score_proportional(line: str, multiplier: float, fiscal_year: int, *,
                       baseline: dict[int, float] | None = None) -> Score:
    """Scale a line's rates by ``multiplier`` (1.10: every rate 10% higher);
    static, so its revenue scales the same."""
    revenue = _line(line, fiscal_year, baseline)
    return Score(fiscal_year, (multiplier - 1.0) * revenue, 0.0, {}, {"revenue": revenue})


def main(argv: list[str] | None = None) -> int:
    """Print a few standard changes.

        python -m state_revenue.excise [--fiscal-year 2027]
    """
    import argparse
    ap = argparse.ArgumentParser(description="Transient accommodations and excise scorers.")
    ap.add_argument("--fiscal-year", type=int, default=2027)
    fy = ap.parse_args(argv).fiscal_year
    tat_now = rate_in("transient_accommodations", fy)
    cases = [
        (f"Transient accommodations +1 point ({tat_now:.2%} to {tat_now + 0.01:.2%})",
         score_tat(tat_now + 0.01, fy)),
        ("  same, elasticity -0.5", score_tat(tat_now + 0.01, fy, elasticity=-0.5)),
        ("Gasoline and highway diesel +5 cents a gallon",
         score_fuel({"gasoline": fuel_rates()["gasoline"] + 0.05,
                     "diesel_highway": fuel_rates()["diesel_highway"] + 0.05}, fy)),
        ("Environmental response tax +$1 a barrel",
         score_fuel({"ert_barrel": fuel_rates()["ert_barrel"] + 1.0}, fy)),
        ("Cigarettes +5 cents each ($1 a pack)", score_cigarettes(0.21, fy)),
        ("Rental vehicle surcharge +$1 a day",
         score_rental_vehicle(rate_in("rental_vehicle", fy) + 1.0, fy)),
        ("Liquor rates +10%", score_proportional("liquor", 1.10, fy)),
    ]
    print(f"FY{fy}, change in state revenue ($ millions):\n")
    print("| Change | State | Borne by visitors |\n|---|---|---|")
    for label, s in cases:
        v = s.borne_by.get("visitors")
        print(f"| {label} | {s.state / 1e3:,.1f} | {'' if v is None else f'{v:.0%}'} |")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
