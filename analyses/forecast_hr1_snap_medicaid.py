#!/usr/bin/env python3
"""What H.R.1 (Public Law 119-21) takes from SNAP and Medicaid in Hawaiʻi, in a full year under the new rules.

Three stages (:mod:`tax_modeler.programs.hr1_safety_net` has the logic):

1. **SNAP, who and how much**: Hawaiʻi's rows of the FY2024 SNAP Quality Control file. Who the work rules newly
   reach, who among them is exempt or already works enough, and, under LOW / MID / HIGH assumptions about how
   many fail to keep the benefit, the adults and dollars lost. Adds the SNAP costs the law moves to the State
   from federal fiscal year 2028.
2. **Medicaid**: ACS PUMS persons who report Medicaid (2020-24 five-year file; the 2024 one-year file as a check),
   scaled to Med-QUEST's enrollment, with counties and legislative districts.
3. **Poverty and geography** (``--skip-poverty`` leaves it out): the SPM poverty run on the ACS population at
   TY2027. The QC file has no county and no household around the SNAP unit, so SNAP is put on ACS households
   (households that report it, FY2027 rules, scaled to the DHS caseload and issuance), ACS adults in them get
   the same groups and statuses, and each one's probability of losing SNAP is set so the ACS reproduces stage 1
   cell by cell and dollar for dollar. Who loses SNAP is drawn (:mod:`tax_modeler.poverty.draws`); the headcounts
   are means over the draws, with ACS replicate-weight standard errors.

    .venv/bin/python analyses/forecast_hr1_snap_medicaid.py
    .venv/bin/python scripts/build_site.py --import-runs hr1-snap-medicaid

Outputs (runs/hr1_snap_medicaid/, published to site/data/hr1-snap-medicaid/): snap_adults.csv, snap_losses.csv,
snap_income_depth.csv, snap_state_cost.csv, medicaid_groups.csv, medicaid_adults.csv, medicaid_losses.csv,
poverty.csv, snap_baseline_check.csv, snap_calibration.csv, by_geography.csv, summary.json, summary.txt,
manifest.json.

Not modeled: the SNAP utility-allowance and internet changes, the frozen Thrifty Food Plan, Medicaid provider-tax
and state-directed-payment limits, cost sharing, retroactive coverage, Medicaid's non-citizen limits, Medicaid in
SPM poverty (coverage is not an SPM resource), and the premium tax credit. ``HR1_SNAP_MEDICAID_METHODOLOGY.md``
says why and gives the sources for every parameter.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
OUT_DIR = REPO / "runs" / "hr1_snap_medicaid"
PUMS_DIR = REPO / "packages" / "data" / "raw" / "pums_2020_2024"
PUMS_DIR_CHECK = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"
SCENARIOS = ("LOW", "MID", "HIGH")
POVERTY_YEAR = 2027          # tax year of incomes, taxes and the SPM line
BENEFIT_YEAR = 2025          # the other benefit modules' latest tables (as in the working-family credits step)
GEOGRAPHIES = (("county", "county"), ("house_district", "house district"), ("senate_district", "senate district"))
N_BOOT = 2000
N_DRAWS = 2000
SEED = 20261122

log = logging.getLogger("hr1")


def _interval(draws: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(draws, [5, 95])
    return float(lo), float(hi)


def _scn(case: str) -> str:
    return f"hr1_snap_{case.lower()}"


# --------------------------------------------------------------------------------------------------------------
# 1. SNAP from the QC file
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
    table["newly_subject"] = table["group"].isin(hr1.NEWLY_SUBJECT_GROUPS)

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

    size6 = units["FSUSIZE"].clip(upper=6)
    ratio = [hr1.max_allotment(int(n), 2027) / hr1.max_allotment(int(n), 2024) for n in units["FSUSIZE"]]
    by_size = (units["FYWGT"] * units["FSBEN"] * ratio).groupby(size6).sum() / units["FYWGT"].groupby(size6).sum()

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
        "_qc_benefit_by_size": {int(k): float(v) for k, v in by_size.items()},
        "_snap_meta": pd.DataFrame([{
            "units": units["FYWGT"].sum(), "members": (units["FYWGT"] * units["FSUSIZE"]).sum(),
            "annual_benefits_fy2024": (units["FYWGT"] * units["FSBEN"]).sum() * 12, "sample_units": len(units),
            "refugee_asylee_members": noncit["FYWGT"].sum(), "refugee_asylee_sample": len(noncit),
        }]),
    }


# --------------------------------------------------------------------------------------------------------------
# 2. Medicaid
# --------------------------------------------------------------------------------------------------------------

_PUMS_COLS = ["SERIALNO", "SPORDER", "PUMA", "PWGTP", "AGEP", "HINS3", "HINS4", "SSIP", "DIS", "ESR", "WKWN", "WAGP",
              "SEMP", "SCH", "POVPIP", "RELSHIPP", "SFN", "RAC1P", "FER", "ADJINC"]


def _load_pums_persons(pums_dir: Path) -> pd.DataFrame:
    want = set(_PUMS_COLS) | {f"PWGTP{i}" for i in range(1, 81)}
    parquet = pums_dir / "psam_p15.parquet"
    if parquet.exists():
        import pyarrow.parquet as pq

        have = [c for c in pq.ParquetFile(parquet).schema.names if c in want]
        return pd.read_parquet(parquet, columns=have)
    return pd.read_csv(pums_dir / "psam_p15.csv", usecols=lambda c: c in want, dtype={"SERIALNO": str})


def _sdr_se(theta: np.ndarray, reps: np.ndarray) -> np.ndarray:
    """ACS successive-difference replicate standard error: ``reps`` is (80, ...) against the full-sample ``theta``."""
    return np.sqrt(4 / len(reps) * ((reps - theta) ** 2).sum(axis=0))


def run_medicaid(params, pums_dir: Path, label: str, *, geography: bool) -> dict[str, pd.DataFrame]:
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
    rep_w = np.stack([exp[c].to_numpy(dtype=float) * exp_factor for c in reps]) if reps else np.empty((0, len(exp)))
    # Each replicate is rescaled to the Med-QUEST count: the survey's level is not what is uncertain.
    rep_w = rep_w * (params.medicaid_expansion_adults / rep_w.sum(axis=1, keepdims=True)) if reps else rep_w

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
    w = exp["w"].to_numpy(dtype=float)
    rows, per_person = [], {}
    for scen in SCENARIOS:
        r = params.medicaid_rates[scen]
        rate = np.where(subject, p_meets * r.meets + (1 - p_meets) * r.at_risk, r.exempt)
        # Each person's expected loss: the work requirement, then six-month renewals on those who stay.
        each = rate + (1 - rate) * r.six_month_renewal
        per_person[scen] = each
        work = float((w * rate).sum())
        renewals = float((w * (1 - rate)).sum() * r.six_month_renewal)
        lost = work + renewals
        se = float(_sdr_se(np.array(lost), rep_w @ each)) if reps else float("nan")
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
    out = {"medicaid_groups": groups.assign(pums=label, acs_to_med_quest_scale=scale),
           "medicaid_adults": table, "medicaid_losses": pd.DataFrame(rows)}
    if geography:
        from tax_modeler.analysis.puma_crosswalk import assign_geography

        geo = assign_geography(exp[["SERIALNO", "PUMA"]].assign(SERIALNO=exp["SERIALNO"].astype(str)))
        geo_rows = []
        for col, kind in GEOGRAPHIES:
            keys = geo[col].to_numpy()
            for key in pd.unique(keys):
                m = keys == key
                for scen in SCENARIOS:
                    each = per_person[scen][m]
                    val = float((w[m] * each).sum())
                    se = float(_sdr_se(np.array(val), rep_w[:, m] @ each)) if reps else float("nan")
                    geo_rows.append({"geography_type": kind, "geography": key, "scenario": scen,
                                     "medicaid_expansion_adults": float(w[m].sum()),
                                     "medicaid_lose_coverage": val, "medicaid_lose_coverage_se": se,
                                     "medicaid_sample": int(m.sum())})
        out["_medicaid_geo"] = pd.DataFrame(geo_rows)
    return out


# --------------------------------------------------------------------------------------------------------------
# 3. SPM poverty and geography on the ACS population
# --------------------------------------------------------------------------------------------------------------


def _population(pums_dir: Path):
    """TY2027 tax units with every SPM resource but SNAP, the persons frame, and the PUMS households' FS."""
    import poverty_impact_report as pir
    from tax_modeler.pipeline import enrich_with_spm_unit_id

    base, persons = pir._load_units(pums_dir, False, with_replicate_weights=True)
    base, persons = enrich_with_spm_unit_id(base, persons)
    u = pir._build_units_for_tax_year(base, POVERTY_YEAR, project=True, use_cbo_aging=True)
    u = pir._apply_eitc_reweight(u)
    u = pir._apply_credit_takeup(u, tax_year=POVERTY_YEAR)
    u = pir._apply_hi_eitc(u, tax_year=POVERTY_YEAR)
    u = pir._apply_hi_renters(u)
    u = pir._apply_arpa_ctc(u)
    parquet = pums_dir / "psam_h15.parquet"
    households = (pd.read_parquet(parquet, columns=["SERIALNO", "WGTP", "FS"]) if parquet.exists() else
                  pd.read_csv(pums_dir / "psam_h15.csv", usecols=["SERIALNO", "WGTP", "FS"], dtype={"SERIALNO": str}))
    return u, persons, households


def _other_resources(u: pd.DataFrame) -> pd.DataFrame:
    """The rest of the SPM stack, as the working-family credits step builds it for TY2028."""
    import poverty_impact_report as pir
    from tax_modeler.benefits.childcare_expense import compute_childcare_expense_for_units
    from tax_modeler.benefits.moop import compute_moop_for_units
    from tax_modeler.benefits.school_lunch import compute_school_lunch_for_units
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units

    u = compute_federal_income_tax_for_units(u, tax_year=POVERTY_YEAR, extrapolate=True)
    u = compute_moop_for_units(u)
    u = pir._apply_housing_subsidy(u, tax_year=BENEFIT_YEAR)
    u = pir._apply_childcare_subsidy(u, tax_year=BENEFIT_YEAR)
    u = pir._apply_wic(u, tax_year=BENEFIT_YEAR)
    u = pir._apply_liheap(u, tax_year=BENEFIT_YEAR)
    u = compute_school_lunch_for_units(u, tax_year=BENEFIT_YEAR)
    u = compute_childcare_expense_for_units(u)
    return compute_work_expense_for_units(u)


def run_poverty(params, snap: dict, pums_dir: Path) -> dict[str, pd.DataFrame]:
    from tax_modeler.poverty.draws import ClaimBlock, DrawSpec
    from tax_modeler.poverty.impact import compute_poverty_impact_with_se
    from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units
    from tax_modeler.programs import hr1_safety_net as hr1

    t0 = time.perf_counter()
    u, persons, households = _population(pums_dir)
    log.info("population built (%.0fs)", time.perf_counter() - t0)
    base = hr1.acs_snap_baseline(u, households, persons, target_households=params.snap_households_2027,
                                 target_dollars=params.snap_annual_benefits_2027,
                                 fallback_benefit_by_size=snap["_qc_benefit_by_size"])
    u = _other_resources(base.units)
    # The same population with the poverty pipeline's own SNAP (the published pages' baseline), for comparison.
    import poverty_impact_report as pir
    from tax_modeler.poverty.impact import compute_poverty_impact

    pipe = aggregate_to_spm_units(_other_resources(pir._apply_snap(base.units.drop(columns="snap_amount"),
                                                                   tax_year=BENEFIT_YEAR)), persons)
    pipe_rate = float(compute_poverty_impact(pipe, tax_year=POVERTY_YEAR, scenarios=(),
                                             extrapolate_thresholds=True).by_state.iloc[0]["poverty_rate_baseline"])
    hh = base.households
    rec = hh[hh["recipient"]]
    p_serial = persons["SERIALNO"].astype(str)
    in_rec = p_serial.isin(set(rec["hh_id"]))
    check = pd.DataFrame([
        {"check": "SNAP households, weighted", "model": float(rec["WGTP"].sum()), "dhs": params.snap_households_2027},
        {"check": "  report SNAP", "model": base.reported, "dhs": np.nan},
        {"check": "    of which fail the FY2027 gross test on annual income (given the QC average for their size)",
         "model": base.reported_ineligible, "dhs": np.nan},
        {"check": "  eligible, do not report SNAP, drawn to reach the caseload", "model": base.imputed, "dhs": np.nan},
        {"check": "  probability each eligible non-reporter is drawn", "model": base.draw_rate, "dhs": np.nan},
        {"check": "persons in SNAP households", "model": float(persons.loc[in_rec, "PWGTP"].sum()),
         "dhs": params.snap_persons_2027},
        {"check": "annual benefits, $",
         "model": float((rec["WGTP"] * rec["snap"]).sum()) * 12 * base.dollar_scale,
         "dhs": params.snap_annual_benefits_2027},
        {"check": "dollar scale on the module's FY2027 benefits", "model": base.dollar_scale, "dhs": np.nan},
        {"check": "sample households receiving SNAP", "model": float(len(rec)), "dhs": np.nan},
        {"check": "SPM poverty rate with the poverty pipeline's own SNAP (for comparison)", "model": pipe_rate,
         "dhs": np.nan},
    ])

    adults = hr1.classify_acs_snap_adults(persons, rec["hh_id"])
    adults["loss_if_removed"] = hr1.acs_snap_loss(adults, base, persons)
    # Geography rides on the household (assign_geography keys its draw on SERIALNO).
    geo = u.assign(hh=u["hh_id"].astype(str)).drop_duplicates("hh").set_index("hh")
    for col, _ in GEOGRAPHIES:
        adults[col] = adults["SERIALNO"].astype(str).map(geo[col])
    qc_cells = snap["snap_adults"]
    qc_losses = snap["snap_losses"].set_index(["scenario", "group"])
    cal_frames, blocks = [], []
    w = adults["PWGTP"].to_numpy(dtype=float)
    for case in SCENARIOS:
        prob, cal = hr1.calibrate_to_qc(adults, qc_cells, params.snap_rates[case])
        native = float((w * prob * adults["loss_if_removed"]).sum())
        target = float(qc_losses.loc[(case, "all_newly_subject"), "annual_benefits_lost_fy2027_usd"])
        k = target / native if native > 0 else 0.0
        adults[f"p_{case}"] = prob
        adults[f"loss_{case}"] = adults["loss_if_removed"] * k
        cal_frames.append(cal.assign(scenario=case, acs_dollars_before_scaling=native, qc_dollars=target,
                                     dollar_scale=k))
        blocks.append(ClaimBlock(adults["spm_unit_id"].to_numpy(), prob, np.zeros(len(adults)),
                                 {_scn(case): -adults[f"loss_{case}"].to_numpy(dtype=float)}))

    frame = aggregate_to_spm_units(u, persons)
    for case in SCENARIOS:
        expected = (adults[f"p_{case}"] * adults[f"loss_{case}"]).groupby(adults["spm_unit_id"]).sum()
        frame[f"{_scn(case)}_loss"] = frame["spm_unit_id"].map(expected).fillna(0.0).to_numpy()

    def unchanged(df: pd.DataFrame) -> np.ndarray:
        return df["spm_resources"].to_numpy(dtype=float)

    spec = DrawSpec(blocks=tuple(blocks), baseline_fixed=None,
                    scenario_fixed={_scn(c): unchanged for c in SCENARIOS}, n_draws=N_DRAWS, seed=SEED)
    scns = tuple(_scn(c) for c in SCENARIOS)
    res = compute_poverty_impact_with_se(frame, tax_year=POVERTY_YEAR, scenarios=scns,
                                         extrapolate_thresholds=True, draws=spec)
    log.info("poverty done (%.0fs)", time.perf_counter() - t0)

    s = res.by_state.iloc[0]
    f = res.units
    thr = f["spm_threshold"].to_numpy(dtype=float)
    kids = f["n_children"].fillna(0).to_numpy(dtype=float)
    wt = f["weight"].to_numpy(dtype=float)
    rep_cols = [c for c in f.columns if c.startswith("weight_r") and c[8:].isdigit()]
    p_old = (f["poor_prob_baseline"].to_numpy(dtype=float) if "poor_prob_baseline" in f.columns
             else (f["spm_resources"].to_numpy(dtype=float) < thr).astype(float))
    z = 1.645
    # Each SNAP loser's SPM unit: poor before the cut (probability), and below half the line.
    poor_before = pd.Series(p_old, index=f["spm_unit_id"])
    half_before = pd.Series((f["spm_resources"].to_numpy(dtype=float) < 0.5 * thr).astype(float), index=f["spm_unit_id"])
    a_poor = adults["spm_unit_id"].map(poor_before).fillna(0.0).to_numpy()
    a_half = adults["spm_unit_id"].map(half_before).fillna(0.0).to_numpy()
    rows = []
    for case in SCENARIOS:
        scn = _scn(case)
        newly = f[f"poor_prob_{scn}"].to_numpy(dtype=float) - p_old
        children = float((kids * wt * newly).sum())
        child_r = np.array([(kids * f[c].to_numpy(dtype=float) * newly).sum() for c in rep_cols])
        child_se = float(_sdr_se(np.array(children), child_r)) if rep_cols else float("nan")
        info = res.draw_info.get(scn, {})
        persons_in = float(s[f"persons_lifted_{scn}"])
        persons_se = float(s.get(f"persons_lifted_{scn}_se", np.nan))
        rows.append({
            "scenario": case, "tax_year": POVERTY_YEAR,
            "poverty_rate_baseline": float(s["poverty_rate_baseline"]),
            "poverty_rate_after": float(s[f"poverty_rate_{scn}"]),
            "persons_into_poverty": persons_in, "persons_into_poverty_se": persons_se,
            "persons_into_poverty_low90": persons_in - z * persons_se,
            "persons_into_poverty_high90": persons_in + z * persons_se,
            "children_into_poverty": children, "children_into_poverty_se": child_se,
            "poverty_gap_increase_usd": float(s[f"gap_closed_{scn}_$"]),
            "persons_into_poverty_mc_se": info.get("persons_lifted_mc_se", np.nan),
            "adults_losing_snap": float((w * adults[f"p_{case}"]).sum()),
            "share_of_losers_already_in_poverty": float((w * adults[f"p_{case}"] * a_poor).sum()
                                                        / (w * adults[f"p_{case}"]).sum()),
            "share_of_losers_under_half_the_line": float((w * adults[f"p_{case}"] * a_half).sum()
                                                         / (w * adults[f"p_{case}"]).sum()),
            "snap_benefits_lost_usd": float((w * adults[f"p_{case}"] * adults[f"loss_{case}"]).sum()),
            "n_draws": N_DRAWS, "seed": SEED,
        })
    poverty = pd.DataFrame(rows)

    geo_rows = []
    by = {"county": res.by_county, "house_district": res.by_house_district, "senate_district": res.by_senate_district}
    for col, kind in GEOGRAPHIES:
        g = by[col].set_index(col)
        for case in SCENARIOS:
            scn = _scn(case)
            sub = adults.assign(n=w * adults[f"p_{case}"], d=w * adults[f"p_{case}"] * adults[f"loss_{case}"])
            snap_geo = sub.groupby(col).agg(snap_adults_losing=("n", "sum"), snap_benefits_lost_usd=("d", "sum"),
                                            snap_sample=("n", lambda x: int((x > 0).sum())))
            for key in g.index:
                geo_rows.append({
                    "geography_type": kind, "geography": key, "scenario": case,
                    "snap_adults_losing": float(snap_geo["snap_adults_losing"].get(key, 0.0)),
                    "snap_benefits_lost_usd": float(snap_geo["snap_benefits_lost_usd"].get(key, 0.0)),
                    "snap_sample": int(snap_geo["snap_sample"].get(key, 0)),
                    "poverty_rate_baseline": float(g.loc[key, "poverty_rate_baseline"]),
                    "persons_into_poverty": float(g.loc[key, f"persons_lifted_{scn}"]),
                    "persons_into_poverty_se": float(g.loc[key].get(f"persons_lifted_{scn}_se", np.nan)),
                })
    return {"poverty": poverty, "snap_baseline_check": check,
            "snap_calibration": pd.concat(cal_frames, ignore_index=True), "_snap_geo": pd.DataFrame(geo_rows)}


def merge_geography(med_geo: pd.DataFrame, snap_geo: pd.DataFrame | None) -> pd.DataFrame:
    keys = ["geography_type", "geography", "scenario"]
    med = med_geo.assign(geography=med_geo["geography"].astype(str))
    if snap_geo is None:
        return med
    out = med.merge(snap_geo.assign(geography=snap_geo["geography"].astype(str)), on=keys, how="outer")
    order = {k: i for i, (_, k) in enumerate(GEOGRAPHIES)}
    out["_o"] = out["geography_type"].map(order)
    out["_n"] = pd.to_numeric(out["geography"], errors="coerce")
    out["_s"] = out["scenario"].map({c: i for i, c in enumerate(SCENARIOS)})
    return out.sort_values(["_o", "_n", "geography", "_s"]).drop(columns=["_o", "_n", "_s"]).reset_index(drop=True)


# --------------------------------------------------------------------------------------------------------------


def _summary(snap: dict, med: dict, pov: dict | None, params) -> str:
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
    if pov is not None:
        lines += ["", f"SPM poverty, TY{POVERTY_YEAR} (ACS 2020-24 population; 90% sampling interval):"]
        p0 = pov["poverty"].iloc[0]
        lines.append(f"  baseline rate {p0.poverty_rate_baseline:.2%}")
        for _, r in pov["poverty"].iterrows():
            lines.append(
                f"  {r.scenario:<5} {r.persons_into_poverty:>7,.0f} people into poverty ({r.persons_into_poverty_low90:,.0f} "
                f"to {r.persons_into_poverty_high90:,.0f}), {r.children_into_poverty:,.0f} children; rate "
                f"{r.poverty_rate_after:.2%}; poverty gap +{usd(r.poverty_gap_increase_usd)}")
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
    from tax_modeler.runs import write_run_manifest

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pums-data-dir", type=Path, default=PUMS_DIR)
    ap.add_argument("--pums-check-dir", type=Path, default=PUMS_DIR_CHECK,
                    help="second PUMS file for the Medicaid check; skipped if missing")
    ap.add_argument("--skip-poverty", action="store_true", help="leave out stage 3 (about 5 minutes)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    log.setLevel(logging.INFO)

    params = Hr1Parameters()
    snap = run_snap(params)
    runs = [run_medicaid(params, args.pums_data_dir, args.pums_data_dir.name, geography=True)]
    if args.pums_check_dir.exists():
        runs.append(run_medicaid(params, args.pums_check_dir, args.pums_check_dir.name, geography=False))
    med = {k: pd.concat([r[k] for r in runs if k in r], ignore_index=True) for k in runs[0]}
    pov = None if args.skip_poverty else run_poverty(params, snap, args.pums_data_dir)
    geography = merge_geography(med["_medicaid_geo"], None if pov is None else pov["_snap_geo"])

    args.out.mkdir(parents=True, exist_ok=True)
    frames = {**snap, **med, **(pov or {}), "by_geography": geography}
    for name, frame in frames.items():
        if not name.startswith("_"):
            frame.to_csv(args.out / f"{name}.csv", index=False, float_format="%.6g")
    text = _summary(snap, med, pov, params)
    (args.out / "summary.txt").write_text(text)
    settings = {
        "parameters": {k: v for k, v in vars(params).items() if not k.endswith("_rates")},
        "snap_rates": {k: vars(v) for k, v in params.snap_rates.items()},
        "medicaid_rates": {k: vars(v) for k, v in params.medicaid_rates.items()},
        "pums": args.pums_data_dir.name, "poverty_year": POVERTY_YEAR, "benefit_year": BENEFIT_YEAR,
        "bootstrap_draws": N_BOOT, "poverty_draws": N_DRAWS, "seed": SEED, "poverty": pov is not None,
    }
    (args.out / "summary.json").write_text(json.dumps(
        {"snap_base": snap["_snap_meta"].iloc[0].to_dict(), **settings}, indent=2, default=float))
    write_run_manifest(args.out, script="analyses/forecast_hr1_snap_medicaid.py", params=settings)
    print(text)


if __name__ == "__main__":
    main()
