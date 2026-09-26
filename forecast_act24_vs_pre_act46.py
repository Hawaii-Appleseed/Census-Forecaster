"""Act 24 (SB 3125 CD2) vs a TRUE pre-Act-46 baseline — ITEP-reconciliation.

Usage:
  python forecast_act24_vs_pre_act46.py

Why this script exists
----------------------
`forecast_sb3125_vs_fy26base.py` measures Act 24 against **Act 46 law frozen
at TY2026**. That baseline already banks Act 46's first two phase-in steps:
the standard deduction has already gone $4,400 -> $8,800 (TY2024) -> $16,000
(TY2026) for joint filers, and the TY2025 bracket vintage is already in
force. So its "total cost" (-$1.85B over 2027-2031) is *not* the cost of
Hawaii's income tax cuts — it is only the slice still in the future as of
2026.

ITEP's widely-cited ~$1.2-1.4B/yr figure for Act 46 is measured against
**pre-Act-46 (2017) law**. This script computes that frame so the two can be
reconciled, and decomposes the total into who-did-what:

    2017 law  --(A)-->  frozen TY2026  --(B)-->  Act 46 TY-yr  --(C)-->  Act 24 TY-yr
              already                  remaining              Act 24
              banked                   Act 46                 increment
              by 2026                  phase-ins

    Total vs pre-Act-46  =  A + B + C

Hawaii does not index brackets or the standard deduction for inflation, so
the pre-Act-46 counterfactual is the 2018 schedule held at its **nominal**
values against projected TY-yr incomes. That nominal freeze is precisely why
the measured cost grows so steeply across the window.

Deduction basis
---------------
All four systems are scored on target-year itemized-deduction parameters, so
the only thing differing between them is statute (brackets / SD / personal
exemption). The one exception is the frozen-TY2026 tie-out column, which
mirrors `forecast_sb3125_vs_fy26base.py` exactly (TY2026 deduction scales)
so this script's output can be validated against the published table.

Requires data/artifacts/sb3125_calibrated_base.pkl (run
forecast_sb3125_enhanced.py --cd 2 first).

Outputs:
  runs/act24_vs_pre_act46/decomposition.csv
"""
from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

REPO = Path(__file__).parent

import numpy as np
import pandas as pd

from tax_modeler.pipeline import compute_base_tax as _compute_base_tax
from tax_modeler.pipeline import enrich_for_credits as _enrich_for_credits
from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry, TaxSystemConfig
from tax_modeler.scenarios.top_income_synthesis import (
    synthesize_top_filers, rescale_synthetic_tail_to_tax_target,
    redistribute_mid_high_incomes,
)
from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
from tax_modeler.scenarios.quintile_analysis import per_unit_tax
from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year

MID_ALPHA       = 1.5
MID_TOP_PREMIUM = 0.010

from _forecast_common import CALIBRATED_PKL, TARGET_YEARS  # noqa: E402

# Published vs-frozen-TY2026 totals from SB3125_CD1_FORECAST.md (Sept 25, 2026 scoring-path
# fixes rerun), used as a tie-out check on the frozen column.
PUBLISHED_VS_FROZEN = {2027: -183.4, 2028: -204.5, 2029: -437.9, 2030: -462.0, 2031: -494.1}


def _cfg_2017(yr: int) -> TaxSystemConfig:
    """Pre-Act-46 (2017) law, nominal-frozen, scored on TY-*yr* incomes."""
    return TaxSystemConfig(
        name=f"pre_act46_2017law_{yr}",
        year=yr,
        bracket_year=2018,             # 2017 law bracket schedule
        standard_deduction_year=2018,  # $4,400 joint / $2,200 single, nominal
        personal_exemption=1144,       # pre-Act-46 personal exemption
        description=f"Pre-Act 46 (2017 law) held nominal, TY {yr} incomes",
    )


def main() -> None:
    if not CALIBRATED_PKL.exists():
        print(f"ERROR: {CALIBRATED_PKL} not found. Run forecast_sb3125_enhanced.py --cd 2 first.")
        sys.exit(1)

    print("Loading calibrated base...", flush=True)
    from tax_modeler.artifacts import load_calibrated_base
    base, cal_ded_params, cal_meta = load_calibrated_base(CALIBRATED_PKL)
    cal_tax_year = int(cal_meta.get("tax_year", 2023))

    units = redistribute_mid_high_incomes(base, pareto_alpha=MID_ALPHA)
    units = synthesize_top_filers(units, pareto_alpha=MID_ALPHA)
    units = _enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)
    units = _compute_base_tax(units, deduction_params=cal_ded_params, tax_year=cal_tax_year)
    units, tail_k = rescale_synthetic_tail_to_tax_target(units)
    units = _compute_base_tax(units, deduction_params=cal_ded_params, tax_year=cal_tax_year)
    print(f"  tail_k={tail_k:.4f}", flush=True)

    calc = TaxCalculator()
    rows = []

    for yr in TARGET_YEARS:
        print(f"  TY {yr}...", flush=True)

        projected, _fwd = project_and_recalibrate(
            units,
            target_year=yr,
            use_forward_targets=True,
            use_soi_anchor=True,
            soi_year=2022,
            hawaii_capgain_adjustment=0.95,
            use_cbo_aging=True,
            cbo_vintage="2025-01",
            top_premium_pct=MID_TOP_PREMIUM,
            top_bracket_differential=0.025,
            method="ensemble",
        )

        w = projected["weight"].to_numpy(dtype=float)

        ded_params_2026   = scale_deduction_params_for_target_year(2026, geoid="15003")
        ded_params_target = scale_deduction_params_for_target_year(yr,   geoid="15003")

        # Target-year deduction basis: used for all statutory comparisons.
        proj_target = _compute_base_tax(
            projected.copy(), tax_year=yr, deduction_params=ded_params_target,
        )
        # TY2026 deduction basis: only for the published-table tie-out.
        proj_frozen26 = _compute_base_tax(
            projected.copy(), tax_year=2026, deduction_params=ded_params_2026,
        )

        # --- the four legal regimes, all on target-year incomes -------------
        tax_2017    = per_unit_tax(proj_target, _cfg_2017(yr), calc)
        tax_frozen  = per_unit_tax(proj_target, TaxSystemRegistry.get_hb2306_orig_system(yr), calc)
        tax_act46   = per_unit_tax(proj_target, TaxSystemRegistry.get_act46_system(yr), calc)
        tax_act24   = per_unit_tax(proj_target, TaxSystemRegistry.get_sb3125_cd2_system(yr), calc)

        # Tie-out: reproduce the published vs-frozen number exactly.
        tax_frozen_tieout = per_unit_tax(
            proj_frozen26, TaxSystemRegistry.get_hb2306_orig_system(yr), calc,
        )
        tieout = float(((tax_act24 - tax_frozen_tieout) * w).sum() / 1e6)

        M = lambda a: float((a * w).sum() / 1e6)  # noqa: E731

        rows.append({
            "tax_year": yr,
            "A_act46_banked_by_2026": M(tax_frozen - tax_2017),
            "B_act46_remaining_phaseins": M(tax_act46 - tax_frozen),
            "C_act24_increment": M(tax_act24 - tax_act46),
            "total_vs_pre_act46": M(tax_act24 - tax_2017),
            "memo_vs_frozen_2026": M(tax_act24 - tax_frozen),
            "tieout_vs_frozen_published_basis": tieout,
            "tieout_published": PUBLISHED_VS_FROZEN[yr],
            "tieout_diff": tieout - PUBLISHED_VS_FROZEN[yr],
        })

    df = pd.DataFrame(rows)

    out_dir = REPO / "runs" / "act24_vs_pre_act46"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "decomposition.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)

    print("\n" + "=" * 100, flush=True)
    print("ACT 24 vs PRE-ACT-46 (2017 law) — bracket + SD + personal exemption, static", flush=True)
    print("Negative = revenue foregone by the State ($M)", flush=True)
    print("=" * 100, flush=True)

    hdr = (f"{'Year':<6} {'A: Act46':>12} {'B: Act46':>12} {'C: Act24':>12} "
           f"{'TOTAL vs':>13} {'memo: vs':>12}")
    print(hdr, flush=True)
    print(f"{'':<6} {'banked<=2026':>12} {'remaining':>12} {'increment':>12} "
          f"{'pre-Act-46':>13} {'frozen 2026':>12}", flush=True)
    print("-" * 100, flush=True)
    for r in rows:
        print(f"{r['tax_year']:<6} {r['A_act46_banked_by_2026']:>+11.1f}M "
              f"{r['B_act46_remaining_phaseins']:>+11.1f}M "
              f"{r['C_act24_increment']:>+11.1f}M "
              f"{r['total_vs_pre_act46']:>+12.1f}M "
              f"{r['memo_vs_frozen_2026']:>+11.1f}M", flush=True)
    print("-" * 100, flush=True)
    print(f"{'5yr':<6} {df['A_act46_banked_by_2026'].sum():>+11.1f}M "
          f"{df['B_act46_remaining_phaseins'].sum():>+11.1f}M "
          f"{df['C_act24_increment'].sum():>+11.1f}M "
          f"{df['total_vs_pre_act46'].sum():>+12.1f}M "
          f"{df['memo_vs_frozen_2026'].sum():>+11.1f}M", flush=True)

    print("\n--- TIE-OUT vs published SB3125_CD1_FORECAST.md vs-frozen table ---", flush=True)
    print(f"{'Year':<6} {'this run':>12} {'published':>12} {'diff':>10}", flush=True)
    for r in rows:
        print(f"{r['tax_year']:<6} {r['tieout_vs_frozen_published_basis']:>+11.1f}M "
              f"{r['tieout_published']:>+11.1f}M {r['tieout_diff']:>+9.1f}M", flush=True)
    print(f"{'5yr':<6} {df['tieout_vs_frozen_published_basis'].sum():>+11.1f}M "
          f"{df['tieout_published'].sum():>+11.1f}M "
          f"{df['tieout_diff'].sum():>+9.1f}M", flush=True)

    print(f"\nSaved: {out_dir / 'decomposition.csv'}", flush=True)


if __name__ == "__main__":
    main()
