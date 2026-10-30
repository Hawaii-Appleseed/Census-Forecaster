"""EXPERIMENTAL — reverse direction: Hawaii fundamentals → ticker forecasts.

The pipeline's standing direction is prices → economy, and for good
reason: prices embed expectations months before statistical agencies
publish. Running the arrow the other way — census/BLS/Zillow data
informing *stock* forecasts — collides with market efficiency: by the
time LAUS or ACS prints, liquid markets have long priced the news. For
SPY-tier assets the expected result is a null, full stop.

The defensible hypothesis is narrower: the **Hawaii tier** (BOH, FHB,
HE, MATX) is thinly followed, and the limited-attention literature
(Hong, Lim & Stein 2000; Hou & Moskowitz 2005 price delay; Da,
Engelberg & Gao 2011) finds slow information diffusion exactly in
small, analyst-light names. If public Hawaii fundamentals diffuse into
these prices with a lag, a screened, ablation-gated signal could shift
the tracker's damped-drift point or condition its bands.

This module pre-registers that hypothesis family and measures it with
the repo's standard gauntlet, run in reverse:

1. ``run_reverse_screen`` — Granger + lead xcorr of fundamental →
   ticker monthly log-returns, BH-FDR within the family, 2020-exclusion
   robustness. Uses the exact machinery of ``screen.py``.
2. ``walkforward_return_ablation`` — expanding-window one-step return
   forecasts vs the two EMH-honest benchmarks: predict-zero (random
   walk) and the expanding historical mean. A fundamental signal earns
   nothing unless it beats BOTH out of sample.

Honesty constraints baked in:

* **Availability lags.** LAUS month *m* publishes weeks into *m+1*;
  Zillow similarly. Each predictor declares ``availability_lag_months``
  and the ablation only uses values that were actually public at
  forecast time. (The Granger screen, per repo convention, is
  predictive-precedence diagnostics and uses calendar lags.)
* **Revision bias.** Zillow revises ~a year of history; LAUS is
  benchmarked annually. We test against *revised* series — treat any
  positive result as an upper bound, mirroring `backtest/cpi.py`'s
  caveat #1.
* **Null results are results.** The repo records them (METHODOLOGY's
  v4-phi section); an EMH-consistent null here closes the question
  with evidence instead of vibes.

Tracker context only — never trading advice, and none of this touches
the census forecaster. Nothing here is wired into ``forecast_ticker``;
promotion would require the ablation gate to pass and a reviewed
integration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .panel import PricesPanel, load_prices_panel
from .screen import (
    MONTHLY_TARGETS,   # noqa: F401  (naming parity for readers)
    _drop_2020,
    benjamini_hochberg,
    cross_correlation_lead,
    granger_f_test,
    series_to_monthly_dict,
    transform_series,
)


@dataclass(frozen=True)
class FundamentalSpec:
    """One fundamental predictor with its availability discipline."""
    name: str
    macro_series_id: str        # key into data/markets/macro_monthly.json
    transform: str              # 'diff' | 'log_diff' (screen conventions)
    availability_lag_months: int
    revision_caveat: str


FUNDAMENTALS: tuple[FundamentalSpec, ...] = (
    FundamentalSpec(
        "hi_unemployment", "LASST150000000000003", "diff", 1,
        "LAUS state estimates are re-benchmarked annually.",
    ),
    FundamentalSpec(
        "honolulu_zhvi", "ZHVI_HONOLULU_MONTHLY", "log_diff", 1,
        "Zillow revises ~12 months of history after first print.",
    ),
    FundamentalSpec(
        "honolulu_zori", "ZORI_HONOLULU_MONTHLY", "log_diff", 1,
        "Zillow revises ~12 months of history after first print.",
    ),
)

# Pre-registered fundamental → ticker pairs (slow-diffusion hypotheses).
# Hawaii tier only: for liquid national tickers the EMH null is assumed,
# not spent from the multiple-testing budget.
REVERSE_PAIRS: tuple[tuple[str, str, str], ...] = (
    # (fundamental, ticker, hypothesis)
    ("hi_unemployment", "BOH",
     "Local labor market drives credit quality and loan demand at an "
     "analyst-light local bank."),
    ("hi_unemployment", "FHB",
     "Same local-credit channel, second bank."),
    ("hi_unemployment", "HE",
     "Utility revenue and regulatory climate track the local economy."),
    ("honolulu_zhvi", "BOH",
     "Home values drive mortgage collateral and origination volume."),
    ("honolulu_zhvi", "FHB",
     "Same collateral channel, second bank."),
    ("honolulu_zori", "BOH",
     "Rents proxy landlord/household cash flow feeding local deposits."),
)


def _fundamental_series(spec: FundamentalSpec, macro: dict) -> dict[int, float]:
    return transform_series(
        series_to_monthly_dict(macro[spec.macro_series_id]), spec.transform)


def _ticker_returns(panel: PricesPanel, symbol: str) -> dict[int, float]:
    return {y * 12 + m: r for y, m, r in panel.log_returns(symbol)}


def _spec(name: str) -> FundamentalSpec:
    return next(s for s in FUNDAMENTALS if s.name == name)


def run_reverse_screen(
    macro: dict,
    panel: Optional[PricesPanel] = None,
    *,
    lags: tuple[int, ...] = (3, 6, 12),
    q_fdr: float = 0.10,
) -> dict:
    """Granger/xcorr screen of the pre-registered fundamental→ticker pairs.

    Returns {'candidates': [...], 'n_tests': int}; each candidate carries
    granger p-values per lag, BH pass flags (corrected across ALL tests
    actually run in this family), best xcorr lead, and the
    2020-exclusion rerun.
    """
    if panel is None:
        panel = load_prices_panel()
    tests: list[dict] = []
    for fund_name, symbol, hypothesis in REVERSE_PAIRS:
        spec = _spec(fund_name)
        x_full = _fundamental_series(spec, macro)
        y_full = _ticker_returns(panel, symbol)
        for drop2020 in (False, True):
            x = _drop_2020(x_full) if drop2020 else x_full
            y = _drop_2020(y_full) if drop2020 else y_full
            lcs = cross_correlation_lead(x, y, max_lead=18)
            best = max(lcs, key=lambda l: abs(l.r)) if lcs else None
            for lag in lags:
                gr = granger_f_test(y, x, lags=lag)
                tests.append({
                    "fundamental": fund_name,
                    "ticker": symbol,
                    "hypothesis": hypothesis,
                    "lags": lag,
                    "exclude_2020": drop2020,
                    "granger_p": gr.p_value if gr else None,
                    "nobs": gr.nobs if gr else 0,
                    "best_xcorr_r": best.r if best else None,
                    "best_xcorr_lead": best.lead if best else None,
                })
    # BH-FDR over the base (2020-included) tests with a defined p-value;
    # the exclusion rerun is a robustness annotation, not extra budget.
    base = [t for t in tests if not t["exclude_2020"] and t["granger_p"] is not None]
    flags = benjamini_hochberg([t["granger_p"] for t in base], q=q_fdr)
    for t, ok in zip(base, flags):
        t["bh_pass"] = bool(ok)
    for t in tests:
        t.setdefault("bh_pass", False)
    return {"candidates": tests, "n_tests": len(base), "q_fdr": q_fdr}


def walkforward_return_ablation(
    macro: dict,
    panel: Optional[PricesPanel] = None,
    *,
    min_train: int = 48,
) -> list[dict]:
    """One-step-ahead return forecasts per pair vs EMH benchmarks.

    For month t, the signal model fits OLS
    ``r_t ~ a + b·x_{t-1-availability_lag}`` on the expanding window and
    predicts the next month. Benchmarks on identical months: zero
    (random walk) and the expanding historical mean return. Reports RMSE
    per model; ``signal_beats_both`` is the gate that matters.
    """
    if panel is None:
        panel = load_prices_panel()
    out: list[dict] = []
    for fund_name, symbol, _hyp in REVERSE_PAIRS:
        spec = _spec(fund_name)
        x = _fundamental_series(spec, macro)
        y = _ticker_returns(panel, symbol)
        shift = 1 + spec.availability_lag_months
        months = sorted(m for m in y if (m - shift) in x)
        if len(months) <= min_train + 12:
            out.append({"fundamental": fund_name, "ticker": symbol,
                        "n_forecasts": 0, "note": "insufficient overlap"})
            continue
        errs_zero, errs_mean, errs_signal = [], [], []
        for i in range(min_train, len(months)):
            train = months[:i]
            target_m = months[i]
            r_actual = y[target_m]
            r_train = np.array([y[m] for m in train])
            x_train = np.array([x[m - shift] for m in train])
            design = np.column_stack([np.ones_like(x_train), x_train])
            try:
                coef, *_ = np.linalg.lstsq(design, r_train, rcond=None)
            except np.linalg.LinAlgError:
                continue
            pred_signal = float(coef[0] + coef[1] * x[target_m - shift])
            errs_zero.append(r_actual ** 2)
            errs_mean.append((r_actual - float(r_train.mean())) ** 2)
            errs_signal.append((r_actual - pred_signal) ** 2)
        n = len(errs_signal)
        rmse = lambda e: float(math.sqrt(sum(e) / len(e))) if e else float("nan")
        rz, rm, rs = rmse(errs_zero), rmse(errs_mean), rmse(errs_signal)
        out.append({
            "fundamental": fund_name,
            "ticker": symbol,
            "n_forecasts": n,
            "rmse_zero": round(rz, 6),
            "rmse_mean": round(rm, 6),
            "rmse_signal": round(rs, 6),
            "signal_vs_zero_pct": round((rs / rz - 1) * 100, 2) if rz else None,
            "signal_beats_both": bool(rs < rz and rs < rm),
            "availability_lag_months": spec.availability_lag_months,
        })
    return out


__all__ = [
    "FundamentalSpec", "FUNDAMENTALS", "REVERSE_PAIRS",
    "run_reverse_screen", "walkforward_return_ablation",
]
