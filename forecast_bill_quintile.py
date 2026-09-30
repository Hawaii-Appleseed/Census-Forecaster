"""Bill-vs-Act-46 quintile distributional analysis (MID scenario, TY 2027-2031).

Usage:
  python forecast_bill_quintile.py --bill sb3125_cd1   # SB 3125 CD1 (default)
  python forecast_bill_quintile.py --bill sb3125_sd1   # SB 3125 SD1
  python forecast_bill_quintile.py --bill hb2306_hd1   # HB 2306 HD1

Replaces (identical pipeline, differed only in tax system + credit overlay
+ report labels):
  forecast_sb3125_quintile.py
  forecast_hb2306_quintile.py

Uses the full revised pipeline:
  - use_forward_targets:  rake to forward DOTAX-shaped filer counts;
                          Phase 2 anchors aggregate tax to COR projection.
  - use_soi_anchor:       SOI Table 1.4 tier composition for $1M+ filers
                          (CG/wages/business shares from administrative data).
  - use_cbo_aging:        per-component CBO Outlook nominal aging (wages 4.4%/yr,
                          CG 5-9%/yr, business 4.8%/yr) instead of uniform B19013
                          county-median growth. With default Hawaii calibration
                          (DEFAULT_HAWAII_FACTORS) on by default.

SB 3125 bills carry the REEC credit overlay via compute_credit_overlay
(REEC §235-12.5, CGEC §235-110.31, TCRA §235-15) at MID scenario parameters:
    obbba_mid REEC demand, eff_share=0.65, cgec_growth=1.5%/yr, reec_cf_m=$6M.
HB 2306 does not cap or restrict REEC — no overlay; its enhanced refundable
CDCC is captured per-filer via credit_scenario='hb2306_hd1'.

Outputs keep the per-bill /tmp filenames of the replaced scripts.
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

import pandas as pd

from tax_modeler.pipeline import _compute_base_tax, _enrich_for_credits
from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
from tax_modeler.config.tax_system_config import TaxCalculator
from tax_modeler.scenarios.top_income_synthesis import (
    synthesize_top_filers, calibrate_synthetic_tail_to_tax_target,
    redistribute_mid_high_incomes,
)
from tax_modeler.calibration.year_recalibrator import project_and_recalibrate
from tax_modeler.scenarios.sb3125_cd1_credits import compute_credit_overlay
from tax_modeler.scenarios.quintile_analysis import (
    generate_quintile_report, compute_quintile_breaks,
    cor_scale_factor_for_year, per_unit_tax,
)
from tax_modeler.reporting import BLUE, GREEN, NAVY, make_quintile_pdf
from tax_modeler.scenarios.registry import baseline_for, get_scenario

from _forecast_common import CALIBRATED_PKL, TARGET_YEARS

# MID scenario parameters (matches forecast_sb3125_enhanced.py MID).
MID_ALPHA           = 1.5
MID_TOP_PREMIUM     = 0.010
MID_REEC            = "obbba_mid"
MID_REEC_EFF_SHARE  = 0.65
MID_CGEC_GROWTH     = 0.015
MID_REEC_CF_M       = 6.0
MID_CORP_AGI_LIMIT  = False

Q_LABELS = ["Q1 (bottom 20%)", "Q2", "Q3", "Q4", "Q5 (top 20%)"]

COR_FOOTNOTE_SHORT = (
    "Per-quintile values are RAW microsim. COR-anchored totals scale to the "
    "official Council on Revenues IIT baseline."
)
COR_FOOTNOTE_LONG = (
    "Per-quintile values are RAW microsim. COR-anchored totals scale to the "
    "official Council on Revenues IIT baseline to account for population-coverage "
    "gaps (PTE filers, non-resident withholding, withholding-only filers)."
)

# Presentation only — output paths, accent color, report copy. The domain
# facts (tax system, credit-overlay mode, baseline) come from
# tax_modeler.scenarios.registry, so adding a bill here is a presentation
# entry rather than another copy of the system/overlay ladder.
SB3125_SUBTITLE = (
    "Per-household impact (bracket change + REEC/CGEC/TCRA credit overlay), "
    "ITEP-anchored 2026 household-income quintiles, TY 2027 – 2031"
)
PRESENTATION = {
    "sb3125_cd1": {
        "accent": BLUE,
        "q_csv": "/tmp/sb3125_quintile_revised_2027_2031.csv",
        "b_csv": "/tmp/sb3125_bracket_revised_2027_2031.csv",
        "pdf": "/tmp/sb3125_quintile_distributional_report.pdf",
        "table_subtitle": SB3125_SUBTITLE,
        "cor_footnote": COR_FOOTNOTE_SHORT,
        "pdf_title": "SB 3125 CD1 Distributional Analysis (Revised Pipeline)",
    },
    "sb3125_sd1": {
        "accent": BLUE,
        "q_csv": "/tmp/sb3125_sd1_quintile_revised_2027_2031.csv",
        "b_csv": "/tmp/sb3125_sd1_bracket_revised_2027_2031.csv",
        "pdf": "/tmp/sb3125_sd1_quintile_distributional_report.pdf",
        "table_subtitle": SB3125_SUBTITLE,
        "cor_footnote": COR_FOOTNOTE_SHORT,
        "pdf_title": "SB 3125 SD1 Distributional Analysis (Revised Pipeline)",
    },
    "hb2306_hd1": {
        "accent": GREEN,
        "q_csv": "/tmp/hb2306_quintile_mid_2027_2031.csv",
        "b_csv": "/tmp/hb2306_bracket_mid_2027_2031.csv",
        "pdf": "/tmp/hb2306_quintile_distributional_report.pdf",
        "table_subtitle": (
            "Per-household impact by ITEP-anchored 2026 household-income "
            "quintile, TY 2027 – 2031"
        ),
        "cor_footnote": COR_FOOTNOTE_LONG,
        "pdf_title": "HB 2306 HD1 Distributional Analysis",
    },
}

# Bills this script can render (registry slugs with presentation defined).
BILLS = sorted(PRESENTATION)


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--bill", choices=BILLS, default="sb3125_cd1",
        help="Bill variant to model vs the Act 46 baseline.",
    )
    return p.parse_args()


def main(bill: str = "sb3125_cd1") -> None:
    scenario = get_scenario(bill)
    baseline = baseline_for(scenario)
    spec = PRESENTATION[bill]
    label = scenario.label

    if not CALIBRATED_PKL.exists():
        print(f"ERROR: {CALIBRATED_PKL} not found. Run forecast_sb3125_enhanced.py first.")
        sys.exit(1)

    import time
    wall = time.perf_counter()

    print("Loading calibrated base...", flush=True)
    from tax_modeler.artifacts import load_calibrated_base
    base, cal_ded_params, cal_meta = load_calibrated_base(CALIBRATED_PKL)
    cal_tax_year = int(cal_meta.get("tax_year", 2023))

    print(f"Synthesizing top filers (MID alpha={MID_ALPHA})...", flush=True)
    # $500K-$1M income redistribution: rewrite each filer's income via
    # truncated Pareto inverse-CDF mapped to weighted percentile. Preserves
    # PUMS demographics (filing status mix stays ~62% MFJ / 26% MFS / 10%
    # Single / 2.5% HoH) so the surcharge-relevant Single+MFS share isn't
    # diluted. Capped just below $1M; no overlap with $1M+ synthesis.
    units = redistribute_mid_high_incomes(base, pareto_alpha=MID_ALPHA)
    # $1M+ Pareto synthesis (existing flow, replaces $1M+ rows)
    units = synthesize_top_filers(units, pareto_alpha=MID_ALPHA)
    units = _enrich_for_credits(units)              # adds total_cash_income for TCI quintile binning
    units = impute_capital_gains_from_soi(units)    # Phase 3: CG rate cap for $100K-$1M filers
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

    # Anchor quintile boundaries to 2026 household-income distribution
    base_2026_breaks = compute_quintile_breaks(units)
    print(
        f"  2026 quintile breaks: "
        f"${base_2026_breaks[0]:,.0f} / ${base_2026_breaks[1]:,.0f} / "
        f"${base_2026_breaks[2]:,.0f} / ${base_2026_breaks[3]:,.0f}",
        flush=True,
    )

    quintile_frames, bracket_frames = [], []

    # MID scenario dict for REEC distribution inside generate_quintile_report
    # (only used when the bill carries the credit overlay).
    mid_scenario_params = {
        "label":          "MID",
        "alpha":          MID_ALPHA,
        "reec":           MID_REEC,
        "behav":          "mid",
        "corp_agi_limit": MID_CORP_AGI_LIMIT,
        "top_premium":    MID_TOP_PREMIUM,
        "reec_eff_share": MID_REEC_EFF_SHARE,
        "cgec_growth":    MID_CGEC_GROWTH,
        "reec_cf_m":      MID_REEC_CF_M,
    } if scenario.overlay != "none" else None

    for yr in TARGET_YEARS:
        print(f"  TY {yr}...", flush=True)
        # ITEP-style year-by-year IRS target matching: re-rake weights to
        # forward DOTAX-shaped filer counts BEFORE the premium, then re-anchor
        # aggregate tax to COR's annual IIT projection AFTER premium + tax
        # recompute.
        # Methodology stack (ITEP-aligned):
        #   - use_forward_targets: rake to forward DOTAX-shaped filer counts
        #     and re-anchor aggregate tax to COR projection per year.
        #   - use_soi_anchor: anchor $1M+ tier composition (CG / wages /
        #     business / dividends) on national IRS SOI Table 1.4.
        #   - use_cbo_aging: age each filer's income components at CBO
        #     Outlook nominal rates (wages 4.4%/yr, CG 5-9%/yr, etc.) instead
        #     of uniform B19013 county-median growth.
        # Default Hawaii calibration factors apply (DEFAULT_HAWAII_FACTORS):
        # wages 0.85x national, business 0.90x, capital_gains 1.00x, others
        # 1.00x. Pass cbo_hawaii_factors={c: 1.0 ...} for pure CBO-national
        # aging (matches ITEP methodology by construction).
        projected, _forward = project_and_recalibrate(
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

        baseline_cfg = baseline.system_for(yr)
        scenario_cfg = scenario.system_for(yr)

        # COR scaling: ratio of official COR FY{yr} IIT projection to our
        # microsim Act 46 baseline. Brings totals into ITEP/COR comparable units.
        act46_M = float(
            (per_unit_tax(projected, baseline_cfg, calc)
             * projected["weight"].to_numpy(dtype=float)).sum() / 1e6
        )
        cor_factor = cor_scale_factor_for_year(yr, act46_M)

        if scenario.overlay != "none":
            # REEC + CGEC + TCRA credit overlay for this year
            credit_overlay = compute_credit_overlay(
                yr,
                reec_demand_scenario=MID_REEC,
                corp_subject_to_agi_limit=MID_CORP_AGI_LIMIT,
                reec_effective_claim_share=MID_REEC_EFF_SHARE,
                cgec_annual_growth=MID_CGEC_GROWTH,
                reec_carryforward_utilization_m=MID_REEC_CF_M,
            )
        else:
            # Bill does not restrict REEC — empty overlay dict makes
            # generate_quintile_report skip credit-loss distribution.
            credit_overlay = {}

        q_df, b_df, _ = generate_quintile_report(
            projected, baseline_cfg, scenario_cfg,
            credit_overlay=credit_overlay,
            calc=calc,
            scenario_params=mid_scenario_params,
            quintile_breaks=base_2026_breaks,
            cor_scale_factor=cor_factor,
        )
        q_df.insert(0, "tax_year", yr)
        b_df.insert(0, "tax_year", yr)
        quintile_frames.append(q_df)
        bracket_frames.append(b_df)

    all_quintiles = pd.concat(quintile_frames, ignore_index=True)
    all_brackets  = pd.concat(bracket_frames,  ignore_index=True)

    Q_CSV = Path(spec["q_csv"])
    B_CSV = Path(spec["b_csv"])
    all_quintiles.to_csv(Q_CSV, index=False)
    all_brackets.to_csv(B_CSV, index=False)
    print(f"Saved: {Q_CSV}", flush=True)
    print(f"Saved: {B_CSV}", flush=True)

    _print_summary(all_quintiles, label, with_credit=scenario.overlay != "none")

    # Generate PDF
    print("\nGenerating PDF...", flush=True)
    subtitle = f"{label} vs Act 46 — MID Scenario"
    make_quintile_pdf(
        all_quintiles, Path(spec["pdf"]),
        q_labels=Q_LABELS,
        table_title=f"{label} vs Act 46 — Distributional Impact (MID Scenario)",
        table_subtitle=spec["table_subtitle"],
        table_specs=[
            ("Avg Tax Change per Household",           "avg_per_hh_total_change", "dollar"),
            ("Share of Households with Tax Increase",  "pct_pay_more",            "pct"),
            ("Total Tax Change for Quintile",          "total_change_$M",         "millions"),
        ],
        chart_specs=[
            ("Average Tax Change per Household by Quintile", subtitle,
             "avg_per_hh_total_change", "dollar", spec["accent"]),
            ("Share of Households with a Tax Increase", subtitle,
             "pct_pay_more", "pct", NAVY),
            ("Total Tax Change by Quintile", subtitle,
             "total_change_$M", "millions", spec["accent"]),
        ],
        cor_footnote=spec["cor_footnote"],
        pdf_meta={"Title": spec["pdf_title"], "Author": "Census-Forecaster"},
    )

    print(f"Total elapsed: {time.perf_counter()-wall:.1f}s", flush=True)


def _print_summary(all_quintiles: pd.DataFrame, label: str, *, with_credit: bool) -> None:
    """TY 2027 summary + cumulative/annual totals to stdout."""
    q27 = all_quintiles[all_quintiles["tax_year"] == 2027]
    pd.set_option("display.float_format", "{:,.1f}".format)
    print("\n" + "=" * 100, flush=True)
    print(f"{label} vs Act 46 — MID Scenario, TY 2027 (full revised pipeline)", flush=True)
    print("=" * 100, flush=True)
    cols = [
        "quintile", "household_count",
        "avg_per_hh_bracket_change", "avg_per_hh_credit_loss", "avg_per_hh_total_change",
        "total_bracket_$M", "total_credit_loss_$M", "total_change_$M",
        "pct_pay_more", "pct_pay_less",
    ]
    print(q27[[c for c in cols if c in q27.columns]].to_string(index=False), flush=True)

    total_act46 = q27["total_act46_$M"].sum()
    _scen_col = next(
        (c for c in ["total_cd1_$M", "total_sd1_$M"] if c in all_quintiles.columns),
        "total_change_$M",
    )
    total_scen = q27[_scen_col].sum()
    total_brk  = q27["total_bracket_$M"].sum()
    total_crd  = q27["total_credit_loss_$M"].sum() if with_credit else 0.0
    print(
        f"\nTY 2027 totals:"
        f"\n  Act 46 baseline:          ${total_act46:>9.1f}M"
        f"\n  {label} scenario:   ${total_scen:>9.1f}M"
        f"\n  Bracket delta:            ${total_brk:>+9.1f}M"
        f"\n  Credit-loss overlay:      ${total_crd:>+9.1f}M"
        f"\n  Total fiscal impact:      ${total_scen-total_act46+total_crd:>+9.1f}M",
        flush=True,
    )

    cum_brk = all_quintiles["total_bracket_$M"].sum()
    cum_crd = all_quintiles["total_credit_loss_$M"].sum() if with_credit else 0.0
    cum_total = all_quintiles["total_change_$M"].sum()
    print(
        f"\n5-year cumulative (TY27-31):"
        f"\n  Bracket delta:          ${cum_brk:>+9.1f}M"
        f"\n  Credit-loss overlay:    ${cum_crd:>+9.1f}M"
        f"\n  Total fiscal impact:    ${cum_total:>+9.1f}M",
        flush=True,
    )

    print("\n" + "-" * 100, flush=True)
    print("Annual totals by year ($M):", flush=True)
    print("-" * 100, flush=True)
    yearly_cols = [c for c in ["total_bracket_$M", "total_credit_loss_$M", "total_change_$M"]
                   if c in all_quintiles.columns]
    yearly = all_quintiles.groupby("tax_year")[yearly_cols].sum()
    print(yearly.to_string(float_format=lambda x: f"{x:>+9.1f}"), flush=True)


if __name__ == "__main__":
    args = _parse_args()
    try:
        main(bill=args.bill)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
