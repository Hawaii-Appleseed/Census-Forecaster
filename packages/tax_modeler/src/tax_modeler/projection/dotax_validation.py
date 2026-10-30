"""Validate the income-growth path against DOTAX monthly collections.

The model's Hawaiʻi nominal income growth (`_hawaii_nominal_growth`,
ACS-forecast × CPI) drives the SB 3125 revenue baseline. DOTAX monthly
collections are the cash-basis outcome side of the same economy:

* ``ind_wh``  — individual withholding on wages: the most direct monthly
  wage-income observation that exists, BUT it conflates wage growth with
  withholding-rate policy — the Act 46 (2024) bracket cuts phase in from
  TY2025 and mechanically depress it. Interpret gaps vs the model as
  *wages + policy*, not wages alone.
* ``ge_use`` / ``ge_allocated`` — General Excise & Use: rate-stable
  (4% + county surcharges), so the cleanest pure-activity gauge, at the
  cost of measuring gross receipts rather than household income.
* ``tat`` — transient accommodations: the tourism channel.

Read-only diagnostics; nothing feeds the forecast. Bundle refreshed by
``python -m census_forecaster.scripts.refresh_dotax_collections`` (the
bundle is an accumulating archive — collec XLSX files fall off DOTAX's
site after roughly a fiscal year).

Because collections are deposit-month cash (deadline spikes, filing
season), all comparisons here are same-window year-over-year sums —
never single months, never trailing windows that straddle unequal
seasonal shapes.
"""
from __future__ import annotations

import json

from .income_forecast import DEFAULT_HAWAII_GEOID  # noqa: F401  (re-export convenience)


def load_dotax_collections() -> dict:
    """Load the bundled DOTAX monthly-collections aggregates."""
    from importlib.resources import files
    path = (files("census_forecaster") / "data" / "dotax_monthly"
            / "collections.json")
    with path.open() as f:
        return json.load(f)


def yoy_window_growth(
    series: str,
    months: list[str],
    data: dict | None = None,
) -> float | None:
    """YoY growth of ``series`` summed over ``months`` vs the same window
    one year earlier. Returns None unless BOTH windows are complete.

    ``months`` are 'YYYY-MM' strings; the prior window is the same months
    shifted back one year. Completeness is strict — a missing month in
    either window disqualifies the comparison rather than silently
    shrinking it.
    """
    if data is None:
        data = load_dotax_collections()
    monthly = data["monthly"]

    def window_sum(ms: list[str]) -> float | None:
        total = 0.0
        for m in ms:
            v = monthly.get(m, {}).get(series)
            if v is None:
                return None
            total += v
        return total

    cur = window_sum(months)
    prior = window_sum([f"{int(m[:4]) - 1}{m[4:]}" for m in months])
    if cur is None or prior is None or prior == 0:
        return None
    return cur / prior - 1.0


def latest_complete_windows(data: dict | None = None) -> dict[str, dict]:
    """Best available YoY growth per key series, with the window used.

    Prefers the longest same-window comparison the bundle supports for
    each series (data availability differs: the full collec report lags
    the GE-specific report by months).
    """
    if data is None:
        data = load_dotax_collections()
    monthly = data["monthly"]

    def months_with(series: str) -> list[str]:
        return sorted(m for m, v in monthly.items() if series in v)

    out: dict[str, dict] = {}
    for series in ("ind_wh", "ge_use", "ge_allocated", "tat", "total"):
        avail = months_with(series)
        # Longest suffix of available months whose year-earlier window is
        # also fully available.
        best: dict | None = None
        for start in range(len(avail)):
            window = avail[start:]
            if len(window) < 3:
                break
            g = yoy_window_growth(series, window, data)
            if g is not None:
                best = {
                    "window": f"{window[0]}..{window[-1]}",
                    "n_months": len(window),
                    "yoy_growth": round(g, 4),
                }
                break
        if best:
            out[series] = best
    return out


def compare_with_model(data: dict | None = None) -> dict:
    """DOTAX YoY growth vs the model's implied annual nominal growth.

    The model factor is cumulative from BASE_YEAR; the comparable
    quantity for a 12-month-ish window ending in year y is the implied
    per-year growth between y−1 and y:
    ``g(y)/g(y−1)`` (with g(BASE_YEAR)=1).
    """
    from tax_modeler.scenarios.sb3125_cd1_credits import (
        BASE_YEAR,
        _hawaii_nominal_growth,
    )
    windows = latest_complete_windows(data)
    if not windows:
        return {"windows": {}, "model_implied_annual": {}}

    # Year the freshest window ends in.
    end_years = {int(w["window"][-7:-3]) for w in windows.values()}
    model: dict[int, float] = {}
    for y in sorted(end_years):
        g_y = _hawaii_nominal_growth(y) if y > BASE_YEAR else 1.0
        g_p = _hawaii_nominal_growth(y - 1) if (y - 1) > BASE_YEAR else 1.0
        model[y] = round(g_y / g_p - 1.0, 4)
    return {
        "windows": windows,
        "model_implied_annual": model,
        "note": ("ind_wh gap = wages + Act 46 withholding policy; "
                 "ge_use/ge_allocated are the rate-stable activity gauges."),
    }


__all__ = [
    "load_dotax_collections",
    "yoy_window_growth",
    "latest_complete_windows",
    "compare_with_model",
]
