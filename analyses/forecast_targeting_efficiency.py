#!/usr/bin/env python3
"""Which group a dollar of new cash support lifts out of poverty most cheaply, TY2028.

The population is built as the working-family credits step builds its own
(``chain/forecast_working_family_credits.py``): ACS households aged to TY2028, fitted to IRS
returns, with every SPM resource the step places. By default it is the 2020-2024 five-year ACS
file, about five times the step's 2024 one-year sample (``--pums one-year`` is the step's file,
and reproduces its Act 163 figures); the IRS fit and the credits' take-up factors are found again
on whichever file is used. The baseline is current law in TY2028, the Act 163 credit expansions
expired. Each policy is a flat annual payment per eligible person in one group, added to the SPM
unit's resources:

* **Same budget** (``--budgets``, $M): the payment is set so every group's policy costs the same.
  The groups are ranked two ways: by the share of the money that closes the poverty gap (dollars
  that bring a poor unit's resources up toward its SPM line), and by people lifted over the line
  per $1M. The headcount turns on the few sample households that sit just under the line, so its
  sampling error is large at small budgets; the gap share uses every poor household and is steady.
* **Same payment** (``--amounts``, $ per eligible person): the cost differs by group.

Each group is scored with no income test and with the payment limited to units whose baseline
resources are under a multiple of their SPM threshold (``--income-tests``; a proxy for a means
test, which a real program would write on AGI or the federal poverty guideline).

Two reference rows sit on the same scale: renewing the Act 163 expansions, and the most people any
payment of that budget could lift if it went only to the poor units nearest the line (no program
can be written that way; it bounds what targeting can do).

    .venv/bin/python analyses/forecast_targeting_efficiency.py

Outputs (runs/targeting_efficiency/, or runs/targeting_efficiency_one_year/): groups.csv, same_budget.csv, same_payment.csv, references.csv,
manifest.json. Standard errors are ACS sampling error from the 80 replicate weights (SDR).

Static: no change in work or household formation, the payment is not taxed and does not reduce
SNAP or any other benefit, and everyone eligible receives it. A uniform take-up rate would scale a
policy's cost and its people lifted alike, leaving people lifted per $1M unchanged; take-up that
differs by group would not.

Headcounts and poverty gaps are means over draws (``--draws``, the step's claim draws): who claims
each state credit, and each unit's medical spending drawn from its donor pool (``benefits.moop``).
Expected credits and a pool-mean MOOP put every unit of a kind on one side of the line together and
the people a payment lifts swing on that bunching. The income test reads expected resources.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "chain"))

RUN_NAME = "targeting_efficiency"
# --pums: the ACS file, its directory under packages/data/raw, and what the manifest calls it
PUMS = {"five-year": ("pums_2020_2024", "HI 5-year 2020-2024"), "one-year": ("pums_2024_1yr", "HI 1-year 2024")}
BUDGETS_M = (25.0, 50.0, 100.0, 250.0)
AMOUNTS = (500.0, 1_000.0, 2_000.0, 3_000.0, 5_000.0)
INCOME_TESTS = (None, 2.0, 1.5)     # None: no test; else baseline resources under this multiple of the SPM line
N_DRAWS = 200                       # draws of claims and medical spending the policies are scored over
Z90 = 1.645                         # 90 percent, as the Census Bureau publishes


@dataclass(frozen=True)
class Population:
    """SPM units as arrays. ``weights`` is (1 + replicates, units): row 0 the full-sample weight."""

    weights: np.ndarray
    persons: np.ndarray
    children: np.ndarray
    resources: np.ndarray       # baseline (current law) SPM resources, expected credits and MOOP
    threshold: np.ndarray
    eligible: dict[str, np.ndarray]   # group key -> eligible persons per unit
    draws: np.ndarray | None = None   # (units, draws) baseline resources in each draw; None: ``resources``, one draw

    def by_draw(self) -> np.ndarray:
        return self.resources[:, None] if self.draws is None else self.draws


GROUPS: tuple[tuple[str, str], ...] = (
    ("everyone", "Everyone"),
    ("children_under_6", "Children under 6"),
    ("children_6_17", "Children 6 to 17"),
    ("children", "All children under 18"),
    ("single_parent_children", "Children living with one adult"),
    ("adults_65_plus", "Adults 65 and older"),
    ("adults_no_children", "Adults 18 to 64 in units without children"),
    ("renters", "People in renter units"),
    ("snap", "People in units receiving SNAP"),
    ("eitc", "People in units receiving the federal EITC"),
)


def eligible_counts(frame: pd.DataFrame, persons: pd.DataFrame) -> dict[str, np.ndarray]:
    """Eligible persons per SPM unit for each of :data:`GROUPS`, in ``frame``'s row order.

    Renters are units whose household pays rent (``tenure``; households that pay none count as owners
    without a mortgage, as the SPM line treats them).
    """
    pers = persons.loc[persons["spm_unit_id"].notna()]
    age = pers["AGEP"]
    renter = (frame["tenure"] == "renter").to_numpy()

    def count(mask: pd.Series) -> np.ndarray:
        per_unit = mask.groupby(pers["spm_unit_id"]).sum()
        return frame["spm_unit_id"].map(per_unit).fillna(0).to_numpy(dtype=float)

    n = frame["n_persons"].to_numpy(dtype=float)
    kids = frame["n_children"].fillna(0).to_numpy(dtype=float)
    adults = frame["n_adults"].fillna(0).to_numpy(dtype=float)
    return {
        "everyone": n,
        "children_under_6": count(age < 6),
        "children_6_17": count((age >= 6) & (age < 18)),
        "children": kids,
        "single_parent_children": np.where(adults == 1, kids, 0.0),
        "adults_65_plus": count(age >= 65),
        "adults_no_children": np.where(kids == 0, count((age >= 18) & (age < 65)), 0.0),
        "renters": np.where(renter, n, 0.0),
        "snap": np.where(frame["snap_amount"].fillna(0).to_numpy(dtype=float) > 0, n, 0.0),
        "eitc": np.where(frame["eitc_amount"].fillna(0).to_numpy(dtype=float) > 0, n, 0.0),
    }


def _sdr(theta: np.ndarray) -> float:
    """SDR standard error of a statistic computed on the full sample (``theta[0]``) and each replicate."""
    reps = theta[1:]
    return float(np.sqrt(4.0 / len(reps) * ((reps - theta[0]) ** 2).sum())) if len(reps) else float("nan")


def score(pop: Population, payment: np.ndarray) -> dict[str, float]:
    """Cost and poverty effect of adding ``payment`` to baseline resources: $ per SPM unit, the same in
    every draw (units,) or draw by draw (units, draws). Headcounts and gaps are means over the draws."""
    w, n, kids, thr = pop.weights, pop.persons, pop.children, pop.threshold[:, None]
    before = pop.by_draw()
    pay = payment[:, None] if payment.ndim == 1 else payment
    after = before + pay
    lifted = (before < thr).mean(axis=1) - (after < thr).mean(axis=1)
    deep_lifted = (before < 0.5 * thr).mean(axis=1) - (after < 0.5 * thr).mean(axis=1)
    gap_closed = (np.maximum(thr - before, 0) - np.maximum(thr - after, 0)).mean(axis=1)
    pay_mean = pay.mean(axis=1) if pay.shape[1] > 1 else pay[:, 0]
    to_poor = (pay * (before < thr)).mean(axis=1)

    cost = w @ pay_mean
    persons_lifted = w @ (n * lifted)
    per_million = np.divide(persons_lifted, cost / 1e6, out=np.zeros_like(cost), where=cost > 0)
    gap_share = np.divide(w @ gap_closed, cost, out=np.zeros_like(cost), where=cost > 0)
    out = {
        "cost_$M": cost[0] / 1e6,
        "persons_lifted": persons_lifted[0],
        "persons_lifted_se": _sdr(persons_lifted),
        "children_lifted": float(w[0] @ (kids * lifted)),
        "persons_lifted_from_deep_poverty": float(w[0] @ (n * deep_lifted)),
        "poverty_gap_closed_$M": float(w[0] @ gap_closed) / 1e6,
        "persons_lifted_per_$M": per_million[0],
        "persons_lifted_per_$M_se": _sdr(per_million),
        # dollars reaching units that are poor before the payment, and dollars that fill their gap
        "share_of_dollars_to_poor_units": float(w[0] @ to_poor) / cost[0] if cost[0] > 0 else 0.0,
        "share_of_dollars_closing_gap": gap_share[0],
        "share_of_dollars_closing_gap_se": _sdr(gap_share),
    }
    out["cost_per_person_lifted_$"] = cost[0] / persons_lifted[0] if persons_lifted[0] > 0 else float("nan")
    out["poverty_rate_after"] = float(w[0] @ (n * (after < thr).mean(axis=1))) / float(w[0] @ n)
    out["persons_lifted_per_$M_low90"] = out["persons_lifted_per_$M"] - Z90 * out["persons_lifted_per_$M_se"]
    out["persons_lifted_per_$M_high90"] = out["persons_lifted_per_$M"] + Z90 * out["persons_lifted_per_$M_se"]
    return out


def _tested(pop: Population, group: str, income_test: float | None) -> np.ndarray:
    """Eligible persons per unit in ``group`` after the income test."""
    count = pop.eligible[group]
    if income_test is None:
        return count
    return np.where(pop.resources < income_test * pop.threshold, count, 0.0)


def _test_label(income_test: float | None) -> str:
    return "none" if income_test is None else f"under {income_test:.0%} of the SPM line"


def group_profile(pop: Population) -> pd.DataFrame:
    """Who is in each group at baseline: size, poverty rate, and how far its poor sit below the line."""
    w, thr = pop.weights[0], pop.threshold
    r = pop.by_draw()
    poor = (r < thr[:, None]).mean(axis=1)                  # probability of being poor
    gap_per_person = np.maximum(thr - pop.resources, 0) / np.maximum(pop.persons, 1)
    all_poor = float(w @ (pop.persons * poor))
    rows = []
    for key, label in GROUPS:
        c = pop.eligible[key]
        members = float(w @ c)
        members_poor = float(w @ (c * poor))
        in_group_poor = (pop.resources < thr) & (c > 0)
        order = np.argsort(gap_per_person[in_group_poor])
        cw = np.cumsum((w * c)[in_group_poor][order])
        median_gap = (float(gap_per_person[in_group_poor][order][np.searchsorted(cw, cw[-1] / 2)])
                      if len(cw) else float("nan"))
        rows.append({
            "group": key, "label": label, "eligible_persons": members,
            "eligible_persons_in_poverty": members_poor,
            "poverty_rate": members_poor / members if members else float("nan"),
            # eligible persons in poverty as a share of everyone in poverty; groups overlap
            "share_of_all_persons_in_poverty": members_poor / all_poor,
            # among units poor on expected resources
            "median_gap_per_person_among_poor_$": median_gap,
            "sample_units": int((c > 0).sum()), "sample_units_in_poverty": int(in_group_poor.sum()),
        })
    return pd.DataFrame(rows)


def same_budget(pop: Population, budgets_m=BUDGETS_M, income_tests=INCOME_TESTS) -> pd.DataFrame:
    rows = []
    for budget in budgets_m:
        for test in income_tests:
            for key, label in GROUPS:
                count = _tested(pop, key, test)
                eligible = float(pop.weights[0] @ count)
                if eligible <= 0:
                    continue
                amount = budget * 1e6 / eligible
                rows.append({"budget_$M": budget, "group": key, "label": label, "income_test": _test_label(test),
                             "eligible_persons": eligible, "payment_per_eligible_person_$": amount,
                             **score(pop, amount * count)})
    out = pd.DataFrame(rows)
    for name, col in (("rank_by_gap_closed", "share_of_dollars_closing_gap"),
                      ("rank_by_persons_lifted", "persons_lifted_per_$M")):
        out[name] = out.groupby("budget_$M")[col].rank(ascending=False, method="first").astype(int)
    return out.sort_values(["budget_$M", "rank_by_gap_closed"]).reset_index(drop=True)


def same_payment(pop: Population, amounts=AMOUNTS, income_tests=INCOME_TESTS) -> pd.DataFrame:
    rows = []
    for amount in amounts:
        for test in income_tests:
            for key, label in GROUPS:
                count = _tested(pop, key, test)
                rows.append({"payment_per_eligible_person_$": amount, "group": key, "label": label,
                             "income_test": _test_label(test),
                             "eligible_persons": float(pop.weights[0] @ count), **score(pop, amount * count)})
    return pd.DataFrame(rows)


def nearest_the_line(pop: Population, budget: float) -> np.ndarray:
    """Payments ($ per unit, per draw) that lift the most people ``budget`` can: in each draw, each poor
    unit's whole gap, cheapest per person first, until the money runs out (the last unit funded is left
    short). Knows every unit's resources in every draw, as no program can."""
    w, thr = pop.weights[0], pop.threshold
    r = pop.by_draw()
    payment = np.zeros_like(r)
    for k in range(r.shape[1]):
        gap = np.maximum(thr - r[:, k], 0)
        poor = np.flatnonzero(gap > 0)
        order = poor[np.argsort(gap[poor] / np.maximum(pop.persons[poor], 1), kind="stable")]
        funded = order[np.cumsum(w[order] * gap[order]) <= budget]
        # a hair over the gap: a unit is counted out of poverty once resources are not below the line
        payment[funded, k] = gap[funded] + 0.01
    return payment


def references(pop: Population, act163_gain: np.ndarray, budgets_m=BUDGETS_M) -> pd.DataFrame:
    rows = [{"reference": "act163_renewed", "label": "Renew the Act 163 credit expansions",
             **score(pop, act163_gain)}]
    for budget in budgets_m:
        rows.append({"reference": "nearest_the_line", "budget_$M": budget,
                     "label": "Upper bound: fill the gaps of the poor units nearest the line",
                     **score(pop, nearest_the_line(pop, budget * 1e6))})
    return pd.DataFrame(rows)


def build_population(pums: str = "five-year", n_draws: int = N_DRAWS) -> tuple[Population, np.ndarray, dict]:
    """TY2028 SPM units built as the working-family credits step's, with current law as the baseline.

    ``pums`` is a key of :data:`PUMS`; "one-year" is the step's own population.

    Returns the population, each unit's gain if Act 163 is renewed in each draw, and the run's parameters.
    """
    import dataclasses

    import forecast_working_family_credits as wfc
    from tax_modeler.benefits.moop import moop_resource_draws
    from tax_modeler.poverty.draws import resources_by_draw
    from tax_modeler.poverty.impact import compute_poverty_impact

    pums_dir = REPO / "packages" / "data" / "raw" / PUMS[pums][0]
    base, persons = wfc.load_population(pums_dir)
    ratios = wfc.unit_replicate_ratios(base)
    rake = wfc.irs_rake(base, ratios=ratios) if wfc.IRS_REWEIGHT else None
    frames = {}
    for yr in sorted({wfc.EITC_CAL_YEAR, wfc.CAL_YEAR, wfc.POVERTY_YEAR}):
        u = wfc.units_for_year(base, yr, rake)
        frames[yr] = (u, wfc.state_credits(u))
    p_eitc, p_food, p_cdcc = wfc.takeup_factors(frames)
    u, c = frames[wfc.POVERTY_YEAR]
    u = wfc.with_expected_credits(u, c, p_eitc, p_food, p_cdcc)
    frame = wfc.spm_frame(u, persons)
    res = compute_poverty_impact(frame, tax_year=wfc.POVERTY_YEAR, scenarios=("act163_sunset",),
                                 extrapolate_thresholds=True)
    f = res.units.reset_index(drop=True)
    rep_cols = [c for c in f.columns if c.startswith("weight_r") and c[8:].isdigit()]
    if not rep_cols:
        raise SystemExit("the SPM units carry no weight_r01..weight_r80 columns; cannot give sampling error")
    # Current law in TY2028 is the expansions expired: the step's renewed-case resources less each unit's loss.
    current_law = f["spm_resources_act163_sunset"].to_numpy(dtype=float)
    # The step's draws of who claims each credit, with medical spending drawn per unit: "baseline" is Act 163
    # renewed, "act163_sunset" current law.
    spec = dataclasses.replace(wfc.claim_draws(u, c, p_eitc, p_food, p_cdcc, n_draws=n_draws),
                               resource_draws=moop_resource_draws(u, persons, year=wfc.POVERTY_YEAR))
    drawn = resources_by_draw(spec, f)
    pop = Population(
        weights=np.vstack([f["weight"].to_numpy(dtype=float)] + [f[c].to_numpy(dtype=float) for c in rep_cols]),
        persons=f["n_persons"].to_numpy(dtype=float),
        children=f["n_children"].fillna(0).to_numpy(dtype=float),
        resources=current_law,
        threshold=f["spm_threshold"].to_numpy(dtype=float),
        eligible=eligible_counts(f, persons),
        draws=drawn["act163_sunset"],
    )
    params = {"pums": pums, "tax_year": wfc.POVERTY_YEAR, "baseline": "current law, Act 163 expansions expired",
              "takeup_eitc": round(p_eitc, 4), "takeup_food": round(p_food, 4), "takeup_cdcc": round(p_cdcc, 4),
              "irs_reweight": rake is not None, "replicates": len(rep_cols), "spm_units": len(f),
              "draws": n_draws, "draw_seed": spec.seed}
    return pop, drawn["baseline"] - drawn["act163_sunset"], params


def _print_summary(pop: Population, groups: pd.DataFrame, budget_table: pd.DataFrame, refs: pd.DataFrame) -> None:
    pd.set_option("display.width", 220)
    w = pop.weights[0]
    rate = float(w @ (pop.persons * (pop.by_draw() < pop.threshold[:, None]).mean(axis=1))) / float(w @ pop.persons)
    print(f"\nBaseline SPM poverty rate (current law): {rate:.2%}")
    print("\nGroups at baseline:")
    print(groups[["label", "eligible_persons", "poverty_rate", "share_of_all_persons_in_poverty",
                  "median_gap_per_person_among_poor_$", "sample_units_in_poverty"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.3f}" if abs(v) < 1 else f"{v:,.0f}"))
    budget = budget_table["budget_$M"].median() if len(budget_table) else None
    budget = min(budget_table["budget_$M"].unique(), key=lambda b: abs(b - budget))
    print(f"\nSame budget, ${budget:,.0f}M:")
    cols = ["label", "income_test", "payment_per_eligible_person_$", "share_of_dollars_closing_gap",
            "share_of_dollars_closing_gap_se", "persons_lifted", "children_lifted",
            "persons_lifted_per_$M", "persons_lifted_per_$M_se"]
    print(budget_table.loc[budget_table["budget_$M"] == budget, cols]
          .to_string(index=False, float_format=lambda v: f"{v:,.3f}" if abs(v) < 1 else f"{v:,.0f}"))
    print("\nReferences:")
    print(refs[["label", "budget_$M", "cost_$M", "share_of_dollars_closing_gap", "persons_lifted",
                "persons_lifted_per_$M", "persons_lifted_per_$M_se"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.3f}" if abs(v) < 1 else f"{v:,.0f}"))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budgets", type=float, nargs="+", default=list(BUDGETS_M), help="budgets to compare, $M")
    ap.add_argument("--amounts", type=float, nargs="+", default=list(AMOUNTS),
                    help="payments per eligible person to compare, $")
    ap.add_argument("--pums", choices=list(PUMS), default="five-year",
                    help="ACS file the population is built from (one-year: the working-family credits step's)")
    ap.add_argument("--draws", type=int, default=N_DRAWS, help="draws of claims and medical spending")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    print(f"Building the TY2028 SPM population from the {args.pums} ACS file...", flush=True)
    pop, act163_gain, params = build_population(args.pums, args.draws)
    poor_units = int((pop.resources < pop.threshold).sum())
    print(f"  {params['spm_units']:,} SPM units, {poor_units:,} of them in poverty ({time.perf_counter() - t0:.0f}s)", flush=True)
    groups = group_profile(pop)
    budget_table = same_budget(pop, args.budgets)
    outputs = {
        "groups.csv": groups,
        "same_budget.csv": budget_table,
        "same_payment.csv": same_payment(pop, args.amounts),
        "references.csv": references(pop, act163_gain, args.budgets),
    }
    from _forecast_common import save_run

    run_dir = save_run(
        RUN_NAME if args.pums == "five-year" else f"{RUN_NAME}_{args.pums.replace('-', '_')}", outputs, script="analyses/forecast_targeting_efficiency.py",
        params={**params, "budgets_$M": args.budgets, "amounts_$": args.amounts,
                "income_tests": [_test_label(t) for t in INCOME_TESTS], "groups": [k for k, _ in GROUPS]},
        inputs={"pums": f"{PUMS[args.pums][1]} (packages/data/raw/{PUMS[args.pums][0]})",
                "population": "chain/forecast_working_family_credits.py (load_population, irs_rake, spm_frame)"},
    )
    _print_summary(pop, groups, budget_table, outputs["references.csv"])
    print(f"\nDone in {time.perf_counter() - t0:.0f}s -> {run_dir}")


if __name__ == "__main__":
    logging.disable(logging.WARNING)
    main()
