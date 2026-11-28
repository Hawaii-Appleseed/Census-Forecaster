"""Behavioural responses for the excise scorers: sourced low, middle and high values.

Each value is the percent change in the taxed quantity for a one percent
change in its price with tax. Every figure below was read from the source
itself on November 28, 2026; none is from memory. ``low`` is the smallest
response (the most revenue from a rate increase), ``high`` the largest.

The scorers stay static unless a caller passes an elasticity. An ad valorem
tax (transient accommodations) needs nothing else. A per-unit tax
(cigarettes, fuel) also needs the price with tax, which is not bundled: no
Hawaiʻi retail price series is read yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Elasticity:
    low: float
    mid: float
    high: float
    what: str
    sources: tuple[str, ...]

    def items(self) -> tuple[tuple[str, float], ...]:
        return (("low", self.low), ("mid", self.mid), ("high", self.high))


ELASTICITIES: dict[str, Elasticity] = {
    # Hawaiʻi-specific, and the studies disagree: the range is wide on purpose.
    "transient_accommodations": Elasticity(
        low=0.0, mid=-1.0, high=-1.5,
        what="hotel room demand in Hawaiʻi with respect to the room price",
        sources=(
            "low: Bonham, C. and Gangnes, B. (1996), 'Intervention analysis with cointegrated time "
            "series: the case of the Hawaii hotel room tax', Applied Economics 28, 1281-1293: 'no "
            "statistically significant impact on hotel room revenues' of the 5% room tax of January "
            "1987. https://economics.hawaii.edu/research/workingpapers/88-98/WP_95-5.pdf",
            "mid and high: Fujii, E., Khaled, M. and Mak, J. (1988), 'The incidence and "
            "exportability of hotel room taxes: some further estimates', University of Hawaii "
            "Department of Economics Working Paper 88-9, abstract: the elasticity of demand for hotel "
            "rooms is 1.0 in their earlier estimates (1985) and 1.5 in the fuller model, in absolute "
            "terms. https://www.economics.hawaii.edu/research/workingpapers/88-98/WP_88-9.pdf",
        ),
    ),
    # National, a review of 16 studies of adults; not Hawaiʻi-specific.
    "cigarettes": Elasticity(
        low=-0.29, mid=-0.37, high=-0.47,
        what="total demand for tobacco by adults (use and amount) with respect to price",
        sources=(
            "Community Preventive Services Task Force (2012), 'Tobacco Use: Interventions to Increase "
            "the Unit Price for Tobacco Products', Finding and Rationale Statement, table 'Summary "
            "Price Elasticity Estimates': adults, total demand, 16 studies, median -0.37, "
            "interquartile interval -0.47 to -0.29. https://stacks.cdc.gov/view/cdc/168602",
        ),
    ),
    # National, short-run. The recent range is the low and middle; the 1970s range the high.
    "gasoline": Elasticity(
        low=-0.034, mid=-0.077, high=-0.21,
        what="short-run US gasoline demand with respect to price",
        sources=(
            "Hughes, J., Knittel, C. and Sperling, D. (2006), 'Evidence of a Shift in the Short-Run "
            "Price Elasticity of Gasoline Demand', NBER Working Paper 12530, abstract: short-run price "
            "elasticities 'range from -0.034 to -0.077 during 2001 to 2006, versus -0.21 to -0.34 for "
            "1975 to 1980'. low and mid are the ends of the 2001-2006 range, high the smaller end of "
            "the 1975-1980 range. https://www.nber.org/papers/w12530",
        ),
    ),
}


def get(name: str) -> Elasticity:
    if name not in ELASTICITIES:
        raise KeyError(f"no sourced elasticity for {name!r}; there are {sorted(ELASTICITIES)}")
    return ELASTICITIES[name]
