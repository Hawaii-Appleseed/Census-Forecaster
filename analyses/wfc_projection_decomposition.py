#!/usr/bin/env python3
"""Why the working-family projections are lower after the claimant-assignment fix: a stage-by-stage trace.

Replays chain/forecast_working_family_credits.py's population path (aged -> IRS household rake -> EITC
by-children reweight "lever 3a" -> IRS-count take-up truncation -> state credits -> take-up factors) for the
three constructions the before/after compared, and records, per tax year and per stage, what the EITC does:

* ``legacy``  : claimant_assignment="legacy", the old filing-status factors (single 0.82, HoH 1.30)  = BEFORE
* ``cons``    : claimant_assignment="parent_first", the old factors                                  = construction only
* ``new``     : claimant_assignment="parent_first", the committed factors (0.83 / 1.18)               = re-derived

Nothing here writes to runs/ or site/, or changes model behaviour: the factor dict is patched in memory only.

    CF_MAX_WORKERS=2 uv run python analyses/wfc_projection_decomposition.py run --variant legacy --out /tmp/wfc_decomp
    (same for cons, new)
    uv run python analyses/wfc_projection_decomposition.py assemble --out /tmp/wfc_decomp --csv-dir docs/data_source_tests
        [--chain-runs legacy=/tmp/cfscratch/runs_C cons=/tmp/cfscratch/runs_A new=/tmp/cfscratch/runs_B]

``run`` (about 6 minutes per variant, under 3 GB) also writes the chain-equivalent ``revenue_by_year`` so it can
be checked against a chain run (``assemble --chain-runs``).
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "chain")]

OLD_FACTORS = {"single": 0.82, "married_filing_jointly": 1.0, "head_of_household": 1.30,
               "married_filing_separately": 1.35}
VARIANTS = {"legacy": ("legacy", OLD_FACTORS), "cons": ("parent_first", OLD_FACTORS),
            "new": ("parent_first", None)}                 # None: the committed factors
YEARS = [2022, 2023, 2024, 2027, 2028, 2029, 2030, 2031]
ANCHOR = 2024
EITC_RATE = 0.40
STAGES = ["aged", "rake", "reweight"]                       # the pipelines compared: each adds one step before take-up
KIDS = [0, 1, 2, 3]


def _stats(u: pd.DataFrame) -> dict:
    """Weighted EITC-eligible count and federal dollars, in total and by qualifying children (0, 1, 2, 3+)."""
    w = u["weight"].to_numpy(float)
    e = u["eitc_amount"].fillna(0).clip(lower=0).to_numpy(float)
    k = np.minimum(u["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
    out = {"w": float(w[e > 0].sum()), "fed": float((w * e).sum())}
    for b in KIDS:
        m = k == b
        out[f"w{b}"] = float(w[m & (e > 0)].sum())
        out[f"fed{b}"] = float((w * e)[m].sum())
    return out


def _region(u: pd.DataFrame, year: int) -> np.ndarray:
    """Federal EITC schedule region of each unit: phase-in (earnings below the plateau), phase-out (the greater of
    earnings and AGI above the phase-out start), else plateau. Parameters are the year's (CPI-extrapolated past 2026)."""
    from tax_modeler.credits.eitc import eitc_parameters_for_year

    p = eitc_parameters_for_year(year, extrapolate=True)
    k = np.minimum(u["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
    ei = u["federal_earned_income"].fillna(0).to_numpy(float)
    agi = u["federal_agi"].fillna(0).to_numpy(float)
    joint = (u["filing_status"].astype(str) == "married_filing_jointly").to_numpy()
    ends = np.array([p.by_children[i].phase_in_ends for i in k])
    start = np.where(joint, [p.by_children[i].phaseout_start_joint for i in k],
                     [p.by_children[i].phaseout_start_single for i in k])
    reg = np.full(len(u), "plateau", dtype=object)
    reg[ei < ends] = "phase_in"
    reg[np.maximum(ei, agi) > start] = "phase_out"
    return reg


def _retained(u: pd.DataFrame, pool: pd.DataFrame, year: int) -> list[dict]:
    """Per children bucket: the pool entering take-up, who is kept, average federal credit, the cut-off credit, regions."""
    rows = []
    w = u["weight"].to_numpy(float)
    e = u["eitc_amount"].fillna(0).clip(lower=0).to_numpy(float)
    k = np.minimum(u["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
    wp = pool["weight"].to_numpy(float)
    ep = pool["eitc_amount"].fillna(0).clip(lower=0).to_numpy(float)
    kp = np.minimum(pool["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
    reg = _region(u, year)
    for b in KIDS:
        m = (k == b) & (e > 0)
        mp = (kp == b) & (ep > 0)
        row = {"kids": b, "pool_w": float(wp[mp].sum()), "pool_fed": float((wp * ep)[mp].sum()),
               "kept_w": float(w[m].sum()), "kept_fed": float((w * e)[m].sum()),
               "min_kept_credit": float(e[m].min()) if m.any() else 0.0}
        for r in ("phase_in", "plateau", "phase_out"):
            row[f"kept_w_{r}"] = float(w[m & (reg == r)].sum())
            row[f"kept_fed_{r}"] = float((w * e)[m & (reg == r)].sum())
        rows.append(row)
    return rows


def run_variant(variant: str, out: Path) -> None:
    import forecast_working_family_credits as wfc
    import poverty_impact_report as pir
    import tax_modeler.base as tb
    from tax_modeler.calibration import irs_household_rake as rk
    from tax_modeler.units import constructor

    mode, factors = VARIANTS[variant]
    real = tb.build_raw_units
    patched = functools.partial(real, claimant_assignment=mode)
    tb.build_raw_units = lambda p, h, **kw: patched(p, h, **{**kw, "parallel": False})
    saved = dict(constructor.FILING_STATUS_WEIGHT_FACTORS)
    if factors is not None:
        constructor.FILING_STATUS_WEIGHT_FACTORS.update(factors)
    try:
        base, _persons = pir._load_units(wfc.PUMS_DIR, False, with_replicate_weights=False)
    finally:
        tb.build_raw_units = real
        constructor.FILING_STATUS_WEIGHT_FACTORS.clear()
        constructor.FILING_STATUS_WEIGHT_FACTORS.update(saved)
    print(f"[{variant}] built {len(base):,} units, weight {base['weight'].sum():,.0f}", flush=True)

    rake = wfc.irs_rake(base, per_replicate=False)
    rows, kept, frames = [], [], {}
    for yr in YEARS:
        aged = pir._build_units_for_tax_year(base, yr, project=True, use_cbo_aging=True)
        u1 = rk.apply_household_factors(aged, rake.factors)
        u2 = pir._apply_eitc_reweight(u1)
        final = pir._apply_credit_takeup(u2, tax_year=yr)
        frames[yr] = (final, wfc.state_credits(final))
        cf = {"aged": pir._apply_credit_takeup(aged, tax_year=yr, programs=("eitc",)),
              "rake": pir._apply_credit_takeup(u1, tax_year=yr, programs=("eitc",)),
              "reweight": final}
        pre = {"aged": aged, "rake": u1, "reweight": u2}
        for stage in STAGES:
            s_pre, s_post = _stats(pre[stage]), _stats(cf[stage])
            rows.append({"variant": variant, "year": yr, "stage": stage,
                         **{f"pre_{k}": v for k, v in s_pre.items()}, **{f"post_{k}": v for k, v in s_post.items()},
                         "pre_total_weight": float(pre[stage]["weight"].sum())})
        for r in _retained(final, u2, yr):
            kept.append({"variant": variant, "year": yr, **r})
        # the by-children lift factors found this year (lever 3a), and the weight it adds
        k2 = np.minimum(u2["eitc_qualifying_children"].fillna(0).to_numpy(int), 3)
        ratio = u2["weight"].to_numpy(float) / u1["weight"].to_numpy(float)
        for b in KIDS:
            m = (k2 == b) & (u1["eitc_amount"].fillna(0).to_numpy(float) > 0)
            kept.append({"variant": variant, "year": yr, "kids": b, "lift_factor": float(ratio[m].max()) if m.any() else 1.0})
        print(f"[{variant}] TY{yr} done", flush=True)

    p_eitc, p_food, p_cdcc = wfc.takeup_factors(frames)
    rev = []
    for yr, (u, c) in frames.items():
        rev.append({"tax_year": yr, **wfc._totals(c, u["weight"].to_numpy(float), p_eitc, p_food, p_cdcc)})
    rev = pd.DataFrame(rev)
    rev["eitc_loss_$M"] = rev["eitc_expanded_$M"] - rev["eitc_reverted_$M"]
    rev["food_loss_$M"] = rev["food_expanded_$M"] - rev["food_reverted_$M"]
    rev["cdcc_loss_$M"] = rev["cdcc_expanded_$M"] - rev["cdcc_reverted_$M"]
    rev["total_loss_$M"] = rev["eitc_loss_$M"] + rev["food_loss_$M"] + rev["cdcc_loss_$M"]
    out.mkdir(parents=True, exist_ok=True)
    rev.to_csv(out / f"{variant}_revenue_by_year.csv", index=False)
    pd.DataFrame(rows).to_csv(out / f"{variant}_stages.csv", index=False)
    pd.DataFrame(kept).to_csv(out / f"{variant}_kept.csv", index=False)
    (out / f"{variant}_takeup.json").write_text(json.dumps({"eitc": p_eitc, "food": p_food, "cdcc": p_cdcc}))
    print(f"[{variant}] take-up EITC {p_eitc:.4f} food {p_food:.4f} care {p_cdcc:.4f}", flush=True)


def _steps(g: pd.DataFrame, v: str, yr: int) -> np.ndarray:
    """The three growth ratios of a variant's credit total: aged, rake over aged, reweight over rake."""
    a, b, c = g.loc[(v, yr), "aged"], g.loc[(v, yr), "rake"], g.loc[(v, yr), "reweight"]
    return np.array([a, b / a, c / b])


def assemble(out: Path, csv_dir: Path, chain_runs: dict[str, Path]) -> None:
    from tax_modeler.calibration import dotax_base

    target_m = dotax_base.eitc_new_credit(ANCHOR)["dollars_M"]
    st = pd.concat([pd.read_csv(out / f"{v}_stages.csv") for v in VARIANTS])
    kept = pd.concat([pd.read_csv(out / f"{v}_kept.csv") for v in VARIANTS])
    rev = {v: pd.read_csv(out / f"{v}_revenue_by_year.csv").set_index("tax_year") for v in VARIANTS}

    # check against the chain runs when given
    if chain_runs:
        for v, d in chain_runs.items():
            ref = pd.read_csv(d / "working_family_credits" / "revenue_by_year.csv").set_index("tax_year")
            diff = (rev[v][ref.columns] - ref).abs().max().max()
            print(f"replay vs chain run {v}: max abs difference over revenue_by_year.csv = {diff:.6f}")

    # dollars at each stage, refitted to the anchor within each pipeline
    st["eitc_unscaled_$M"] = st["post_fed"] * EITC_RATE / 1e6
    anchor = st[st["year"] == ANCHOR].set_index(["variant", "stage"])["eitc_unscaled_$M"]
    st["p_fit"] = [target_m / anchor[(v, s)] for v, s in zip(st["variant"], st["stage"], strict=True)]
    st["eitc_$M"] = st["eitc_unscaled_$M"] * st["p_fit"]
    st["growth_vs_anchor"] = st["eitc_$M"] / target_m
    st.to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_stages.csv", index=False)
    kept.to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_kept_by_children.csv", index=False)

    # waterfall: swap the three growth ratios (aged, rake/aged, reweight/rake) from legacy's to the new run's in turn
    g = st.pivot_table(index=["variant", "year"], columns="stage", values="growth_vs_anchor")
    rows = []
    for tgt in ("new", "cons"):
        for yr in (2027, 2028, 2029, 2030, 2031):
            sl, sn = _steps(g, "legacy", yr), _steps(g, tgt, yr)
            cur = sl.copy()
            level = target_m * cur.prod()
            rows.append({"to": tgt, "year": yr, "step": "legacy (full pipeline)", "eitc_40pct_$M": level, "delta_$M": np.nan})
            for i, name in enumerate(["raw units and income ageing (growth of the take-up-truncated credit total)",
                                      "IRS household rake (extra growth)", "by-children reweight, lever 3a (extra growth)"]):
                cur[i] = sn[i]
                new_level = target_m * cur.prod()
                rows.append({"to": tgt, "year": yr, "step": name, "eitc_40pct_$M": new_level, "delta_$M": new_level - level})
                level = new_level
            rows.append({"to": tgt, "year": yr, "step": "end (the " + ("re-derived" if tgt == "new" else "construction-only") + " run)",
                         "eitc_40pct_$M": rev[tgt].loc[yr, "eitc_expanded_$M"], "delta_$M": np.nan})
    pd.DataFrame(rows).to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_waterfall.csv", index=False)


    # by children bucket: the same EITC difference (new vs legacy, 40% scenario, take-up applied) bucket by bucket
    pe = {v: json.loads((out / f"{v}_takeup.json").read_text())["eitc"] for v in VARIANTS}
    kp = kept[kept["lift_factor"].isna()].set_index(["variant", "year", "kids"])
    bk = []
    for tgt in ("new", "cons"):
        for yr in (2024, 2028, 2031):
            for b in KIDS:
                o, n = kp.loc[("legacy", yr, b)], kp.loc[(tgt, yr, b)]
                d_o, d_n = EITC_RATE * pe["legacy"] * o["kept_fed"] / 1e6, EITC_RATE * pe[tgt] * n["kept_fed"] / 1e6
                bk.append({"to": tgt, "year": yr, "kids": b, "legacy_$M": d_o, "after_$M": d_n, "delta_$M": d_n - d_o,
                           "legacy_pool_w": o["pool_w"], "after_pool_w": n["pool_w"], "legacy_kept_w": o["kept_w"],
                           "after_kept_w": n["kept_w"], "legacy_avg_fed_credit": o["kept_fed"] / o["kept_w"],
                           "after_avg_fed_credit": n["kept_fed"] / n["kept_w"]})
    pd.DataFrame(bk).to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_by_children_waterfall.csv", index=False)

    # claimants: eligible units at each stage, the kept units, the take-up factor
    cl = []
    for v in VARIANTS:
        for yr in (2023, 2024, 2028, 2031):
            sv = st[(st["variant"] == v) & (st["year"] == yr)].set_index("stage")
            cl.append({"variant": v, "year": yr, "eligible_raw": sv.loc["aged", "pre_w"],
                       "eligible_after_rake": sv.loc["rake", "pre_w"],
                       "eligible_after_reweight": sv.loc["reweight", "pre_w"],
                       "kept_after_takeup_truncation": sv.loc["reweight", "post_w"], "takeup_factor": pe[v],
                       "claimants": rev[v].loc[yr, "eitc_claimants"],
                       "fed_credit_per_kept_unit": sv.loc["reweight", "post_fed"] / sv.loc["reweight", "post_w"],
                       "hi_credit_per_claimant": rev[v].loc[yr, "eitc_expanded_$M"] * 1e6 / rev[v].loc[yr, "eitc_claimants"],
                       "total_unit_weight_after_reweight": sv.loc["reweight", "pre_total_weight"]})
    pd.DataFrame(cl).to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_claimants.csv", index=False)

    # Act 163 expiry cost, by credit
    cost = []
    for v in VARIANTS:
        for yr in (2024, 2027, 2028, 2029, 2030, 2031):
            r = rev[v].loc[yr]
            cost.append({"variant": v, "year": yr, "eitc_loss": r["eitc_loss_$M"], "food_loss": r["food_loss_$M"],
                         "cdcc_loss": r["cdcc_loss_$M"], "total_loss": r["total_loss_$M"],
                         "eitc_expanded": r["eitc_expanded_$M"], "eitc_claimants": r["eitc_claimants"]})
    pd.DataFrame(cost).to_csv(csv_dir / "tax-unit-claimant-assignment_wfc_cost_by_variant.csv", index=False)
    print("assembled", csv_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=list(VARIANTS), required=True)
    r.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("assemble")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--csv-dir", type=Path, required=True)
    a.add_argument("--chain-runs", nargs="*", default=[], help="variant=dir pairs")
    args = ap.parse_args()
    if args.cmd == "run":
        run_variant(args.variant, args.out)
    else:
        assemble(args.out, args.csv_dir, {k: Path(v) for k, v in (x.split("=") for x in args.chain_runs)})


if __name__ == "__main__":
    main()
