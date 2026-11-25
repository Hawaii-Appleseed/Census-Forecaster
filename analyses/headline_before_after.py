#!/usr/bin/env python3
"""The published headline figures, before and after the parent-first claimant assignment.

BEFORE is a runs directory made with ``claimant_assignment="legacy"`` (which reproduces the published ``site/data``
byte for byte, checked), AFTER-A one made with the parent-first construction and the earlier filing-status weight
factors, AFTER-B one with the re-derived factors. Each is a directory laid out like ``runs/`` (chain steps and the
H.R.1 and targeting-efficiency analyses), plus optionally a directory of the EITC/CTC geography report
(``by_state.csv``).

    uv run python analyses/headline_before_after.py --before R_old --after-old-factors R_a --after-rederived R_b \
        [--geo-before G_old --geo-after-old-factors G_a --geo-after-rederived G_b] --out headline.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

YEARS = (2027, 2028, 2029, 2030, 2031)


def _csv(root: Path, rel: str) -> pd.DataFrame:
    return pd.read_csv(root / rel)


def _row(df: pd.DataFrame, **where) -> pd.Series:
    m = np.ones(len(df), dtype=bool)
    for k, v in where.items():
        m &= (df[k].astype(str) == str(v)).to_numpy()
    if m.sum() != 1:
        raise KeyError(f"{where}: {int(m.sum())} rows")
    return df[m].iloc[0]


def _flat(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flat(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flat(v, f"{prefix}{i}.")
    else:
        yield prefix.rstrip("."), obj


def figures(root: Path, geo: Path | None) -> dict[tuple[str, str], float]:
    """{(figure group, figure label): value} for one set of runs."""
    out: dict[tuple[str, str], float] = {}

    def put(group, label, value):
        out[(group, label)] = float(value)

    enh = _csv(root, "sb3125_cd2_enhanced/enhanced.csv")
    for sc in ("LOW", "MID", "HIGH"):
        for y in YEARS:
            r = enh[(enh.scenario == sc) & (enh.tax_year == y)].iloc[0]
            put("Act 24 (CD2) total impact, $M", f"{sc} {y}", r["total_impact_$M"])
    for y in YEARS:
        r = enh[(enh.scenario == "MID") & (enh.tax_year == y)].iloc[0]
        put("Act 24 (CD2) MID, $M", f"{y} bracket change after response", r["bracket_delta_post_$M"])
        put("Act 24 (CD2) MID, $M", f"{y} Act 46 baseline revenue", r["act46_baseline_$M"])
    dec = _csv(root, "act24_vs_pre_act46/decomposition.csv")
    for y in YEARS:
        r = dec[dec.tax_year == y].iloc[0]
        put("Act 24 vs pre-Act-46 law, $M", f"{y} total", r["total_vs_pre_act46"])
        put("Act 24 vs pre-Act-46 law, $M", f"{y} Act 24 increment", r["C_act24_increment"])
    cg = _csv(root, "cg_rate_options/revenue_by_year.csv")
    for opt in ("cap9", "ordinary"):
        for y in YEARS:
            r = cg[(cg.tax_year == y) & (cg.law == "act24") & (cg.option == opt) & (cg.top_share == "central")].iloc[0]
            put("Capital gains, Act 24 law, central, $M", f"{opt} {y} static", r["static_M"])
            put("Capital gains, Act 24 law, central, $M", f"{opt} {y} behavioral", r["behavioral_M"])
    wf = _csv(root, "working_family_credits/revenue_by_year.csv")
    for y in (2024, 2027, 2028, 2031):
        r = wf[wf.tax_year == y].iloc[0]
        for c, nm in (("eitc_expanded_$M", "Hawaii EITC at 40%"), ("food_expanded_$M", "food/excise credit, Act 163 table"),
                      ("cdcc_expanded_$M", "care credit, Act 163 limits"), ("eitc_loss_$M", "EITC cost of expiry"),
                      ("food_loss_$M", "food/excise cost of expiry"), ("cdcc_loss_$M", "care credit cost of expiry"),
                      ("total_loss_$M", "all three, cost of expiry (cost of renewing)")):
            put("Working-family credits, $M", f"TY{y} {nm}", r[c])
        put("Working-family credits, claimants", f"TY{y} EITC claimants", r["eitc_claimants"])
    cal = _csv(root, "working_family_credits/calibration.csv").set_index("check")
    for chk in ("take-up factor, EITC", "take-up factor, food/excise", "take-up factor, child and dependent care"):
        put("Working-family credits, take-up factors", chk, cal.loc[chk, "model"])
    for chk in ("TY2024 EITC claims", "TY2023 EITC claims (out of sample)", "TY2023 EITC $M (out of sample)"):
        put("Working-family credits, fit checks", chk, cal.loc[chk, "model"])
    pov = _csv(root, "working_family_credits/poverty_ty2028.csv").iloc[0]
    for c in ("poverty_rate_renewed", "poverty_rate_expired", "persons_into_poverty", "children_into_poverty",
              "poverty_gap_increase_$M", "persons_into_poverty_se"):
        put("Working-family credits, TY2028 poverty", c, pov[c])
    bc = _csv(root, "working_family_credits/by_credit_ty2028.csv").set_index(
        _csv(root, "working_family_credits/by_credit_ty2028.csv").columns[0])
    for name in bc.index:
        put("Working-family credits, TY2028 households losing", f"{name}: households losing, share",
            bc.loc[name, "pct_households_losing"])
    chk = _csv(root, "working_family_credits/eitc_check_ty2024.csv")
    kcol = chk.columns[0]
    for _, r in chk.iterrows():
        put("Working-family credits, TY2024 EITC by status, model $M", f"{r[kcol]} {r[chk.columns[1]]}", r["model_$M"])
    # H.R.1
    hs = json.loads((root / "hr1_snap_medicaid/summary.json").read_text())
    put("H.R.1 SNAP/Medicaid", "SNAP households, model base", hs["snap_base"]["units"])
    snap = _csv(root, "hr1_snap_medicaid/snap_losses.csv")
    for sc in ("LOW", "MID", "HIGH"):
        r = snap[(snap.scenario == sc) & (snap.group == "all_newly_subject")].iloc[0]
        put("H.R.1 SNAP/Medicaid", f"{sc} adults losing SNAP", r["adults_losing_snap"])
    hp = _csv(root, "hr1_snap_medicaid/poverty.csv")
    for sc in ("LOW", "MID", "HIGH"):
        r = hp[hp.scenario == sc].iloc[0]
        put("H.R.1 SNAP/Medicaid", f"{sc} people pushed into poverty", r["persons_into_poverty"])
        put("H.R.1 SNAP/Medicaid", f"{sc} children pushed into poverty", r["children_into_poverty"])
        put("H.R.1 SNAP/Medicaid", f"{sc} poverty gap increase, $", r["poverty_gap_increase_usd"])
    put("H.R.1 SNAP/Medicaid", "baseline SPM poverty rate", hp[hp.scenario == "MID"].iloc[0]["poverty_rate_baseline"])
    mc = _csv(root, "hr1_snap_medicaid/medicaid_losses.csv")
    for sc in ("LOW", "MID", "HIGH"):
        r = mc[(mc.pums == "pums_2020_2024") & (mc.scenario == sc)].iloc[0]
        put("H.R.1 SNAP/Medicaid", f"{sc} Medicaid expansion adults losing coverage", r["lose_coverage_total"])
    # targeting efficiency
    te = _csv(root, "targeting_efficiency/references.csv")
    for _, r in te.iterrows():
        for c in ("cost_$M", "persons_lifted", "persons_lifted_per_$M", "poverty_gap_closed_$M"):
            put("Targeting efficiency, reference policies", f"{r['reference']} {c}", r[c])
    tg = _csv(root, "targeting_efficiency/groups.csv")
    for _, r in tg.iterrows():
        put("Targeting efficiency, SPM poverty rate by group", r["group"], r["poverty_rate"])
        put("Targeting efficiency, persons in poverty by group", r["group"], r["eligible_persons_in_poverty"])
    sb = _csv(root, "targeting_efficiency/same_budget.csv")
    first = sb.iloc[0]
    sub = sb[(sb["budget_$M"] == first["budget_$M"]) & (sb["income_test"] == first["income_test"])]
    for _, r in sub.iterrows():
        put("Targeting efficiency, share of $ closing the gap (smallest budget, first income test)", r["group"],
            r["share_of_dollars_closing_gap"])
        put("Targeting efficiency, rank by gap closed (smallest budget, first income test)", r["group"],
            r["rank_by_gap_closed"])
    # simulator: preset revenue and the credit pricing the web page quotes
    pres = json.loads((root / "tax_simulator/web/presets_results.json").read_text())
    for name in ("current_law", "cg_9", "cg_ordinary", "renew_act163"):
        for r in pres[name]["revenue"]:
            if r["scenario"] == "mid":
                put("Tax simulator preset revenue, MID, $M", f"{name} {r['tax_year']} reform", r["reform_$M"])
                put("Tax simulator preset revenue, MID, $M", f"{name} {r['tax_year']} static vs baseline", r["static_$M"])
    cr = json.loads((root / "tax_simulator/credits.json").read_text())
    for y, v in cr["eitc_per_point_$M"].items():
        put("Tax simulator credit pricing, $M", f"EITC, per percentage point of the federal credit, TY{y}", v)
    for law, d in cr["food_excise_$M"].items():
        for y, v in d.items():
            put("Tax simulator credit pricing, $M", f"food/excise {law}, TY{y}", v)
    if geo is not None and (geo / "by_state.csv").exists():
        g = pd.read_csv(geo / "by_state.csv").iloc[0]
        for c in g.index:
            put("EITC/CTC geography, TY2025 state totals", c, g[c])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after-old-factors", type=Path, required=True)
    ap.add_argument("--after-rederived", type=Path, required=True)
    ap.add_argument("--geo-before", type=Path)
    ap.add_argument("--geo-after-old-factors", type=Path)
    ap.add_argument("--geo-after-rederived", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    b = figures(a.before, a.geo_before)
    x = figures(a.after_old_factors, a.geo_after_old_factors)
    y = figures(a.after_rederived, a.geo_after_rederived)
    rows = []
    for (grp, lab), vb in b.items():
        va, vr = x.get((grp, lab), np.nan), y.get((grp, lab), np.nan)
        rows.append({
            "group": grp, "figure": lab, "before": vb,
            "after_construction_only": va, "abs_change_construction": va - vb,
            "pct_change_construction": (va / vb - 1) * 100 if vb else np.nan,
            "after_rederived_factors": vr, "abs_change_total": vr - vb,
            "pct_change_total": (vr / vb - 1) * 100 if vb else np.nan,
            "abs_change_from_factors": vr - va,
        })
    pd.DataFrame(rows).to_csv(a.out, index=False)
    print(f"{len(rows)} figures -> {a.out}")


if __name__ == "__main__":
    main()
