"""Estimated Hawaii-minus-national growth differentials, with standard errors.

Replaces the hand-set ``cbo_aging.DEFAULT_HAWAII_FACTORS`` ratios (wages 0.85,
business 0.90, gains 0.85, ...) in ``"additive"`` mode: Hawaii log growth = CBO
log growth + d̂_c, where d̂_c is the recency-weighted mean of
``d_t = ln(HI_t / HI_{t-1}) - ln(US_t / US_{t-1})`` over the years on file.

Data: IRS SOI state data TY2012-2023, all returns
(``data/calibration/soi_state_components_hi_us.json``, written by
``scripts/fetch_soi_state_components.py``): wages A00200, interest A00300,
dividends A00600, business A00900, retirement = taxable IRA + pensions
(A01400 + A01700; TY2018 prints only the combined A01750) + taxable Social
Security A02500. Capital gains: DOTAX Table 21 resident net long-term gains
(``cg_anchor.DOTAX_NLTCG_RES``, TY2018-2023) against SOI US A01000, so the
gains differential is on the Hawaii series, not SOI's HI A01000. ``other`` has
no series and keeps its ratio factor (flagged).

Estimator: weights ``0.5 ** (age / 3)`` (age = years before the last year in the
sample), SE = weighted SD / sqrt(n_eff), n_eff = (sum w)^2 / sum w^2.

Wages: QCEW Hawaii total wages (``qcew_hawaii_total_wages.json``, refreshed by
``census_forecaster.scripts.refresh_annual_anchors``) replace CBO+d̂ for every
year through the latest complete QCEW year.

Backtest and report: ``python -m tax_modeler.calibration.hawaii_differentials --write``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.calibration.cbo_aging import HawaiiFactors

_DATA = Path(__file__).resolve().parents[1] / "data" / "calibration" / "soi_state_components_hi_us.json"
COMPONENTS = ("wages", "business", "capital_gains", "dividends", "interest", "retirement")
NO_SERIES = ("other",)
"""CBO components with no SOI series: they keep their ratio factor (flagged in the report)."""
COVID_YEARS = (2020, 2021)
HALF_LIFE = 3.0
Z90 = 1.645
_RESULTS = Path(__file__).resolve().parents[5] / "backtests" / "results"


# ── series ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _soi() -> dict:
    return json.loads(_DATA.read_text())


def _cf_data_dir() -> Path:
    import census_forecaster
    return Path(census_forecaster.__file__).resolve().parent / "data"


def component_levels(component: str) -> tuple[pd.Series, pd.Series]:
    """``(HI, US)`` level series by tax year for an engine component."""
    yrs = _soi()["years"]

    def pick(geo: str, key) -> pd.Series:
        return pd.Series({int(y): (sum(v[geo][k] for k in key) if isinstance(key, tuple) else v[geo][key])
                          for y, v in yrs.items()}).sort_index()

    if component == "capital_gains":
        from tax_modeler.calibration.cg_anchor import DOTAX_NLTCG_RES
        hi = pd.Series({y: float(sum(v)) for y, v in DOTAX_NLTCG_RES.items()}).sort_index()
        us = pick("US", "net_capital_gain")
        return hi, us.loc[hi.index]
    key = {"wages": "wages", "business": "business", "dividends": "dividends", "interest": "interest",
           "retirement": ("pensions_ira", "taxable_social_security")}[component]
    return pick("HI", key), pick("US", key)


def growth_table(component: str) -> pd.DataFrame:
    """Annual log growth: ``g_hi``, ``g_us``, ``d`` indexed by the growth year."""
    hi, us = component_levels(component)
    g_hi, g_us = np.log(hi).diff().dropna(), np.log(us).diff().dropna()
    return pd.DataFrame({"g_hi": g_hi, "g_us": g_us, "d": g_hi - g_us})


# ── estimator ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Estimate:
    d: float
    se: float
    n: int
    n_eff: float

    def lo_hi(self, z: float = Z90) -> tuple[float, float]:
        return self.d - z * self.se, self.d + z * self.se


def weighted_estimate(d: pd.Series, half_life: float = HALF_LIFE) -> Estimate:
    """Recency-weighted mean of ``d`` (index = years), SE = weighted SD / sqrt(n_eff)."""
    d = d.dropna().sort_index()
    n = len(d)
    if n == 0:
        return Estimate(float("nan"), float("nan"), 0, 0.0)
    age = d.index.max() - d.index.to_numpy()
    w = 0.5 ** (age / half_life)
    m = float(np.sum(w * d.to_numpy()) / w.sum())
    n_eff = float(w.sum() ** 2 / np.sum(w ** 2))
    if n < 2:
        return Estimate(m, float("nan"), n, n_eff)
    var = float(np.sum(w * (d.to_numpy() - m) ** 2) / (w.sum() - np.sum(w ** 2) / w.sum()))
    return Estimate(m, math.sqrt(var) / math.sqrt(n_eff), n, n_eff)


def estimate(component: str, *, before: int | None = None, exclude: tuple[int, ...] = ()) -> Estimate:
    """d̂ for ``component`` from growth years ``< before`` (all if None), minus ``exclude``."""
    d = growth_table(component)["d"]
    if before is not None:
        d = d[d.index < before]
    return weighted_estimate(d[~d.index.isin(exclude)])


def estimate_all(*, before: int | None = None, exclude: tuple[int, ...] = ()) -> dict[str, Estimate]:
    return {c: estimate(c, before=before, exclude=exclude) for c in COMPONENTS}


# ── QCEW wages ───────────────────────────────────────────────────────────────

def qcew_total_wages() -> pd.Series:
    """Hawaii QCEW total wages ($000), by calendar year (complete years only)."""
    v = json.loads((_cf_data_dir() / "anchors" / "qcew_hawaii_total_wages.json").read_text())["values_by_year"]
    return pd.Series({int(y): float(x) for y, x in v.items()}).sort_index()


def qcew_wage_growth() -> dict[int, float]:
    """Realized Hawaii wage growth factors by year (``y-1`` -> ``y``) through the latest complete QCEW year."""
    s = qcew_total_wages()
    return {int(y): float(s[y] / s[y - 1]) for y in s.index[1:]}


# ── the engine's factors ─────────────────────────────────────────────────────

def build_factors(mode: str = "additive", *, shift_se: float = 0.0, exclude: tuple[int, ...] = (),
                  qcew_wages: bool = True):
    """The engine's Hawaii factors for ``mode``.

    ``"additive"``: d̂ from all years on file; ``shift_se`` moves every d̂_c by that
    many SEs together (the aging band). ``"ratio"``: the old constants, with no
    d̂. Either way ``qcew_wages`` replaces wage growth with QCEW's through its
    latest complete year. Plain ``DEFAULT_HAWAII_FACTORS`` dict when there is
    nothing to add (ratio, QCEW off).
    """
    from tax_modeler.calibration.cbo_aging import DEFAULT_HAWAII_FACTORS
    realized = {"wages": qcew_wage_growth()} if qcew_wages else {}
    if mode == "ratio":
        return HawaiiFactors({}, realized=realized) if realized else dict(DEFAULT_HAWAII_FACTORS)
    est = estimate_all(exclude=exclude)
    d_hat = {c: e.d + shift_se * (0.0 if math.isnan(e.se) else e.se) for c, e in est.items()}
    se = {c: e.se for c, e in est.items()}
    d_hat["proprietors"], se["proprietors"] = d_hat["business"], se["business"]
    return HawaiiFactors(d_hat, realized=realized, se=se, shift_se=shift_se)


# ── backtest ─────────────────────────────────────────────────────────────────

def ratio_prediction(g_us: float, ratio: float) -> float:
    """Old rule applied to a realized national log growth: ln(1 + ratio * (e^g - 1))."""
    return math.log(1.0 + ratio * (math.exp(g_us) - 1.0))


def backtest(exclude: tuple[int, ...] = ()) -> pd.DataFrame:
    """One-step-ahead errors (predicted - realized HI log growth), per component-year.

    Targets TY2016-2022 (gains TY2019-2023; TY2019 has no earlier gains growth, so
    its additive row is absent). Additive: ``g_US,t(realized) + d̂(years < t)``;
    ratio: the old constant on the same realized ``g_US,t``.
    """
    from tax_modeler.calibration.cbo_aging import DEFAULT_HAWAII_FACTORS
    rows = []
    for c in COMPONENTS:
        tab = growth_table(c)
        targets = range(2019, 2024) if c == "capital_gains" else range(2016, 2023)
        for t in targets:
            if t not in tab.index:
                continue
            est = estimate(c, before=t, exclude=exclude)
            realized = float(tab.loc[t, "g_hi"])
            ratio_pred = ratio_prediction(float(tab.loc[t, "g_us"]), DEFAULT_HAWAII_FACTORS[c])
            add_pred = float(tab.loc[t, "g_us"]) + est.d if est.n else float("nan")
            rows.append({"component": c, "year": t, "realized": realized, "n_hist": est.n,
                         "d_hat": est.d, "err_additive": add_pred - realized,
                         "err_ratio": ratio_pred - realized})
    return pd.DataFrame(rows)


def score(bt: pd.DataFrame) -> pd.DataFrame:
    """RMSE/bias per component and pooled, on rows where both methods have a prediction."""
    bt = bt.dropna(subset=["err_additive"])

    def one(g: pd.DataFrame, name: str) -> dict:
        r = {"component": name, "n": len(g)}
        for m in ("additive", "ratio"):
            e = g[f"err_{m}"]
            r[f"rmse_{m}"] = float(np.sqrt(np.mean(e ** 2)))
            r[f"bias_{m}"] = float(e.mean())
        r["rmse_change"] = r["rmse_additive"] / r["rmse_ratio"] - 1.0
        return r

    out = [one(g, c) for c, g in bt.groupby("component", sort=False)]
    out.append(one(bt, "POOLED"))
    return pd.DataFrame(out)


def verdict(sc: pd.DataFrame, tol: float = 0.10) -> tuple[bool, str]:
    """Ship additive iff pooled RMSE is lower and no component worsens by more than ``tol``."""
    pooled = sc[sc["component"] == "POOLED"].iloc[0]
    comps = sc[sc["component"] != "POOLED"]
    worse = comps[comps["rmse_change"] > tol]
    ok = bool(pooled["rmse_additive"] < pooled["rmse_ratio"] and worse.empty)
    why = (f"pooled RMSE {pooled['rmse_additive']:.4f} vs {pooled['rmse_ratio']:.4f} "
           f"({pooled['rmse_change']:+.1%}); "
           + ("no component worsens by more than 10%" if worse.empty else
              "worsens by more than 10%: " + ", ".join(f"{r.component} {r.rmse_change:+.1%}" for r in worse.itertuples())))
    return ok, why


def qcew_check() -> pd.DataFrame:
    """QCEW realized wage growth vs SOI HI A00200 growth, TY2016-2022, against CBO_US+d̂ (SOI US growth + d̂<t)."""
    tab = growth_table("wages")
    q = qcew_total_wages()
    rows = []
    for t in range(2016, 2023):
        est = estimate("wages", before=t)
        g_q = math.log(q[t] / q[t - 1])
        rows.append({"year": t, "soi_hi": tab.loc[t, "g_hi"], "qcew": g_q,
                     "us_plus_d": tab.loc[t, "g_us"] + est.d})
    return pd.DataFrame(rows).set_index("year")


# ── report ───────────────────────────────────────────────────────────────────

def _fmt(e: Estimate) -> str:
    se = "n/a" if math.isnan(e.se) else f"{e.se:.4f}"
    return f"{e.d:+.4f} | {se} | {e.n}"


def report_markdown(today: dt.date | None = None) -> tuple[str, bool]:
    from tax_modeler.calibration.cbo_aging import DEFAULT_HAWAII_FACTORS
    today = today or dt.date.today()
    incl, excl = estimate_all(), estimate_all(exclude=COVID_YEARS)
    L = [f"# Hawaii aging factors: estimated differentials vs the fixed ratios ({today.isoformat()})", "",
         "Generated by `python -m tax_modeler.calibration.hawaii_differentials --write`. Data: IRS SOI state "
         "data TY2012-2023 (all returns), DOTAX Table 21 resident gains TY2018-2023 against SOI US A01000. "
         "d = ln(HI growth) - ln(US growth), per year.", "",
         "## Differentials (log points per year), all years on file", "",
         "| component | old ratio | d̂ (incl. 2020-21) | SE | n | d̂ (excl. 2020-21) | SE | n |", "|---|---|---|---|---|---|---|---|"]
    for c in COMPONENTS:
        a, b = incl[c], excl[c]
        se = lambda e: "n/a" if math.isnan(e.se) else f"{e.se:.4f}"
        L.append(f"| {c} | {DEFAULT_HAWAII_FACTORS[c]:.2f} | {a.d:+.4f} | {se(a)} | {a.n} | {b.d:+.4f} | {se(b)} | {b.n} |")
    L += [f"| other | {DEFAULT_HAWAII_FACTORS['other']:.2f} | no SOI series: keeps its ratio factor (flagged) | | | | | |", ""]
    verdicts = {}
    for label, ex in (("including TY2020-2021 (default)", ()), ("excluding TY2020-2021", COVID_YEARS)):
        bt = backtest(ex)
        sc = score(bt)
        ok, why = verdict(sc)
        verdicts[label] = ok
        L += [f"## Backtest, {label}", "",
              "One-step-ahead, targets TY2016-2022 (gains TY2020-2023: TY2019 has no earlier gains growth to estimate on). "
              "Prediction = realized national log growth + d̂ estimated on years before the target; ratio = the old constant "
              "on the same realized national growth. Errors in log points (predicted - realized).", "",
              "| component | n | RMSE additive | RMSE ratio | change | bias additive | bias ratio |", "|---|---|---|---|---|---|---|"]
        for r in sc.itertuples():
            L.append(f"| {r.component} | {r.n} | {r.rmse_additive:.4f} | {r.rmse_ratio:.4f} | {r.rmse_change:+.1%} | "
                     f"{r.bias_additive:+.4f} | {r.bias_ratio:+.4f} |")
        L += ["", f"**Verdict ({label}): {'ADDITIVE ships' if ok else 'RATIO stays the default (null)'}** — {why}.", ""]
    q = qcew_check()
    rm = lambda s: float(np.sqrt(np.mean((s - q["soi_hi"]) ** 2)))
    L += ["## QCEW wage check (TY2016-2022)", "",
          "| year | SOI HI A00200 | QCEW total wages | US + d̂ (<t) |", "|---|---|---|---|"]
    for y, r in q.iterrows():
        L.append(f"| {y} | {r.soi_hi:+.4f} | {r.qcew:+.4f} | {r.us_plus_d:+.4f} |")
    L += ["", f"RMSE vs SOI HI wage growth: QCEW **{rm(q['qcew']):.4f}**, US + d̂ **{rm(q['us_plus_d']):.4f}**; "
          f"correlation with SOI HI growth: QCEW {q['qcew'].corr(q['soi_hi']):.2f}, US + d̂ {q['us_plus_d'].corr(q['soi_hi']):.2f}. "
          f"Gate ({'passes' if rm(q['qcew']) < rm(q['us_plus_d']) else 'FAILS'}): QCEW must track SOI HI better than US + d̂.", ""]
    return "\n".join(L), verdicts["including TY2020-2021 (default)"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write backtests/results/hawaii_aging_factors_<date>.md")
    args = ap.parse_args()
    md, ok = report_markdown()
    print(md)
    if args.write:
        _RESULTS.mkdir(parents=True, exist_ok=True)
        path = _RESULTS / f"hawaii_aging_factors_{dt.date.today().isoformat()}.md"
        path.write_text(md + "\n")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
