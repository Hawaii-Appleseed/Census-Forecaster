"""Exploratory: estimate eitc_poverty_alpha from an IRS SOI county x ACS 1-yr S1701 panel.

Spec (tax_modeler.adjustments.eitc_poverty_scaling / calibration.eitc_alpha_calibration):
    log(EITC[t+1]/EITC[t]) - log(B19013[t+1]/B19013[t]) = alpha * log(pov[t+1]/pov[t]) + e
Pooled over consecutive year pairs, no intercept, COVID/ARPA pairs dropped.
Read-only: writes nothing outside the path given by --out (markdown-ish text dump).

    python scripts/soi_county_alpha_panel.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOI = ROOT / "data/external/irs_soi_county_eitc_panel.csv"
ACS = ROOT / "packages/census_forecaster/src/census_forecaster/data/calibration_panel/acs_panel.json"
HI = {"15001", "15003", "15007", "15009"}
EXCL = {(2019, 2020), (2020, 2021), (2021, 2022)}


def load() -> pd.DataFrame:
    s = pd.read_csv(SOI, dtype={"geoid": str})
    tot = s.groupby(["year", "geoid"])[["N1", "A00100", "N59660", "A59660"]].sum().reset_index()
    tot = tot.rename(columns={"N59660": "eitc_n", "A59660": "eitc_amt", "N1": "returns", "A00100": "agi"})
    obs = pd.DataFrame(json.load(open(ACS))["observations"])
    obs = obs[obs.vintage == "1y"]
    def piv(ind, name):
        o = obs[obs.indicator == ind][["year", "geoid", "estimate", "moe"]]
        return o.rename(columns={"estimate": name, "moe": name + "_moe"})
    a = piv("S1701_C03_001E", "pov").merge(piv("B19013_001E", "med"), on=["year", "geoid"])
    return tot.merge(a, on=["year", "geoid"])


def pairs(df: pd.DataFrame, eitc: str = "eitc_n") -> pd.DataFrame:
    d = df.sort_values(["geoid", "year"])
    n = d.copy()
    n["year"] = n["year"] - 1
    m = d.merge(n, on=["geoid", "year"], suffixes=("0", "1"))
    m["y1"] = m["year"] + 1
    m = m[[(a, b) not in EXCL for a, b in zip(m["year"], m["y1"])]]
    m = m[(m.pov0 > 0) & (m.pov1 > 0) & (m[eitc + "0"] > 0) & (m[eitc + "1"] > 0)]
    m["y"] = np.log(m[eitc + "1"] / m[eitc + "0"]) - np.log(m.med1 / m.med0)
    m["x"] = np.log(m.pov1 / m.pov0)
    # delta-method SE of x from the two ACS MOEs (90% -> /1.645)
    se0, se1 = m.pov_moe0 / 1.645 / m.pov0, m.pov_moe1 / 1.645 / m.pov1
    m["x_se"] = np.sqrt(se0**2 + se1**2)
    m["period"] = m["year"]
    return m.reset_index(drop=True)


def ols(y, x, cl, w=None):
    """slope of y on columns x (n,k) with cluster-robust (CR1) SE; returns beta[0], se[0], n, nclusters."""
    y = np.asarray(y, float); X = np.asarray(x, float).reshape(len(y), -1)
    w = np.ones(len(y)) if w is None else np.asarray(w, float)
    sw = np.sqrt(w); Xw = X * sw[:, None]; yw = y * sw
    XtXi = np.linalg.pinv(Xw.T @ Xw)
    b = XtXi @ Xw.T @ yw
    e = yw - Xw @ b
    cl = np.asarray(cl); G = np.unique(cl)
    meat = sum(np.outer(Xw[cl == g].T @ e[cl == g], Xw[cl == g].T @ e[cl == g]) for g in G)
    n, k = Xw.shape
    c = len(G) / max(len(G) - 1, 1) * (n - 1) / max(n - k, 1)
    V = c * XtXi @ meat @ XtXi
    return b[0], float(np.sqrt(V[0, 0])), n, len(G)


def demean(m, by):
    return m["y"] - m.groupby(by)["y"].transform("mean"), m["x"] - m.groupby(by)["x"].transform("mean")


def run(m, label, fe=None, w=None, cluster="geoid"):
    m = m.copy()
    if fe:
        for f in fe:  # sequential demeaning: exact for balanced, approximate otherwise
            for _ in range(20):
                m["y"], m["x"] = demean(m, f)
    b, se, n, g = ols(m["y"], m["x"], m[cluster], None if w is None else m[w])
    return dict(spec=label, alpha=b, se=se, lo=b - 1.96 * se, hi=b + 1.96 * se, n=n, clusters=g)


def main():
    df = load()
    out = []
    for eitc in ("eitc_n", "eitc_amt"):
        P = pairs(df, eitc)
        H = P[P.geoid.isin(HI)]
        tag = f"[{eitc}] "
        out += [
            run(P, tag + "all, pooled, no intercept (the spec)"),
            run(P, tag + "all, + year-pair FE", fe=["period"]),
            run(P, tag + "all, + county FE", fe=["geoid"]),
            run(P, tag + "all, + county & year FE", fe=["geoid", "period"]),
            run(P.assign(w=P.eitc_n0 if eitc == "eitc_n" else P.eitc_amt0), tag + "all, spec, EITC-weighted", w="w"),
            run(P.assign(w=1 / P.x_se**2), tag + "all, spec, precision-weighted (1/se_x^2)", w="w"),
            run(P[P.x_se < 0.10], tag + "all, spec, x_se<0.10 (large counties)"),
            run(P[P.x_se < 0.10], tag + "x_se<0.10, + county & year FE", fe=["geoid", "period"]),
            run(H, tag + "Hawaii 4 counties, spec", cluster="period"),
            run(H, tag + "Hawaii 4 counties, + year FE", fe=["period"], cluster="period"),
            run(P[P.geoid == "15003"], tag + "Honolulu only (existing spec)", cluster="period"),
            run(P[P.period != 2022], tag + "all, spec, excluding 2022-23 pair"),
            run(P[P.period == 2022], tag + "all, 2022-23 pair only"),
        ]
    r = pd.DataFrame(out)
    pd.set_option("display.width", 200, "display.max_colwidth", 70)
    print(r.round(3).to_string(index=False))
    P = pairs(df)
    print("\npairs per period:", P.groupby("period").size().to_dict())
    print("var(x)=%.5f  mean noise var(x_se^2)=%.5f  -> reliability ~ %.2f" % (
        P.x.var(), (P.x_se**2).mean(), max(0, 1 - (P.x_se**2).mean() / P.x.var())))
    print("var(y)=%.5f  corr(x,y)=%.3f" % (P.y.var(), P[["x", "y"]].corr().iloc[0, 1]))
    H = P[P.geoid.isin(HI)]
    print("\nHawaii pair rows:\n", H[["geoid", "period", "eitc_n0", "eitc_n1", "pov0", "pov1", "x", "x_se", "y"]].round(3).to_string(index=False))
    # per-pair slopes (cross-county within period)
    print("\nper-period cross-county slope:")
    for p, g in P.groupby("period"):
        b, se, n, _ = ols(g.y, g.x, g.geoid)
        print(f"  {p}->{p+1}: alpha={b:.2f} se={se:.2f} n={n}")
    L = P[P.x_se < 0.10]
    print("large counties (x_se<0.10): n=%d var(x)=%.5f noise=%.5f reliability~%.2f" % (
        len(L), L.x.var(), (L.x_se**2).mean(), max(0, 1 - (L.x_se**2).mean() / L.x.var())))
    # placebo: does reliable signal exist? slope of eitc-growth-minus-median on x with mean-reverting lag
    return r


if __name__ == "__main__":
    main()
