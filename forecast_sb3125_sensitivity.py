"""SB 3125 fiscal-impact sensitivity analysis.

Runs three LOW/MID/HIGH parameter combinations varying top-income synthesis
(Pareto alpha) and REEC demand scenario (post-OBBBA Section 25D impact).

Usage:
  python forecast_sb3125_sensitivity.py --cd 1   # SB 3125 CD1 (default)
  python forecast_sb3125_sensitivity.py --cd 2   # SB 3125 CD2

Replaces the two separate scripts:
  forecast_sb3125_cd1_sensitivity.py  (identical except TaxSystemRegistry call)
  forecast_sb3125_cd2_sensitivity.py  (identical except TaxSystemRegistry call)

Combinations:
  LOW    = (alpha=1.6, reec=obbba_severe)  -- conservative for revenue
  MID    = (alpha=1.5, reec=obbba_mid)     -- recommended default
  HIGH   = (alpha=1.4, reec=pre_obbba)     -- aggressive for revenue

Requires tax-unit cache at data/artifacts/tax_units_cache.parquet (run forecast_sb3125.py first).

Output:
  /tmp/sb3125_cd{N}_sensitivity_2027_2031.csv
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from _forecast_common import (
    TARGET_YEARS, load_cached_units, parse_cd_args, silence_noise,
)

SCENARIOS = [
    ("LOW",  1.6, "obbba_severe"),
    ("MID",  1.5, "obbba_mid"),
    ("HIGH", 1.4, "pre_obbba"),
]


def run_one_scenario(
    base_calibrated, *, label, pareto_alpha, reec_scenario, cd,
    _compute_base_tax, project_tax_units_forward,
    TaxCalculator, TaxSystemRegistry, compare_systems,
    compute_credit_overlay, synthesize_top_filers, validate_top_synthesis,
    ded_params=None, cal_tax_year=2023,
):
    import time
    print(f"\n{'='*70}\nSCENARIO {label}: pareto_alpha={pareto_alpha}, reec={reec_scenario}\n{'='*70}", flush=True)

    if ded_params is None:
        from tax_modeler.artifacts import load_canonical_deduction_params
        ded_params = load_canonical_deduction_params()

    # Re-score on the SAME deduction basis the base was calibrated under —
    # bare _compute_base_tax (SD-only) made the tail validation inconsistent (C3).
    units = synthesize_top_filers(base_calibrated, pareto_alpha=pareto_alpha)
    units = _compute_base_tax(units, deduction_params=ded_params, tax_year=cal_tax_year)
    v = validate_top_synthesis(units)
    print(f"  Synthesis: {v['filers_1m_plus']:,.0f} filers @ $1M+ "
          f"({100*v['filer_target_ratio']:.1f}%), ${v['tax_1m_plus_$M']:,.1f}M tax "
          f"({100*v['tax_target_ratio']:.1f}%)", flush=True)

    get_scenario_system = (
        TaxSystemRegistry.get_sb3125_cd1_system if cd == "1"
        else TaxSystemRegistry.get_sb3125_cd2_system
    )

    calc = TaxCalculator()
    rows = []
    for year in TARGET_YEARS:
        t0 = time.perf_counter()
        projected = project_tax_units_forward(units, target_year=year, method="ensemble")

        baseline_cfg = TaxSystemRegistry.get_act46_system(year)
        scenario_cfg = get_scenario_system(year)
        cmp = compare_systems(projected, baseline_cfg, scenario_cfg, calculator=calc)
        diff_row = cmp[cmp["system"] == "Difference"].iloc[0]
        bracket_delta = float(diff_row["revenue_millions"])

        credit = compute_credit_overlay(year, reec_demand_scenario=reec_scenario)
        total = bracket_delta + credit["total_credit_savings_$M"]

        rows.append({
            "scenario":          label,
            "tax_year":          year,
            "bracket_delta_$M":  round(bracket_delta, 2),
            "reec_savings_$M":   credit["reec_savings_$M"],
            "cgec_savings_$M":   credit["cgec_savings_$M"],
            "tcra_savings_$M":   credit["tcra_savings_$M"],
            "credit_total_$M":   credit["total_credit_savings_$M"],
            "total_impact_$M":   round(total, 2),
        })
        print(f"  TY {year}: bracket={bracket_delta:+.1f}M  "
              f"credit={credit['total_credit_savings_$M']:+.1f}M  "
              f"total={total:+.1f}M  ({time.perf_counter()-t0:.1f}s)", flush=True)
    return rows


if __name__ == "__main__":
    silence_noise()

    args = parse_cd_args(__doc__)
    CD = args.cd
    OUT_CSV = Path(f"/tmp/sb3125_cd{CD}_sensitivity_2027_2031.csv")

    try:
        import time
        import pandas as pd

        print(f"SB 3125 CD{CD} sensitivity analysis", flush=True)
        print("Importing modules...", flush=True)
        from tax_modeler.base import build_calibrated_base
        from tax_modeler.pipeline import compute_base_tax as _compute_base_tax
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        from tax_modeler.config.tax_system_config import (
            TaxCalculator, TaxSystemRegistry, compare_systems,
        )
        from tax_modeler.scenarios.sb3125_cd1_credits import compute_credit_overlay
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers, validate_top_synthesis,
        )

        wall_start = time.perf_counter()

        units = load_cached_units(CD, built_by="forecast_sb3125_sensitivity.py")

        print("Enriching + base tax + calibrating (one-time)...", flush=True)
        # Calibrate and re-score on the canonical itemized-deduction basis —
        # bare SD-only scoring diverged from downstream scoring (C3).
        calibrated_base, CAL_DED_PARAMS = build_calibrated_base(units)

        all_rows = []
        for label, alpha, reec in SCENARIOS:
            all_rows.extend(run_one_scenario(
                calibrated_base, label=label, pareto_alpha=alpha, reec_scenario=reec, cd=CD,
                _compute_base_tax=_compute_base_tax,
                project_tax_units_forward=project_tax_units_forward,
                TaxCalculator=TaxCalculator,
                TaxSystemRegistry=TaxSystemRegistry,
                compare_systems=compare_systems,
                compute_credit_overlay=compute_credit_overlay,
                synthesize_top_filers=synthesize_top_filers,
                validate_top_synthesis=validate_top_synthesis,
                ded_params=CAL_DED_PARAMS, cal_tax_year=2023,
            ))

        df = pd.DataFrame(all_rows)
        df.to_csv(OUT_CSV, index=False)

        print("\n" + "=" * 90, flush=True)
        print(f"SB 3125 CD{CD} FISCAL IMPACT SENSITIVITY ($M, vs Act 46 baseline)", flush=True)
        print("=" * 90, flush=True)

        pv = df.pivot(index="tax_year", columns="scenario", values="total_impact_$M")
        pv = pv[["LOW", "MID", "HIGH"]]
        pv.loc["5yr cum"] = pv.sum()
        print("\nTotal annual fiscal impact ($M):")
        print(pv.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        pv_b = df.pivot(index="tax_year", columns="scenario", values="bracket_delta_$M")[["LOW", "MID", "HIGH"]]
        pv_b.loc["5yr cum"] = pv_b.sum()
        print("\nBracket-change impact only ($M):")
        print(pv_b.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        pv_c = df.pivot(index="tax_year", columns="scenario", values="credit_total_$M")[["LOW", "MID", "HIGH"]]
        pv_c.loc["5yr cum"] = pv_c.sum()
        print("\nCredit-cap impact only ($M):")
        print(pv_c.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        print(f"\nSaved: {OUT_CSV}", flush=True)
        print(f"Total elapsed: {time.perf_counter() - wall_start:.1f}s", flush=True)

    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
