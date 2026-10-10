"""Working-family tax credits after Act 163 expires: revenue, distribution, poverty.

Usage:
  python forecast_working_family_credits.py            # TY2022-2031, writes runs/working_family_credits/

Question
--------
Act 163 (SLH 2023) doubled Hawaii's two main working-family credits for tax
years 2023-2027:

  - Earned income tax credit (HRS §235-55.75): 20% -> 40% of the federal EITC
    (refundable since Act 114, 2022).
  - Refundable food/excise tax credit (HRS §235-55.85): the per-exemption table
    raised from $35-$110 to $70-$220 and extended $10,000 up the income scale.

Act 163 is repealed on December 31, 2027 and both sections are reenacted as
they read before it (L 2023, c 163, §5). The 2026 session did not extend it
(HB 2306's extension was not enacted). So from TY2028 the credits return to
20% of federal and the pre-2023 food/excise table. What do working families
lose, and what would renewing the expansions cost?

Method
------
Population: tax units built from the 2024 1-year Hawaii PUMS with real
dependents (scripts/poverty_impact_report.py's pipeline), projected to each
tax year with CBO per-component aging; federal EITC take-up anchored to IRS
counts after lifting short with-children EITC buckets to IRS Hawaii targets
(the production poverty pipeline's default). Federal EITC parameters past
TY2025 are CPI-extrapolated.

State credits: each tax unit's credit under the expanded law (40%; Act 163
table) and the reverted law (20%; prior table), computed from the statutory
schedules in tax_modeler.credits. Take-up is a uniform factor per credit, held
constant, set so modeled dollars match DOTAX in the latest year it reports each
credit:
  - EITC: TY2024, DOTAX's Earned Income Tax Credit Report (Act 107), Table 1's
    NEW credit claimed for the year, all filers: $76.98M on 78,399 claims.
    New, not applied: applied ($78.5M) adds legacy nonrefundable carryforwards
    that the 40% rate does not touch and Act 25 (2025) ends.
  - Food/excise: TY2023, "Tax Credits Claimed by Hawaiʻi Taxpayers" Table A-1:
    $63.96M (the report that carries this credit).
Checks: the EITC report's claims, its distribution by federal AGI range and by
filing status (eitc_check_ty2024.csv); the Credits Claimed claim counts; and,
out of sample, TY2023 EITC dollars and TY2022 food/excise dollars under the old
table ($24.96M).

Food/excise amounts are fixed dollars and income ceilings are not indexed, so
the credit shrinks in real terms as incomes grow; the EITC follows the
inflation-indexed federal credit.

Poverty: SPM poverty in TY2028 with the expansions renewed (baseline) vs
expired (scenario ``act163_sunset``), through the same SPM resource pipeline
as the EITC revert analysis. Static: no labor-supply response. Money income,
credits, Hawaii and federal income tax and the SPM threshold are all in TY2028
dollars: money income is aged with the tax inputs, and federal tax parameters
and the threshold are indexed past their last published year by the same CPI
assumption as the federal credits. Not indexed: benefits use the package's
latest tables (TY2025; FY2024 for SNAP), and the medical, childcare and
work-expense deductions are CPS ASEC donor cell means ranked on base-year
income, in both cases.

Outputs (runs/working_family_credits/):
  revenue_by_year.csv      $M by year: expanded vs reverted, per credit, and claimants
  calibration.csv          take-up factors and the TY2022-TY2024 checks
  eitc_check_ty2024.csv    modeled EITC by federal AGI range and filing status vs DOTAX
  distribution_ty2028.csv  loss by household-income fifth
  by_family_type_ty2028.csv loss by family type (single, single parent, couple)
  poverty_ty2028.csv       SPM poverty with and without the expansions
  manifest.json
"""
from __future__ import annotations

import logging
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO / "scripts"))
OUT_DIR = REPO / "runs" / "working_family_credits"
PUMS_DIR = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"

YEARS = [2022, 2023, 2024, 2027, 2028, 2029, 2030, 2031]
SCORE_YEARS = [2028, 2029, 2030, 2031]
POVERTY_YEAR = 2028
CAL_YEAR = 2023         # food/excise: the year DOTAX's Credits Claimed report fits it to
EITC_CAL_YEAR = 2024    # EITC: the latest year of DOTAX's Earned Income Tax Credit Report

# DOTAX "Tax Credits Claimed by Hawaiʻi Taxpayers", Tables A-1 ($K) and A-2 (claims).
# The EITC rows here are the out-of-sample check for TY2023: the EITC report puts that
# year's credit applied at $75.6M, against Table A-1's $77.05M, so the two reports
# differ by about 2% for the same year.
DOTAX = {
    2022: {"eitc_$M": 19.786, "eitc_claims": 60_903, "food_$M": 24.961, "food_claims": 208_786},
    2023: {"eitc_$M": 77.054, "eitc_claims": 84_470, "food_$M": 63.957, "food_claims": 249_806},
}
EITC_EXPANDED, EITC_REVERTED = 0.40, 0.20


def load_population():
    import poverty_impact_report as pir
    from tax_modeler.pipeline import enrich_with_spm_unit_id

    units, persons = pir._load_units(PUMS_DIR, False)
    units, persons = enrich_with_spm_unit_id(units, persons)
    return units, persons


def units_for_year(base: pd.DataFrame, year: int) -> pd.DataFrame:
    import poverty_impact_report as pir

    u = pir._build_units_for_tax_year(base, year, project=True, use_cbo_aging=True)
    # As in poverty_impact_report's default path: lift the short 1-/2-child
    # EITC buckets to IRS Hawaii counts, then apply IRS-anchored take-up.
    u = pir._apply_eitc_reweight(u)
    return pir._apply_credit_takeup(u, tax_year=year)


def state_credits(u: pd.DataFrame) -> pd.DataFrame:
    """Eligible (pre-take-up) state credits under both laws, per tax unit."""
    from tax_modeler.credits.hi_food_excise import (
        ACT163_JOINT,
        ACT163_SINGLE,
        PRIOR_JOINT,
        PRIOR_SINGLE,
        HawaiiFoodExciseParameters,
        compute_hi_food_excise_for_units,
    )

    fed = u["eitc_amount"].fillna(0).clip(lower=0).to_numpy(dtype=float)
    act163 = HawaiiFoodExciseParameters(single=ACT163_SINGLE, joint=ACT163_JOINT)
    prior = HawaiiFoodExciseParameters(single=PRIOR_SINGLE, joint=PRIOR_JOINT)
    return pd.DataFrame({
        "eitc_expanded": EITC_EXPANDED * fed,
        "eitc_reverted": EITC_REVERTED * fed,
        "food_expanded": compute_hi_food_excise_for_units(u, params=act163, out_col="x")["x"].to_numpy(),
        "food_reverted": compute_hi_food_excise_for_units(u, params=prior, out_col="x")["x"].to_numpy(),
    }, index=u.index)


def _totals(c: pd.DataFrame, w: np.ndarray, p_eitc: float, p_food: float) -> dict:
    def m(col, p):
        return float((c[col] * w).sum() * p / 1e6)

    def n(col, p):
        return float(w[c[col] > 0].sum() * min(p, 1.0))

    return {
        "eitc_expanded_$M": m("eitc_expanded", p_eitc), "eitc_reverted_$M": m("eitc_reverted", p_eitc),
        "food_expanded_$M": m("food_expanded", p_food), "food_reverted_$M": m("food_reverted", p_food),
        "eitc_claimants": n("eitc_expanded", p_eitc),
        "food_claimants_expanded": n("food_expanded", p_food),
        "food_claimants_reverted": n("food_reverted", p_food),
    }


def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    print("Loading PUMS population...", flush=True)
    base, persons = load_population()

    frames: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for yr in YEARS:
        u = units_for_year(base, yr)
        frames[yr] = (u, state_credits(u))
        print(f"  TY{yr} built ({time.perf_counter() - t0:.0f}s)", flush=True)

    # Take-up: a uniform factor per credit matching DOTAX's dollars under Act 163, in
    # the latest year it reports each credit (EITC_CAL_YEAR, CAL_YEAR). Below 1 it is a
    # claim probability (not every eligible household files). Above 1 it is a dollar
    # scale (the model's base runs short); claim probabilities are capped at 1 wherever
    # they are used.
    from tax_modeler.calibration import dotax_base

    eitc_dotax = dotax_base.eitc_new_credit(EITC_CAL_YEAR)
    uE, cE = frames[EITC_CAL_YEAR]
    wE = uE["weight"].to_numpy(dtype=float)
    p_eitc = eitc_dotax["dollars_M"] * 1e6 / float((cE["eitc_expanded"] * wE).sum())
    uF, cF = frames[CAL_YEAR]
    wF = uF["weight"].to_numpy(dtype=float)
    p_food = DOTAX[CAL_YEAR]["food_$M"] * 1e6 / float((cF["food_expanded"] * wF).sum())
    if not (0.5 < p_eitc <= 1.10 and 0.5 < p_food <= 1.10):
        raise SystemExit(f"implausible take-up factors: EITC {p_eitc:.3f}, food {p_food:.3f}")

    rows = []
    for yr, (u, c) in frames.items():
        w = u["weight"].to_numpy(dtype=float)
        rows.append({"tax_year": yr, **_totals(c, w, p_eitc, p_food)})
    rev = pd.DataFrame(rows)
    rev["eitc_loss_$M"] = rev["eitc_expanded_$M"] - rev["eitc_reverted_$M"]
    rev["food_loss_$M"] = rev["food_expanded_$M"] - rev["food_reverted_$M"]
    rev["total_loss_$M"] = rev["eitc_loss_$M"] + rev["food_loss_$M"]
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)

    r = rev.set_index("tax_year")
    r22, r23, r24 = r.loc[2022], r.loc[2023], r.loc[EITC_CAL_YEAR]
    cal = pd.DataFrame([
        {"check": "take-up factor, EITC", "model": p_eitc, "dotax": None},
        {"check": "take-up factor, food/excise", "model": p_food, "dotax": None},
        {"check": "TY2024 EITC $M (fit)", "model": r24["eitc_expanded_$M"], "dotax": eitc_dotax["dollars_M"]},
        {"check": "TY2024 EITC claims", "model": r24["eitc_claimants"], "dotax": eitc_dotax["claims"]},
        {"check": "TY2023 EITC $M (out of sample)", "model": r23["eitc_expanded_$M"], "dotax": DOTAX[2023]["eitc_$M"]},
        {"check": "TY2023 EITC claims (out of sample)", "model": r23["eitc_claimants"], "dotax": DOTAX[2023]["eitc_claims"]},
        {"check": "TY2023 food/excise $M (fit)", "model": r23["food_expanded_$M"], "dotax": DOTAX[2023]["food_$M"]},
        {"check": "TY2023 food/excise claims", "model": r23["food_claimants_expanded"], "dotax": DOTAX[2023]["food_claims"]},
        {"check": "TY2022 food/excise $M, prior table (out of sample)", "model": r22["food_reverted_$M"], "dotax": DOTAX[2022]["food_$M"]},
        {"check": "TY2022 food/excise claims, prior table", "model": r22["food_claimants_reverted"], "dotax": DOTAX[2022]["food_claims"]},
    ])
    cal.to_csv(OUT_DIR / "calibration.csv", index=False)
    eitc_check(uE, cE, p_eitc, dotax_base.load_eitc_report(EITC_CAL_YEAR)).to_csv(
        OUT_DIR / f"eitc_check_ty{EITC_CAL_YEAR}.csv", index=False)

    # Distribution and poverty, TY2028.
    u, c = frames[POVERTY_YEAR]
    u = u.copy()
    u["hi_eitc_amount"] = c["eitc_expanded"] * p_eitc
    u["hi_food_excise_amount"] = c["food_expanded"] * p_food
    u["act163_sunset_loss"] = ((c["eitc_expanded"] - c["eitc_reverted"]) * p_eitc
                               + (c["food_expanded"] - c["food_reverted"]) * p_food)
    q_eitc = np.where(c["eitc_expanded"] > 0, min(p_eitc, 1.0), 0.0)
    q_food = np.where(c["food_expanded"] > c["food_reverted"], min(p_food, 1.0), 0.0)
    u["_claim_prob"] = 1 - (1 - q_eitc) * (1 - q_food)
    distribution(u).to_csv(OUT_DIR / f"distribution_ty{POVERTY_YEAR}.csv", index=False)
    by_family_type(u).to_csv(OUT_DIR / f"by_family_type_ty{POVERTY_YEAR}.csv", index=False)
    poverty(u, persons).to_csv(OUT_DIR / f"poverty_ty{POVERTY_YEAR}.csv", index=False)

    # Calibration report. This script runs no IPF rake: its calibration is the
    # two closed-form take-up factors above, fitted to one DOTAX year each.
    # Record those as margins (with their source years), the EITC/food
    # out-of-sample checks, and the weight dispersion of the population the
    # factors are applied to (PUMS 1-year weights after the EITC reweight).
    from tax_modeler.calibration.report import (
        CalibrationReport, MarginResidual, WeightDiagnostics,
    )
    from _forecast_common import CALIBRATION_REPORT_NAME
    cal_margins = []
    for _, row in cal.iterrows():
        if row["dotax"] is None or pd.isna(row["dotax"]):
            continue
        name = str(row["check"])
        src_year = int(name[2:6]) if name.startswith("TY") else None
        cal_margins.append(MarginResidual(
            "eitc" if "EITC" in name else "food_excise", name,
            float(row["dotax"]), float(row["model"]),
            source="DOTAX Credits Claimed / EITC report", source_year=src_year,
            counted="(fit)" in name,
        ))
    cal_report = CalibrationReport(
        stage="credit_takeup_calibration",
        converged=True, iterations=1, max_iterations=1, tolerance=0.0,
        convergence_mode="closed_form",
        margins=cal_margins,
        weights_before=WeightDiagnostics.from_weights(base["weight"].to_numpy(dtype=float)),
        weights_after=WeightDiagnostics.from_weights(wE),
        source_years={"eitc_takeup": EITC_CAL_YEAR, "food_excise_takeup": CAL_YEAR},
        warnings=[f"take-up factors: EITC {p_eitc:.3f} (TY{EITC_CAL_YEAR}), "
                  f"food/excise {p_food:.3f} (TY{CAL_YEAR}); above 1 a factor scales "
                  "dollars, not claims"],
    )
    print("\n" + cal_report.to_markdown(), flush=True)
    cal_report.to_json(OUT_DIR / CALIBRATION_REPORT_NAME)

    from tax_modeler.runs import write_run_manifest
    write_run_manifest(
        OUT_DIR, script="forecast_working_family_credits.py",
        params={"years": YEARS, "poverty_year": POVERTY_YEAR, "calibration_year": CAL_YEAR,
                "eitc_calibration_year": EITC_CAL_YEAR,
                "eitc_rates": [EITC_EXPANDED, EITC_REVERTED], "takeup_eitc": round(p_eitc, 4),
                "takeup_food": round(p_food, 4), "dotax": DOTAX},
        inputs={"pums": "HI 1-year 2024 (packages/data/raw/pums_2024_1yr)",
                "dotax": "Tax Credits Claimed by Hawaiʻi Taxpayers, TY2022-2023, Tables A-1/A-2",
                "dotax_eitc": "Earned Income Tax Credit Report, TY2024 (Act 107), Tables 1-4 "
                              "(data/calibration/dotax_eitc_2024.json)"},
    )
    _print_summary(rev, cal)
    print(f"\nDone in {time.perf_counter() - t0:.0f}s -> {OUT_DIR.relative_to(REPO)}")


EITC_RANGES = [("Under $15,000", -np.inf, 15_000), ("$15,000 to $29,999", 15_000, 30_000),
               ("$30,000 to $44,999", 30_000, 45_000), ("$45,000 to $54,999", 45_000, 55_000),
               ("$55,000 or more", 55_000, np.inf)]
# DOTAX Table 4's rows against the model's filing statuses (qualifying surviving spouses file jointly).
EITC_STATUS = [("Single", ("single",), ("Single",)),
               ("Joint", ("married_filing_jointly",), ("Joint", "Qualifying surviving spouse")),
               ("Married filing separately", ("married_filing_separately",), ("Married filing separately",)),
               ("Head of household", ("head_of_household",), ("Head of household",))]


def eitc_check(u: pd.DataFrame, c: pd.DataFrame, p_eitc: float, report: dict) -> pd.DataFrame:
    """The calibration year's modeled EITC against the report's distribution.

    By federal AGI range (Table 1's NEW credit, the quantity the factor is fitted
    to) in claims and dollars. By filing status (Table 4 reports the credit APPLIED,
    which includes legacy carryforwards) in shares of the total, since the levels
    differ by that. The model is the resident population: the report's N-11 returns
    are 94.6% of its claims and 97.3% of its dollars.
    """
    w = u["weight"].to_numpy(dtype=float)
    e = c["eitc_expanded"].to_numpy(dtype=float)
    claim = e > 0
    q = min(p_eitc, 1.0)
    agi = u["federal_agi"].to_numpy(dtype=float)
    rows = []
    for (label, lo, hi), r in zip(EITC_RANGES, report["table_1_by_federal_agi"]["ranges"], strict=True):
        m = claim & (agi >= lo) & (agi < hi)
        rows.append({"table": "federal AGI range", "group": label,
                     "dotax_claims": r["new"]["claims"], "dotax_$M": r["new"]["dollars"] / 1e6,
                     "model_claims": float(w[m].sum() * q), "model_$M": float((e * w)[m].sum() * p_eitc / 1e6)})
    t4 = report["table_4_filing_status"]
    fs = u["filing_status"].to_numpy()
    for label, mine, theirs in EITC_STATUS:
        m = claim & np.isin(fs, mine)
        rows.append({"table": "filing status", "group": label,
                     "dotax_claims": sum(t4[k]["claims"] for k in theirs),
                     "dotax_$M": sum(t4[k]["dollars"] for k in theirs) / 1e6,
                     "model_claims": float(w[m].sum() * q), "model_$M": float((e * w)[m].sum() * p_eitc / 1e6)})
    out = pd.DataFrame(rows)
    for col in ("claims", "$M"):
        for who in ("dotax", "model"):
            out[f"{who}_{col}_share"] = out.groupby("table")[f"{who}_{col}"].transform(lambda x: x / x.sum())
    return out


def _household_frame(u: pd.DataFrame) -> pd.DataFrame:
    """One row per household: summed loss, household income, weights, composition.

    ``loss_w`` is the household's weighted loss, each unit's loss times its
    own filer weight (they differ within a household), so group totals are
    the revenue table's; ``hh_weight`` (WGTP) counts households.
    """
    income_col = "total_cash_income" if "total_cash_income" in u.columns else "income"
    g = u.groupby("hh_id", observed=True)
    hh = pd.DataFrame({
        "income": g[income_col].sum(),
        "loss": g["act163_sunset_loss"].sum(),
        "loss_w": (u["act163_sunset_loss"] * u["weight"]).groupby(u["hh_id"], observed=True).sum(),
        "hh_weight": g["hh_weight"].first() if "hh_weight" in u.columns else g["weight"].first(),
        "children": g["num_dependents"].sum(),
        "joint": g["filing_status"].agg(lambda s: (s == "married_filing_jointly").any()),
        # A household loses if any of its tax units claims: q = 1 - prod(1 - q_unit).
        "claim_prob": 1 - np.exp(g["_claim_prob"].apply(lambda s: np.log1p(-s.clip(upper=1 - 1e-12)).sum())),
    })
    return hh


def distribution(u: pd.DataFrame) -> pd.DataFrame:
    hh = _household_frame(u).sort_values("income")
    cum = hh["hh_weight"].cumsum() / hh["hh_weight"].sum()
    labels = ["Lowest 20%", "Second 20%", "Middle 20%", "Fourth 20%", "Top 20%"]
    hh["group"] = pd.cut(cum, [0, .2, .4, .6, .8, 1.0001], labels=labels, include_lowest=True)
    return _summarize(hh, "group", labels)


def by_family_type(u: pd.DataFrame) -> pd.DataFrame:
    hh = _household_frame(u)
    hh["group"] = np.select(
        [(hh["children"] > 0) & ~hh["joint"], hh["children"] > 0, hh["joint"]],
        ["Single parent", "Couple with children", "Couple, no children"], "Single, no children")
    order = ["Single parent", "Couple with children", "Single, no children", "Couple, no children"]
    return _summarize(hh, "group", order)


def _summarize(hh: pd.DataFrame, col: str, order: list[str]) -> pd.DataFrame:
    out = []
    for key in order:
        g = hh[hh[col] == key]
        hw = g["hh_weight"]
        losers = (hw * g["claim_prob"] * (g["loss"] > 0)).sum()
        total = float(g["loss_w"].sum())
        out.append({
            "group": key, "households": float(hw.sum()),
            "income_lo": float(g["income"].min()), "income_hi": float(g["income"].max()),
            "total_loss_$M": total / 1e6,
            "avg_loss_per_household": total / hw.sum() if hw.sum() else 0.0,
            "pct_households_losing": 100 * losers / hw.sum() if hw.sum() else 0.0,
            "avg_loss_per_losing_household": total / losers if losers else 0.0,
        })
    return pd.DataFrame(out)


def poverty(u: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    """SPM poverty in POVERTY_YEAR, expansions renewed (baseline) vs expired."""
    import poverty_impact_report as pir
    from tax_modeler.benefits.childcare_expense import compute_childcare_expense_for_units
    from tax_modeler.benefits.moop import compute_moop_for_units
    from tax_modeler.benefits.school_lunch import compute_school_lunch_for_units
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units
    from tax_modeler.liability.federal import compute_federal_income_tax_for_units
    from tax_modeler.poverty.impact import compute_poverty_impact
    from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units

    yr = POVERTY_YEAR
    # Income, taxes, credits and the poverty line are in TY2028 dollars. The projector
    # aged money income with the tax inputs (``spm_money_income``, which
    # compute_spm_resources reads in place of the base-year
    # ``total_cash_income``); the credits and Hawaii tax are TY2028 amounts;
    # federal income tax uses TY2028 brackets and standard deductions, and the
    # SPM threshold the last tabled year indexed to TY2028, both by the CPI
    # assumption the federal credits use past their last Rev. Proc.
    # Benefits stay on the package's latest tables (TY2025, where school lunch
    # stops; SNAP's FY2024 USDA tables), applied to TY2028 incomes. Benefits
    # are identical in the renewed and expired cases, but they still move who
    # sits near the line and so how many people cross it. The MOOP,
    # childcare-expense and work-expense subtractions are CPS ASEC donor cell
    # means, not indexed to TY2028 either (they rank on base-year income).
    benefit_year = min(yr, 2025)
    u = pir._apply_arpa_ctc(u)
    u = pir._apply_snap(u, tax_year=benefit_year)
    u = compute_federal_income_tax_for_units(u, tax_year=yr, extrapolate=True)
    u = compute_moop_for_units(u)
    u = pir._apply_housing_subsidy(u, tax_year=benefit_year)
    u = pir._apply_childcare_subsidy(u, tax_year=benefit_year)
    u = pir._apply_wic(u, tax_year=benefit_year)
    u = pir._apply_liheap(u, tax_year=benefit_year)
    u = compute_school_lunch_for_units(u, tax_year=benefit_year)
    u = compute_childcare_expense_for_units(u)
    u = compute_work_expense_for_units(u)
    frame = aggregate_to_spm_units(u, persons)
    res = compute_poverty_impact(frame, tax_year=yr, scenarios=("act163_sunset",),
                                 extrapolate_thresholds=True)
    s = res.by_state.iloc[0]
    hoh = res.by_household_type
    row = {
        "tax_year": yr,
        "poverty_rate_renewed": float(s["poverty_rate_baseline"]),
        "poverty_rate_expired": float(s["poverty_rate_act163_sunset"]),
        "persons_into_poverty": float(s["persons_lifted_act163_sunset"]),
        "poverty_gap_increase_$M": float(s["gap_closed_act163_sunset_$"]) / 1e6,
    }
    # Children newly below the threshold (SPM-unit child counts).
    f = res.units
    thr = f["spm_threshold"].to_numpy(dtype=float)
    newly = ((f["spm_resources_act163_sunset"].to_numpy(dtype=float) < thr)
             & ~(f["spm_resources"].to_numpy(dtype=float) < thr))
    kids = f["n_children"].fillna(0).to_numpy(dtype=float)
    row["children_into_poverty"] = float((kids * f["weight"].to_numpy(dtype=float))[newly].sum())
    # SPM household types: head_of_household = one adult with children.
    for _, r in hoh.iterrows():
        row[f"persons_into_poverty_{r['filing_status']}"] = float(r["persons_lifted_act163_sunset"])
        row[f"poverty_rate_renewed_{r['filing_status']}"] = float(r["poverty_rate_baseline"])
        row[f"poverty_rate_expired_{r['filing_status']}"] = float(r["poverty_rate_act163_sunset"])
    return pd.DataFrame([row])


def _print_summary(rev: pd.DataFrame, cal: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    print("\nCalibration and checks:")
    print(cal.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    print("\nState credits, $M (expanded = Act 163 renewed; reverted = current law from TY2028):")
    cols = ["tax_year", "eitc_expanded_$M", "eitc_reverted_$M", "food_expanded_$M", "food_reverted_$M",
            "eitc_loss_$M", "food_loss_$M", "total_loss_$M"]
    print(rev[cols].to_string(index=False, float_format=lambda v: f"{v:,.1f}"))


if __name__ == "__main__":
    logging.disable(logging.WARNING)
    run()
