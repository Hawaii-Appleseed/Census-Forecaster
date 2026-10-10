"""SB 3125 vs TY2026-frozen baseline — ITEP-comparable distributional analysis.

Usage:
  python forecast_sb3125_vs_fy26base.py --cd 1   # SB 3125 CD1 (default)
  python forecast_sb3125_vs_fy26base.py --cd 2   # SB 3125 CD2

Replaces the two separate scripts:
  forecast_sb3125_cd1_vs_fy26base.py  (identical except TaxSystemRegistry call)
  forecast_sb3125_cd2_vs_fy26base.py  (identical except TaxSystemRegistry call)

Comparison frame:
  Baseline: Act 46 law frozen at TY2026 (TY2025 brackets + TY2026 standard
            deductions held constant). Same frozen-law config as HB 2306 original.
  Bill:     SB 3125 CD1 or CD2 full implementation.

Delta = CD{N} full - TY2026 frozen law.

Decomposed into:
  bracket_delta: (CD{N} brackets + Act 46 TY-yr SDs) - (frozen 2026 brackets + Act 46 TY-yr SDs)
  sd_delta:      Act 46 SD-expansion effect (ongoing current law, not the bill):
                 (frozen 2026 brackets + Act 46 TY-yr SDs) - (frozen 2026 brackets + frozen 2026 SDs)
  total_delta:   bracket_delta + sd_delta (= ITEP's headline number)

All three regimes are scored on one TY-yr frame (score_regimes): only
statute differs between them. Itemized-deduction amounts are an economic
input, not statute, so the frozen baseline reads the same target-year
amounts as the bill.

Requires calibrated_base.pkl (run forecast_sb3125_enhanced.py --cd {N} first).

Outputs:
  /tmp/cd{N}_vs_fy26base_bracket_mid_2027_2031.csv
  /tmp/cd{N}_vs_fy26base_quintile_mid_2027_2031.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
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
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemConfig, TaxSystemRegistry
from tax_modeler.scenarios.top_income_synthesis import (
    synthesize_top_filers, calibrate_synthetic_tail_to_tax_target,
    redistribute_mid_high_incomes,
)
from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
from tax_modeler.scenarios.quintile_analysis import (
    generate_quintile_report, compute_quintile_breaks, per_unit_tax,
)
from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year
from tax_modeler.liability.hawaii import NO_ITEMIZING

MID_ALPHA       = 1.5
MID_TOP_PREMIUM = 0.010
from _forecast_common import CALIBRATED_PKL, TARGET_YEARS, add_replicate_se_arg  # noqa: E402

INCOME_BINS = {
    "below_50K":   (0,          50_000),
    "50K_200K":    (50_000,    200_000),
    "200K_1M":     (200_000, 1_000_000),
    "1M_plus":     (1_000_000, float("inf")),
}

# ITEP annual figures from "26.05.06 HI SB 3125 CD Analysis.xlsx" (residents, $M).
ITEP_ANNUAL = {2027: -227.0, 2028: -258.0, 2029: -534.0, 2030: -563.0, 2031: -622.0}


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cd", choices=["1", "2"], default="1",
        help="Conference draft to model: 1=CD1 (default), 2=CD2",
    )
    add_replicate_se_arg(p)
    return p.parse_args()


def regime_systems(yr: int, get_scenario_system) -> tuple[TaxSystemConfig, TaxSystemConfig, TaxSystemConfig]:
    """The three regimes for TY *yr*: (a) Act 46 frozen at TY2026, (b) frozen
    2026 brackets + Act 46 TY-*yr* SDs, (c) the bill (CD{N} full)."""
    cfg_a = TaxSystemRegistry.get_hb2306_orig_system(yr)  # frozen 2026
    cfg_b = TaxSystemConfig(
        name=f"frozen2026_brackets_act46_sds_{yr}",
        year=yr,
        bracket_year=2025,
        standard_deduction_year=yr,
        personal_exemption=TaxSystemRegistry.PERSONAL_EXEMPTIONS.get(2026, 1200),
        description=f"Frozen 2026 brackets + Act46 TY{yr} SDs",
    )
    cfg_c = get_scenario_system(yr)                       # CD full
    return cfg_a, cfg_b, cfg_c


def score_regimes(projected: pd.DataFrame, yr: int, systems, calc):
    """Per-unit net tax under each of *systems*, all on one TY-*yr* frame.

    Returns ``(proj_target, taxes)``: the frame re-scored with TY-*yr*
    itemized-deduction params, and one per-unit tax array per system.

    Only statute may differ between regimes. The itemized amounts
    (``hi_itemized_deduction``) are an economic input — the mortgage-interest
    tiers scale with projected home values — so the frozen baseline reads the
    same target-year amounts as the bill. Until September 2026 the frozen
    regime was scored on a frame built with TY2026 params: TY-*yr* incomes
    with TY2026 mortgage interest. In the CD2 run that put -$32.4M into the
    TY2027 SD-expansion effect, which is exactly 0 by statute (Act 46's TY2027
    SD equals its TY2026 SD), and -$346.3M into the 5-year total.
    """
    ded_params = scale_deduction_params_for_target_year(yr, geoid="15003")
    proj_target = _compute_base_tax(
        projected.copy(), tax_year=yr, deduction_params=ded_params,
    )
    return proj_target, tuple(per_unit_tax(proj_target, cfg, calc) for cfg in systems)


def _bin_delta(inc, w, diffs):
    result = {}
    for label, (lo, hi) in INCOME_BINS.items():
        mask = (inc >= lo) & (inc < hi)
        result[label] = float(diffs[mask].sum() / 1e6)
    result["total"] = float(diffs.sum() / 1e6)
    return result


def main(cd: str, replicate_se: bool = True) -> None:
    if not CALIBRATED_PKL.exists():
        print(f"ERROR: {CALIBRATED_PKL} not found. Run forecast_sb3125_enhanced.py --cd {cd} first.")
        sys.exit(1)

    import time
    wall = time.perf_counter()

    get_scenario_system = (
        TaxSystemRegistry.get_sb3125_cd1_system if cd == "1"
        else TaxSystemRegistry.get_sb3125_cd2_system
    )

    print(f"Loading calibrated base (CD{cd})...", flush=True)
    from tax_modeler.artifacts import load_calibrated_base
    base, cal_ded_params, cal_meta = load_calibrated_base(CALIBRATED_PKL)
    cal_tax_year = int(cal_meta.get("tax_year", 2023))
    units = redistribute_mid_high_incomes(base, pareto_alpha=MID_ALPHA)
    units = synthesize_top_filers(units, pareto_alpha=MID_ALPHA)
    units = _enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)
    # Re-score on the SAME deduction basis the base was calibrated under —
    # bare _compute_base_tax (SD-only) made tail_k inconsistent (C3). The
    # tail is not aged to the PUMS dollar year (age_synthetic_tail):
    # project_and_recalibrate's SOI anchor zeroes every $1M+ unit and
    # replaces them with SOI tiers aged from TY2022, so its level is moot.
    def score(u):
        return _compute_base_tax(u, deduction_params=cal_ded_params, tax_year=cal_tax_year)

    units, tail_k = calibrate_synthetic_tail_to_tax_target(score(units), score=score)
    print(f"  tail_k={tail_k:.4f}", flush=True)

    calc = TaxCalculator()
    base_2026_breaks = compute_quintile_breaks(units)
    print(
        f"  2026 quintile breaks: "
        f"${base_2026_breaks[0]:,.0f} / ${base_2026_breaks[1]:,.0f} / "
        f"${base_2026_breaks[2]:,.0f} / ${base_2026_breaks[3]:,.0f}",
        flush=True,
    )

    rows = []
    quintile_frames, bracket_frames = [], []
    wedge_by_year = {}
    year_reports = {}
    if replicate_se:
        from tax_modeler.uncertainty.replicates import (
            replicate_columns, replicate_weight_matrix, sdr_columns, sdr_totals,
        )
        if not replicate_columns(units) or "weight_uncal" not in units.columns:
            print("  NOTE: the calibrated base carries no replicate weights / weight_uncal; "
                  "no sampling band this run.", flush=True)
            replicate_se = False

    for yr in TARGET_YEARS:
        print(f"  TY {yr}...", flush=True)

        projected, fwd = project_and_recalibrate(
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
        if fwd is not None:
            year_reports[yr] = fwd.calibration_report
        if fwd is not None and fwd.statute_vs_cor_wedge is not None:
            wedge_by_year[yr] = fwd
            print(
                f"    statute-vs-COR wedge: statutory ${fwd.statutory_tax_M:,.0f}M "
                f"vs COR ${fwd.aggregate_tax_M:,.0f}M (ratio {fwd.statute_vs_cor_wedge:.3f})",
                flush=True,
            )

        inc = projected["income"].to_numpy(dtype=float)
        w   = projected["weight"].to_numpy(dtype=float)

        cfg_a, cfg_b, cfg_c = regime_systems(yr, get_scenario_system)
        proj_target, (tax_a, tax_b, tax_c) = score_regimes(
            projected, yr, (cfg_a, cfg_b, cfg_c), calc,
        )

        bracket_effect = (tax_c - tax_b) * w
        sd_effect      = (tax_b - tax_a) * w
        total_effect   = (tax_c - tax_a) * w

        br = _bin_delta(inc, w, bracket_effect)
        sd = _bin_delta(inc, w, sd_effect)
        tt = _bin_delta(inc, w, total_effect)

        # SDR sampling band on the statewide totals (80 PUMS replicate
        # weights, calibration ratio per replicate: the re-rake and the SOI
        # anchor act on ``weight`` and so on each replicate). The SOI tiers
        # project_and_recalibrate adds are not sampled and carry their weight.
        bands = {"bracket_delta_$M": {}, "sd_expansion_delta_$M": {}, "total_delta_$M": {}}
        if replicate_se:
            W = replicate_weight_matrix(proj_target)
            ests = sdr_totals({"bracket_delta_$M": tax_c - tax_b,
                               "sd_expansion_delta_$M": tax_b - tax_a,
                               "total_delta_$M": tax_c - tax_a}, W, scale=1e-6)
            for k, est in ests.items():
                bands[k] = {"total_se": round(est.se, 3), "total_ci90_low": round(est.ci_low, 3),
                            "total_ci90_high": round(est.ci_high, 3)}
        rows.append({"tax_year": yr, "component": "bracket_delta_$M", **br, **bands["bracket_delta_$M"]})
        rows.append({"tax_year": yr, "component": "sd_expansion_delta_$M", **sd, **bands["sd_expansion_delta_$M"]})
        rows.append({"tax_year": yr, "component": "total_delta_$M", **tt, **bands["total_delta_$M"]})

        empty_overlay = {}
        q_df, b_df, _ = generate_quintile_report(
            proj_target, cfg_a, cfg_c,
            credit_overlay=empty_overlay,
            calc=calc,
            scenario_params=None,
            quintile_breaks=base_2026_breaks,
            cor_scale_factor=1.0,
        )
        q_df.insert(0, "tax_year", yr)
        b_df.insert(0, "tax_year", yr)
        quintile_frames.append(q_df)
        bracket_frames.append(b_df)

    df_summary = pd.DataFrame(rows)
    all_quintiles = pd.concat(quintile_frames, ignore_index=True)
    all_brackets  = pd.concat(bracket_frames,  ignore_index=True)

    Q_CSV = Path(f"/tmp/cd{cd}_vs_fy26base_quintile_mid_2027_2031.csv")
    B_CSV = Path(f"/tmp/cd{cd}_vs_fy26base_bracket_mid_2027_2031.csv")
    all_quintiles.to_csv(Q_CSV, index=False)
    all_brackets.to_csv(B_CSV, index=False)

    # ---- Manifested run output (canonical; /tmp copies kept above) ---------
    from tax_modeler.runs import tidy_long, write_run_manifest
    from _forecast_common import RUNS_DIR, assemble_calibration_report, cache_provenance
    run_dir = RUNS_DIR / f"sb3125_cd{cd}_fy26base"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Base rake (from the artifact) + each year's re-anchoring: printed, and
    # written as calibration_report.json next to the manifest.
    assemble_calibration_report(cal_meta, year_reports, run_dir=run_dir)
    all_quintiles.to_csv(run_dir / "quintile.csv", index=False)
    all_brackets.to_csv(run_dir / "bracket.csv", index=False)
    tidy_long(all_quintiles, ["tax_year", "quintile"]).to_csv(
        run_dir / "distribution_tidy.csv", index=False,
    )
    write_run_manifest(
        run_dir,
        script=f"forecast_sb3125_vs_fy26base.py --cd {cd}",
        params={"cd": cd, "target_years": TARGET_YEARS},
        inputs={
            "calibrated_base": str(CALIBRATED_PKL),
            "tax_units_cache": cache_provenance(),
        },
    )
    print(f"Saved run: {run_dir}", flush=True)

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 90, flush=True)
    print(f"SB 3125 CD{cd} vs TY2026-FROZEN BASELINE — MID Scenario", flush=True)
    print("Baseline = Act 46 frozen at TY2026 (no further phase-ins)", flush=True)
    print(f"Bill     = SB 3125 CD{cd} brackets + Act 46 SD phase-in continues", flush=True)
    print("=" * 90, flush=True)

    def _print_component(component_label, header_title):
        print(f"\n--- {header_title} ---", flush=True)
        print(f"{'Year':<6} {'<$50K':>10} {'$50K-$200K':>12} {'$200K-$1M':>12} {'$1M+':>10} {'Annual':>10} {'Cumulative':>12}", flush=True)
        print("-" * 78, flush=True)
        cum = {k: 0.0 for k in list(INCOME_BINS.keys()) + ["total"]}
        for yr in TARGET_YEARS:
            r = df_summary[(df_summary["tax_year"] == yr) & (df_summary["component"] == component_label)].iloc[0]
            for k in cum:
                cum[k] += r[k]
            print(f"{yr:<6} {r['below_50K']:>+9.1f}M {r['50K_200K']:>+11.1f}M "
                  f"{r['200K_1M']:>+11.1f}M {r['1M_plus']:>+9.1f}M "
                  f"{r['total']:>+9.1f}M {cum['total']:>+11.1f}M", flush=True)
        print("-" * 78, flush=True)
        print(f"{'5yr':<6} {cum['below_50K']:>+9.1f}M {cum['50K_200K']:>+11.1f}M "
              f"{cum['200K_1M']:>+11.1f}M {cum['1M_plus']:>+9.1f}M {cum['total']:>+9.1f}M", flush=True)
        return cum

    _print_component(
        "bracket_delta_$M",
        f"Bracket effect only (CD{cd} brackets vs TY2026-frozen brackets, SDs held at Act46)",
    )
    _print_component(
        "sd_expansion_delta_$M",
        "Act 46 SD-expansion effect (ongoing current law, not the bill)",
    )
    _print_component(
        "total_delta_$M",
        f"TOTAL: CD{cd} full vs TY2026-frozen (= ITEP's headline number, our microsim)",
    )
    if replicate_se:
        print("\n--- SDR sampling 90% half-widths on the annual totals (±1.645 SE, $M; "
              "sampling variance only) ---", flush=True)
        print(f"{'Year':<6} {'Bracket':>10} {'SD exp.':>10} {'Total':>10}", flush=True)
        for yr in TARGET_YEARS:
            d = df_summary[df_summary["tax_year"] == yr].set_index("component")
            print(f"{yr:<6} {1.645*d.loc['bracket_delta_$M', 'total_se']:>+9.1f}M "
                  f"{1.645*d.loc['sd_expansion_delta_$M', 'total_se']:>+9.1f}M "
                  f"{1.645*d.loc['total_delta_$M', 'total_se']:>+9.1f}M", flush=True)

    print("\n--- Quintile distribution (total delta, TY 2027) ---", flush=True)
    q27 = all_quintiles[all_quintiles["tax_year"] == 2027]
    pd.set_option("display.float_format", "{:,.1f}".format)
    print(q27[[
        "quintile", "household_count",
        "avg_per_hh_bracket_change", "avg_per_hh_total_change",
        "total_bracket_$M", "total_change_$M",
        "pct_pay_more", "pct_pay_less",
    ]].to_string(index=False), flush=True)

    print(f"\n\nComparison to ITEP — annual snapshots vs TY2026-frozen baseline (residents, $M):", flush=True)
    print(
        f"{'Year':<6} {'Ours (total)':>14} {'Ours (bracket)':>16} "
        f"{'ITEP':>10} {'Gap (total-ITEP)':>18}",
        flush=True,
    )
    print("-" * 70, flush=True)
    sum_tt = 0.0
    sum_br = 0.0
    sum_itep = 0.0
    for yr in TARGET_YEARS:
        ours_br = df_summary[(df_summary["tax_year"] == yr) & (df_summary["component"] == "bracket_delta_$M")].iloc[0]["total"]
        ours_tt = df_summary[(df_summary["tax_year"] == yr) & (df_summary["component"] == "total_delta_$M")].iloc[0]["total"]
        itep    = ITEP_ANNUAL[yr]
        gap     = ours_tt - itep
        sum_tt   += ours_tt
        sum_br   += ours_br
        sum_itep += itep
        print(
            f"{yr:<6} {ours_tt:>+12.1f}M {ours_br:>+14.1f}M "
            f"{itep:>+8.1f}M {gap:>+16.1f}M",
            flush=True,
        )
    print("-" * 70, flush=True)
    print(
        f"{'5yrΣ':<6} {sum_tt:>+12.1f}M {sum_br:>+14.1f}M "
        f"{sum_itep:>+8.1f}M {sum_tt - sum_itep:>+16.1f}M",
        flush=True,
    )
    print(
        "(All years above are TAX years. COR / fiscal-note tables are FISCAL "
        "years — FY = TY+1 per DOTAX convention, e.g. TY2027 liability lands "
        "in FY2028 collections. 5yrΣ sums annual snapshots.)",
        flush=True,
    )

    # ── Statute-vs-COR wedge (calibration residual) ───────────────────────────
    # Levels above are STATUTORY recomputes — Phase 2's COR anchoring is not
    # carried into scenario scoring. The wedge below is the gap between the
    # statutory baseline aggregate and the COR target each year.
    if wedge_by_year:
        print("\n--- Statute-vs-COR wedge (baseline calibration residual) ---", flush=True)
        print(f"{'TY':<6} {'Statutory $M':>14} {'COR target $M':>15} {'Ratio':>8}", flush=True)
        print("-" * 47, flush=True)
        for yr in TARGET_YEARS:
            f = wedge_by_year.get(yr)
            if f is None:
                continue
            print(
                f"{yr:<6} {f.statutory_tax_M:>13,.0f}M {f.aggregate_tax_M:>14,.0f}M "
                f"{f.statute_vs_cor_wedge:>8.3f}",
                flush=True,
            )
        print(
            "(Reported levels are statutory, NOT COR-anchored; deltas are "
            "differences of statutory recomputes and unaffected by the wedge.)",
            flush=True,
        )

    print(f"\nSaved: {Q_CSV}", flush=True)
    print(f"Saved: {B_CSV}", flush=True)
    print(f"Total elapsed: {time.perf_counter() - wall:.1f}s", flush=True)


if __name__ == "__main__":
    args = _parse_args()
    try:
        main(cd=args.cd, replicate_se=bool(args.replicate_se))
    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
