"""SB 3125 distributional analysis by income quintile, TY 2027–2031.

Usage:
  python forecast_sb3125_static_quintile.py --cd 1   # SB 3125 CD1 (default)
  python forecast_sb3125_static_quintile.py --cd 2   # SB 3125 CD2

Replaces:
  forecast_sb3125_cd1_quintile.py
  forecast_sb3125_cd2_quintile.py

"Static" reflects the scoring methodology: per-unit tax is computed at projected
income before ETI/migration adjustments, consistent with standard distributional
analysis (CBO / Tax Policy Center methodology). Distinguishes this script from
the dynamic forecast_sb3125_quintile.py which uses the full project_and_recalibrate
pipeline.

For each tax year, computes the per-filer bracket-change impact (SB 3125 CD{N}
vs Act 46 baseline) and aggregates into five equal-population income quintiles.

Scope: bracket change only (§235-51). REEC/CGEC/TCRA credit components are
aggregate static-scoring overlays not attributable to individual filers and are
excluded from the quintile breakdown (CD2: shown in a separate REEC section of
the PDF).

Scenario: MID (Pareto α=1.5, itemized_adj=True) — calibrated best-estimate.

Output (--cd 1):
  /tmp/sb3125_cd1_quintile_2027_2031.csv

Output (--cd 2):
  /tmp/sb3125_cd2_quintile_2027_2031.csv
  /tmp/sb3125_cd2_quintile_distributional_report.pdf
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from _forecast_common import (
    TARGET_YEARS, load_cached_units, parse_cd_args, silence_noise,
)

# MID scenario parameters (calibrated best-estimate)
PARETO_ALPHA   = 1.5
ITEMIZED_ADJ   = True
TOP_PREMIUM    = 0.0   # no additional top-income growth premium for MID

QUINTILE_LABELS = [
    "Q1 (Bottom 20%)",
    "Q2",
    "Q3",
    "Q4",
    "Q5 (Top 20%)",
]


def _fmt_dollar(x: float) -> str:
    if abs(x) >= 1_000_000:
        return f"${x/1_000_000:,.2f}M"
    if abs(x) >= 1_000:
        return f"${x:,.0f}"
    return f"${x:.0f}"


def compute_quintile_breakdown(projected, baseline_cfg, scenario_cfg, calc, *, cd="1"):
    """Return a DataFrame with one row per income quintile for the given year."""
    import pandas as pd

    proj = projected.copy()

    # ---- Per-unit tax under each system (static scoring) --------------------
    # CG income for §235-16 cap: synthetic filers carry synthetic_cg_share;
    # base PUMS units don't (default 0 → no cap applied, consistent with
    # ACS not capturing realized capital gains for sub-$1M filers).
    import numpy as _np
    cg_shares = (
        proj["synthetic_cg_share"].fillna(0.0).values
        if "synthetic_cg_share" in proj.columns
        else _np.zeros(len(proj))
    )
    from tax_modeler.liability.hawaii import _count_exemptions

    base_tax, scen_tax = [], []
    ndep_col = proj.get("num_dependents") if hasattr(proj, "get") else proj["num_dependents"]
    for inc, fs, ndep, cg_share in zip(
        proj["income"], proj["filing_status"], ndep_col, cg_shares
    ):
        cg_inc = float(inc) * float(cg_share)
        # Filer, spouse on a joint return, dependents (was dependents + 1,
        # which dropped the spouse's exemption on every joint return).
        n_ex = _count_exemptions(fs, int(ndep))
        b = calc.calculate_tax(inc, baseline_cfg, fs,
                               num_exemptions=n_ex, cg_income=cg_inc)
        s = calc.calculate_tax(inc, scenario_cfg, fs,
                               num_exemptions=n_ex, cg_income=cg_inc)
        base_tax.append(b["tax_liability"])
        scen_tax.append(s["tax_liability"])

    proj["base_tax"] = base_tax
    proj["scen_tax"] = scen_tax
    proj["delta"]    = proj["scen_tax"] - proj["base_tax"]

    # ---- Quintile assignment (equal-population, cumulative weight) ----------
    proj_sorted = proj.sort_values("income").reset_index(drop=True)
    cumw        = proj_sorted["weight"].cumsum()
    total_w     = float(cumw.iloc[-1])
    proj_sorted["quintile"] = pd.cut(
        cumw / total_w,
        bins=[i / 5 for i in range(6)],
        labels=QUINTILE_LABELS,
        include_lowest=True,
    )

    # ---- Weighted aggregation per quintile ----------------------------------
    grp = proj_sorted.groupby("quintile", observed=True)

    def wsum(col):
        return grp.apply(lambda d: (d[col] * d["weight"]).sum())

    def wavg(col):
        return grp.apply(
            lambda d: (d[col] * d["weight"]).sum() / d["weight"].sum()
        )

    result = pd.DataFrame({
        "income_min_$":                       grp["income"].min(),
        "income_max_$":                       grp["income"].max(),
        "avg_income_$":                       wavg("income"),
        "n_filers":                           grp["weight"].sum(),
        "base_tax_total_$M":                  wsum("base_tax") / 1e6,
        f"sb3125_cd{cd}_tax_total_$M":        wsum("scen_tax") / 1e6,
        "delta_total_$M":                     wsum("delta") / 1e6,
        "avg_delta_per_filer_$":              wavg("delta"),
        "pct_filers_with_change":             grp.apply(
            lambda d: d.loc[d["delta"] != 0, "weight"].sum() / d["weight"].sum() * 100
        ),
    }).reset_index()

    return result


def print_quintile_table(year: int, qt, *, cd: str = "1"):
    """Pretty-print a quintile table for one tax year."""
    cd_label = f"CD{cd}"
    print(f"\n{'─'*95}", flush=True)
    print(
        f"TY {year} — SB 3125 {cd_label} vs Act 46  |  "
        f"Bracket change by income quintile (MID scenario)",
        flush=True,
    )
    print(
        "  (Bracket only; REEC/CGEC/TCRA credit overlay not included in quintile delta)",
        flush=True,
    )
    print(f"{'─'*95}", flush=True)
    hdr = (
        f"  {'Quintile':<20}  {'Income range':>26}  {'Avg income':>12}  "
        f"{'N filers':>10}  {'Delta $M':>10}  {'Avg $/filer':>13}  {'% affected':>10}"
    )
    print(hdr, flush=True)
    print(f"  {'─'*89}", flush=True)
    total_delta = 0.0
    for _, row in qt.iterrows():
        rng = f"{_fmt_dollar(row['income_min_$'])} – {_fmt_dollar(row['income_max_$'])}"
        delta_str = f"{row['delta_total_$M']:+.1f}"
        avg_str   = f"{row['avg_delta_per_filer_$']:+,.0f}"
        pct_str   = f"{row['pct_filers_with_change']:.1f}%"
        print(
            f"  {row['quintile']:<20}  {rng:>26}  "
            f"{_fmt_dollar(row['avg_income_$']):>12}  "
            f"{row['n_filers']:>10,.0f}  "
            f"{delta_str:>10}  "
            f"{avg_str:>13}  "
            f"{pct_str:>10}",
            flush=True,
        )
        total_delta += row["delta_total_$M"]
    print(f"  {'─'*89}", flush=True)
    print(f"  {'TOTAL (all quintiles)':<20}  {'':>26}  {'':>12}  "
          f"{'':>10}  {total_delta:>+10.1f}", flush=True)


def _make_pdf(df: "pd.DataFrame", *, cd: str = "2") -> None:
    """Generate distributional PDF report. Only called for --cd 2.

    Table + three chart pages come from tax_modeler.reporting; the two
    REEC pages (incidence by income group, savings trajectory) are
    supplied as extra_pages.
    """
    import matplotlib
    matplotlib.rcParams["text.parse_math"] = False
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"]
    import matplotlib.pyplot as plt
    import numpy as np

    from tax_modeler.reporting import (
        BLUE, NAVY, ORANGE, PILL_BG, PILL_FG, RULE, TEAL, TXT_GREY,
        make_quintile_pdf,
    )
    from tax_modeler.scenarios.sb3125_cd1_credits import (
        REEC_CORPORATE_TOTAL_M,
        REEC_INDIVIDUAL_BY_AGI_BIN,
        REEC_OTHER_TOTAL_M,
    )

    cd_label     = f"CD{cd}"
    PDF_OUT      = Path(f"/tmp/sb3125_cd{cd}_quintile_distributional_report.pdf")
    # Prefer the manifested runs/ copy; fall back to the legacy /tmp mirror.
    _runs_enh    = Path(__file__).parent / "runs" / f"sb3125_cd{cd}_enhanced" / "enhanced.csv"
    ENHANCED_CSV = _runs_enh if _runs_enh.exists() else Path(f"/tmp/sb3125_cd{cd}_enhanced_2027_2031.csv")

    # DOTAX TY2023 actuals from the scenario library; en-dash for display.
    REEC_IND_BINS = [
        (label.replace("-", "\u2013"), claim, elig)
        for label, claim, elig in REEC_INDIVIDUAL_BY_AGI_BIN
    ]
    REEC_CORP_M = REEC_CORPORATE_TOTAL_M + REEC_OTHER_TOTAL_M  # corp + other TY2023

    def _reec_rows(pro_rata: float) -> list:
        rows = []
        for label, claim, elig in REEC_IND_BINS:
            eligible  = claim * elig
            after_cap = eligible * pro_rata
            rows.append({
                "group":      label,
                "baseline":   claim,
                "eligible":   eligible,
                "lost_agi":   claim - eligible,
                "after_cap":  after_cap,
                "lost_cap":   eligible - after_cap,
                "total_lost": (claim - eligible) + (eligible - after_cap),
                "pct_lost":   ((claim - eligible) + (eligible - after_cap)) / claim * 100,
            })
        corp_after = REEC_CORP_M * pro_rata
        rows.append({
            "group":      "Corporate / Other",
            "baseline":   REEC_CORP_M,
            "eligible":   REEC_CORP_M,
            "lost_agi":   0.0,
            "after_cap":  corp_after,
            "lost_cap":   REEC_CORP_M - corp_after,
            "total_lost": REEC_CORP_M - corp_after,
            "pct_lost":   (REEC_CORP_M - corp_after) / REEC_CORP_M * 100,
        })
        return rows

    def reec_incidence_page(pdf, pro_rata: float = 0.7806):
        rows = _reec_rows(pro_rata)
        fig  = plt.figure(figsize=(11, 10))
        fig.suptitle(
            "REEC Credit Restriction — Impact by Income Group",
            fontsize=14, fontweight="bold", y=0.97, color=NAVY,
        )
        fig.text(
            0.5, 0.935,
            f"TY2023 DOTAX actuals  ·  TY2027 MID pro-rata factor {pro_rata:.0%}  "
            f"·  §235-12.5 AGI limits ($175K single / $350K joint) + $40M aggregate cap",
            ha="center", fontsize=9.5, style="italic", color=TXT_GREY,
        )
        retained = [r["after_cap"]  for r in rows]
        lost_cap = [r["lost_cap"]   for r in rows]
        lost_agi = [r["lost_agi"]   for r in rows]
        yp       = np.arange(len(rows))[::-1]
        ax       = fig.add_axes([0.26, 0.18, 0.68, 0.70])

        ax.barh(yp, retained, height=0.55, color=TEAL,   edgecolor="none", label="Retained after cap")
        ax.barh(yp, lost_cap, height=0.55, color=ORANGE, edgecolor="none",
                left=retained, label="Lost to cap (pro-rata)")
        ax.barh(yp, lost_agi, height=0.55, color=NAVY,   edgecolor="none",
                left=[r + c for r, c in zip(retained, lost_cap)], label="Lost to AGI filter")

        for ypos, row in zip(yp, rows):
            ax.text(
                row["baseline"] + 0.4, ypos,
                f"-${row['total_lost']:.1f}M  ({row['pct_lost']:.0f}%)",
                va="center", ha="left", fontsize=9, color=NAVY, fontweight="bold",
            )

        ax.set_yticks(yp)
        ax.set_yticklabels([])
        for ypos, row in zip(yp, rows):
            is_corp = row["group"] == "Corporate / Other"
            ax.text(
                -0.01, ypos, row["group"],
                transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontsize=9.5, fontweight="bold" if is_corp else "normal",
                color=NAVY if is_corp else PILL_FG,
                bbox=dict(boxstyle="round,pad=0.35", facecolor=PILL_BG, edgecolor="none"),
            )

        ax.axhline(y=0.5, color=RULE, linewidth=1.0, linestyle="--")
        ax.set_xlabel("Credit ($M, TY2023 basis)", fontsize=9, color=TXT_GREY)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", colors=TXT_GREY, labelsize=8)
        ax.grid(axis="x", linestyle=":", alpha=0.35, color=RULE)
        ax.set_xlim(0, max(r["baseline"] for r in rows) * 1.45)
        ax.legend(loc="lower right", fontsize=9, framealpha=0.9, edgecolor=RULE)

        ind_lost  = sum(r["total_lost"] for r in rows if r["group"] != "Corporate / Other")
        corp_lost = rows[-1]["total_lost"]
        grand_b   = sum(r["baseline"]   for r in rows)
        grand_l   = sum(r["total_lost"] for r in rows)
        fig.text(
            0.05, 0.10,
            "Amounts from DOTAX TY2023 'Tax Credits Claimed' (most recent available). "
            "TY2027 individual demand is lower due to federal §25D termination (OBBBA, PL 119-21) "
            "— relative incidence across groups is similar. "
            "Corporate REEC not subject to AGI limit under MID scenario. "
            "Pro-rata factor reflects endogenous demand suppression (η=0.3, MID).",
            ha="left", fontsize=7.5, style="italic", color=TXT_GREY,
        )
        fig.text(
            0.05, 0.055,
            f"TY2023 totals: individual "
            f"${sum(r['baseline'] for r in rows if r['group'] != 'Corporate / Other'):.1f}M  "
            f"|  corporate ${REEC_CORP_M:.1f}M  |  combined ${grand_b:.1f}M.  "
            f"Aggregate lost (TY2023 basis): "
            f"individual −${ind_lost:.1f}M  |  corporate −${corp_lost:.1f}M  "
            f"|  total −${grand_l:.1f}M.",
            ha="left", fontsize=7.5, color=TXT_GREY,
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    def reec_time_series_page(pdf, df_enh: "pd.DataFrame"):
        scenarios = ["LOW", "MID", "HIGH"]
        colors    = {"LOW": "#8fa8c8", "MID": TEAL, "HIGH": ORANGE}
        yr_list   = [2027, 2028, 2029, 2030, 2031]
        x         = np.arange(len(yr_list))

        fig = plt.figure(figsize=(11, 10))
        fig.suptitle(
            "REEC Savings Over Time — Baseline vs. Bill State Cost",
            fontsize=14, fontweight="bold", y=0.97, color=NAVY,
        )
        fig.text(
            0.5, 0.935,
            f"§235-12.5 renewable energy credit  ·  TY2027–2031  ·  LOW / MID / HIGH scenarios",
            ha="center", fontsize=10, style="italic", color=TXT_GREY,
        )

        # Top: grouped bars of REEC savings by scenario
        ax_top = fig.add_axes([0.10, 0.52, 0.84, 0.36])
        n_s     = len(scenarios)
        width   = 0.22
        offsets = np.linspace(-(n_s - 1) / 2 * width, (n_s - 1) / 2 * width, n_s)
        for scen, offset in zip(scenarios, offsets):
            sub  = df_enh[df_enh["scenario"] == scen].set_index("tax_year")
            vals = [sub.loc[y, "reec_savings_$M"] for y in yr_list]
            bars = ax_top.bar(x + offset, vals, width=width * 0.92,
                              color=colors[scen], edgecolor="none", label=scen)
            for bar, val in zip(bars, vals):
                ax_top.text(
                    bar.get_x() + bar.get_width() / 2, val + 1.0,
                    f"${val:.0f}M", ha="center", va="bottom",
                    fontsize=7.5, color=colors[scen], fontweight="bold",
                )

        ax_top.set_xticks(x)
        ax_top.set_xticklabels([f"TY{y}" for y in yr_list], fontsize=10, color=TXT_GREY)
        ax_top.set_ylabel("REEC savings ($M)", fontsize=9, color=TXT_GREY)
        ax_top.set_title("Annual REEC Fiscal Savings (Baseline − Bill State Cost)",
                         loc="left", fontsize=11, fontweight="bold", color=NAVY)
        ax_top.spines["top"].set_visible(False)
        ax_top.spines["right"].set_visible(False)
        ax_top.spines["left"].set_color(RULE)
        ax_top.spines["bottom"].set_color(RULE)
        ax_top.tick_params(colors=TXT_GREY, labelsize=8)
        ax_top.grid(axis="y", linestyle=":", alpha=0.35, color=RULE)
        ax_top.set_ylim(0, 145)
        ax_top.legend(loc="upper left", fontsize=9, framealpha=0.9, edgecolor=RULE)
        ax_top.axvspan(2.5, 4.5, alpha=0.06, color=NAVY, zorder=0)
        ax_top.text(3.5, 135, "§235-12.5(p)\nsunset", ha="center", va="top",
                    fontsize=8.5, color=NAVY, style="italic")

        # Cumulative callout
        for scen, color in colors.items():
            sub = df_enh[df_enh["scenario"] == scen]
            cum = sub["reec_savings_$M"].sum()
            ax_top.annotate(
                f"{scen} 5yr: ${cum:.0f}M",
                xy=(0, 0), xycoords="axes fraction",
                xytext=(0.01 + list(scenarios).index(scen) * 0.18, 0.05),
                textcoords="axes fraction",
                fontsize=8, color=color, fontweight="bold",
            )

        # Bottom: MID cost decomposition
        ax_bot = fig.add_axes([0.10, 0.10, 0.84, 0.34])
        mid      = df_enh[df_enh["scenario"] == "MID"].set_index("tax_year")
        base_c   = [mid.loc[y, "reec_base_state_cost_$M"]  for y in yr_list]
        scen_ref = [mid.loc[y, "reec_scen_refundable_$M"]   for y in yr_list]
        scen_nr  = [mid.loc[y, "reec_scen_nonref_usage_$M"] for y in yr_list]

        ax_bot.plot(x, base_c, color=NAVY, linewidth=2.0, marker="o",
                    markersize=5, label="Baseline (no cap)", zorder=3)
        ax_bot.bar(x, scen_ref, width=0.45, color=TEAL,     edgecolor="none",
                   label="Bill: refundable (in-year)", zorder=2)
        ax_bot.bar(x, scen_nr,  width=0.45, color="#8fa8c8", edgecolor="none",
                   bottom=scen_ref, label="Bill: nonref carryforward drawdown", zorder=2)

        ax_bot.set_xticks(x)
        ax_bot.set_xticklabels([f"TY{y}" for y in yr_list], fontsize=10, color=TXT_GREY)
        ax_bot.set_ylabel("State cost ($M)", fontsize=9, color=TXT_GREY)
        ax_bot.set_title("MID Scenario — State Cost Components",
                         loc="left", fontsize=11, fontweight="bold", color=NAVY)
        ax_bot.spines["top"].set_visible(False)
        ax_bot.spines["right"].set_visible(False)
        ax_bot.spines["left"].set_color(RULE)
        ax_bot.spines["bottom"].set_color(RULE)
        ax_bot.tick_params(colors=TXT_GREY, labelsize=8)
        ax_bot.grid(axis="y", linestyle=":", alpha=0.35, color=RULE)
        ax_bot.set_ylim(0, 125)
        ax_bot.legend(loc="upper right", fontsize=9, framealpha=0.9, edgecolor=RULE)
        ax_bot.axvspan(2.5, 4.5, alpha=0.06, color=NAVY, zorder=0)

        fig.text(
            0.05, 0.03,
            "Baseline: Act 46 (no cap, no AGI limit, no sunset). "
            f"Bill: §235-12.5 — $40M cap TY2027–2029, $0 new certifications TY2030+ (§(p) sunset). "
            "Savings = baseline − bill state cost. "
            "LOW: interpretation A (TY2026 cap binds), η=0.5. "
            "MID/HIGH: interpretation B, η=0.3/0.15.",
            ha="left", fontsize=7.5, style="italic", color=TXT_GREY,
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    # Pull TY2027 MID pro-rata for incidence page; fall back to hardcoded if CSV missing
    pro_rata_mid = 0.7806
    df_enh       = None
    if ENHANCED_CSV.exists():
        import pandas as _pd
        df_enh = _pd.read_csv(ENHANCED_CSV)
        mid_2027 = df_enh[(df_enh["scenario"] == "MID") & (df_enh["tax_year"] == 2027)]
        if not mid_2027.empty and "reec_pro_rata_factor" in mid_2027.columns:
            pro_rata_mid = float(mid_2027["reec_pro_rata_factor"].iloc[0])

    extra_pages = [lambda pdf: reec_incidence_page(pdf, pro_rata=pro_rata_mid)]
    if df_enh is not None:
        extra_pages.append(lambda pdf: reec_time_series_page(pdf, df_enh))
    else:
        print(f"  (skipping REEC time-series page — {ENHANCED_CSV} not found)", flush=True)

    subtitle = f"SB 3125 {cd_label} vs Act 46 — MID Scenario  ·  §235-51 bracket changes only"
    make_quintile_pdf(
        df, PDF_OUT,
        q_labels=QUINTILE_LABELS,
        table_title=f"SB 3125 {cd_label} vs Act 46 — Distributional Impact (MID Scenario)",
        table_subtitle=(
            f"Per-filer bracket-change impact by income quintile, TY 2027–2031  "
            f"·  §235-51 only  ·  REEC/CGEC/TCRA shown separately on pages 5–6"
        ),
        table_specs=[
            ("Avg Tax Change per Filer",        "avg_delta_per_filer_$",  "dollar"),
            ("Share of Filers with Any Change", "pct_filers_with_change", "pct"),
            ("Total Tax Change for Quintile",   "delta_total_$M",         "millions"),
        ],
        chart_specs=[
            ("Average Tax Change per Filer by Quintile", subtitle,
             "avg_delta_per_filer_$", "dollar", BLUE),
            ("Share of Filers with Any Tax Change", subtitle,
             "pct_filers_with_change", "pct", NAVY),
            ("Total Tax Change by Quintile", subtitle,
             "delta_total_$M", "millions", BLUE),
        ],
        table_footnote=(
            f"Positive = filer pays more under SB 3125 {cd_label} vs Act 46 (bracket change only). "
            "Bottom quintiles see tax cuts from lower mid-bracket rates; top quintile sees "
            "increases from the new 13% bracket. Credit-cap savings (REEC) shown on pages 5–6."
        ),
        table_bottom=0.06,
        extra_pages=extra_pages,
        pdf_meta={
            "Title":   f"SB 3125 {cd_label} Distributional Analysis",
            "Author":  "Census-Forecaster",
            "Subject": f"Per-quintile bracket impact + REEC credit incidence, TY 2027–2031, MID",
        },
    )


if __name__ == "__main__":
    silence_noise()

    args = parse_cd_args(__doc__)
    CD       = args.cd
    OUT_CSV  = Path(f"/tmp/sb3125_cd{CD}_quintile_2027_2031.csv")
    cd_label = f"CD{CD}"

    try:
        import time
        import pandas as pd

        print(f"SB 3125 {cd_label} static quintile analysis", flush=True)
        print("Importing modules...", flush=True)
        from tax_modeler.pipeline import _enrich_for_credits, _compute_base_tax, _calibrate
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        from tax_modeler.config.tax_system_config import (
            TaxCalculator, TaxSystemRegistry,
        )
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers, validate_top_synthesis,
            rescale_synthetic_tail_to_tax_target,
        )
        from tax_modeler.scenarios.behavioral_response import (
            apply_top_income_growth_premium,
            apply_itemized_deduction_adjustment,
        )

        get_scenario_system = (
            TaxSystemRegistry.get_sb3125_cd1_system if CD == "1"
            else TaxSystemRegistry.get_sb3125_cd2_system
        )

        wall_start = time.perf_counter()

        # ---- One-time setup -------------------------------------------------
        from tax_modeler.artifacts import load_canonical_deduction_params
        units = load_cached_units(CD)

        print("Enriching + base tax + calibrating...", flush=True)
        t0 = time.perf_counter()
        # Score on the canonical itemized-deduction basis — bare
        # _compute_base_tax (SD-only) made the tail rescale inconsistent (C3).
        CAL_DED_PARAMS = load_canonical_deduction_params()
        units = _enrich_for_credits(units)
        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        units = _calibrate(units)
        print(f"  Done in {time.perf_counter()-t0:.1f}s", flush=True)

        print(f"Synthesizing top-income filers (Pareto α={PARETO_ALPHA})...", flush=True)
        t0 = time.perf_counter()
        units = synthesize_top_filers(units, pareto_alpha=PARETO_ALPHA)
        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        units, tail_k = rescale_synthetic_tail_to_tax_target(units)
        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        v = validate_top_synthesis(units)
        print(f"  {v['filers_1m_plus']:,.0f} filers @ $1M+ "
              f"({100*v['filer_target_ratio']:.1f}%), "
              f"${v['tax_1m_plus_$M']:,.1f}M tax "
              f"({100*v['tax_target_ratio']:.1f}% of $663M target), "
              f"tail_k={tail_k:.4f} "
              f"in {time.perf_counter()-t0:.1f}s", flush=True)

        # ---- Per-year quintile analysis -------------------------------------
        calc = TaxCalculator()
        all_rows = []

        for year in TARGET_YEARS:
            t0 = time.perf_counter()
            print(f"\nTY {year}: projecting...", flush=True)

            projected = project_tax_units_forward(
                units, target_year=year, method="ensemble"
            )
            # MID adjustments: no growth premium, itemized deduction adjustment
            projected = apply_top_income_growth_premium(
                projected, target_year=year, annual_premium=TOP_PREMIUM
            )
            if ITEMIZED_ADJ:
                projected = apply_itemized_deduction_adjustment(projected)

            baseline_cfg = TaxSystemRegistry.get_act46_system(year)
            scenario_cfg = get_scenario_system(year)

            print(f"  Computing per-unit tax ({len(projected):,} units × 2 systems)...",
                  flush=True)
            qt = compute_quintile_breakdown(projected, baseline_cfg, scenario_cfg, calc, cd=CD)
            qt.insert(0, "tax_year", year)

            print_quintile_table(year, qt, cd=CD)
            all_rows.append(qt)
            print(f"  TY {year} done in {time.perf_counter()-t0:.1f}s", flush=True)

        # ---- Save combined CSV ----------------------------------------------
        df_out = pd.concat(all_rows, ignore_index=True)
        df_out.to_csv(OUT_CSV, index=False)

        # ---- Summary pivot: Q5 delta across years ---------------------------
        print(f"\n{'='*95}", flush=True)
        print("Q5 (Top 20%) bracket delta by year — the filers driving most of the revenue gain",
              flush=True)
        print(f"{'='*95}", flush=True)
        q5 = df_out[df_out["quintile"] == "Q5 (Top 20%)"].set_index("tax_year")
        for yr in TARGET_YEARS:
            r = q5.loc[yr]
            print(f"  TY {yr}: income ${r['income_min_$']:,.0f}–${r['income_max_$']:,.0f}  "
                  f"avg ${r['avg_income_$']:,.0f}  "
                  f"delta {r['delta_total_$M']:+.1f}M  "
                  f"avg/filer ${r['avg_delta_per_filer_$']:+,.0f}",
                  flush=True)

        print(f"\nSaved: {OUT_CSV}", flush=True)

        # ---- PDF generation (CD2 only) -------------------------------------
        if CD == "2":
            print("\nRendering PDF...", flush=True)
            _make_pdf(df_out, cd=CD)

        print(f"Total elapsed: {time.perf_counter()-wall_start:.1f}s", flush=True)

    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
