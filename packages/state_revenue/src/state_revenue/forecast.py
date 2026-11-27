"""Annual forecasts of one tax line from its own history.

Two methods, chosen by how noisy the line is:

``damped``
    Log growth starts at the recency-weighted geometric mean of the last
    :data:`GROWTH_WINDOW` years' growth and decays toward the line's long-run growth
    at φ = 0.85 a year, the repository's damping for annual series
    (``METHODOLOGY.md`` §2.3.1). For lines that move with the economy:
    general excise, income, insurance premiums, public service companies.
``level``
    The median of the last :data:`LEVEL_WINDOW` years, grown at the long-run rate
    from the middle of that window. For lines a few payers or deals decide
    (estate, corporate income, banks, conveyance), whose last year says
    little about the next.

A line is ``level`` when the spread of its year-over-year growth exceeds
:data:`NOISY_SPREAD`. Neither method knows about law changes: a rate cut
already enacted (the Act 46 income tax steps) or a change in how a tax is
split between funds is invisible to it. That is what the backtest against the
Council on Revenues measures.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal

Method = Literal["damped", "level"]

PHI = 0.85              # per year; METHODOLOGY.md §2.3.1, annual cadence
# Chosen on the backtest against the Council (state_revenue.backtest; the grid
# is in STATE_REVENUE_SCOPE.md), so the backtest flatters them somewhat.
GROWTH_WINDOW = 8       # years of growth behind the starting rate
RECENCY = 0.85          # weight per year back
LEVEL_WINDOW = 5        # years whose median is the "level" method's base
NOISY_SPREAD = 0.25     # sd of year-over-year log growth above which a line is "level"
LONG_RUN_YEARS = 15     # history the long-run growth is the median of
#: Fewer years of growth than this and the line's own long-run rate is noise
#: (rental vehicles have six, from FY2019); ``fallback_growth`` is used instead.
MIN_LONG_RUN = 8
#: Growth beyond this either way in one year is a break (a law change, COVID),
#: kept out of the growth estimates.
BREAK = 0.40


@dataclass(frozen=True)
class LineForecast:
    method: Method
    origin: int                      # last fiscal year of history used
    values: dict[int, float]         # fiscal year -> forecast
    start_growth: float              # log growth in the first forecast year's rule
    long_run_growth: float


def _growths(history: dict[int, float]) -> list[tuple[int, float]]:
    years = sorted(fy for fy, v in history.items() if v > 0)
    return [(fy, math.log(history[fy] / history[prev]))
            for prev, fy in zip(years, years[1:], strict=False) if fy == prev + 1]


def choose_method(history: dict[int, float]) -> Method:
    g = [x for _, x in _growths(history)]
    if len(g) < 3:
        return "level"
    return "level" if statistics.pstdev(g) > NOISY_SPREAD else "damped"


def long_run_growth(history: dict[int, float]) -> float | None:
    """Median log growth over the last :data:`LONG_RUN_YEARS`, breaks out;
    None with fewer than :data:`MIN_LONG_RUN` years of it."""
    origin = max(history)
    g = [x for fy, x in _growths(history) if abs(x) <= BREAK and fy > origin - LONG_RUN_YEARS]
    return statistics.median(g) if len(g) >= MIN_LONG_RUN else None


def forecast_line(history: dict[int, float], horizon: int, *,
                  method: Method | None = None,
                  fallback_growth: float = 0.0) -> LineForecast:
    """Forecast ``horizon`` fiscal years past the last year of ``history``.

    ``fallback_growth`` is the long-run growth for a line with too short a
    history to have its own (callers pass the total's)."""
    if not history:
        raise ValueError("empty history")
    origin = max(history)
    method = method or choose_method(history)
    growths = [(fy, g) for fy, g in _growths(history) if abs(g) <= BREAK]
    own = long_run_growth(history)
    g_lr = fallback_growth if own is None else own
    values: dict[int, float] = {}

    if method == "damped":
        recent = [(fy, g) for fy, g in growths if fy > origin - GROWTH_WINDOW]
        if recent:
            w = [RECENCY ** (origin - fy) for fy, _ in recent]
            g0 = sum(wi * g for wi, (_, g) in zip(w, recent, strict=True)) / sum(w)
        else:
            g0 = g_lr
        level = history[origin]
        for h in range(1, horizon + 1):
            level *= math.exp(g_lr + (g0 - g_lr) * PHI ** h)
            values[origin + h] = level
        return LineForecast("damped", origin, values, g0, g_lr)

    window = [history[fy] for fy in range(origin - LEVEL_WINDOW + 1, origin + 1) if fy in history]
    mid = origin - (len(window) - 1) / 2
    base = statistics.median(window)
    for h in range(1, horizon + 1):
        values[origin + h] = base * math.exp(g_lr * (origin + h - mid))
    return LineForecast("level", origin, values, g_lr, g_lr)
