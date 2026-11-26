#!/usr/bin/env python3
"""Replays of the working-family EITC path for two validation tests. Analysis only: nothing is written to
runs/ or site/, and model behaviour is changed in memory only (module attributes patched inside the process).

``targets``  (production population, ACS 2024 1-year): the by-children reweight (lever 3a) and the take-up
    truncation use IRS TY2022 counts held flat. Replays the path for ``legacy`` and ``new`` (re-derived factors)
    under three target sets and reports HI EITC at 40% and claimants for TY2024/2028/2031:
      flat2022  production (reproduces the decomposition replay exactly: the check)
      flat2023  IRS SOI Table 2 TY2023 by-children counts (26,400 / 27,650 / 18,470 / 11,940), held flat
      trend     TY2023 shares, total moving at the measured log-linear trend of Hawaii EITC returns (history CSV)
    The truncation count is scaled with the same ratio production uses (84,010 pooled / 84,970 by-children total).

``backtest`` (ACS 2018-2022 5-year file, income dollar-year patched to 2022): calibrate only on information
    through TY2022 (IRS household rake to the TY2022 table, lever 3a and truncation on TY2022 counts, dollar
    re-peg at TY2022), age to TY2023 and TY2024 with the CBO engine, and report the unscaled federal EITC
    returns and dollars (no DOTAX take-up factor: nothing from TY2023 or later enters).

    CF_MAX_WORKERS=2 uv run python analyses/wfc_targets_backtest.py targets  --variant legacy --out /tmp/wfc_tb
    CF_MAX_WORKERS=2 uv run python analyses/wfc_targets_backtest.py backtest --variant new    --out /tmp/wfc_tb
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "chain"), str(HERE)]

import wfc_projection_decomposition as dec  # noqa: E402  (the variants and the stage statistics)

HISTORY = REPO / "docs/data_source_tests/tax-unit-claimant-assignment_hi_eitc_history.csv"
PUMS_5YR_2018_2022 = REPO / "packages/data/raw/pums"
YEARS = dec.YEARS                      # [2022, 2023, 2024, 2027, ..., 2031]: same order as the chain, for the RNG
KIDS = [0, 1, 2, 3]
PROD_POOLED, PROD_SPLIT_TOTAL = 84_010.0, 84_970.0     # AdminCaseload eitc TY2022 count; sum of the by-children counts


def soi_counts(year: int) -> dict[int, float]:
    h = pd.read_csv(HISTORY)
    return {k: float(h[(h.series == f"fed_eitc_returns_kids{k}") & (h.year == year)].value.iloc[0]) for k in KIDS}


def returns_trend() -> float:
    """Annual log-linear trend of Hawaii federal EITC returns, TY2010-2023 without the pandemic-distorted 2020-21."""
    h = pd.read_csv(HISTORY)
    s = h[h.series == "fed_eitc_returns"].set_index("year").value.drop([2020, 2021])
    return float(np.exp(np.polyfit(s.index.to_numpy(float), np.log(s.to_numpy(float)), 1)[0]) - 1)


@contextlib.contextmanager
def patched_targets(counts: dict[int, float] | None):
    """Swap the by-children counts lever 3a and the truncation read, and the pooled truncation count; None = production."""
    if counts is None:
        yield
        return
    from tax_modeler.calibration import eitc_reweight, takeup_imputation
    from tax_modeler.calibration.admin_caseload import AdminCaseload

    keep = (takeup_imputation.EITC_RETURNS_BY_CHILDREN_HI_TY2022, eitc_reweight.EITC_RETURNS_BY_CHILDREN_HI_TY2022,
            AdminCaseload.target)
    real_target = AdminCaseload.target
    pooled = PROD_POOLED * sum(counts.values()) / PROD_SPLIT_TOTAL

    def target(self, program, year):
        t = real_target(self, program, year)
        return dataclasses.replace(t, count=pooled) if program == "eitc" else t

    takeup_imputation.EITC_RETURNS_BY_CHILDREN_HI_TY2022 = dict(counts)
    eitc_reweight.EITC_RETURNS_BY_CHILDREN_HI_TY2022 = dict(counts)
    AdminCaseload.target = target
    try:
        yield
    finally:
        takeup_imputation.EITC_RETURNS_BY_CHILDREN_HI_TY2022, eitc_reweight.EITC_RETURNS_BY_CHILDREN_HI_TY2022 = keep[:2]
        AdminCaseload.target = keep[2]


def build_base(variant: str, pums_dir: Path):
    import poverty_impact_report as pir
    import tax_modeler.base as tb
    from tax_modeler.units import constructor

    mode, factors = dec.VARIANTS[variant]
    real = tb.build_raw_units
    patched = functools.partial(real, claimant_assignment=mode)
    tb.build_raw_units = lambda p, h, **kw: patched(p, h, **{**kw, "parallel": False})
    saved = dict(constructor.FILING_STATUS_WEIGHT_FACTORS)
    if factors is not None:
        constructor.FILING_STATUS_WEIGHT_FACTORS.update(factors)
    try:
        base, _ = pir._load_units(pums_dir, False, with_replicate_weights=False)
    finally:
        tb.build_raw_units = real
        constructor.FILING_STATUS_WEIGHT_FACTORS.clear()
        constructor.FILING_STATUS_WEIGHT_FACTORS.update(saved)
    print(f"[{variant}] built {len(base):,} units, weight {base['weight'].sum():,.0f}", flush=True)
    return base


def _row(u: pd.DataFrame) -> dict:
    s = dec._stats(u)
    return {"kept_w": s["w"], "fed": s["fed"], **{f"kept_w{b}": s[f"w{b}"] for b in KIDS}}


def run_targets(variant: str, out: Path, trend_g: float, extra_rates: tuple[float, ...] = ()) -> None:
    import forecast_working_family_credits as wfc
    import poverty_impact_report as pir
    from tax_modeler.calibration import dotax_base
    from tax_modeler.calibration import irs_household_rake as rk

    base = build_base(variant, wfc.PUMS_DIR)
    rake = wfc.irs_rake(base, per_replicate=False)
    pre = {}
    for yr in YEARS:
        aged = pir._build_units_for_tax_year(base, yr, project=True, use_cbo_aging=True)
        pre[yr] = rk.apply_household_factors(aged, rake.factors)
        print(f"[{variant}] aged TY{yr}", flush=True)
    c23 = soi_counts(2023)
    scenarios = {"flat2022": lambda y: None, "flat2023": lambda y: c23,
                 "trend": lambda y: {k: v * (1 + trend_g) ** (y - 2023) for k, v in c23.items()}}
    if extra_rates:                       # only these trend rates (a sensitivity run): flat scenarios are not repeated
        scenarios = {f"trend{100 * g:+.1f}": (lambda y, g=g: {k: v * (1 + g) ** (y - 2023) for k, v in c23.items()})
                     for g in extra_rates}
    target_m = dotax_base.eitc_new_credit(wfc.EITC_CAL_YEAR)["dollars_M"]
    rows = []
    for name, fn in scenarios.items():
        recs = {}
        for yr in YEARS:
            with patched_targets(fn(yr)):
                final = pir._apply_credit_takeup(pir._apply_eitc_reweight(pre[yr]), tax_year=yr)
            recs[yr] = _row(final)
        p = target_m * 1e6 / (wfc.EITC_EXPANDED * recs[wfc.EITC_CAL_YEAR]["fed"])
        for yr, r in recs.items():
            rows.append({"variant": variant, "scenario": name, "year": yr, "takeup_factor": p,
                         "hi_eitc_40_$M": p * wfc.EITC_EXPANDED * r["fed"] / 1e6, "claimants": p * r["kept_w"], **r})
        print(f"[{variant}] {name}: p={p:.4f}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / f"{variant}_targets{'_sens' if extra_rates else ''}.csv", index=False)


def run_backtest(variant: str, out: Path) -> None:
    import forecast_working_family_credits as wfc
    import poverty_impact_report as pir
    from tax_modeler.calibration import irs_household_rake as rk
    from tax_modeler.pipeline import apply_credit_takeup
    from tax_modeler.projection import tax_unit_projector as tup

    tup._DEFAULT_BASE_YEAR = 2022                      # the 2018-2022 file's incomes are in 2022 dollars
    real_targets = rk.irs_targets
    rk.irs_targets = functools.partial(real_targets, 2022)      # rake to the TY2022 table, not TY2023
    base = build_base(variant, PUMS_5YR_2018_2022)
    rake = wfc.irs_rake(base, year=2022, per_replicate=False)
    rows = []
    for yr in (2022, 2023, 2024, 2025):
        aged = pir._build_units_for_tax_year(base, yr, project=True, use_cbo_aging=True)
        u2 = pir._apply_eitc_reweight(rk.apply_household_factors(aged, rake.factors))
        # unscaled: the count truncation only; TY2022 is also reported after the dollar re-peg production applies
        raw = apply_credit_takeup(u2, year=2022, programs=("eitc",), scale_eitc_dollars=False)
        rows.append({"variant": variant, "year": yr, "peg": "none", **_row(raw)})
        shown = rows[-1]
        if yr == 2022:
            rows.append({"variant": variant, "year": yr, "peg": "ty2022_dollars", **_row(pir._apply_credit_takeup(u2, tax_year=2022))})
        pool = dec._stats(u2)
        rows.append({"variant": variant, "year": yr, "peg": "pool_after_reweight", "kept_w": pool["w"], "fed": pool["fed"],
                     **{f"kept_w{b}": pool[f"w{b}"] for b in KIDS}})
        print(f"[{variant}] backtest TY{yr} raw returns {shown['kept_w']:,.0f} fed ${shown['fed'] / 1e6:,.1f}M", flush=True)
    rk.irs_targets = real_targets
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / f"{variant}_backtest.csv", index=False)
    (out / f"{variant}_backtest_info.json").write_text(json.dumps({"rake": rake.info}, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["targets", "backtest"])
    ap.add_argument("--variant", choices=["legacy", "new"], required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--trend-rates", type=float, nargs="*", default=[], help="annual changes of the targets (sensitivity run)")
    a = ap.parse_args()
    if a.cmd == "targets":
        run_targets(a.variant, a.out, returns_trend(), tuple(a.trend_rates))
    else:
        run_backtest(a.variant, a.out)


if __name__ == "__main__":
    main()
