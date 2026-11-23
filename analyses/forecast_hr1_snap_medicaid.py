#!/usr/bin/env python3
"""What H.R.1 (Public Law 119-21) takes from SNAP and Medicaid in Hawaii, in a full year under the new rules.

SNAP is read from Hawaii's rows of the FY2024 SNAP Quality Control file, Medicaid from ACS PUMS persons who report
it, scaled to Med-QUEST's enrollment count (:mod:`tax_modeler.programs.hr1_safety_net`). The script counts who the
work rules newly reach, who among them already works enough or is exempt, and, under LOW / MID / HIGH assumptions
about how many fail to keep the benefit, the people and federal dollars lost and what the State would pay to
replace them. It adds the SNAP costs the law moves to the State from federal fiscal year 2028.

    .venv/bin/python analyses/forecast_hr1_snap_medicaid.py

Outputs (reports/hr1_snap_medicaid/): snap_adults.csv, snap_losses.csv, snap_income_depth.csv,
snap_state_cost.csv, medicaid_adults.csv, medicaid_losses.csv, summary.json, summary.txt.

Not modeled: the SNAP utility-allowance and internet changes, the frozen Thrifty Food Plan, Medicaid provider-tax
and state-directed-payment limits, cost sharing, retroactive coverage, Medicaid's non-citizen limits, and the
premium tax credit. ``HR1_SNAP_MEDICAID_METHODOLOGY.md`` says why and gives the sources for every parameter.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
OUT_DIR = REPO / "reports" / "hr1_snap_medicaid"
PUMS_DIR = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"
PUMS_DIR_5YR = REPO / "packages" / "data" / "raw" / "pums_2020_2024"
SCENARIOS = ("LOW", "MID", "HIGH")
N_BOOT = 2000
SEED = 20261122

log = logging.getLogger("hr1")


def _interval(draws: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(draws, [5, 95])
    return float(lo), float(hi)


# --------------------------------------------------------------------------------------------------------------
# SNAP
# --------------------------------------------------------------------------------------------------------------


def run_snap(params) -> dict[str, pd.DataFrame]:
    from tax_modeler.programs import hr1_safety_net as hr1

    units, persons = hr1.load_hawaii_qc()
    adults = hr1.classify_snap_adults(units, persons)
    adults["loss_if_removed"] = hr1.snap_loss_if_removed(units, adults)
    # Restate FY2024 dollars at the FY2027 allotment level, by unit size.
    size = adults["FSUSIZE"].astype(int)
    adults["to_2027"] = [hr1.max_allotment(n, 2027) / hr1.max_allotment(n, 2024) for n in size]

    table = (
        adults.assign(n=1, persons=adults["FYWGT"],
                      loss_w=adults["FYWGT"] * adults["loss_if_removed"])
        .groupby(["group", "status"], as_index=False)
        .agg(sample=("n", "sum"), persons=("persons", "sum"), loss_w=("loss_w", "sum"))
    )
    table["monthly_loss_if_removed_fy2024"] = table.pop("loss_w") / table["persons"]
    table["newly_subject"] = table["group"].isin(("age_55_64", "child_14_17", "removed_exception"))

    new = adults[adults["newly_subject"]].copy()
    rng = np.random.default_rng(SEED)
    unit_ids = units["unit_id"].to_numpy()
    counts = rng.multinomial(len(unit_ids), np.full(len(unit_ids), 1 / len(unit_ids)), size=N_BOOT)
    position = pd.Series(np.arange(len(unit_ids)), index=unit_ids)

    rows = []
    for scen in SCENARIOS:
        rate = params.snap_rates[scen].rate(new["status"])
        # A unit cannot lose more than its benefit, however many members drop out.
        new["p"] = rate
        new["exp_persons"] = new["FYWGT"] * rate
        new["exp_loss"] = new["FYWGT"] * rate * new["loss_if_removed"]
        cap = new.groupby("unit_id")["exp_loss"].transform("sum") / (new["FYWGT"] * new["FSBEN"])
        new["exp_loss"] = new["exp_loss"] / np.maximum(cap, 1.0)
        new["exp_loss_2027"] = new["exp_loss"] * new["to_2027"]
        for group, g in [("all_newly_subject", new)] + list(new.groupby("group")):
            per_unit = g.groupby("unit_id").agg(
                persons=("exp_persons", "sum"), usd24=("exp_loss", "sum"),
                usd27=("exp_loss_2027", "sum"),
            )
            dense = np.zeros((len(unit_ids), 3))
            dense[position[per_unit.index].to_numpy()] = per_unit.to_numpy()
            boot = counts @ dense
            p_lo, p_hi = _interval(boot[:, 0])
            d_lo, d_hi = _interval(boot[:, 2] * 12)
            rows.append({
                "scenario": scen, "group": group, "sample_adults": len(g),
                "adults_subject": g["FYWGT"].sum(),
                "adults_losing_snap": per_unit["persons"].sum(),
                "adults_losing_snap_p5": p_lo, "adults_losing_snap_p95": p_hi,
                "annual_benefits_lost_fy2024_usd": per_unit["usd24"].sum() * 12,
                "annual_benefits_lost_fy2027_usd": per_unit["usd27"].sum() * 12,
                "annual_benefits_lost_fy2027_p5": d_lo, "annual_benefits_lost_fy2027_p95": d_hi,
            })
    losses = pd.DataFrame(rows)

    # How poor the units that lose benefits already are (MID), on countable income plus SNAP.
    mid = params.snap_rates["MID"].rate(new["status"])
    u = units.set_index("unit_id").loc[new["unit_id"]]
    line = np.array([hr1.poverty_line_2024(int(n)) for n in u["FSUSIZE"]])
    w = (new["FYWGT"] * mid).to_numpy()
    gross = u["FSGRINC"].to_numpy()
    ben = u["FSBEN"].to_numpy()
    loss = new["loss_if_removed"].to_numpy()
    before = (gross + ben) / line
    after = (gross + ben - loss) / line
    depth = pd.DataFrame([
        {"measure": "adults losing SNAP (MID)", "value": w.sum()},
        {"measure": "share with countable income under half the poverty line", "value": w[gross / line < 0.5].sum() / w.sum()},
        {"measure": "share with countable income under the poverty line", "value": w[gross / line < 1.0].sum() / w.sum()},
        {"measure": "share with no countable income", "value": w[gross == 0].sum() / w.sum()},
        {"measure": "share under the poverty line on income plus SNAP, before", "value": w[before < 1.0].sum() / w.sum()},
        {"measure": "share under the poverty line on income plus SNAP, after", "value": w[after < 1.0].sum() / w.sum()},
        {"measure": "share under half the poverty line on income plus SNAP, before", "value": w[before < 0.5].sum() / w.sum()},
        {"measure": "share under half the poverty line on income plus SNAP, after", "value": w[after < 0.5].sum() / w.sum()},
        {"measure": "mean monthly benefit lost, FY2024 dollars", "value": (w * loss).sum() / w.sum()},
        {"measure": "benefit lost as a share of income plus SNAP", "value": (w * loss).sum() / (w * (gross + ben)).sum()},
    ])

    noncit = hr1.refugee_asylee_members(units, persons)
    base = params.snap_annual_benefits_2027
    cost = [{"item": f"benefit share at a {pct:.0%} rate (error rate {band})",
             "annual_state_cost_usd": base * pct}
            for pct, band in [(0.0, "under 6%"), (0.05, "6% to under 8%"), (0.10, "8% to under 10%"),
                              (0.15, "10% or more")]]
    share = hr1.snap_state_share(params.snap_error_rate_fy2025)
    cost.append({"item": f"benefit share on the FY2025 error rate ({params.snap_error_rate_fy2025}%): {share:.0%}",
                 "annual_state_cost_usd": base * share})
    cost.append({"item": "administrative cost share rising from 50% to 75% (DHS estimate)",
                 "annual_state_cost_usd": params.snap_admin_cost_shift})
    return {
        "snap_adults": table, "snap_losses": losses, "snap_income_depth": depth,
        "snap_state_cost": pd.DataFrame(cost),
        "_snap_meta": pd.DataFrame([{
            "units": units["FYWGT"].sum(), "members": (units["FYWGT"] * units["FSUSIZE"]).sum(),
            "annual_benefits_fy2024": (units["FYWGT"] * units["FSBEN"]).sum() * 12, "sample_units": len(units),
            "refugee_asylee_members": noncit["FYWGT"].sum(), "refugee_asylee_sample": len(noncit),
        }]),
    }


# --------------------------------------------------------------------------------------------------------------
# Medicaid
# --------------------------------------------------------------------------------------------------------------

_PUMS_COLS = ["SERIALNO", "SPORDER", "PWGTP", "AGEP", "HINS3", "HINS4", "SSIP", "DIS", "ESR", "WKWN", "WAGP", "SEMP",
              "SCH", "POVPIP", "RELSHIPP", "SFN", "RAC1P", "FER", "ADJINC"]


def _load_pums_persons(pums_dir: Path) -> pd.DataFrame:
    want = set(_PUMS_COLS) | {f"PWGTP{i}" for i in range(1, 81)}
    parquet = pums_dir / "psam_p15.parquet"
    if parquet.exists():
        import pyarrow.parquet as pq

        have = [c for c in pq.ParquetFile(parquet).schema.names if c in want]
        return pd.read_parquet(parquet, columns=have)
    return pd.read_csv(pums_dir / "psam_p15.csv", usecols=lambda c: c in want, dtype={"SERIALNO": str})


def run_medicaid(params, pums_dir: Path, label: str) -> dict[str, pd.DataFrame]:
    from tax_modeler.programs import hr1_safety_net as hr1

    persons = _load_pums_persons(pums_dir)
    adults = hr1.split_expansion(
        hr1.classify_medicaid_adults(persons),
        expansion_target=params.medicaid_expansion_adults,
        parent_caretaker_target=params.medicaid_parent_caretakers,
    )
    pool = adults[adults["mq_group"] == "non_disabled_adult"]
    scale = (params.medicaid_expansion_adults + params.medicaid_parent_caretakers) / pool["PWGTP"].sum()
    exp = pool[pool["expansion_share"] > 0].copy()
    # Each person's share of their survey weight that is an expansion adult; replicates reuse it.
    exp_factor = exp["expansion_share"].to_numpy()
    exp["w"] = exp["mq_weight"] * exp_factor
    reps = [c for c in exp.columns if c.startswith("PWGTP") and c != "PWGTP"]

    groups = pd.DataFrame([
        {"group": "aged_blind_disabled (Medicare or SSI)", "sample": int((adults["mq_group"] != "non_disabled_adult").sum()),
         "acs_persons": adults.loc[adults["mq_group"] != "non_disabled_adult", "PWGTP"].sum(), "med_quest_enrollment": np.nan},
        {"group": "non-disabled adults", "sample": len(pool), "acs_persons": pool["PWGTP"].sum(),
         "med_quest_enrollment": params.medicaid_expansion_adults + params.medicaid_parent_caretakers},
        {"group": "  of which parents of a child under 19", "sample": int(pool["is_parent"].sum()),
         "acs_persons": pool.loc[pool["is_parent"], "PWGTP"].sum(), "med_quest_enrollment": np.nan},
        {"group": "  parents scaled / parent and caretaker group", "sample": int(pool["is_parent"].sum()),
         "acs_persons": pool.loc[pool["is_parent"], "PWGTP"].sum() * scale,
         "med_quest_enrollment": params.medicaid_parent_caretakers},
    ])

    table = exp.groupby("status", as_index=False).agg(sample=("w", "size"), expansion_adults=("w", "sum"))
    table["share"] = table["expansion_adults"] / table["expansion_adults"].sum()
    table.insert(0, "pums", label)

    subject = (exp["status"] != "exempt").to_numpy()
    p_meets = exp["p_meets"].to_numpy()
    cost = params.medicaid_cost_per_expansion_adult
    fed = params.medicaid_expansion_federal_share
    rows = []
    for scen in SCENARIOS:
        r = params.medicaid_rates[scen]
        rate = np.where(subject, p_meets * r.meets + (1 - p_meets) * r.at_risk, r.exempt)

        def total(weight: pd.Series, rate=rate, r=r) -> tuple[float, float]:
            # Each replicate is rescaled to the Med-QUEST count: the survey's level is not what is uncertain.
            k = params.medicaid_expansion_adults / weight.sum()
            work = float((weight * rate).sum() * k)
            renewals = (params.medicaid_expansion_adults - work) * r.six_month_renewal
            return work, renewals

        work, renewals = total(exp["w"])
        rep = np.array([sum(total(exp[c] * exp_factor)) for c in reps]) if reps else np.array([])
        se = float(np.sqrt(4 / 80 * ((rep - (work + renewals)) ** 2).sum())) if len(rep) else float("nan")
        lost = work + renewals
        rows.append({
            "pums": label, "scenario": scen,
            "expansion_adults": params.medicaid_expansion_adults,
            "lose_coverage_work_requirement": work, "lose_coverage_six_month_renewals": renewals,
            "lose_coverage_total": lost, "sampling_p5": lost - 1.645 * se, "sampling_p95": lost + 1.645 * se,
            "share_of_expansion_adults": lost / params.medicaid_expansion_adults,
            "annual_federal_dollars_lost_usd": lost * cost * fed,
            "annual_state_savings_usd": lost * cost * (1 - fed),
            "annual_cost_to_cover_with_state_funds_usd": lost * cost,
        })
    return {"medicaid_groups": groups.assign(pums=label, acs_to_med_quest_scale=scale),
            "medicaid_adults": table, "medicaid_losses": pd.DataFrame(rows)}


# --------------------------------------------------------------------------------------------------------------


def _summary(snap: dict, med: dict, params) -> str:
    def usd(x: float) -> str:
        return f"${x / 1e6:,.1f}M"

    meta = snap["_snap_meta"].iloc[0]
    lines = [
        "H.R.1 in Hawaii: SNAP and Medicaid, a full year under the new rules",
        "=" * 68, "",
        f"SNAP base: FY2024 Quality Control file, {int(meta.sample_units)} Hawaii units = {meta.units:,.0f} units, "
        f"{meta.members:,.0f} members, {usd(meta.annual_benefits_fy2024)} a year.",
        "", "Adults the work rules newly reach, and who loses SNAP (90% sampling interval):",
    ]
    all_new = snap["snap_losses"][snap["snap_losses"]["group"] == "all_newly_subject"].set_index("scenario")
    lines.append(f"  newly under the time limit: {all_new.loc['MID', 'adults_subject']:,.0f} adults")
    for s in SCENARIOS:
        r = all_new.loc[s]
        lines.append(
            f"  {s:<5} {r.adults_losing_snap:>7,.0f} adults ({r.adults_losing_snap_p5:,.0f} to "
            f"{r.adults_losing_snap_p95:,.0f}); benefits lost {usd(r.annual_benefits_lost_fy2027_usd)} a year at "
            f"FY2027 allotments ({usd(r.annual_benefits_lost_fy2027_p5)} to {usd(r.annual_benefits_lost_fy2027_p95)})")
    lines += ["", "State cost of SNAP from federal fiscal year 2028:"]
    for _, r in snap["snap_state_cost"].iterrows():
        lines.append(f"  {usd(r.annual_state_cost_usd):>9}  {r['item']}")
    lines += ["", f"Medicaid: {params.medicaid_expansion_adults:,.0f} expansion adults (Med-QUEST, September 2026)."]
    for label in med["medicaid_adults"]["pums"].unique():
        t = med["medicaid_adults"][med["medicaid_adults"]["pums"] == label].set_index("status")
        lines.append(f"  [{label}] " + "; ".join(
            f"{s} {t.loc[s, 'share']:.0%} ({t.loc[s, 'expansion_adults']:,.0f})" for s in t.index))
        losses = med["medicaid_losses"][med["medicaid_losses"]["pums"] == label]
        for _, r in losses.iterrows():
            lines.append(
                f"    {r.scenario:<5} {r.lose_coverage_total:>7,.0f} lose coverage ({r.share_of_expansion_adults:.0%}; "
                f"sampling {r.sampling_p5:,.0f} to {r.sampling_p95:,.0f}); federal dollars lost "
                f"{usd(r.annual_federal_dollars_lost_usd)} a year")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    from tax_modeler.programs.hr1_safety_net import Hr1Parameters

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pums-data-dir", type=Path, default=PUMS_DIR)
    ap.add_argument("--pums-5yr-dir", type=Path, default=PUMS_DIR_5YR,
                    help="second PUMS file for the Medicaid sensitivity; skipped if missing")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    params = Hr1Parameters()
    snap = run_snap(params)
    runs = [run_medicaid(params, args.pums_data_dir, args.pums_data_dir.name)]
    if args.pums_5yr_dir.exists():
        runs.append(run_medicaid(params, args.pums_5yr_dir, args.pums_5yr_dir.name))
    med = {k: pd.concat([r[k] for r in runs], ignore_index=True) for k in runs[0]}

    args.out.mkdir(parents=True, exist_ok=True)
    for name, frame in {**snap, **med}.items():
        if not name.startswith("_"):
            frame.to_csv(args.out / f"{name}.csv", index=False, float_format="%.6g")
    text = _summary(snap, med, params)
    (args.out / "summary.txt").write_text(text)
    (args.out / "summary.json").write_text(json.dumps({
        "snap_base": snap["_snap_meta"].iloc[0].to_dict(),
        "parameters": {k: v for k, v in vars(params).items() if not k.endswith("_rates")},
        "snap_rates": {k: vars(v) for k, v in params.snap_rates.items()},
        "medicaid_rates": {k: vars(v) for k, v in params.medicaid_rates.items()},
        "bootstrap_draws": N_BOOT, "seed": SEED,
    }, indent=2, default=float))
    print(text)


if __name__ == "__main__":
    main()
