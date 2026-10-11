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

Growth-assumption sweep (``--growth-sweep``):
  One run per forward-target growth dial at its low and high sweep value
  from ``data/calibration/forward_growth_assumptions.json`` (2.5%/yr
  low-bracket growth, 1.0%/yr top differential, 0.5%/yr rate drift, 0.5%/yr
  population, 2.5%/yr COR extrapolation), plus the central case, through
  ``project_and_recalibrate`` on the MID path of
  ``forecast_sb3125_vs_fy26base.py`` (SOI anchor, CBO aging, age margins).
  Requires the calibrated base pickle (``forecast_sb3125_enhanced.py`` or
  ``scripts/build_calibrated_base.py``). Output:
  /tmp/sb3125_cd{N}_growth_sweep_2027_2031.csv
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from _forecast_common import (
    TARGET_YEARS, load_cached_units, silence_noise,
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


def run_growth_sweep(cd: str):
    """Bracket delta (bill vs Act 46 frozen at TY2026, statutory re-score)
    per TY under each growth dial's low / central / high value."""
    import time
    import pandas as pd
    from _forecast_common import CALIBRATED_PKL
    from tax_modeler.artifacts import load_calibrated_base
    from tax_modeler.calibration.forward_targets import (
        GROWTH_ASSUMPTIONS, GROWTH_VINTAGE, growth_sensitivity, growth_value,
    )
    from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
    from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    from tax_modeler.scenarios.quintile_analysis import per_unit_tax
    from tax_modeler.scenarios.top_income_synthesis import (
        calibrate_synthetic_tail_to_tax_target, redistribute_mid_high_incomes,
        synthesize_top_filers,
    )
    from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year

    if not CALIBRATED_PKL.exists():
        print(f"ERROR: {CALIBRATED_PKL} not found; run scripts/build_calibrated_base.py first.", flush=True)
        sys.exit(1)
    base, cal_ded_params, cal_meta = load_calibrated_base(CALIBRATED_PKL)
    cal_tax_year = int(cal_meta.get("tax_year", 2023))
    # The MID population exactly as forecast_sb3125_vs_fy26base.py builds it.
    units = redistribute_mid_high_incomes(base, pareto_alpha=1.5)
    units = synthesize_top_filers(units, pareto_alpha=1.5)
    units = enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)

    def score(u):
        return compute_base_tax(u, deduction_params=cal_ded_params, tax_year=cal_tax_year)

    units, tail_k = calibrate_synthetic_tail_to_tax_target(score(units), score=score)
    print(f"  tail_k={tail_k:.4f}; growth assumptions vintage {GROWTH_VINTAGE}", flush=True)
    get_scenario_system = (
        TaxSystemRegistry.get_sb3125_cd1_system if cd == "1"
        else TaxSystemRegistry.get_sb3125_cd2_system
    )
    calc = TaxCalculator()
    dials = list(GROWTH_ASSUMPTIONS["assumptions"])
    cases = [("central", None, growth_value)] + [
        (f"{d}={v:g}", d, v) for d in dials for v in growth_sensitivity(d)
        if v != growth_value(d)
    ]
    rows = []
    for label, dial, value in cases:
        t0 = time.perf_counter()
        kw = {}
        if dial is not None:
            kw = {{"low_growth": "low_growth", "top_differential": "top_bracket_differential",
                   "rate_drift": "rate_drift", "population_growth_rate": "population_growth_rate",
                   "cor_extrapolation_growth": "cor_extrapolation_growth"}[dial]: value}
        for yr in TARGET_YEARS:
            projected, fwd = project_and_recalibrate(
                units, target_year=yr, use_forward_targets=True, use_soi_anchor=True,
                soi_year=2022, hawaii_capgain_adjustment=0.95, use_cbo_aging=True,
                cbo_vintage="2025-01", top_premium_pct=0.010, method="ensemble",
                **({"top_bracket_differential": 0.025} if dial != "top_differential" else {}),
                **kw,
            )
            ded = scale_deduction_params_for_target_year(yr, geoid="15003")
            scored = compute_base_tax(projected.copy(), tax_year=yr, deduction_params=ded)
            w = scored["weight"].to_numpy(dtype=float)
            tax_a = per_unit_tax(scored, TaxSystemRegistry.get_hb2306_orig_system(yr), calc)
            tax_c = per_unit_tax(scored, get_scenario_system(yr), calc)
            rows.append({
                "case": label, "dial": dial or "", "value": value if dial else "",
                "tax_year": yr,
                "baseline_tax_$M": round(float((tax_a * w).sum() / 1e6), 2),
                "bill_delta_$M": round(float(((tax_c - tax_a) * w).sum() / 1e6), 2),
                "filers": round(float(w.sum())),
                "phase1_converged": bool(fwd.calibration_report.converged) if fwd else None,
                "wedge": round(float(fwd.statute_vs_cor_wedge), 4) if fwd else None,
            })
        print(f"  {label}: done ({time.perf_counter()-t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    out = Path(f"/tmp/sb3125_cd{cd}_growth_sweep_2027_2031.csv")
    df.to_csv(out, index=False)
    pv = df.pivot(index="case", columns="tax_year", values="bill_delta_$M")
    pv["5yr"] = pv.sum(axis=1)
    print("\nBill delta vs Act 46 frozen at TY2026 ($M, statutory re-score) by growth dial:")
    print(pv.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)
    print(f"\nSaved: {out}", flush=True)
    return df


if __name__ == "__main__":
    silence_noise()

    import argparse
    _p = argparse.ArgumentParser(description=__doc__)
    _p.add_argument("--cd", choices=["1", "2"], default="1")
    _p.add_argument("--growth-sweep", action="store_true",
                    help="sweep the forward-target growth dials instead of alpha/REEC")
    args = _p.parse_args()
    CD = args.cd
    OUT_CSV = Path(f"/tmp/sb3125_cd{CD}_sensitivity_2027_2031.csv")

    if args.growth_sweep:
        try:
            run_growth_sweep(CD)
        except Exception as e:
            print(f"\nERROR: {e}", flush=True)
            traceback.print_exc()
            sys.exit(1)
        sys.exit(0)

    try:
        import time
        import pandas as pd

        print(f"SB 3125 CD{CD} sensitivity analysis", flush=True)
        print("Importing modules...", flush=True)
        from tax_modeler.pipeline import _enrich_for_credits, _compute_base_tax, _calibrate
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        from tax_modeler.config.tax_system_config import (
            TaxCalculator, TaxSystemRegistry, compare_systems,
        )
        from tax_modeler.scenarios.sb3125_cd1_credits import compute_credit_overlay
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers, validate_top_synthesis,
        )
        from tax_modeler.artifacts import load_canonical_deduction_params

        wall_start = time.perf_counter()

        units = load_cached_units(CD)

        print("Enriching + base tax + calibrating (one-time)...", flush=True)
        # Calibrate and re-score on the canonical itemized-deduction basis —
        # bare _compute_base_tax (SD-only) diverged from downstream scoring (C3).
        CAL_DED_PARAMS = load_canonical_deduction_params()
        units = _enrich_for_credits(units)
        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        calibrated_base = _calibrate(units)

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
