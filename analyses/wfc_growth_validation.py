#!/usr/bin/env python3
"""History against the projections: Hawaii EITC returns, dollars per return and the statutory parameters, side by side.

Reads the assembled history (``analyses/hi_eitc_history.py`` writes it), and the replay outputs of
``wfc_projection_decomposition.py`` (``run``) and ``wfc_targets_backtest.py``; prints markdown tables and writes
``tax-unit-claimant-assignment_growth_rates.csv``. Analysis only.

    uv run python analyses/wfc_growth_validation.py --decomp /tmp/cfscratch/decomp --tb /tmp/wfc_tb \
        --csv-dir docs/data_source_tests
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
HISTORY = REPO / "docs/data_source_tests/tax-unit-claimant-assignment_hi_eitc_history.csv"
PANDEMIC = {2020: "pandemic (lookback election, stimulus)", 2021: "ARPA childless expansion, expanded CTC"}
ROW_PAIRS = [(2010, 2011), (2011, 2012), (2012, 2014), (2014, 2015), (2015, 2017), (2017, 2018), (2018, 2019),
             (2019, 2020), (2020, 2021), (2021, 2022), (2022, 2023)]
WINDOWS = {"TY2010-2019 (pre-pandemic)": (2010, 2019), "TY2015-2019": (2015, 2019), "TY2019-2023 (spans pandemic)": (2019, 2023),
           "TY2022-2023 (one post-pandemic pair)": (2022, 2023)}


def pct(x: float) -> str:
    return f"{100 * x:+.2f}%"


def ann(a: float, b: float, n: int) -> float:
    return (b / a) ** (1 / n) - 1


def history() -> pd.DataFrame:
    h = pd.read_csv(HISTORY)
    w = h.pivot_table(index="year", columns="series", values="value")
    mix = np.array([w.loc[2022, f"fed_eitc_returns_kids{k}"] for k in range(4)])
    mix = mix / mix.sum()                                           # TY2022 by-children return mix, held fixed
    out = pd.DataFrame({"returns": w["fed_eitc_returns"], "fed_$M": w["fed_eitc_dollars_k"] / 1e3}).dropna()
    out["all_returns"] = w["fed_all_returns"]
    out["eitc_share_of_returns"] = out["returns"] / out["all_returns"]
    out["$_per_return"] = out["fed_$M"] * 1e6 / out["returns"]
    param = pd.Series({y: float(np.dot(mix, [w.loc[y, f"fed_max_credit_kids{k}"] for k in range(4)])) for y in w.index
                       if not np.isnan(w.loc[y, "fed_max_credit_kids0"])})
    out["param_index_$"] = param.reindex(out.index)
    out["real_$_per_return"] = out["$_per_return"] / out["param_index_$"]
    return out


def pairs(out: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for a, b in ROW_PAIRS:
        n = b - a
        rows.append({"from": a, "to": b, "years": n,
                     "returns_pa": ann(out.returns[a], out.returns[b], n),
                     "$_per_return_pa": ann(out["$_per_return"][a], out["$_per_return"][b], n),
                     "param_pa": ann(out["param_index_$"][a], out["param_index_$"][b], n),
                     "real_$_per_return_pa": ann(out["real_$_per_return"][a], out["real_$_per_return"][b], n),
                     "flag": "; ".join(PANDEMIC[y] for y in range(a + 1, b + 1) if y in PANDEMIC) or ("pandemic" if a in PANDEMIC else "")})
    return pd.DataFrame(rows)


def windows(out: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, (a, b) in WINDOWS.items():
        n = b - a
        rows.append({"window": name, "years": n, "returns_pa": ann(out.returns[a], out.returns[b], n),
                     "$_per_return_pa": ann(out["$_per_return"][a], out["$_per_return"][b], n),
                     "param_pa": ann(out["param_index_$"][a], out["param_index_$"][b], n),
                     "real_$_per_return_pa": ann(out["real_$_per_return"][a], out["real_$_per_return"][b], n),
                     "fed_$_pa": ann(out["fed_$M"][a], out["fed_$M"][b], n)})
    return pd.DataFrame(rows)


def trends(out: pd.DataFrame) -> list[tuple[str, int, float, float]]:
    res = []
    for name, lo, hi, drop in [("TY2010-2019", 2010, 2019, []), ("TY2012-2019", 2012, 2019, []), ("TY2015-2019", 2015, 2019, []),
                               ("TY2010-2023 without 2020-21", 2010, 2023, [2020, 2021]),
                               ("TY2017-2023 without 2020-21", 2017, 2023, [2020, 2021])]:
        s = out.returns[(out.index >= lo) & (out.index <= hi)].drop([y for y in drop if y in out.index])
        x, y = s.index.to_numpy(float), np.log(s.to_numpy(float))
        b, a = np.polyfit(x, y, 1)
        resid = y - (a + b * x)
        se = float(np.sqrt((resid**2).sum() / (len(x) - 2) / ((x - x.mean()) ** 2).sum())) if len(x) > 2 else float("nan")
        res.append((name, len(s), float(np.exp(b) - 1), se))
    return res


def dotax_table(out: pd.DataFrame) -> pd.DataFrame:
    """DOTAX state EITC against the federal credit, per federal dollar. Rate: 20% nonrefundable TY2018-22, 40% refundable TY2023+."""
    h = pd.read_csv(HISTORY)
    w = h.pivot_table(index="year", columns="series", values="value")
    rows = []
    for y in range(2018, 2025):
        rate = 0.20 if y <= 2022 else 0.40
        claims = w.loc[y, "dotax_eitc_claims"] if y <= 2023 else w.loc[y, "dotax_eitc_new_claims"]
        dollars = w.loc[y, "dotax_eitc_dollars_k"] if y <= 2023 else w.loc[y, "dotax_eitc_new_dollars_k"]
        r = {"year": y, "statutory_rate": rate, "dotax_claims": claims, "dotax_$M": dollars / 1e3,
             "dotax_$_per_claim_federal_equiv": dollars * 1e3 / rate / claims}
        if y in out.index:
            r["soi_returns"] = out.returns[y]
            r["claims_per_soi_return"] = claims / out.returns[y]
            r["dotax_$_per_statutory_$"] = dollars / 1e3 / (rate * out["fed_$M"][y])
        rows.append(r)
    return pd.DataFrame(rows)


def csv_param(h: pd.DataFrame, year: int) -> float:
    """Parameter index from the history file (maximum credits), TY2022 by-children return mix."""
    mix = np.array([h.loc[2022, f"fed_eitc_returns_kids{k}"] for k in range(4)])
    return float(np.dot(mix / mix.sum(), [h.loc[year, f"fed_max_credit_kids{k}"] for k in range(4)]))


def central_table(decomp: Path) -> pd.DataFrame:
    """IRS EITC Central (claims processed, rounded) pairs through TY2024, and the models' TY2024 federal credit per unit."""
    h = pd.read_csv(HISTORY).pivot_table(index="year", columns="series", values="value")
    n, dol = h["irs_central_eitc_claims"], h["irs_central_eitc_dollars_k"] * 1e3
    avg = h["irs_central_eitc_avg"]
    rows = []
    for a, b in ((2019, 2024), (2022, 2024), (2023, 2024), (2022, 2023)):
        k = b - a
        pg = ann(csv_param(h, a), csv_param(h, b), k)
        rows.append({"from": a, "to": b, "claims_pa": ann(n[a], n[b], k), "dollars_pa": ann(dol[a], dol[b], k),
                     "avg_credit_pa": ann(avg[a], avg[b], k), "param_pa": pg,
                     "real_avg_credit_pa": (1 + ann(avg[a], avg[b], k)) / (1 + pg) - 1})
    m = {v: pd.read_csv(decomp / f"{v}_revenue_by_year.csv").set_index("tax_year") for v in ("legacy", "new")}
    for v, r in m.items():
        pc = r["eitc_expanded_$M"][2024] * 1e6 / 0.40 / r["eitc_claimants"][2024]
        rows.append({"from": 2024, "to": 2024, "claims_pa": float("nan"), "dollars_pa": float("nan"),
                     "avg_credit_pa": pc / avg[2024] - 1, "param_pa": float("nan"), "real_avg_credit_pa": float("nan"),
                     "note": f"{v} model federal credit per unit TY2024 ${pc:,.0f} vs IRS ${avg[2024]:,.0f}"})
    return pd.DataFrame(rows)


def backtest_table(tb: Path, out: pd.DataFrame) -> pd.DataFrame:
    """One- and two-year-ahead errors of the 2018-2022-file replays (``wfc_targets_backtest.py backtest``)."""
    h = pd.read_csv(HISTORY).pivot_table(index="year", columns="series", values="value")
    rows = []
    for v in ("legacy", "new"):
        d = pd.read_csv(tb / f"{v}_backtest.csv")
        d = d[d.peg == "none"].set_index("year")
        r22, r23, r24 = d.loc[2022], d.loc[2023], d.loc[2024]
        act22, act23 = out["fed_$M"][2022], out["fed_$M"][2023]
        growth = r23.fed / r22.fed
        rows.append({"variant": v, "model_fed_2022_$M_raw": r22.fed / 1e6, "soi_2022_$M": act22,
                     "level_error_2022": r22.fed / 1e6 / act22 - 1,
                     "model_growth_22_23": growth - 1, "actual_growth_22_23": act23 / act22 - 1,
                     "anchored_fed_2023_$M": act22 * growth, "soi_2023_$M": act23,
                     "anchored_error_2023": act22 * growth / act23 - 1,
                     "raw_level_error_2023": r23.fed / 1e6 / act23 - 1,
                     "kept_returns_2023": r23.kept_w, "soi_returns_2023": out.returns[2023],
                     "returns_error_2023": r23.kept_w / out.returns[2023] - 1,
                     "hi_$M_2024_from_dotax23": h.loc[2023, "dotax_eitc_dollars_k"] / 1e3 * r24.fed / r23.fed,
                     "dotax_new_2024_$M": h.loc[2024, "dotax_eitc_new_dollars_k"] / 1e3,
                     "dotax_applied_2024_$M": h.loc[2024, "dotax_eitc_applied_dollars_k"] / 1e3,
                     "claims_2024_from_dotax23": h.loc[2023, "dotax_eitc_claims"] * r24.kept_w / r23.kept_w,
                     "dotax_new_claims_2024": h.loc[2024, "dotax_eitc_new_claims"],
                     "dotax_applied_claims_2024": h.loc[2024, "dotax_eitc_applied_claims"]})
    return pd.DataFrame(rows)


def targets_table(tb: Path) -> pd.DataFrame:
    parts = [pd.read_csv(tb / f"{v}_targets{sfx}.csv") for v in ("legacy", "new") for sfx in ("", "_sens")
             if (tb / f"{v}_targets{sfx}.csv").exists()]
    d = pd.concat(parts)
    return d[d.year.isin([2024, 2028, 2031])][["variant", "scenario", "year", "takeup_factor", "hi_eitc_40_$M", "claimants",
                                              "kept_w", "kept_w0", "kept_w1", "kept_w2", "kept_w3"]]


def model_param(year: int) -> float:
    from tax_modeler.credits.eitc import eitc_parameters_for_year

    h = pd.read_csv(HISTORY)
    mix = np.array([h[(h.series == f"fed_eitc_returns_kids{k}") & (h.year == 2022)].value.iloc[0] for k in range(4)])
    mix = mix / mix.sum()
    p = eitc_parameters_for_year(year, extrapolate=True)
    return float(np.dot(mix, [p.by_children[k].max_credit for k in range(4)]))


def projections(decomp: Path) -> pd.DataFrame:
    rows = []
    for v in ("legacy", "new"):
        rev = pd.read_csv(decomp / f"{v}_revenue_by_year.csv").set_index("tax_year")
        claim = rev["eitc_claimants"]
        fed_pc = rev["eitc_expanded_$M"] * 1e6 / 0.40 / claim        # federal credit per claimant (take-up factor cancels)
        for a, b in ((2024, 2028), (2024, 2031)):
            n = b - a
            c_g, d_g = ann(claim[a], claim[b], n), ann(fed_pc[a], fed_pc[b], n)
            p_g = ann(model_param(a), model_param(b), n)
            rows.append({"variant": v, "from": a, "to": b, "years": n, "claimants_pa": c_g, "$_per_claimant_pa": d_g,
                         "param_pa": p_g, "real_$_per_claimant_pa": (1 + d_g) / (1 + p_g) - 1,
                         "fed_$_pa": ann(rev["eitc_expanded_$M"][a], rev["eitc_expanded_$M"][b], n),
                         "claimants_a": claim[a], "claimants_b": claim[b], "fed_pc_a": fed_pc[a], "fed_pc_b": fed_pc[b]})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decomp", type=Path, required=True)
    ap.add_argument("--csv-dir", type=Path, required=True)
    ap.add_argument("--tb", type=Path, help="output dir of wfc_targets_backtest.py (backtest and targets)")
    a = ap.parse_args()
    sys.path.insert(0, str(REPO / "packages" / "tax_modeler" / "src"))
    out = history()
    print("## Series\n")
    print(out.round(1).to_markdown())
    pr = pairs(out)
    print("\n## Year pairs, annualized\n")
    print(pr.assign(**{c: pr[c].map(pct) for c in pr.columns if c.endswith("_pa")}).to_markdown(index=False))
    wd = windows(out)
    print("\n## Windows\n")
    print(wd.assign(**{c: wd[c].map(pct) for c in wd.columns if c.endswith("_pa")}).to_markdown(index=False))
    print("\n## Log-linear trend of returns\n")
    for name, n, g, se in trends(out):
        print(f"{name}: {n} points, {pct(g)} a year (se {100 * se:.2f} pt)")
    print("\n## DOTAX state EITC on a federal-equivalent basis\n")
    print(dotax_table(out).round(3).to_markdown(index=False))
    print("\n## IRS EITC Central (processed claims) pairs and the TY2024 level check\n")
    ct = central_table(a.decomp)
    print(ct.assign(**{c: ct[c].map(pct) for c in ct.columns if c.endswith("_pa")}).to_markdown(index=False))
    pj = projections(a.decomp)
    print("\n## Projections, annualized\n")
    print(pj.assign(**{c: pj[c].map(pct) for c in pj.columns if c.endswith("_pa")}).round(1).to_markdown(index=False))
    side = pd.concat([wd.rename(columns={"window": "period"}).assign(source="history"),
                      pj.assign(period=lambda d: "TY" + d["from"].astype(str) + "-" + d["to"].astype(str) + " " + d["variant"],
                                source="projection").rename(columns={"claimants_pa": "returns_pa", "$_per_claimant_pa": "$_per_return_pa",
                                                                      "real_$_per_claimant_pa": "real_$_per_return_pa"})],
                     ignore_index=True)
    side[["source", "period", "years", "returns_pa", "$_per_return_pa", "param_pa", "real_$_per_return_pa", "fed_$_pa"]].to_csv(
        a.csv_dir / "tax-unit-claimant-assignment_growth_rates.csv", index=False)
    out.to_csv(a.csv_dir / "tax-unit-claimant-assignment_hi_eitc_history_derived.csv")
    if a.tb is not None and (a.tb / "legacy_backtest.csv").exists():
        bt = backtest_table(a.tb, out)
        print("\n## Backtest\n")
        print(bt.set_index("variant").T.to_markdown())
        bt.to_csv(a.csv_dir / "tax-unit-claimant-assignment_backtest.csv", index=False)
    if a.tb is not None and (a.tb / "new_targets.csv").exists() and (a.tb / "legacy_targets.csv").exists():
        tg = targets_table(a.tb)
        print("\n## Targets\n")
        print(tg.round(3).to_markdown(index=False))
        tg.to_csv(a.csv_dir / "tax-unit-claimant-assignment_stale_targets.csv", index=False)


if __name__ == "__main__":
    main()
