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

Population and deduction basis
------------------------------
The four regimes are scored on the Act 24 page's own population and
projection (``scenarios.act24_population``, MID: Pareto alpha 1.5, top-income
premium 1.0%/yr), so the only thing differing between them is statute
(brackets / SD / personal exemption), and column C is exactly the page's
static bracket change — the ``tieout_act24_page`` column checks that every
year. Until September 28, 2026 this script built its own population
(``redistribute_mid_high_incomes`` + ``project_and_recalibrate``, as
`forecast_sb3125_vs_fy26base.py` still does), on which C ran about 70% above
the page's figure for the same comparison.

So the ``memo_vs_frozen_2026`` column no longer reproduces that script's
published vs-frozen table, which is still computed on the other population.

Capital gains
-------------
The four regimes are scored on the DOTAX-anchored gains base
(``calibration.cg_anchor``, anchored on this population each year, which
reproduces the Act 24 run's own scale factors) with the statutory
alternative tax (HRS §235-51(f)), as on the Act 24 page, the tax simulator
and the capital-gains page.

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

import pandas as pd

from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry, TaxSystemConfig
from tax_modeler.scenarios.quintile_analysis import per_unit_tax
from tax_modeler.calibration.cg_anchor import anchor_factors, apply_anchor_factors
from tax_modeler.scenarios.act24_population import build_units, project_units, statute

MID_ALPHA       = 1.5
MID_TOP_PREMIUM = 0.010

from _forecast_common import CALIBRATED_PKL, RUNS_DIR, TARGET_YEARS  # noqa: E402

# The Act 24 run's static bracket change (MID), which column C must equal:
# same population, same gains base, same statute, same two systems.
ENHANCED = RUNS_DIR / "sb3125_cd2_enhanced" / "enhanced.csv"


def _cfg_2017(yr: int) -> TaxSystemConfig:
    """Pre-Act-46 (2017) law, nominal-frozen, scored on TY-*yr* incomes."""
    return TaxSystemConfig(
        name=f"pre_act46_2017law_{yr}",
        year=yr,
        bracket_year=2018,             # 2017 law bracket schedule
        standard_deduction_year=2018,  # $4,400 joint / $2,200 single, nominal
        personal_exemption=1144,       # pre-Act-46 personal exemption
        cg_alt_tax="statute",          # HRS §235-51(f), as the other regimes here
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

    units, tail_k = build_units(base, alpha=MID_ALPHA, ded_params=cal_ded_params,
                                cal_tax_year=cal_tax_year)
    print(f"  tail_k={tail_k:.4f}", flush=True)

    page = pd.read_csv(ENHANCED) if ENHANCED.exists() else None
    if page is None:
        print(f"  NOTE: {ENHANCED} missing, so column C is not checked against the Act 24 "
              f"page; run forecast_sb3125_enhanced.py --cd 2.", flush=True)

    calc = TaxCalculator()
    rows = []

    for yr in TARGET_YEARS:
        print(f"  TY {yr}...", flush=True)

        # The Act 24 page's own population and projection (MID), then its
        # gains base: column C is then the page's static bracket change.
        projected = project_units(units, year=yr, top_premium=MID_TOP_PREMIUM)
        w = projected["weight"].to_numpy(dtype=float)
        proj_target = apply_anchor_factors(projected, anchor_factors(projected, yr))

        # --- the four legal regimes, all on target-year incomes -------------
        tax_2017    = per_unit_tax(proj_target, _cfg_2017(yr), calc)
        tax_frozen  = per_unit_tax(proj_target, statute(TaxSystemRegistry.get_hb2306_orig_system)(yr), calc)
        tax_act46   = per_unit_tax(proj_target, statute(TaxSystemRegistry.get_act46_system)(yr), calc)
        tax_act24   = per_unit_tax(proj_target, statute(TaxSystemRegistry.get_sb3125_cd2_system)(yr), calc)

        M = lambda a: float((a * w).sum() / 1e6)  # noqa: E731

        rows.append({
            "tax_year": yr,
            "A_act46_banked_by_2026": M(tax_frozen - tax_2017),
            "B_act46_remaining_phaseins": M(tax_act46 - tax_frozen),
            "C_act24_increment": M(tax_act24 - tax_act46),
            "total_vs_pre_act46": M(tax_act24 - tax_2017),
            "memo_vs_frozen_2026": M(tax_act24 - tax_frozen),
            "tieout_act24_page": (None if page is None else float(
                page[(page.scenario == "MID") & (page.tax_year == yr)].iloc[0]["bracket_delta_static_$M"])),
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

    if page is not None:
        print("\n--- TIE-OUT: column C is the Act 24 page's static bracket change ---", flush=True)
        print(f"{'Year':<6} {'C here':>12} {'Act 24 page':>12} {'diff':>10}", flush=True)
        worst = 0.0
        for r in rows:
            d = r["C_act24_increment"] - r["tieout_act24_page"]
            worst = max(worst, abs(d))
            print(f"{r['tax_year']:<6} {r['C_act24_increment']:>+11.2f}M "
                  f"{r['tieout_act24_page']:>+11.2f}M {d:>+9.3f}M", flush=True)
        # The page rounds to 2 decimals; anything above that is a real difference.
        if worst > 0.01:
            print(f"\nWARNING: column C differs from the Act 24 page by up to {worst:.3f}M. "
                  f"Both must score the same population, gains base and systems; re-run "
                  f"forecast_sb3125_enhanced.py --cd 2 if it is stale.", flush=True)

    print(f"\nSaved: {out_dir / 'decomposition.csv'}", flush=True)


if __name__ == "__main__":
    main()
