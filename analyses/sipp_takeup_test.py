"""Exploratory test: can SIPP (2023 public-use file, reference year 2022) feed the take-up propensity module and the
H.R.1 SNAP/Medicaid loss-rate assumptions?  Not on the chain; changes no published number.

Usage (from the repo root):
    .venv/bin/python -I analyses/sipp_takeup_test.py data/artifacts/sipp_raw/sipp.parquet

Input is the parquet written by ``analyses/sipp_extract.py``.  Prints every table that ``docs/data_source_tests/
sipp-takeup.md`` quotes.  Weights: WPFINWGT as given (the four panels' weights add to about the population in total,
checked below, so the pooled file needs no rescaling).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "tax_modeler" / "src"))
from tax_modeler.calibration.takeup_propensity import Term, fit_from_survey  # noqa: E402

HI = 15.0
WORK_HRS = 80 / 4.33  # 80 hours a month as a weekly average: 18.5
rng = np.random.default_rng(20261124)


def load(path: str) -> pd.DataFrame:
    d = pd.read_parquet(path)
    d = d.sort_values(["SSUID", "PNUM", "SPANEL", "MONTHCODE"]).reset_index(drop=True)
    assert not d.duplicated(["SSUID", "PNUM", "SPANEL", "MONTHCODE"]).any()
    d["state"] = d["TEHC_ST"].fillna(d["TST_INTV"])
    d["snap"] = (d["RSNAP_MNYN"] == 1).astype(float).where(d["RSNAP_MNYN"].notna())
    d["mcaid"] = (d["RPUBTYPE2"] == 1).astype(float)
    d["hrs"] = d["TMWKHRS"].fillna(0.0)
    d["pov"] = d["THINCPOV"]
    d["pid"] = d["SSUID"].astype(str) + "_" + d["PNUM"].astype(int).astype(str) + "_" + d["SPANEL"].astype(int).astype(str)
    return d


def wmean(x, w):
    m = np.isfinite(x)
    return float((x[m] * w[m]).sum() / w[m].sum())


def sample_counts(d):
    print("\n== 1. Sample and Hawaii identification")
    print("persons", d["pid"].nunique(), "person-months", len(d), "households (SSUID)", d["SSUID"].nunique())
    print("weights, sum over panels in month 1 (millions):",
          round(d.loc[d.MONTHCODE == 1, "WPFINWGT"].sum() / 1e6, 1))
    hi = d[d.state == HI]
    print("states identified (FIPS codes with rows):", d.state.nunique(), "| Hawaii (15) rows:", len(hi))
    print("HI persons", hi.pid.nunique(), "HI households", hi.SSUID.nunique(), "by panel:",
          hi.groupby("SPANEL").pid.nunique().to_dict())
    print("HI weighted persons, month 6 (thousands):", round(hi.loc[hi.MONTHCODE == 6, "WPFINWGT"].sum() / 1e3, 1))
    ad = hi[(hi.TAGE >= 18)]
    print("HI adult person-months", len(ad), "| SNAP person-months", int(ad.snap.sum()),
          "| persons ever on SNAP", ad[ad.snap == 1].pid.nunique(),
          "| Medicaid person-months", int(ad.mcaid.sum()), "persons ever", ad[ad.mcaid == 1].pid.nunique())
    return hi


def terms_full():
    return (Term("pov_lt_50", "pov", lo=None, hi=0.5), Term("pov_50_99", "pov", lo=0.5, hi=1.0),
            Term("has_child_u18", "RHNUMU18", lo=1.0), Term("age_18_24", "TAGE", lo=18, hi=25),
            Term("age_50_64", "TAGE", lo=50, hi=65), Term("age_65p", "TAGE", lo=65),
            Term("renter", "ETENURE", kind="isin", values=(2.0,)), Term("disabled", "EDISABL", kind="isin", values=(1.0,)),
            Term("noncitizen", "ECITIZEN", kind="isin", values=(2.0,)),
            Term("less_than_hs", "EEDUC", hi=39), Term("college_degree", "EEDUC", lo=43),
            Term("works", "hrs", lo=0.01), Term("hispanic", "EORIGIN", kind="isin", values=(1.0,)))


def terms_unit_columns():
    """Only what a tax-unit frame carries (income, num_dependents, primary_agep); survey columns renamed to match."""
    return (Term("pov_lt_50", "pov", lo=None, hi=0.5), Term("pov_50_99", "pov", lo=0.5, hi=1.0),
            Term("num_dependents_1p", "num_dependents", lo=1.0), Term("primary_agep_18_24", "primary_agep", lo=18, hi=25),
            Term("primary_agep_50_64", "primary_agep", lo=50, hi=65), Term("primary_agep_65p", "primary_agep", lo=65))


def logit(d):
    print("\n== 2. SNAP participation among income-eligible adults (month 6, household income <= 130% of poverty)")
    s = d[(d.MONTHCODE == 6) & (d.TAGE >= 18) & (d.pov <= 1.30) & d.snap.notna()].copy()
    s["num_dependents"] = s["RHNUMU18"]
    s["primary_agep"] = s["TAGE"]
    print("national eligible-proxy adults:", len(s), "weighted millions:", round(s.WPFINWGT.sum() / 1e6, 1),
          "| participation (weighted):", round(wmean(s.snap.to_numpy(), s.WPFINWGT.to_numpy()), 3))
    hi = s[s.state == HI]
    print("Hawaii eligible-proxy adults:", len(hi), "| on SNAP:", int(hi.snap.sum()),
          "| participation (weighted):", round(wmean(hi.snap.to_numpy(), hi.WPFINWGT.to_numpy()), 3))
    out = {}
    for name, terms in (("full", terms_full()), ("unit-column-only", terms_unit_columns())):
        m = fit_from_survey(s, outcome="snap", terms=terms, weight="WPFINWGT", ridge=1.0, source="SIPP 2023 pu month 6")
        t = pd.DataFrame({"coef": m.coefficients, "se": m.standard_errors})
        t["z"] = t.coef / t.se
        print(f"\n-- spec {name}: intercept {m.intercept:.3f}, n={len(s)}")
        print(t.round(3).to_string())
        out[name] = m
        p = m.predict(s)
        print("predicted national mean", round(wmean(p, s.WPFINWGT.to_numpy()), 3),
              "| predicted at HI frame (national coefficients):", round(wmean(m.predict(hi), hi.WPFINWGT.to_numpy()), 3),
              "vs HI sample actual", round(wmean(hi.snap.to_numpy(), hi.WPFINWGT.to_numpy()), 3))
    # stability across panels: fit on panels 2020-21, test on 2022-23
    a, b = s[s.SPANEL <= 2021], s[s.SPANEL >= 2022]
    m = fit_from_survey(a, outcome="snap", terms=terms_full(), weight="WPFINWGT", ridge=1.0)
    pb = m.predict(b)
    print(f"\nholdout (fit panels 2020-21, n={len(a)}; test 2022-23, n={len(b)}): predicted "
          f"{wmean(pb, b.WPFINWGT.to_numpy()):.3f} actual {wmean(b.snap.to_numpy(), b.WPFINWGT.to_numpy()):.3f}")
    q = pd.qcut(pd.Series(pb, index=b.index), 5, labels=False, duplicates="drop")
    print(b.assign(p=pb, q=q).groupby("q").apply(
        lambda g: pd.Series({"pred": wmean(g.p.to_numpy(), g.WPFINWGT.to_numpy()),
                             "act": wmean(g.snap.to_numpy(), g.WPFINWGT.to_numpy()), "n": len(g)})).round(3).to_string())
    return out


def transitions(d, flag, horizon):
    """Person-month rows at t with the flag's status at t+horizon (same person, same reference year)."""
    g = d.groupby("pid", sort=False)
    d = d.assign(**{f"{flag}_next": g[flag].shift(-horizon), "mc_next": g["MONTHCODE"].shift(-horizon)})
    return d[d.mc_next == d.MONTHCODE + horizon]


def boot_ci(x, w, cl, reps=300):
    """Weighted mean of x with a household-cluster bootstrap 95% interval."""
    df = pd.DataFrame({"xw": x * w, "w": w, "cl": cl}).groupby("cl").sum()
    n = len(df)
    xs, ws = df.xw.to_numpy(), df.w.to_numpy()
    est = xs.sum() / ws.sum()
    bs = []
    for _ in range(reps):
        i = rng.integers(0, n, n)
        bs.append(xs[i].sum() / ws[i].sum())
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return est, lo, hi, n


def exits(d):
    print("\n== 3. SNAP exits, adults 18-64 with no child under 18, not disabled, on SNAP in month t")
    base = d[(d.TAGE >= 18) & (d.TAGE <= 64) & (d.RHNUMU18 == 0) & (d.EDISABL != 1)]
    for h in (1, 3, 6):
        t = transitions(d, "snap", h)
        t = t[t.index.isin(base.index) & (t.snap == 1) & t.snap_next.notna()].copy()
        t["off"] = 1.0 - t.snap_next
        t["grp"] = pd.cut(t.hrs, [-1, 0.001, WORK_HRS, 1000], labels=["no work", f"0-{WORK_HRS:.1f} h/wk", f">={WORK_HRS:.1f} h/wk"])
        rows = []
        for lab, g in [("all", t)] + [(k, v) for k, v in t.groupby("grp", observed=True)]:
            if len(g) < 30:
                rows.append({"group": lab, "rows": len(g)})
                continue
            est, lo, hi, ncl = boot_ci(g.off.to_numpy(), g.WPFINWGT.to_numpy(), g.SSUID.to_numpy())
            rows.append({"group": lab, "rows": len(g), "persons": g.pid.nunique(), "off_rate": est, "lo": lo, "hi": hi})
        print(f"\n-- share off SNAP {h} month(s) later (rows are person-months at t)")
        print(pd.DataFrame(rows).round(3).to_string(index=False))
        if h == 1:
            hi_ = t[t.state == HI]
            print("Hawaii rows:", len(hi_), "persons:", hi_.pid.nunique())
    # persistent exit: off at t+1 and still off at t+3
    t1 = d.assign(s1=d.groupby("pid")["snap"].shift(-1), s3=d.groupby("pid")["snap"].shift(-3),
                  m3=d.groupby("pid")["MONTHCODE"].shift(-3))
    t1 = t1[t1.index.isin(base.index) & (t1.snap == 1) & (t1.m3 == t1.MONTHCODE + 3)].copy()
    t1["gone"] = ((t1.s1 == 0) & (t1.s3 == 0)).astype(float)
    t1["grp"] = pd.cut(t1.hrs, [-1, 0.001, WORK_HRS, 1000], labels=["no work", "low", "work"])
    print("\n-- off at t+1 and still off at t+3, by hours at t")
    print(t1.groupby("grp", observed=True).apply(lambda g: pd.Series(
        {"rows": len(g), "gone_persistent": wmean(g.gone.to_numpy(), g.WPFINWGT.to_numpy())})).round(3).to_string())
    # population view: of adults in this group on SNAP in month 1, share still on SNAP in month 12
    m1 = d[(d.MONTHCODE == 1) & d.index.isin(base.index) & (d.snap == 1)][["pid", "hrs", "WPFINWGT", "SSUID"]]
    m12 = d[d.MONTHCODE == 12][["pid", "snap"]].rename(columns={"snap": "snap12"})
    j = m1.merge(m12, on="pid")
    j["off12"] = 1.0 - j.snap12
    j["grp"] = pd.cut(j.hrs, [-1, 0.001, WORK_HRS, 1000], labels=["no work", "low", "work"])
    print("\n-- on SNAP in month 1, off in month 12, by hours in month 1")
    print(j.groupby("grp", observed=True).apply(lambda g: pd.Series(
        {"n": len(g), "off_by_12": wmean(g.off12.to_numpy(), g.WPFINWGT.to_numpy())})).round(3).to_string())
    print("(all)", len(j), round(wmean(j.off12.to_numpy(), j.WPFINWGT.to_numpy()), 3))


def medicaid(d):
    print("\n== 4. Medicaid exits, adults 19-64 with household income <= 138% of poverty, on Medicaid in month t")
    nun = d.groupby("pid")["mcaid"].nunique()
    print("persons whose Medicaid status changes within the 12 months:", round(float((nun > 1).mean()), 4),
          "| ever-covered share:", round(float((d.groupby("pid")["mcaid"].max() == 1).mean()), 3))
    nun = d.groupby("pid")["snap"].nunique()
    print("persons whose SNAP status changes within the 12 months:", round(float((nun > 1).mean()), 4))
    base = d[(d.TAGE >= 19) & (d.TAGE <= 64) & (d.pov <= 1.38)]
    for h in (1, 6):
        t = transitions(d, "mcaid", h)
        t = t[t.index.isin(base.index) & (t.mcaid == 1)].copy()
        t["off"] = 1.0 - t.mcaid_next
        t["grp"] = np.where(t.RHNUMU18 > 0, "child in hh", np.where(t.hrs >= WORK_HRS, "no child, works", "no child, low/no work"))
        rows = []
        for lab, g in [("all", t)] + list(t.groupby("grp")):
            if len(g) < 30:
                continue
            est, lo, hi, _ = boot_ci(g.off.to_numpy(), g.WPFINWGT.to_numpy(), g.SSUID.to_numpy())
            rows.append({"group": lab, "rows": len(g), "persons": g.pid.nunique(), "off_rate": est, "lo": lo, "hi": hi})
        print(f"\n-- share without Medicaid {h} month(s) later")
        print(pd.DataFrame(rows).round(3).to_string(index=False))


def eitc(d):
    print("\n== 5. EITC and filing questions (month 6)")
    s = d[(d.MONTHCODE == 6) & (d.TAGE >= 18) & (d.TAGE <= 64)].copy()
    print("EFILING distribution (weighted share):", s.groupby("EFILING", dropna=False).WPFINWGT.sum().div(s.WPFINWGT.sum()).round(3).to_dict())
    print("EEITC distribution among all adults 18-64 (weighted share):",
          s.groupby("EEITC", dropna=False).WPFINWGT.sum().div(s.WPFINWGT.sum()).round(3).to_dict())
    print("persons with EEITC==1, weighted millions:", round(s.loc[s.EEITC == 1, "WPFINWGT"].sum() / 1e6, 2))
    s["band"] = pd.cut(s.pov, [-1, 1.0, 2.0, 3.0, 1e9], labels=["<100%", "100-199%", "200-299%", "300%+"])
    s["kids"] = np.where(s.RHNUMU18 > 0, "child", "no child")
    f = s[(s.EFILING.isin([1, 2])) & (s.hrs > 0)]
    print("\nworking adults 18-64 (hours > 0): share who filed, share who report an EITC, by income-to-poverty and children")
    print(f.groupby(["kids", "band"], observed=True).apply(lambda g: pd.Series({
        "n": len(g), "filed": wmean((g.EFILING == 1).astype(float).to_numpy(), g.WPFINWGT.to_numpy()),
        "eitc": wmean((g.EEITC == 1).astype(float).to_numpy(), g.WPFINWGT.to_numpy())})).round(3).to_string())
    hi = f[f.state == HI]
    print("Hawaii working adults with EFILING answered:", len(hi), "| EEITC==1:", int((hi.EEITC == 1).sum()))
    print("EEITC / EFILING item nonresponse among adults 18-64 (share missing):",
          round(float(s.EEITC.isna().mean()), 3), round(float(s.EFILING.isna().mean()), 3))


if __name__ == "__main__":
    d = load(sys.argv[1])
    sample_counts(d)
    logit(d)
    exits(d)
    medicaid(d)
    eitc(d)
