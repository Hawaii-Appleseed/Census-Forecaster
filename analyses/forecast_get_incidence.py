#!/usr/bin/env python3
"""Who pays the general excise tax: resident households by income, and what exemptions would cost.

    .venv/bin/python analyses/forecast_get_incidence.py

The households are the working-family credits step's 2024 population (the 2024 one-year ACS, its
tax units' cash income, SNAP placed to the DHS caseload, the food/excise credit at the take-up
that step fits), one row per household, with each renter's own contract rent. Spending and tax are
``tax_modeler.excise.get_incidence``: the expenditure survey's shape over income, BEA's Hawaiʻi
totals for the level, the 4 percent state rate and 0.5 percent county surcharge, the wholesale
levy on goods.

Outputs (runs/get_incidence/):

* ``by_quintile.csv``: tax per household and as a share of income by fifth of household income,
  with the food/excise credit beside it. Shares are a group's tax over the group's income.
* ``by_category.csv``: spending, the scale to BEA's total and tax by category, and each fifth's tax
  on the category as a share of its income.
* ``reforms.csv``: exemptions (groceries, out-of-pocket medical services, residential rent), their
  cost to the state and counties from resident households only, and who gains.
* ``control_totals.csv``: the modelled state tax beside the Department of Taxation's collections.
* ``levels.csv``: the modelled total at BEA's level and at the survey's own dollars (a floor).

Standard errors are ACS sampling error from the 80 replicate weights, with the income groups held
at the full sample's. They carry none of the error in the expenditure survey, in BEA's state
figures, or in the assumptions (full pass-through, spending read from income alone).

Static, and of resident households' own purchases only: see GET_INCIDENCE_METHODOLOGY.md for what
is left out and why the lowest fifth's share of income is overstated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "chain"))

RUN_NAME = "get_incidence"
YEAR = 2024             # the ACS file, BEA's spending and the expenditure survey
FISCAL_YEAR = 2025      # the Department's collections the total is set beside (July 2024 to June 2025)
QUINTILES = ("Lowest 20%", "Second 20%", "Middle 20%", "Fourth 20%", "Top 20%")
REFORMS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("groceries", "Exempt groceries", frozenset({"groceries"})),
    ("medical", "Exempt out-of-pocket medical services", frozenset({"medical_services"})),
    ("groceries_medical", "Exempt groceries and medical services", frozenset({"groceries", "medical_services"})),
    ("rent", "Exempt residential rent", frozenset({"rent"})),
)


def build_households() -> tuple[pd.DataFrame, np.ndarray, dict]:
    """One row per household in YEAR, and its weights as (1 + replicates, households).

    Columns: income (the tax units' cash income, summed), rent (annual contract rent, zero for
    owners), snap (annual benefit), food_credit (expected food/excise credit), persons.
    """
    import forecast_working_family_credits as wfc
    import poverty_impact_report as pir

    base, _ = wfc.load_population()
    rake = wfc.irs_rake(base, ratios=wfc.unit_replicate_ratios(base)) if wfc.IRS_REWEIGHT else None
    cal = wfc.units_for_year(base, wfc.CAL_YEAR, rake)
    p_food = wfc.dotax()[wfc.CAL_YEAR]["food_$M"] * 1e6 / float(
        (wfc.state_credits(cal)["food_expanded"] * cal["weight"]).sum())
    u = wfc.units_for_year(base, YEAR, rake)
    u = pir._apply_snap(u, tax_year=YEAR)
    u = u.assign(_food=wfc.state_credits(u)["food_expanded"] * p_food)
    g = u.groupby("hh_id", observed=True)
    hh = pd.DataFrame({"income": g["total_cash_income"].sum(), "snap": g["snap_amount"].sum(),
                       "food_credit": g["_food"].sum()})
    reps = [f"WGTP{i}" for i in range(1, 81)]
    h = pd.read_parquet(wfc.PUMS_DIR / "psam_h15.parquet",
                        columns=["SERIALNO", "WGTP", *reps, "RNTP", "ADJHSG", "NP"]).set_index("SERIALNO")
    hh = hh.join(h, how="inner")
    if len(hh) != g.ngroups:
        raise SystemExit(f"{g.ngroups - len(hh)} households of the tax units are not in the ACS household file")
    hh["rent"] = hh["RNTP"].fillna(0) * 12 * hh["ADJHSG"] / 1e6
    hh["persons"] = hh["NP"]
    weights = hh[["WGTP", *reps]].to_numpy(dtype=float).T
    params = {"year": YEAR, "fiscal_year": FISCAL_YEAR, "pums": wfc.PUMS_DIR.name, "households": len(hh),
              "takeup_food": round(p_food, 4), "irs_reweight": rake is not None, "replicates": len(reps)}
    return hh[["income", "rent", "snap", "food_credit", "persons"]].reset_index(drop=True), weights, params


def _sdr(theta: np.ndarray) -> np.ndarray:
    """SDR standard error along axis 0: row 0 the full sample, the rest the replicates."""
    reps = theta[1:]
    return np.sqrt(4.0 / len(reps) * ((reps - theta[0]) ** 2).sum(axis=0))


def tax_under(hh: pd.DataFrame, weight: np.ndarray, law, *, level: str = "pce"):
    """(spending, tax) for the households under ``law``, the level set on ``weight``."""
    from tax_modeler.excise import get_incidence as gi

    pce = gi.pce_hawaii(YEAR)
    spending = gi.impute_spending(hh["income"].to_numpy(), weight, hh["rent"].to_numpy(),
                                  gi.SpendingProfile.load(YEAR), pce=pce if level == "pce" else None)
    return spending, gi.burden(spending, law, snap=hh["snap"].to_numpy(), pce=pce)


def by_quintile(hh: pd.DataFrame, weights: np.ndarray, groups: np.ndarray, law) -> pd.DataFrame:
    income, credit = hh["income"].to_numpy(), hh["food_credit"].to_numpy()
    masks = [groups == i for i in range(len(QUINTILES))] + [np.ones(len(hh), dtype=bool)]
    share = np.empty((len(weights), len(masks)))
    for r, w in reversed(list(enumerate(weights))):      # the full sample last, so ``tax`` is its
        _, tax = tax_under(hh, w, law)
        total = tax["total"].to_numpy()
        share[r] = [(total[m] * w[m]).sum() / (income[m] * w[m]).sum() for m in masks]
    rows = []
    for i, (label, m) in enumerate(zip((*QUINTILES, "All households"), masks, strict=True)):
        n, inc = w[m].sum(), (income[m] * w[m]).sum()

        def mean(x, m=m, n=n):
            return float((np.asarray(x)[m] * w[m]).sum() / n)

        rows.append({
            "group": label, "households": round(n), "mean_income": round(inc / n),
            "state_tax": round(mean(tax["state"])), "county_tax": round(mean(tax["county"])),
            "total_tax": round(mean(tax["total"])),
            "tax_share_of_income": share[0, i], "tax_share_se": _sdr(share)[i],
            "food_excise_credit": round(mean(credit)),
            "net_share_of_income": float(((tax["total"].to_numpy() - credit)[m] * w[m]).sum() / inc),
        })
    return pd.DataFrame(rows)


def by_category(hh: pd.DataFrame, w: np.ndarray, groups: np.ndarray, law) -> pd.DataFrame:
    from tax_modeler.excise import get_incidence as gi

    spending, tax = tax_under(hh, w, law)
    income = hh["income"].to_numpy()
    all_tax = float((tax["total"] * w).sum())
    rows = []
    for key in gi.CATEGORY_KEYS:
        t = (tax[f"state_{key}"] + tax[f"county_{key}"]).to_numpy()
        row = {"category": key, "label": gi.LABELS[key],
               "spending_M": float((spending.amounts[key] * w).sum()) / 1e6, "scale_to_bea": spending.scale[key],
               "state_tax_M": float((tax[f"state_{key}"] * w).sum()) / 1e6,
               "county_tax_M": float((tax[f"county_{key}"] * w).sum()) / 1e6,
               "share_of_tax": float((t * w).sum()) / all_tax}
        for i, label in enumerate(QUINTILES):
            m = groups == i
            row[f"share_of_income_{label}"] = float((t[m] * w[m]).sum() / (income[m] * w[m]).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def reforms(hh: pd.DataFrame, weights: np.ndarray, groups: np.ndarray, law) -> pd.DataFrame:
    """Each exemption's cost and who gains. The cost is the tax resident households no longer pay;
    visitors' and businesses' purchases of the same things are not in it."""
    import dataclasses

    income = hh["income"].to_numpy()
    rows = []
    for key, label, exempt in REFORMS:
        after = dataclasses.replace(law, exempt=exempt)
        cost = np.empty((len(weights), 2))
        for r, w in enumerate(weights):
            gain = tax_under(hh, w, law)[1][["state", "county"]] - tax_under(hh, w, after)[1][["state", "county"]]
            cost[r] = (gain.to_numpy() * w[:, None]).sum(axis=0)
            if r == 0:
                saved = gain.sum(axis=1).to_numpy()
        w = weights[0]
        row = {"reform": key, "label": label, "state_cost_M": cost[0, 0] / 1e6, "state_cost_se_M": _sdr(cost)[0] / 1e6,
               "county_cost_M": cost[0, 1] / 1e6}
        for i, q in enumerate(QUINTILES):
            m = groups == i
            row[f"saving_{q}"] = round(float((saved[m] * w[m]).sum() / w[m].sum()))
            row[f"saving_share_of_income_{q}"] = float((saved[m] * w[m]).sum() / (income[m] * w[m]).sum())
        row["share_of_saving_lowest_40"] = float((saved[groups <= 1] * w[groups <= 1]).sum() / (saved * w).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rent-pass-through", type=float, default=1.0,
                   help="share of the tax on rent that renters bear (default 1.0)")
    args = p.parse_args(argv)

    from _forecast_common import save_run, silence_noise
    from tax_modeler.excise import get_incidence as gi

    silence_noise()
    hh, weights, params = build_households()
    w = weights[0]
    law = gi.Law(wholesale=gi.wholesale_markup(FISCAL_YEAR), rent_pass_through=args.rent_pass_through)
    groups = gi.income_groups(hh["income"].to_numpy(), w, len(QUINTILES))
    quint = by_quintile(hh, weights, groups, law)
    cats = by_category(hh, w, groups, law)
    ref = reforms(hh, weights, groups, law)
    _, tax = tax_under(hh, w, law)
    control = gi.control_totals(tax, w, FISCAL_YEAR)
    levels = pd.DataFrame([
        {"level": level, "state_tax_M": float((t["state"] * w).sum()) / 1e6,
         "county_tax_M": float((t["county"] * w).sum()) / 1e6}
        for level in ("pce", "cex") for t in [tax_under(hh, w, law, level=level)[1]]])

    pd.set_option("display.width", 200)
    print(quint[["group", "mean_income", "total_tax", "tax_share_of_income", "tax_share_se",
                 "net_share_of_income"]].to_string(index=False))
    print(control.to_string(index=False))
    print(ref[["label", "state_cost_M", "state_cost_se_M", "share_of_saving_lowest_40"]].to_string(index=False))

    params |= {"state_rate": law.state_rate, "county_rate": law.county_rate, "wholesale_markup": round(law.wholesale, 6),
               "rent_pass_through": law.rent_pass_through, "snap_groceries_exempt": law.snap_exempt}
    inputs = {name: {"path": str((gi.DATA_DIR / name).relative_to(REPO)), "fetch_date": gi._read(name)["fetch_date"]}
              for name in ("cex_income_deciles.json", "bea_pce_hawaii.json", "dotax_get_by_activity.json")}
    run_dir = save_run(RUN_NAME, {"by_quintile.csv": quint, "by_category.csv": cats, "reforms.csv": ref,
                                  "control_totals.csv": control, "levels.csv": levels},
                       script="analyses/forecast_get_incidence.py", params=params, inputs=inputs)
    print(f"wrote {run_dir.relative_to(REPO)}")


if __name__ == "__main__":
    main()
