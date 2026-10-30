"""Phase-2 tests: national-macro predictors in the stock causal screen."""
from __future__ import annotations

import math
import random

from lab.markets.screen import (
    HYPOTHESIS_PAIRS,
    NATIONAL_PREDICTORS,
    month_index,
    run_screen,
)


def _levels_from_returns(returns, base=100.0):
    levels, lv = {}, base
    for m in sorted(returns):
        lv *= math.exp(returns[m])
        levels[m] = lv
    return levels


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_national_predictors_all_appear_in_hypothesis_pairs():
    paired = {p for p, _ in HYPOTHESIS_PAIRS}
    for name in NATIONAL_PREDICTORS:
        assert name in paired, f"{name} declared but never paired"


def test_national_predictor_names_are_us_prefixed():
    assert all(n.startswith("US_") for n in NATIONAL_PREDICTORS)


# ---------------------------------------------------------------------------
# Screen behaviour with a national predictor
# ---------------------------------------------------------------------------

def test_planted_national_lead_is_detected():
    """A national series that leads HI unemployment by 6 months must pass."""
    rng = random.Random(11)
    m0 = month_index(2005, 1)
    n = 250
    # US mortgage-rate-like level series (random walk of pp moves)
    mort = {m0: 5.0}
    for i in range(1, n):
        mort[m0 + i] = max(1.0, mort[m0 + i - 1] + rng.gauss(0, 0.15))
    # HI home-value growth responds to mortgage *changes* 6 months earlier
    zhvi = {m0: 5e5}
    for i in range(1, n):
        m = m0 + i
        d_mort = mort[m - 6] - mort[m - 7] if (m - 7) in mort else 0.0
        zhvi[m] = zhvi[m - 1] * math.exp(0.004 - 0.05 * d_mort + rng.gauss(0, 0.004))

    tickers = {"US_MORTGAGE30": mort}
    targets = {"HONOLULU_ZHVI": zhvi}
    report = run_screen(
        tickers, targets,
        pairs=(("US_MORTGAGE30", "HONOLULU_ZHVI"),),
        lags=(6,), q=0.10,
    )
    cand = next(c for c in report.candidates
                if c.ticker == "US_MORTGAGE30" and c.transform == "log_return")
    assert cand.granger is not None
    assert cand.bh_pass, f"planted national lead missed (p={cand.granger.p_value})"


def test_missing_national_predictor_series_is_noted_not_crashed():
    # target present, predictor absent from the dict → no crash, no test
    targets = {"HI_UNEMPLOYMENT": {month_index(2005, 1) + i: 4.0
                                   for i in range(60)}}
    report = run_screen(
        {}, targets, pairs=(("US_JOLTS", "HI_UNEMPLOYMENT"),), lags=(3,))
    # predictor missing → the pair yields no Granger test
    assert all(c.granger is None for c in report.candidates)
