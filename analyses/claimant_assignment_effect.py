#!/usr/bin/env python3
"""What the parent-first claimant assignment does to the raw tax units, against the bundled benchmarks.

Builds the 2024 1-year Hawaii PUMS units (the working-family step's population) under the legacy assignment and
under each parent-link policy (``tax_modeler.units.claimants.PARENT_LINKS``), enriches them as the chain does, and
measures, on the model's own TY2024 basis (2024-dollar incomes, TY2024 rules) and on a TY2022 basis (incomes
deflated by CPI-U to 2022 dollars, TY2022 rules: the vintage of the IRS SOI Table 2 cells):

* returns by filing status, returns with dependents, dependents, and the persons the units cover;
* federal EITC-eligible units by number of qualifying children and by AGI class;
* returns with the nonrefundable CTC and with the ACTC, HoH returns by AGI class;
* for the 5-year production cache: filing status and dependents of the cache on disk against a given old cache.

Read-only on the model: writes CSVs to ``--out``. Run from the repo root (about 6 minutes, under 2 GB):

    CF_MAX_WORKERS=2 uv run python analyses/claimant_assignment_effect.py --out /tmp/claimant_effect
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts")]

PUMS_1YR = REPO / "packages" / "data" / "raw" / "pums_2024_1yr"
CPI_2022, CPI_2024 = 292.655, 313.689       # CPI-U annual averages (BLS), as in the diagnosis
AGI_LABELS = ["<$1", "$1-10K", "$10-25K", "$25-50K", "$50-75K", "$75K+"]
AGI_BINS = [-1e12, 1, 10_000, 25_000, 50_000, 75_000, 1e12]
SOI_EITC_BY_KIDS = {0: 26_900, 1: 27_820, 2: 18_460, 3: 11_790}     # IRS SOI Hawaii TY2022 published totals
SOI_EITC_TOTAL_2022 = 84_970
IRS_EITC_TY2024 = 81_800                                              # from the diagnosis (IRS TY2024 claims), not re-fetched
INCOME_COLS = ["income", "federal_agi", "federal_earned_income", "earned_income", "investment_income",
               "total_cash_income", "primary_wagp", "primary_semp", "primary_intp", "primary_div", "primary_retp",
               "primary_ssp", "primary_ssp_full", "primary_ssip", "primary_pap", "primary_oip",
               "secondary_wagp", "secondary_semp", "secondary_intp", "secondary_div", "secondary_retp",
               "secondary_ssp", "secondary_ssp_full", "secondary_ssip", "secondary_pap", "secondary_oip"]


def soi_table2() -> dict:
    p = REPO / "packages/tax_modeler/src/tax_modeler/data/calibration/irs_soi_hi_table2.json"
    return json.loads(p.read_text())["years"]["2022"]


def build(mode: str, link: str):
    """Chain-style 2024 1-year units (raw, enriched, geography) under one assignment policy."""
    import functools

    import poverty_impact_report as pir
    import tax_modeler.base as base

    real = base.build_raw_units
    base.build_raw_units = functools.partial(real, claimant_assignment=mode, parent_link=link)
    try:
        units, persons = pir._load_units(PUMS_1YR, False, with_replicate_weights=False)
    finally:
        base.build_raw_units = real
    return units, persons


def credits(units: pd.DataFrame, tax_year: int, scale: float) -> pd.DataFrame:
    """Federal EITC and CTC on the units at ``tax_year`` rules, incomes multiplied by ``scale``."""
    from tax_modeler.credits.eitc import _EITC_PARAMS_BY_YEAR, calculate_eitc_for_tax_units
    from tax_modeler.projection.tax_unit_projector import _recalculate_ctc

    u = units.copy()
    if scale != 1.0:
        for c in INCOME_COLS:
            if c in u.columns:
                u[c] = u[c].astype(float) * scale
    supported = sorted(_EITC_PARAMS_BY_YEAR)
    extrapolate = tax_year > supported[-1]
    u = _recalculate_ctc(u, tax_year=tax_year, extrapolate=extrapolate)
    u = calculate_eitc_for_tax_units(u, tax_year=tax_year, extrapolate=extrapolate)
    return u


def coverage(units: pd.DataFrame, persons: pd.DataFrame) -> dict:
    """Persons in a unit (as a filer or a dependent) over all persons in the households, headcount and PUMS-weighted."""
    pid = persons["SERIALNO"].astype(str) + "_" + persons["SPORDER"].astype(int).astype(str)
    covered = set(units["primary_filer_id"].astype(str)) | set(units["secondary_filer_id"].dropna().astype(str))
    for d in units["dependents"]:
        covered |= {str(x) for x in d}
    inn = pid.isin(covered).to_numpy()
    w = persons["PWGTP"].to_numpy(float)
    return {"persons": int(len(persons)), "covered": int(inn.sum()),
            "covered_pwgtp": float(w[inn].sum()), "all_pwgtp": float(w.sum())}


def measure(units: pd.DataFrame, persons: pd.DataFrame, label: str) -> list[dict]:
    rows: list[dict] = []

    def add(basis: str, measure_: str, key: str, value: float) -> None:
        rows.append({"variant": label, "basis": basis, "measure": measure_, "key": key, "value": float(value)})

    w = units["weight"].to_numpy(float)
    fs = units["filing_status"].astype(str).to_numpy()
    for s in ("single", "married_filing_jointly", "head_of_household", "married_filing_separately"):
        add("units", "returns_by_status", s, w[fs == s].sum())
    nd = units["num_dependents"].to_numpy(float)
    add("units", "returns_with_dependents", "all", w[nd > 0].sum())
    add("units", "dependents_unit_weight", "all", (w * nd).sum())
    for k, v in coverage(units, persons).items():
        add("units", "coverage", k, v)
    # how many dependents are EITC/CTC-qualifying children
    # HoH by AGI class (SOI TY2022 basis: deflated)
    for basis, year, scale in (("TY2024", 2024, 1.0), ("TY2022cpi", 2022, CPI_2022 / CPI_2024)):
        u = credits(units, year, scale)
        wu = u["weight"].to_numpy(float)
        agi = u["federal_agi"].to_numpy(float)
        cls = pd.cut(agi, AGI_BINS, labels=AGI_LABELS, right=False)
        elig = u["eitc_amount"].to_numpy(float) > 0
        k = np.minimum(u["eitc_qualifying_children"].to_numpy(int), 3)
        add(basis, "eitc_eligible", "all", wu[elig].sum())
        add(basis, "eitc_usd_m", "all", (wu * u["eitc_amount"].to_numpy(float)).sum() / 1e6)
        for b in range(4):
            add(basis, "eitc_eligible_by_kids", str(b), wu[elig & (k == b)].sum())
            for lab in AGI_LABELS:
                add(basis, "eitc_eligible_by_kids_agi", f"{b}|{lab}", wu[elig & (k == b) & (cls == lab)].sum())
        tot, ref = u["ctc_total"].to_numpy(float), u["ctc_refundable"].to_numpy(float)
        add(basis, "ctc_returns_nonrefundable", "all", wu[(tot - ref) > 0].sum())
        add(basis, "ctc_returns_actc", "all", wu[ref > 0].sum())
        add(basis, "ctc_usd_m_nonrefundable", "all", (wu * np.maximum(tot - ref, 0)).sum() / 1e6)
        add(basis, "ctc_usd_m_actc", "all", (wu * ref).sum() / 1e6)
        hoh = fs == "head_of_household"
        for lab in AGI_LABELS:
            add(basis, "hoh_by_agi", lab, wu[hoh & (cls == lab)].sum())
        add(basis, "eitc_with_children", "all", wu[elig & (k > 0)].sum())
        add(basis, "units_with_eitc_children", "all", wu[u["eitc_qualifying_children"].to_numpy(int) > 0].sum())
    return rows


def cache_compare(old_path: Path, new_path: Path) -> list[dict]:
    """Filing status, dependents and coverage of two raw 5-year tax-unit caches."""
    rows = []
    for label, path in (("legacy", old_path), ("parent_first", new_path)):
        u = pd.read_parquet(path, columns=["filing_status", "weight", "num_dependents", "primary_filer_id",
                                           "secondary_filer_id", "dependents"])
        w = u["weight"].to_numpy(float)
        fs = u["filing_status"].astype(str).to_numpy()
        for s in ("single", "married_filing_jointly", "head_of_household", "married_filing_separately"):
            rows.append({"variant": label, "basis": "cache5yr", "measure": "returns_by_status", "key": s,
                         "value": float(w[fs == s].sum())})
        nd = u["num_dependents"].to_numpy(float)
        rows.append({"variant": label, "basis": "cache5yr", "measure": "returns_with_dependents", "key": "all",
                     "value": float(w[nd > 0].sum())})
        rows.append({"variant": label, "basis": "cache5yr", "measure": "dependents_unit_weight", "key": "all",
                     "value": float((w * nd).sum())})
        rows.append({"variant": label, "basis": "cache5yr", "measure": "units", "key": "n", "value": float(len(u))})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variants", default="legacy,sfn,sfn+rule_nearest,rule_low,rule_nearest,rule_high")
    ap.add_argument("--cache-old", type=Path, help="raw 5-year unit cache built with the legacy assignment")
    ap.add_argument("--cache-new", type=Path, help="raw 5-year unit cache built with the parent-first assignment")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if args.cache_old and args.cache_new:
        rows += cache_compare(args.cache_old, args.cache_new)
    for v in args.variants.split(","):
        mode, link = ("legacy", "sfn") if v == "legacy" else ("parent_first", v)
        print(f"building {v}", flush=True)
        units, persons = build(mode, link)
        rows += measure(units, persons, v)
        units.to_pickle(args.out / f"units_{v.replace('+', '_')}.pkl")
    pd.DataFrame(rows).to_csv(args.out / "claimant_effect.csv", index=False)
    print(f"wrote {args.out / 'claimant_effect.csv'}")


if __name__ == "__main__":
    main()
