"""SB 3125 fiscal forecast with behavioral response and growth corrections.

Usage:
  python forecast_sb3125_enhanced.py --cd 1   # SB 3125 CD1 (default)
  python forecast_sb3125_enhanced.py --cd 2   # SB 3125 CD2

Replaces:
  forecast_sb3125_cd1_enhanced.py
  forecast_sb3125_cd2_enhanced.py

Improvements over forecast_sb3125.py:

  1. **ETI behavioral response** — per-filer taxable-income elasticity.
     Each filer's marginal rate is computed under both the Act 46 baseline
     and SB 3125 CD{N} schedules; ETI shrinkage is applied based on the
     actual rate change (not a hard-coded 11% → 13%). This correctly
     captures behavior for $350K–$1M MFJ filers (and equivalents) who
     also face rate increases under the bill, not just $1M+ filers.
     Saez/Slemrod/Giertz range: ETI=0.15 (low) / 0.25 (mid) / 0.40 (high).

  2. **Migration response** — Young/Varner top-1% migration elasticity per pp
     top-rate change. Phased in over 5 years from 2027.

  3. **PTE election shift** — Bill creates 4pp incentive for $1M+ pass-through
     income to elect PTE (9%) vs face individual 13%. Reported as a
     deduction from gross bracket revenue.

  4. **Top-income growth premium** — Top earners (>$500K) get an additional
     premium above the median-anchored projection. Anchored to PUMS 2024
     base year (5-year vintage panel inflation-adjusted to 2024 dollars).

  5. **Effective deductions plumbed through** — The bracket comparison
     uses each filer's projection-time effective deduction (greater of
     standard and itemized via expected-value Pease-limited Hawaii rules)
     instead of a fixed-amount itemized-premium subtraction. This is the
     same deduction that drives ``hi_tax_liability`` upstream, so the
     comparison is internally consistent.

  6. **Corporate REEC: §48E vs §25D split** — OBBBA terminated §25D
     (residential) but extended §48E (commercial). Corporate REEC
     baseline now uses the §48E factor (no decay through forecast
     horizon); residential decay still applies to individual REEC.

  7. **REEC refundable / nonrefundable utilization split** — The
     ``reec_eff_share`` parameter is the **nonrefundable** utilization
     rate; refundable claims are always 100% effective. Aggregate
     effective shares are blended per the DOTAX 2023 refundable mix
     (~23% individual, ~89% corporate).

  8. **Recession scenario** — Mild-to-moderate recession with trough
     2027 and gradual recovery through 2031. MID behavioral params.

  9. **Baseline-scaling diagnostic** — Reports both raw and COR-scaled
     bracket delta (microsim baseline ≈ $2.3B; COR FY27 = $3.05B).

CD2-only additions (gated by --cd 2):
  - Round-2 REEC knobs per scenario: pro_rata_elasticity, interpretation,
    dynamic_refundable_share
  - compute_dynamic_agi_eligibility_share: per-year AGI eligibility recomputed
    from projected filer distribution (§235-12.5(a) thresholds are unindexed,
    so nominal income growth erodes them over the forecast horizon)
  - Vintage carryforward simulation (model_carryforward_pool=True)
  - 11 extra diagnostic columns in output CSV

Four integrated scenarios (three behavioral + one recession macro):
    LOW       — eti=0.60, migration=0.15, pte=0.90, alpha=1.7, reec=obbba_severe
    MID       — eti=0.40, migration=0.10, pte=0.70, alpha=1.5, reec=obbba_mid  [no recession]
    HIGH      — eti=0.15, migration=0.05, pte=0.40, alpha=1.4, reec=pre_obbba
    RECESSION — MID behavioral params + moderate recession macro shock
                (−2.0% all-filer income in 2027, −3.5% top-income, partial
                rebound 2028, back to baseline 2029+)

REEC carryforward (§235-12.5(e) 5-year window):
  TY2031 $0 new-claim cap may still be partially offset by carryforward
  utilization from TY2026-2030 nonrefundable claims. Scenario-specific
  estimates: LOW=$4M, MID=$6M, HIGH=$9M, RECESSION=$6M.

Output:
  /tmp/sb3125_cd1_enhanced_2027_2031.csv  (--cd 1)
  /tmp/sb3125_cd2_enhanced_2027_2031.csv  (--cd 2)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from _forecast_common import (
    ARTIFACT_DIR, TARGET_YEARS, load_cached_units, parse_cd_args, silence_noise,
)

# Three calibrated scenarios. Calibration is anchored to (a) the official
# 5yr fiscal-impact estimate of ~$680M, (b) state-level ETI literature
# (Rauh/Shyu 2024 California 13.3% study), (c) PTE rate 9% (Act 50, Hawaii)
# vs bill's 13% individual top → 4pp arbitrage gap, and (d) REEC nonrefundable
# utilization considerations + Hawaii solar saturation.
# Scenarios (effective deductions are computed automatically as
# max(filing-status SD, hi_itemized_deduction) inside compare_systems).
#
# Round-2 knobs (pro_rata_elasticity, interpretation, dynamic_refundable_share)
# are used only by the CD2 code path; CD1 ignores them.
SCENARIOS = [
    {
        "label":  "LOW",
        "alpha":  1.7,                # steeper top tail (less concentration)
        "reec":   "obbba_severe",
        "behav":  "high",             # strong behavioral response (low revenue gain)
        "corp_agi_limit": True,
        "top_premium":     0.003,     # +0.3%/yr: MID – 1.0pp (strong outmigration)
        "reec_eff_share":  0.50,      # IRS §25D 25th-pct in-year offset for HI nonref
        "cgec_growth":     0.010,     # 1%/yr: pre-COVID long-run floor
        "macro_shock":     None,
        "reec_cf_m":       4.0,       # TY2031 REEC carryforward utilization ($M)
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.5,    # strong demand response
        "interpretation":           "A",    # conservative: TY2026 cap binds
        "dynamic_refundable_share": True,
    },
    {
        "label":  "MID",
        "alpha":  1.5,
        "reec":   "obbba_mid",
        "behav":  "mid",              # ETI=0.40, migr=0.10, pte=0.70
        "corp_agi_limit": False,
        "top_premium":     0.010,     # +1.0%/yr: IRS SOI 1.8pp – 0.8pp Hawaii haircut
        "reec_eff_share":  0.65,      # IRS §25D median full-year offset
        "cgec_growth":     0.015,     # 1.5%/yr: pre-COVID average
        "macro_shock":     None,
        "reec_cf_m":       6.0,
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.3,    # mid demand response
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
    {
        "label":  "HIGH",
        "alpha":  1.4,
        "reec":   "pre_obbba",
        "behav":  "low",              # weak behavioral (high revenue gain)
        "corp_agi_limit": False,
        "top_premium":     0.023,     # +2.3%/yr: MID + 1.0pp (national-rate convergence)
        "reec_eff_share":  0.80,      # IRS §25D 75th-pct full-use share
        "cgec_growth":     0.025,     # 2.5%/yr: moderate growth assumption
        "macro_shock":     None,
        "reec_cf_m":       9.0,
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.15,   # weak demand response
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
    {
        # Recession scenario: MID behavioral params + moderate recession macro shock.
        "label":  "RECESSION",
        "alpha":  1.5,                # same as MID
        "reec":   "obbba_mid",
        "behav":  "mid",              # MID behavioral: ETI=0.40, migr=0.10, pte=0.70
        "corp_agi_limit": False,
        "top_premium":     0.013,
        "reec_eff_share":  0.65,
        "cgec_growth":     0.015,     # same as MID
        "macro_shock":     "moderate",
        "reec_cf_m":       6.0,
        # CD2 round-2 knobs
        "pro_rata_elasticity":      0.3,
        "interpretation":           "B",
        "dynamic_refundable_share": True,
    },
]

# Requires the workspace to be installed: `uv sync --all-packages`.
REPO = Path(__file__).parent


def _scenario_worker(scenario_dict, target_years, calibrated_path, cd="1"):
    """Top-level worker (must be picklable) for ProcessPoolExecutor.

    Each worker is a fresh Python process — package imports rely on
    ``tax_modeler`` being installed in the spawned interpreter (it is
    when launched under ``uv run`` or after ``uv pip install -e``).
    """
    from tax_modeler.artifacts import load_calibrated_base
    base, ded_params, meta = load_calibrated_base(calibrated_path)
    return run_one_scenario(
        base, scenario=scenario_dict, target_years=target_years, cd=cd,
        ded_params=ded_params, cal_tax_year=int(meta.get("tax_year", 2023)),
    )


def run_one_scenario(
    base_calibrated, *, scenario, target_years, cd="1",
    ded_params=None, cal_tax_year=2023,
):
    """Run a single scenario across all target years and return per-year rows.

    Imports its dependencies internally so this function is self-contained
    and trivially executable in a worker process (ProcessPoolExecutor).

    ``ded_params``/``cal_tax_year`` are the deduction params and vintage the
    calibrated base was scored under — re-scoring here must use the SAME
    basis or the synthetic-tail rescale derives a wrong ``tail_k`` (C3).
    """
    if ded_params is None:
        from tax_modeler.artifacts import load_canonical_deduction_params
        ded_params = load_canonical_deduction_params()
    import time
    import warnings; warnings.filterwarnings("ignore")
    import logging; logging.disable(logging.WARNING)

    from tax_modeler.pipeline import _compute_base_tax
    from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
    from tax_modeler.config.tax_system_config import (
        TaxCalculator, TaxSystemRegistry, compare_systems,
    )
    from tax_modeler.scenarios.sb3125_cd1_credits import (
        compute_credit_overlay,
        compute_dynamic_agi_eligibility_share,
    )
    from tax_modeler.scenarios.top_income_synthesis import (
        synthesize_top_filers, validate_top_synthesis,
        rescale_synthetic_tail_to_tax_target,
    )
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    from tax_modeler.scenarios.behavioral_response import (
        BehavioralParams,
        apply_behavioral_response,
        apply_top_income_growth_premium,
    )
    from tax_modeler.scenarios.macro_scenarios import apply_macro_recession_shock
    from tax_modeler.scenarios.quintile_analysis import cor_scale_factor_for_year

    get_scenario_system = (
        TaxSystemRegistry.get_sb3125_cd1_system if cd == "1"
        else TaxSystemRegistry.get_sb3125_cd2_system
    )

    label  = scenario["label"]
    alpha  = scenario["alpha"]
    reec   = scenario["reec"]
    behav  = scenario["behav"]
    corp_agi = scenario["corp_agi_limit"]
    top_premium = scenario["top_premium"]
    reec_eff = scenario["reec_eff_share"]
    cgec_g   = scenario["cgec_growth"]
    macro_shock = scenario.get("macro_shock")   # None = baseline macro; "moderate" = recession
    reec_cf_m = scenario.get("reec_cf_m", 0.0)          # TY2031 carryforward utilization

    print(f"\n{'='*78}\nSCENARIO {label}: alpha={alpha} reec={reec} behav={behav} "
          f"corp_agi={corp_agi} top_premium={top_premium:+.3f}\n"
          f"  reec_eff_share={reec_eff} cgec_growth={cgec_g:.3f} "
          f"reec_cf_m={reec_cf_m:.1f}M "
          f"macro_shock={macro_shock or 'none'}", end="", flush=True)
    if cd == "2":
        pro_rata_elasticity      = float(scenario.get("pro_rata_elasticity", 0.0))
        interpretation           = scenario.get("interpretation", "B")
        dynamic_refundable_share = bool(scenario.get("dynamic_refundable_share", False))
        print(f"\n  pro_rata_eta={pro_rata_elasticity:.2f} interp={interpretation} "
              f"dyn_refundable={dynamic_refundable_share}", end="", flush=True)
    print(f"\n{'='*78}", flush=True)

    # Synthesize fresh from the calibrated (no-synthesis) base.
    # Re-score with the SAME deduction params the base was calibrated under —
    # bare _compute_base_tax (SD-only) made tail_k inconsistent (C3).
    units = synthesize_top_filers(base_calibrated, pareto_alpha=alpha)
    units = impute_capital_gains_from_soi(units)    # Phase 3: CG rate cap for $100K-$1M filers
    units = _compute_base_tax(units, deduction_params=ded_params, tax_year=cal_tax_year)
    units, tail_k = rescale_synthetic_tail_to_tax_target(units)
    units = _compute_base_tax(units, deduction_params=ded_params, tax_year=cal_tax_year)
    v = validate_top_synthesis(units)
    print(f"  Synthesis: {v['filers_1m_plus']:,.0f} filers @ $1M+ "
          f"({100*v['filer_target_ratio']:.1f}%), ${v['tax_1m_plus_$M']:,.1f}M tax "
          f"({100*v['tax_target_ratio']:.1f}%), tail_k={tail_k:.4f}", flush=True)

    behav_params = BehavioralParams.named(behav)
    print(f"  Behavioral: ETI={behav_params.eti}, migration_elast="
          f"{behav_params.migration_elast}, pte_capture={behav_params.pte_capture}",
          flush=True)

    # Per-filer effective deduction from the projection step
    # (greater of standard and expected itemized, with Hawaii Pease applied).
    # Plumbed into compare_systems so the bracket comparison reflects
    # realistic itemized behavior at the top of the distribution.
    calc = TaxCalculator()
    rows = []
    for year in target_years:
        t0 = time.perf_counter()
        # 1) Standard projection (county B19013 income growth + per-filer
        #    effective deduction populated as `hi_standard_deduction`).
        projected = project_tax_units_forward(units, target_year=year, method="ensemble")

        # 2) Top-income growth premium (corrects under-projection of top-1%)
        projected = apply_top_income_growth_premium(
            projected, target_year=year, annual_premium=top_premium,
        )

        # 2d) Macro recession shock (RECESSION scenario only; no-op for None)
        if macro_shock is not None:
            projected = apply_macro_recession_shock(
                projected, target_year=year, scenario=macro_shock,
            )

        # 3) Pre-behavioral baseline + scenario revenue (static)
        baseline_cfg = TaxSystemRegistry.get_act46_system(year)
        scenario_cfg = get_scenario_system(year)
        cmp_static = compare_systems(
            projected, baseline_cfg, scenario_cfg,
            calculator=calc,
        )
        diff_static = float(cmp_static[cmp_static["system"] == "Difference"].iloc[0]["revenue_millions"])
        baseline_static = float(cmp_static[cmp_static["system"] == baseline_cfg.name].iloc[0]["revenue_millions"])

        # 4) Apply behavioral response (per-filer ETI + migration), recompute
        adjusted, behav_diag = apply_behavioral_response(
            projected, behav_params, target_year=year,
            baseline_cfg=baseline_cfg, scenario_cfg=scenario_cfg, calculator=calc,
        )
        cmp_behav = compare_systems(
            adjusted, baseline_cfg, scenario_cfg,
            calculator=calc,
        )
        diff_behav = float(cmp_behav[cmp_behav["system"] == "Difference"].iloc[0]["revenue_millions"])

        # 5) PTE election shift (revenue moves from individual to PTE form)
        pte_shift = behav_diag["pte_revenue_loss_$M"]
        bracket_delta_after_response = diff_behav - pte_shift

        # 6) Credit-cap overlay
        if cd == "2":
            # CD2: vintage carryforward simulation + round-2 demand/eligibility knobs.
            # Per-year AGI eligibility recomputed from projected distribution:
            # §235-12.5(a) thresholds ($175K/$350K) are unindexed, so nominal
            # income growth erodes them — fewer filers eligible each forecast year.
            agi_elig_share = compute_dynamic_agi_eligibility_share(projected)
            credit = compute_credit_overlay(
                year, reec_demand_scenario=reec,
                corp_subject_to_agi_limit=corp_agi,
                reec_effective_claim_share=reec_eff,
                cgec_annual_growth=cgec_g,
                reec_carryforward_utilization_m=reec_cf_m,
                model_carryforward_pool=True,
                agi_eligibility_by_year={year: agi_elig_share},
                pro_rata_elasticity=pro_rata_elasticity,
                interpretation=interpretation,
                dynamic_refundable_share=dynamic_refundable_share,
            )
        else:
            # CD1: standard credit overlay (no vintage simulation).
            credit = compute_credit_overlay(
                year, reec_demand_scenario=reec,
                corp_subject_to_agi_limit=corp_agi,
                reec_effective_claim_share=reec_eff,
                cgec_annual_growth=cgec_g,
                reec_carryforward_utilization_m=reec_cf_m,
            )
        credit_total = credit["total_credit_savings_$M"]

        total = bracket_delta_after_response + credit_total

        # 7) COR-scaled diagnostic: scale bracket delta to match COR baseline
        cor_scale_factor = cor_scale_factor_for_year(year, baseline_static)
        bracket_delta_cor_scaled = bracket_delta_after_response * cor_scale_factor

        row = {
            "scenario":                       label,
            "tax_year":                       year,
            "act46_baseline_$M":              round(baseline_static, 2),
            "bracket_delta_static_$M":        round(diff_static, 2),
            "eti_response_$M":                round(diff_behav - diff_static, 2),
            "pte_shift_$M":                   round(-pte_shift, 2),
            "bracket_delta_post_$M":          round(bracket_delta_after_response, 2),
            "bracket_delta_cor_scaled_$M":    round(bracket_delta_cor_scaled, 2),
            "reec_savings_$M":                credit["reec_savings_$M"],
            "cgec_savings_$M":                credit["cgec_savings_$M"],
            "tcra_savings_$M":                credit["tcra_savings_$M"],
            "credit_total_$M":                credit_total,
            "total_impact_$M":                round(total, 2),
            "filers_1m_post_response":        round(behav_diag["filers_1m_post_response"], 0),
        }
        if cd == "2":
            row.update({
                # Vintage-simulation diagnostics: pre-2027 carryforward dynamics
                "reec_base_state_cost_$M":        credit.get("reec_baseline_state_cost_$M"),
                "reec_scen_state_cost_$M":        credit.get("reec_scenario_state_cost_$M"),
                "reec_scen_new_certs_$M":         credit.get("reec_scenario_new_certs_$M"),
                "reec_scen_refundable_$M":        credit.get("reec_scenario_refundable_$M"),
                "reec_scen_nonref_usage_$M":      credit.get("reec_scenario_nonref_usage_$M"),
                "reec_scen_end_stock_$M":         credit.get("reec_scenario_end_stock_$M"),
                "reec_base_end_stock_$M":         credit.get("reec_baseline_end_stock_$M"),
                # Round-2 diagnostics
                "reec_eligibility_share":         credit.get("reec_eligibility_share"),
                "reec_pro_rata_factor":           credit.get("reec_pro_rata_factor"),
                "reec_suppression_factor":        credit.get("reec_suppression_factor"),
                "reec_ind_refundable_share":      credit.get("reec_ind_refundable_share"),
                "reec_interpretation":            credit.get("reec_interpretation"),
            })
        rows.append(row)
        print(f"  TY {year}: static={diff_static:+.1f}M  "
              f"ETI={diff_behav-diff_static:+.1f}M  "
              f"PTE={-pte_shift:+.1f}M  "
              f"-> bracket={bracket_delta_after_response:+.1f}M  "
              f"credit={credit_total:+.1f}M  "
              f"TOTAL={total:+.1f}M  ({time.perf_counter()-t0:.1f}s)", flush=True)
    return rows


if __name__ == "__main__":
    silence_noise()

    args = parse_cd_args(__doc__)
    CD = args.cd
    OUT_CSV = Path(f"/tmp/sb3125_cd{CD}_enhanced_2027_2031.csv")
    cd_label = f"CD{CD}"

    try:
        import time
        import pandas as pd

        print(f"SB 3125 {cd_label} enhanced fiscal forecast", flush=True)
        print("Importing modules...", flush=True)
        # The scenario workers import their own dependencies; the main process
        # only needs the one-time calibration setup (enrich, base tax, rake).
        from tax_modeler.pipeline import _enrich_for_credits, _compute_base_tax, _calibrate
        from tax_modeler.config.tax_system_config import TaxSystemRegistry

        get_scenario_system = (
            TaxSystemRegistry.get_sb3125_cd1_system if CD == "1"
            else TaxSystemRegistry.get_sb3125_cd2_system
        )

        wall_start = time.perf_counter()

        from tax_modeler.artifacts import load_canonical_deduction_params
        units = load_cached_units(CD)

        print("Enriching + base tax + calibrating (one-time)...", flush=True)
        units = _enrich_for_credits(units)

        # Load the same itemized-deduction params used in projection so the
        # calibration target and projected tax are computed on the same
        # deduction basis. Avoids the standard-only-vs-itemized "double
        # discount" at high incomes that previously made TY 2027 baseline
        # drop ~$349M below the calibration anchor.
        CAL_DED_PARAMS = load_canonical_deduction_params()
        print(f"  Loaded itemized-deduction params for calibration", flush=True)

        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        calibrated_base = _calibrate(units)
        # Re-compute Hawaii tax post-calibration so hi_tax_liability reflects
        # final calibrated weights with itemized deductions on.
        calibrated_base = _compute_base_tax(
            calibrated_base, deduction_params=CAL_DED_PARAMS, tax_year=2023,
        )

        # Validate post-rake AND post-synthesis (top-tier filers added in
        # synthesis fill the $1M+ bin which the IPF rake can't reach via its
        # 1.5x bin-cap). Calibration target is met after synthesis.
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers as _synth, rescale_synthetic_tail_to_tax_target as _rescale,
        )
        _vb = _synth(calibrated_base, pareto_alpha=1.5)
        _vb = _compute_base_tax(_vb, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        _vb, _ = _rescale(_vb)
        _vb = _compute_base_tax(_vb, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        _hi_tl = (_vb["hi_tax_liability"] * _vb["weight"]).sum() / 1e6
        _hi_st = (_vb["hi_state_tax"]     * _vb["weight"]).sum() / 1e6
        print(
            f"  CALIBRATION VALIDATION (post-rake + post-synthesis, itemized on):\n"
            f"    hi_tax_liability:      ${_hi_tl:>7,.0f}M  (DOTAX Table A8 target: $3,030M)\n"
            f"    hi_state_tax (net):    ${_hi_st:>7,.0f}M  (DOTAX TY2022 actual:  ~$2,400M)",
            flush=True,
        )

        # Save calibrated base to a pickle so each parallel worker can read
        # it independently. Pickle (not parquet) is required because the
        # ``dependents`` column holds Python objects (lists of dicts) that
        # parquet can't round-trip without lossy string conversion.
        # save_calibrated_base embeds CAL_DED_PARAMS + provenance metadata so
        # consumers re-score on the SAME deduction basis (C3 fix).
        from tax_modeler.artifacts import save_calibrated_base
        calibrated_pkl = ARTIFACT_DIR / "sb3125_calibrated_base.pkl"
        save_calibrated_base(
            calibrated_base, calibrated_pkl,
            deduction_params=CAL_DED_PARAMS, tax_year=2023,
            extra_meta={"built_by": "forecast_sb3125_enhanced.py", "cd": CD},
        )

        # Parallel: run all four scenarios concurrently. Each worker imports
        # its own modules and reads the calibrated parquet. With the
        # vectorized compare_systems, each scenario takes ~30-60s on its
        # own; in parallel the wall-clock collapses to one scenario's worth.
        from concurrent.futures import ProcessPoolExecutor, as_completed

        all_rows = []
        n_workers = min(len(SCENARIOS), 4)
        print(f"\nLaunching {n_workers} parallel scenario workers ({cd_label})...", flush=True)
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(_scenario_worker, sc, TARGET_YEARS, str(calibrated_pkl), CD): sc["label"]
                for sc in SCENARIOS
            }
            for fut in as_completed(futures):
                label = futures[fut]
                rows = fut.result()
                all_rows.extend(rows)
                print(f"  ✓ {label} complete ({len(rows)} rows)", flush=True)

        # Sort rows so the CSV stays in scenario × year order
        scen_order = {s["label"]: i for i, s in enumerate(SCENARIOS)}
        all_rows.sort(key=lambda r: (scen_order[r["scenario"]], r["tax_year"]))

        df = pd.DataFrame(all_rows)
        df.to_csv(OUT_CSV, index=False)

        # ---- Manifested run output (canonical; /tmp copy kept above) --------
        from tax_modeler.runs import tidy_long, write_run_manifest
        from _forecast_common import RUNS_DIR, cache_provenance
        RUN_DIR = RUNS_DIR / f"sb3125_cd{CD}_enhanced"
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(RUN_DIR / "enhanced.csv", index=False)
        tidy_long(df, ["scenario", "tax_year"]).to_csv(
            RUN_DIR / "fiscal_tidy.csv", index=False,
        )
        write_run_manifest(
            RUN_DIR,
            script=f"forecast_sb3125_enhanced.py --cd {CD}",
            params={"cd": CD, "target_years": TARGET_YEARS, "scenarios": SCENARIOS},
            inputs={"tax_units_cache": cache_provenance()},
        )
        print(f"Saved run: {RUN_DIR}", flush=True)

        # ---- Distributional quintile analysis (MID scenario, all years) -----
        print("\nRunning distributional quintile analysis (MID scenario)...", flush=True)
        import warnings; warnings.filterwarnings("ignore")
        import logging; logging.disable(logging.WARNING)
        from tax_modeler.pipeline import _compute_base_tax, _enrich_for_credits
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers, rescale_synthetic_tail_to_tax_target,
        )
        from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
        from tax_modeler.scenarios.behavioral_response import (
            apply_top_income_growth_premium,
        )
        from tax_modeler.scenarios.sb3125_cd1_credits import (
            compute_credit_overlay,
            compute_dynamic_agi_eligibility_share,
        )
        from tax_modeler.scenarios.quintile_analysis import (
            generate_quintile_report, compute_quintile_breaks,
            cor_scale_factor_for_year, per_unit_tax,
        )

        mid_sc = next(s for s in SCENARIOS if s["label"] == "MID")
        q_calc = TaxCalculator()

        # Re-synthesize MID (same params as the parallel worker); re-score on
        # the calibration deduction basis (C3 fix — see run_one_scenario).
        q_units = synthesize_top_filers(calibrated_base, pareto_alpha=mid_sc["alpha"])
        q_units = _enrich_for_credits(q_units)           # adds total_cash_income for TCI quintile binning
        q_units = impute_capital_gains_from_soi(q_units) # Phase 3: CG rate cap for $100K-$1M filers
        q_units = _compute_base_tax(q_units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        q_units, _ = rescale_synthetic_tail_to_tax_target(q_units)
        q_units = _compute_base_tax(q_units, deduction_params=CAL_DED_PARAMS, tax_year=2023)

        # Anchor quintile boundaries to the 2026 base-year income distribution
        # so Q1–Q5 membership is fixed across all projection years.
        base_2026_breaks = compute_quintile_breaks(q_units)
        print(f"  2026 quintile AGI breaks (p20/p40/p60/p80): "
              f"${base_2026_breaks[0]:,.0f} / ${base_2026_breaks[1]:,.0f} / "
              f"${base_2026_breaks[2]:,.0f} / ${base_2026_breaks[3]:,.0f}", flush=True)

        quintile_frames = []
        bracket_frames  = []

        for yr in TARGET_YEARS:
            projected_q = project_tax_units_forward(q_units, target_year=yr, method="ensemble")
            projected_q = apply_top_income_growth_premium(
                projected_q, target_year=yr, annual_premium=mid_sc["top_premium"],
            )

            base_cfg = TaxSystemRegistry.get_act46_system(yr)
            scen_cfg = get_scenario_system(yr)

            if CD == "2":
                agi_elig = compute_dynamic_agi_eligibility_share(projected_q)
                credit_ov = compute_credit_overlay(
                    yr,
                    reec_demand_scenario=mid_sc["reec"],
                    corp_subject_to_agi_limit=mid_sc["corp_agi_limit"],
                    reec_effective_claim_share=mid_sc["reec_eff_share"],
                    cgec_annual_growth=mid_sc["cgec_growth"],
                    reec_carryforward_utilization_m=mid_sc.get("reec_cf_m", 0.0),
                    model_carryforward_pool=True,
                    agi_eligibility_by_year={yr: agi_elig},
                    pro_rata_elasticity=mid_sc.get("pro_rata_elasticity", 0.0),
                    interpretation=mid_sc.get("interpretation", "B"),
                    dynamic_refundable_share=mid_sc.get("dynamic_refundable_share", False),
                )
            else:
                credit_ov = compute_credit_overlay(
                    yr,
                    reec_demand_scenario=mid_sc["reec"],
                    corp_subject_to_agi_limit=mid_sc["corp_agi_limit"],
                    reec_effective_claim_share=mid_sc["reec_eff_share"],
                    cgec_annual_growth=mid_sc["cgec_growth"],
                    reec_carryforward_utilization_m=mid_sc.get("reec_cf_m", 0.0),
                )

            # Compute year-specific COR scale factor from this scenario's
            # Act 46 microsim baseline (per-filer aggregated revenue, $M).
            act46_baseline_M = float(
                (per_unit_tax(projected_q, base_cfg, q_calc)
                 * projected_q["weight"].to_numpy(dtype=float)).sum() / 1e6
            )
            cor_factor = cor_scale_factor_for_year(yr, act46_baseline_M)

            q_df, b_df, _ = generate_quintile_report(
                projected_q, base_cfg, scen_cfg, credit_ov,
                q_calc,
                scenario_params=mid_sc,
                quintile_breaks=base_2026_breaks,
                cor_scale_factor=cor_factor,
            )
            q_df.insert(0, "tax_year", yr)
            b_df.insert(0, "tax_year", yr)
            quintile_frames.append(q_df)
            bracket_frames.append(b_df)
            print(f"  TY {yr} quintile done", flush=True)

        all_quintiles = pd.concat(quintile_frames, ignore_index=True)
        all_brackets  = pd.concat(bracket_frames,  ignore_index=True)

        Q_CSV = Path(f"/tmp/sb3125_cd{CD}_quintile_mid_2027_2031.csv")
        B_CSV = Path(f"/tmp/sb3125_cd{CD}_bracket_mid_2027_2031.csv")
        all_quintiles.to_csv(Q_CSV, index=False)
        all_brackets.to_csv(B_CSV, index=False)
        print(f"Saved: {Q_CSV}", flush=True)
        print(f"Saved: {B_CSV}", flush=True)
        # Also keep them with the run: /tmp vanishes on reboot, and the
        # estimates site (scripts/build_site.py) imports from RUN_DIR.
        all_quintiles.to_csv(RUN_DIR / "quintile.csv", index=False)
        all_brackets.to_csv(RUN_DIR / "bracket.csv", index=False)
        write_run_manifest(
            RUN_DIR,
            script=f"forecast_sb3125_enhanced.py --cd {CD}",
            params={"cd": CD, "target_years": TARGET_YEARS, "scenarios": SCENARIOS},
            inputs={"tax_units_cache": cache_provenance()},
        )

        # Print TY 2027 quintile summary
        q27 = all_quintiles[all_quintiles["tax_year"] == 2027].copy()
        print("\n" + "=" * 100, flush=True)
        print(f"DISTRIBUTIONAL ANALYSIS — MID Scenario, TY 2027 "
              f"(bracket change + REEC credit loss per household)", flush=True)
        print("=" * 100, flush=True)
        pd.set_option("display.float_format", "{:,.1f}".format)
        pd.set_option("display.max_colwidth", 20)
        display_cols = [
            "quintile", "household_count",
            "avg_per_hh_bracket_change", "avg_per_hh_credit_loss", "avg_per_hh_total_change",
            "total_bracket_$M", "total_credit_loss_$M", "total_change_$M",
            "pct_pay_more", "pct_pay_less",
        ]
        print(q27[display_cols].to_string(index=False), flush=True)

        # ---- Summary tables -------------------------------------------------
        print("\n" + "=" * 100, flush=True)
        print(f"SB 3125 {cd_label} ENHANCED FISCAL IMPACT  "
              f"($M, vs Act 46 baseline, with behavioral response)", flush=True)
        print("=" * 100, flush=True)

        # Column order for summary tables
        scen_order = [s["label"] for s in SCENARIOS]  # ["LOW","MID","HIGH","RECESSION"]

        # Total impact pivot
        pv = df.pivot(index="tax_year", columns="scenario", values="total_impact_$M")
        pv = pv[scen_order]
        pv.loc["5yr cum"] = pv.sum()
        print("\nTotal annual fiscal impact ($M, post-behavioral):")
        print(pv.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        # Bracket-only (post-response) pivot
        pv_b = df.pivot(index="tax_year", columns="scenario", values="bracket_delta_post_$M")[scen_order]
        pv_b.loc["5yr cum"] = pv_b.sum()
        print("\nBracket-change impact (post-ETI/migration/PTE) ($M):")
        print(pv_b.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        # COR-scaled pivot
        pv_cs = df.pivot(index="tax_year", columns="scenario", values="bracket_delta_cor_scaled_$M")[scen_order]
        pv_cs.loc["5yr cum"] = pv_cs.sum()
        print("\nBracket-change impact, COR-scaled to actual revenue base ($M):")
        print(pv_cs.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        # Credit pivot
        pv_c = df.pivot(index="tax_year", columns="scenario", values="credit_total_$M")[scen_order]
        pv_c.loc["5yr cum"] = pv_c.sum()
        print("\nCredit-cap impact ($M):")
        print(pv_c.to_string(float_format=lambda x: f"{x:>9,.1f}"), flush=True)

        # Behavioral decomposition for MID at TY 2027
        mid27 = df[(df["scenario"] == "MID") & (df["tax_year"] == 2027)].iloc[0]
        print("\n" + "-" * 100, flush=True)
        print(f"BEHAVIORAL DECOMPOSITION (MID scenario, TY 2027):", flush=True)
        print("-" * 100, flush=True)
        print(f"  Static bracket delta:                   ${mid27['bracket_delta_static_$M']:>+8.2f}M", flush=True)
        print(f"  ETI/migration response (income shrink): ${mid27['eti_response_$M']:>+8.2f}M", flush=True)
        print(f"  PTE election shift to entity tax:       ${mid27['pte_shift_$M']:>+8.2f}M", flush=True)
        print(f"  Post-behavioral bracket delta:          ${mid27['bracket_delta_post_$M']:>+8.2f}M", flush=True)
        cor_diag = (mid27['bracket_delta_cor_scaled_$M'] / mid27['bracket_delta_post_$M']
                    if mid27['bracket_delta_post_$M'] else 1.0)
        print(f"  COR-scaled (×{cor_diag:.3f}):                "
              f"${mid27['bracket_delta_cor_scaled_$M']:>+8.2f}M", flush=True)

        print(f"\nSaved: {OUT_CSV}", flush=True)
        print(f"Total elapsed: {time.perf_counter() - wall_start:.1f}s", flush=True)

    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
