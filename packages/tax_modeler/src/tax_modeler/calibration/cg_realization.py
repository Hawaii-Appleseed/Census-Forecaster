"""A Hawaiʻi capital-gains realization model, and why the gains anchor does not use one.

``cg_anchor`` carries DOTAX Table 21 resident net long-term gains from the
base edition to each tax year. Until October 2026 it grew them at CBO's
national realizations x 0.85; the TY2022 -> TY2023 scorecard
(``DOTAX_SCORECARD.md``) had that at +7.2% against a realized -11.7%. The
alternative scoped there: fit log(resident NLTCG) on the lagged S&P 500 total
return and the FHFA Hawaiʻi house-price change, both bundled in
``census_forecaster.data`` (``markets/prices_panel.json`` SPY adjusted close,
December over December; ``anchors/fred_hi_hpi.json``), on the editions on file.

:func:`fit_report` fits every one- and two-regressor specification on the
bundled series (TY2018-2023: six points; the archive editions before TY2018
are not bundled and the DOTAX archive rate-limited the fetch when this was
written) and scores each by leave-one-out RMSE in logs against the
windowed-mean baseline (predict a year by the mean of the others). With six
points nothing beats the mean by more than noise: on the series as bundled the
best two-regressor fit (lagged S&P and lagged HPI) comes in at 0.284 against
0.289, a 2% improvement that one point would undo, and the single regressors
are worse than the mean. **So the anchor ships the windowed mean**
(``dotax_base.nltcg_anchor_M``: each class's mean share of resident tax before
credits over ``DOTAX_GAINS_WINDOW``, applied to the base edition's total) and
grows it with the income base (``cg_anchor.cg_growth(model="window")``, CBO
wages x the Hawaiʻi wage factor: gains hold their window share of income).
The fit is kept here so the null is reproducible and re-run when DOTAX adds an
edition: ``python -m tax_modeler.calibration.cg_realization``.
"""
from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

_CF_DATA = Path(__file__).resolve().parents[4] / "census_forecaster" / "src" / "census_forecaster" / "data"

REGRESSORS = ("spx_t", "spx_l1", "hpi_t", "hpi_l1")
"""S&P 500 total return (SPY, Dec/Dec) in the year and lagged one year; FHFA
Hawaiʻi all-transactions HPI change (annual means) in the year and lagged."""

MIN_RELATIVE_GAIN = 0.10
"""A specification is 'supported' only if its LOO RMSE is at least this much
below the windowed mean's; with six points, less than that is noise."""


def _cf_data_dir() -> Path:
    try:
        import census_forecaster
        return Path(census_forecaster.__file__).resolve().parent / "data"
    except ImportError:  # pragma: no cover
        return _CF_DATA


def spx_total_return() -> dict[int, float]:
    """SPY adjusted close, December over the prior December, by year."""
    p = json.loads((_cf_data_dir() / "markets" / "prices_panel.json").read_text())["series"]["SPY"]
    dec = {int(r["year"]): float(r["adj_close"]) for r in p if r["period"] == "M12"}
    return {y: dec[y] / dec[y - 1] - 1.0 for y in sorted(dec) if y - 1 in dec}


def hi_hpi_change() -> dict[int, float]:
    """FHFA Hawaiʻi HPI (annual mean of the quarters), change on the prior year."""
    v = json.loads((_cf_data_dir() / "anchors" / "fred_hi_hpi.json").read_text())["values_by_year"]
    hpi = {int(y): float(x) for y, x in v.items()}
    return {y: hpi[y] / hpi[y - 1] - 1.0 for y in sorted(hpi) if y - 1 in hpi}


def design(years: Sequence[int]) -> pd.DataFrame:
    spx, hpi = spx_total_return(), hi_hpi_change()
    return pd.DataFrame({
        "year": list(years),
        "spx_t": [spx[y] for y in years], "spx_l1": [spx[y - 1] for y in years],
        "hpi_t": [hpi[y] for y in years], "hpi_l1": [hpi[y - 1] for y in years],
    }).set_index("year")


def _loo(y: np.ndarray, X: np.ndarray) -> float:
    n = len(y)
    errs = []
    for i in range(n):
        tr = np.array([j for j in range(n) if j != i])
        A = np.column_stack([np.ones(len(tr)), X[tr]])
        b = np.linalg.lstsq(A, y[tr], rcond=None)[0]
        errs.append(b[0] + X[i] @ b[1:] - y[i])
    return float(np.sqrt(np.mean(np.square(errs))))


def fit_report(nltcg_M: dict[int, float] | None = None,
               regressors: Iterable[str] = REGRESSORS) -> pd.DataFrame:
    """Every one- and two-regressor fit of log(resident NLTCG) on the bundled
    series: coefficients, in-sample R², leave-one-out RMSE and the windowed-mean
    baseline's. ``nltcg_M`` defaults to ``cg_anchor.DOTAX_NLTCG_RES`` totals."""
    if nltcg_M is None:
        from tax_modeler.calibration.cg_anchor import DOTAX_NLTCG_RES
        nltcg_M = {y: float(sum(v)) for y, v in DOTAX_NLTCG_RES.items()}
    years = sorted(nltcg_M)
    y = np.log([nltcg_M[k] for k in years])
    D = design(years)
    base = float(np.sqrt(np.mean([(np.delete(y, i).mean() - y[i]) ** 2 for i in range(len(y))])))
    rows = [{"spec": "window_mean", "n": len(y), "coef": {}, "r2": 0.0, "loo_rmse": base,
             "baseline_loo_rmse": base, "relative_gain": 0.0, "supported": False}]
    regs = list(regressors)
    for k in (1, 2):
        for cols in itertools.combinations(regs, k):
            X = D[list(cols)].to_numpy(float)
            A = np.column_stack([np.ones(len(y)), X])
            b = np.linalg.lstsq(A, y, rcond=None)[0]
            resid = y - A @ b
            r2 = 1 - resid.var() / y.var()
            loo = _loo(y, X)
            gain = 1 - loo / base
            rows.append({"spec": "+".join(cols), "n": len(y),
                         "coef": {c: round(float(v), 3) for c, v in zip(("const",) + cols, b, strict=True)},
                         "r2": float(r2), "loo_rmse": loo, "baseline_loo_rmse": base,
                         "relative_gain": float(gain), "supported": bool(gain >= MIN_RELATIVE_GAIN)})
    return pd.DataFrame(rows)


def chosen_model(report: pd.DataFrame | None = None) -> str:
    """``"window_mean"`` unless a specification clears ``MIN_RELATIVE_GAIN``."""
    r = fit_report() if report is None else report
    sup = r[r["supported"]]
    if sup.empty:
        return "window_mean"
    return str(sup.sort_values("loo_rmse").iloc[0]["spec"])


def main() -> int:
    pd.set_option("display.width", 200)
    r = fit_report()
    print(r[["spec", "n", "r2", "loo_rmse", "baseline_loo_rmse", "relative_gain", "supported"]]
          .to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\nchosen: {chosen_model(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
