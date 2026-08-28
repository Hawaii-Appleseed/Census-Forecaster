"""Standalone RETITC analysis PDF report — §235-12.5 Renewable Energy Technologies Tax Credit.

9-page PDF, portrait letter (8.5 × 11), focused entirely on the RETITC:
  1. Cover
  2. About the RETITC — plain-language intro & what SB 3125 changes
  3. Historical claims TY2018-2023 by taxpayer type
  4. AGI distribution of individual claims TY2023
  5. Baseline demand projections TY2024-2031 (3 OBBBA scenarios)
  6. SB 3125 CD{N} projected RETITC savings TY2027-2031
  7. Carryforward pool dynamics (OBBBA Mid)
  8. Summary table — all scenarios × all years
  9. Glossary

Self-contained: no cached tax-unit file required.

Usage:
  python generate_reec_report.py --cd 1
  python generate_reec_report.py --cd 2

Output: /tmp/reec_cd{N}_analysis_report.pdf
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

REPO         = Path(__file__).parent
TARGET_YEARS = [2027, 2028, 2029, 2030, 2031]
DEMAND_YEARS = list(range(2024, 2032))

SCENARIO_KEYS  = ["pre_obbba", "obbba_mid", "obbba_severe"]
SCENARIO_SHORT = ["Pre-OBBBA",  "OBBBA Mid",  "OBBBA Severe"]

UTIL_RATE = 0.65
_CD2_KNOBS = dict(pro_rata_elasticity=0.3, interpretation="A", dynamic_refundable_share=True)

# Page geometry — every page is portrait letter
PAGE = (8.5, 11)

# ---------------------------------------------------------------------------
# Color palette — light theme anchored on #3478bd
# ---------------------------------------------------------------------------
PRIMARY      = "#3478bd"   # anchor blue
PRIMARY_DARK = "#1e5a99"   # emphasis/heading
PRIMARY_LIGHT = "#7eaad7"  # mid-tone fills
PRIMARY_PALE = "#b9d2ea"   # soft fill

INK      = "#1e3a5f"   # deep blue-slate for body headings
HEADING  = "#274b73"
BODY     = "#475569"   # slate-600
MUTED    = "#94a3b8"   # slate-400
LINE     = "#dbe5f0"   # light blue rule
BG_SOFT  = "#f4f8fc"   # very light blue wash
PILL     = "#eaf1f8"   # light blue table cell

WARM     = "#d4794d"   # warm orange
GREEN    = "#5a9e6f"   # sage green
ROSE     = "#c25450"   # muted red

GREEN_TINT = "#eaf5ee"
WARM_TINT  = "#fbf0e9"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cd", choices=["1", "2"], default="1",
                   help="Conference draft: 1=CD1 (default), 2=CD2")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_historical():
    """Return DataFrame with RETITC historical claims TY2018-2022 + hardcoded TY2023.

    "Other" is derived as `total − individual − corporate` so disclosure-
    suppressed cells (large in TY2020) are captured in the stack.
    """
    import pandas as pd

    csv_path = (
        REPO / "packages" / "tax_modeler" / "src"
        / "tax_modeler" / "data" / "raw" / "dotax_reec_historical.csv"
    )
    df = pd.read_csv(csv_path)
    df = df.rename(columns={
        "year": "tax_year",
        "all_total_$M":        "total_$M",
        "individual_total_$M": "individual_$M",
        "corporate_total_$M":  "corporate_$M",
    })
    df["other_$M"] = (df["total_$M"] - df["individual_$M"] - df["corporate_$M"]).clip(lower=0)

    out = df[["tax_year", "individual_$M", "corporate_$M", "other_$M", "total_$M"]].copy()
    ty23 = pd.DataFrame([{
        "tax_year": 2023,
        "individual_$M": 58.293,
        "corporate_$M":  38.565,
        "other_$M":       3.217,
        "total_$M":     100.075,
    }])
    return (
        pd.concat([out, ty23], ignore_index=True)
        .sort_values("tax_year")
        .reset_index(drop=True)
    )


def _compute_scenarios(cd: str, compute_fn, agi_elig_share: float):
    import pandas as pd
    rows = []
    for scen_key, scen_label in zip(SCENARIO_KEYS, SCENARIO_SHORT):
        for year in TARGET_YEARS:
            cd2_kw: dict = {}
            if cd == "2":
                cd2_kw = dict(agi_eligibility_by_year={year: agi_elig_share}, **_CD2_KNOBS)
            else:
                cd2_kw = dict(interpretation="A")
            result = compute_fn(
                year,
                reec_demand_scenario=scen_key,
                corp_subject_to_agi_limit=False,
                reec_effective_claim_share=UTIL_RATE,
                cgec_annual_growth=0.030,
                model_carryforward_pool=True,
                **cd2_kw,
            )
            rows.append({"tax_year": year, "scenario": scen_label, **result})
    return pd.DataFrame(rows)


def _compute_demand_projection(compute_fn):
    import pandas as pd
    rows = []
    for scen_key, scen_label in zip(SCENARIO_KEYS, SCENARIO_SHORT):
        for year in DEMAND_YEARS:
            result = compute_fn(
                year,
                reec_demand_scenario=scen_key,
                corp_subject_to_agi_limit=False,
                reec_effective_claim_share=1.0,
                cgec_annual_growth=0.030,
                model_carryforward_pool=False,
            )
            rows.append({"tax_year": year, "scenario": scen_label,
                         "baseline_$M": result["reec_baseline_$M"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _clean_ax(ax, *, keep_left=True, keep_bottom=True, grid_axis="y"):
    """Strip chartjunk — hide unused spines, soften remaining ones."""
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(keep_left if s == "left" else keep_bottom)
        if ax.spines[s].get_visible():
            ax.spines[s].set_color(LINE)
            ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=BODY, labelsize=8.5, length=0)
    if grid_axis:
        ax.grid(axis=grid_axis, linestyle="-", linewidth=0.6, alpha=0.6, color=LINE)
    ax.set_axisbelow(True)


def _header_strip(fig, kicker: str, headline: str, subtitle: str | None = None):
    """Consistent page header: accent rule, kicker, headline, optional subtitle."""
    from matplotlib.patches import Rectangle
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_zorder(-1)
    # Left vertical accent stripe (full height)
    ax.add_patch(Rectangle((0, 0), 0.022, 1, facecolor=PRIMARY,
                           transform=ax.transAxes, clip_on=False))
    # Kicker
    ax.text(0.07, 0.965, kicker.upper(),
            ha="left", va="center", fontsize=12, color=PRIMARY,
            fontweight="bold", transform=ax.transAxes)
    # Headline
    ax.text(0.07, 0.935, headline,
            ha="left", va="center", fontsize=17, color=INK,
            fontweight="bold", transform=ax.transAxes)
    if subtitle:
        # Each ·-separated segment becomes its own rounded badge/chip,
        # sized to its text via TextPath measurement (see _text_width_axes).
        from matplotlib.patches import FancyBboxPatch
        parts = [p.strip() for p in subtitle.split("·") if p.strip()]
        x       = 0.072
        badge_h = 0.026
        cy      = 0.905
        pad_x   = 0.012
        gap     = 0.013
        fs      = 12
        for part in parts:
            bw = _text_width_axes(part, fs) + 2 * pad_x
            ax.add_patch(FancyBboxPatch(
                (x, cy - badge_h / 2), bw, badge_h,
                boxstyle="round,pad=0.0,rounding_size=0.009",
                facecolor=PILL, edgecolor=LINE, lw=0.6,
                transform=ax.transAxes, clip_on=False))
            ax.text(x + pad_x, cy, part,
                    ha="left", va="center", fontsize=fs, color=HEADING,
                    transform=ax.transAxes)
            x += bw + gap
    # Thin underline — clears the badge row
    ax.add_patch(Rectangle((0.07, 0.884), 0.86, 0.0012, facecolor=LINE,
                           transform=ax.transAxes, clip_on=False))
    return ax


def _footer_caption(fig, text: str):
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_zorder(-1)
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((0.07, 0.045), 0.86, 0.0012, facecolor=LINE,
                           transform=ax.transAxes, clip_on=False))
    ax.text(0.07, 0.025, text,
            ha="left", va="center", fontsize=12, color=MUTED,
            style="italic", transform=ax.transAxes, wrap=True)


def _text_width_axes(text: str, fontsize: float, fig_w_in: float = 8.5) -> float:
    """Rendered width of `text` as a fraction of figure width.

    Uses TextPath for a deterministic measurement (no draw cycle needed) so
    header badges can be sized to fit their text exactly.
    """
    from matplotlib.textpath import TextPath
    from matplotlib.font_manager import FontProperties
    fp = FontProperties(family="sans-serif", size=fontsize)
    tp = TextPath((0, 0), text, prop=fp)
    return tp.get_extents().width / (fig_w_in * 72.0)


# ---------------------------------------------------------------------------
# Page generators
# ---------------------------------------------------------------------------

def _page_cover(pdf, cd: str, total_ty23: float):
    """Page 1 — Cover (portrait)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, FancyBboxPatch

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # --- Hero band (soft-blue wash anchoring the top third) ------------------
    band_bottom = 0.605
    ax.add_patch(Rectangle((0, band_bottom), 1.0, 1.0 - band_bottom,
                           facecolor=BG_SOFT, edgecolor="none",
                           transform=ax.transAxes, clip_on=False, zorder=0))
    ax.add_patch(Rectangle((0, band_bottom), 1.0, 0.0016, facecolor=LINE,
                           transform=ax.transAxes, clip_on=False, zorder=1))

    # Left accent stripe (drawn over the band)
    ax.add_patch(Rectangle((0, 0), 0.022, 1, facecolor=PRIMARY,
                           transform=ax.transAxes, clip_on=False, zorder=3))

    # Eyebrow pill — statute reference
    ax.add_patch(FancyBboxPatch((0.072, 0.902), 0.118, 0.034,
                 boxstyle="round,pad=0.0,rounding_size=0.012",
                 facecolor=PRIMARY, edgecolor="none",
                 transform=ax.transAxes, clip_on=False, zorder=4))
    ax.text(0.131, 0.919, "§235-12.5",
            ha="center", va="center", fontsize=12, color="white",
            fontweight="bold", transform=ax.transAxes, zorder=5)

    # Title
    ax.text(0.07, 0.855,
            "Renewable Energy\nTechnologies\nTax Credit",
            ha="left", va="top", fontsize=33, color=INK,
            fontweight="bold", linespacing=1.10, transform=ax.transAxes,
            zorder=5)

    # Short accent rule under the title
    ax.add_patch(Rectangle((0.073, 0.690), 0.135, 0.006, facecolor=PRIMARY,
                           transform=ax.transAxes, clip_on=False, zorder=5))

    # Deck
    ax.text(0.07, 0.663,
            f"An analysis of the RETITC provisions in SB 3125 CD{cd}.",
            ha="left", va="top", fontsize=13.5, color=BODY,
            linespacing=1.5, transform=ax.transAxes, zorder=5)

    # --- Key figures ---------------------------------------------------------
    ax.text(0.07, 0.560, "KEY FIGURES",
            ha="left", va="center", fontsize=12, color=PRIMARY,
            fontweight="bold", transform=ax.transAxes)

    cards = [
        (f"${total_ty23:.0f}M", "TOTAL RETITC CLAIMS · TAX YEAR 2023", PRIMARY),
        ("$40M",                "ANNUAL CAP · TAX YEAR 2027–2030",   WARM),
    ]
    card_y, card_h = 0.430, 0.095
    card_gap = 0.030
    card_w   = (0.86 - card_gap) / 2
    for i, (value, label, accent) in enumerate(cards):
        x = 0.07 + i * (card_w + card_gap)
        ax.add_patch(Rectangle((x, card_y), card_w, card_h,
                               facecolor="white", edgecolor=LINE, lw=1.0,
                               transform=ax.transAxes, clip_on=False))
        ax.add_patch(Rectangle((x, card_y), 0.006, card_h,
                               facecolor=accent, transform=ax.transAxes,
                               clip_on=False))
        ax.text(x + 0.030, card_y + card_h - 0.034, value,
                ha="left", va="center", fontsize=27, color=INK,
                fontweight="bold", transform=ax.transAxes)
        ax.text(x + 0.030, card_y + 0.022, label,
                ha="left", va="center", fontsize=12, color=MUTED,
                fontweight="bold", transform=ax.transAxes)

    # --- Contents ------------------------------------------------------------
    ax.text(0.07, 0.378, "INSIDE THIS REPORT",
            ha="left", va="center", fontsize=12, color=PRIMARY,
            fontweight="bold", transform=ax.transAxes)

    contents = [
        ("02", "What the credit is, and what SB 3125 changes"),
        ("03", "Historical claims, Tax Year 2018–2023"),
        ("04", "Who claims the RETITC, by income bracket"),
        ("05", "Tax-burden change by income group"),
        ("06", "Projected fiscal impact of SB 3125"),
        ("07", "Glossary of key terms"),
    ]
    cy     = 0.343
    row_h  = 0.0385
    for num, title in contents:
        ax.add_patch(Rectangle((0.07, cy + row_h / 2 - 0.0008), 0.86, 0.0010,
                               facecolor=LINE, transform=ax.transAxes,
                               clip_on=False))
        ax.text(0.078, cy, num, ha="left", va="center",
                fontsize=12, color=PRIMARY, fontweight="bold",
                transform=ax.transAxes)
        ax.text(0.150, cy, title, ha="left", va="center",
                fontsize=12, color=BODY, transform=ax.transAxes)
        cy -= row_h
    # Closing rule under the last row
    ax.add_patch(Rectangle((0.07, cy + row_h / 2 - 0.0008), 0.86, 0.0010,
                           facecolor=LINE, transform=ax.transAxes,
                           clip_on=False))

    # --- Footer --------------------------------------------------------------
    ax.add_patch(Rectangle((0.07, 0.075), 0.86, 0.0012, facecolor=LINE,
                           transform=ax.transAxes, clip_on=False))
    ax.text(0.07, 0.053,
            "Source: Hawaiʻi Department of Taxation — Tax Credits Claimed by "
            "Hawaiʻi Taxpayers (Dec 2025).",
            ha="left", va="center", fontsize=12, color=MUTED,
            transform=ax.transAxes)
    ax.text(0.07, 0.036,
            f"Prepared {datetime.date.today().strftime('%B %Y')}  ·  "
            f"SB 3125 CD{cd}  ·  Post-OBBBA federal §25D termination scenarios.",
            ha="left", va="center", fontsize=12, color=MUTED,
            transform=ax.transAxes)

    pdf.savefig(fig)
    plt.close(fig)


def _page_about(pdf, cd: str):
    """Page 2 — Plain-language explainer (portrait, single column with sections)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="About this credit",
        headline="What is the Renewable Energy Tax Credit?",
        subtitle="§235-12.5  ·  Hawaiʻi Revised Statutes",
    )
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    x_left  = 0.07
    x_width = 0.86

    def _sec_label(y, text, color):
        """Section kicker with a short accent rule beneath it."""
        ax.text(x_left, y, text.upper(), ha="left", va="center",
                fontsize=12, color=color, fontweight="bold",
                transform=ax.transAxes)
        ax.add_patch(Rectangle((x_left, y - 0.015), 0.052, 0.0042,
                               facecolor=color, transform=ax.transAxes,
                               clip_on=False))

    # ===== 1 — What the credit is ===========================================
    _sec_label(0.852, "How the credit works", PRIMARY)
    lead1 = (
        "Hawaiʻi's Renewable Energy Technologies Tax Credit (§235-12.5) lets "
        "homeowners and businesses cut their state income tax when they install "
        "renewable energy equipment — most commonly rooftop solar, but also "
        "solar water heaters and wind turbines."
    )
    lead2 = (
        "The State does not pay for installations directly. It forgoes the tax "
        "revenue instead: the taxpayer's out-of-pocket cost drops, and the "
        "State's income-tax receipts fall by the same amount."
    )
    ax.text(x_left, 0.822,
            textwrap.fill(lead1, width=94, break_long_words=False),
            ha="left", va="top", fontsize=12, color=INK,
            linespacing=1.5, transform=ax.transAxes)
    ax.text(x_left, 0.748,
            textwrap.fill(lead2, width=94, break_long_words=False),
            ha="left", va="top", fontsize=12, color=BODY,
            linespacing=1.5, transform=ax.transAxes)

    # ===== 2 — How is the credit being utilized =============================
    _sec_label(0.662, "How is the credit being utilized", PRIMARY_DARK)
    ax.text(x_left, 0.628, "About $100M a year — a major State tax break",
            ha="left", va="center", fontsize=15, color=INK,
            fontweight="bold", transform=ax.transAxes)
    s2 = ("In Tax Year 2023, Hawaiʻi taxpayers claimed roughly $100M in RETITC credits. "
          "Here is how that total splits by who claimed it:")
    ax.text(x_left, 0.602, textwrap.fill(s2, width=104, break_long_words=False),
            ha="left", va="top", fontsize=12, color=BODY,
            linespacing=1.4, transform=ax.transAxes)

    # 100%-stacked share bar — Individual / Corporate / Other
    bar_y, bar_h = 0.508, 0.048
    segs = [
        ("Individual", 58.293, PRIMARY),
        ("Corporate",  38.565, PRIMARY_DARK),
        ("Other",       3.217, WARM),
    ]
    seg_total = sum(s[1] for s in segs)
    bx = x_left
    for name, amt, color in segs:
        w   = x_width * amt / seg_total
        pct = amt / seg_total * 100
        ax.add_patch(Rectangle((bx, bar_y), w, bar_h, facecolor=color,
                     edgecolor="white", lw=2.0, transform=ax.transAxes,
                     clip_on=False))
        if w > 0.12:
            ax.text(bx + w / 2, bar_y + bar_h * 0.63, name.upper(),
                    ha="center", va="center", fontsize=12, color="white",
                    fontweight="bold", transform=ax.transAxes)
            ax.text(bx + w / 2, bar_y + bar_h * 0.27,
                    f"${amt:.1f}M  ·  {pct:.0f}%",
                    ha="center", va="center", fontsize=12, color="white",
                    transform=ax.transAxes)
        bx += w
    other_amt = segs[2][1]
    ax.text(x_left + x_width, bar_y - 0.016,
            f"Other — trusts, estates, exempt orgs:  "
            f"${other_amt:.1f}M · {other_amt / seg_total * 100:.0f}%",
            ha="right", va="top", fontsize=12, color=BODY,
            transform=ax.transAxes)
    cap2 = ("Claims swing year to year — Tax Year 2020 alone reached $113M, lifted "
            "by several large commercial solar deals during the COVID era.")
    ax.text(x_left, bar_y - 0.046,
            textwrap.fill(cap2, width=104, break_long_words=False),
            ha="left", va="top", fontsize=12, color=MUTED,
            style="italic", linespacing=1.4, transform=ax.transAxes)

    # ===== 3 — What SB 3125 changes =========================================
    _sec_label(0.402, f"What changes under SB 3125 CD{cd}", WARM)
    ax.text(x_left, 0.362, "Implementation of a cap, income limit, and a sunset date",
            ha="left", va="center", fontsize=15, color=INK,
            fontweight="bold", transform=ax.transAxes)

    change_cards = [
        ("$40M",  "ANNUAL CAP", WARM,
         "Applies Tax Year 2027–2030. If total claims exceed $40M, every "
         "credit is scaled down by the same percentage (pro-rata)."),
        ("$175K", "INCOME LIMIT", PRIMARY,
         "Filers with AGI above $175K (single) or $350K (married filing "
         "jointly) no longer qualify for the credit."),
        ("2029",  "FINAL YEAR", GREEN,
         "After Tax Year 2029, no new credits are issued. Carryforward "
         "balances from earlier years can still be drawn down."),
    ]
    cg  = 0.024
    cw  = (x_width - 2 * cg) / 3
    cy0 = 0.122
    ch  = 0.200
    for i, (val, lab, color, desc) in enumerate(change_cards):
        cx = x_left + i * (cw + cg)
        ax.add_patch(Rectangle((cx, cy0), cw, ch, facecolor="white",
                     edgecolor=LINE, lw=1.0, transform=ax.transAxes,
                     clip_on=False))
        ax.add_patch(Rectangle((cx, cy0 + ch - 0.006), cw, 0.006,
                     facecolor=color, transform=ax.transAxes, clip_on=False))
        ax.text(cx + 0.022, cy0 + ch - 0.052, val,
                ha="left", va="center", fontsize=25, color=color,
                fontweight="bold", transform=ax.transAxes)
        ax.text(cx + 0.022, cy0 + ch - 0.086, lab,
                ha="left", va="center", fontsize=12, color=MUTED,
                fontweight="bold", transform=ax.transAxes)
        ax.add_patch(Rectangle((cx + 0.022, cy0 + ch - 0.100),
                     cw - 0.044, 0.0010, facecolor=LINE,
                     transform=ax.transAxes, clip_on=False))
        ax.text(cx + 0.022, cy0 + ch - 0.116,
                textwrap.fill(desc, width=32, break_long_words=False),
                ha="left", va="top", fontsize=12, color=BODY,
                linespacing=1.5, transform=ax.transAxes)

    _footer_caption(fig,
        f"RETITC = Renewable Energy Technologies Tax Credit (Hawaiʻi Revised "
        f"Statutes §235-12.5). Claim totals from DOTAX Tax Year 2023. SB 3125 CD{cd} "
        f"provisions as drafted.")

    pdf.savefig(fig)
    plt.close(fig)


def _page_historical(pdf, hist_df):
    """Page 3 — Stacked bar (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Historical claims",
        headline="RETITC claims by taxpayer type",
        subtitle="Tax Year 2018–2023  ·  nominal $M",
    )

    ax = fig.add_axes([0.10, 0.13, 0.85, 0.72])

    xs = np.arange(len(hist_df))
    ax.bar(xs, hist_df["individual_$M"],  color=PRIMARY,        width=0.62,
           edgecolor="white", lw=1.2, label="Individual")
    ax.bar(xs, hist_df["corporate_$M"],   color=PRIMARY_DARK,   width=0.62,
           edgecolor="white", lw=1.2,
           bottom=hist_df["individual_$M"], label="Corporate")
    ax.bar(xs, hist_df["other_$M"],       color=WARM,            width=0.62,
           edgecolor="white", lw=1.2,
           bottom=hist_df["individual_$M"] + hist_df["corporate_$M"],
           label="Other / Fin. Corp.")

    y_max = hist_df["total_$M"].max()
    for i, (_, row) in enumerate(hist_df.iterrows()):
        ax.text(i, row["total_$M"] + y_max * 0.018,
                f"${row['total_$M']:.0f}M",
                ha="center", va="bottom", fontsize=12, fontweight="bold", color=INK)

    # COVID-2020 callout
    covid = hist_df[hist_df["tax_year"] == 2020]
    if not covid.empty:
        ci = hist_df.index.get_loc(covid.index[0])
        ct = hist_df.iloc[ci]["total_$M"]
        ax.annotate(
            "COVID-era\ncommercial surge",
            xy=(ci, ct), xytext=(ci + 0.9, ct + y_max * 0.10),
            fontsize=12, color=WARM, style="italic", linespacing=1.4,
            arrowprops=dict(arrowstyle="-|>", color=WARM, lw=1.2,
                            connectionstyle="arc3,rad=-0.2"),
        )

    ax.set_xticks(xs)
    ax.set_xticklabels([f"Tax Year {int(y)}" for y in hist_df["tax_year"]],
                       fontsize=12, color=BODY, rotation=28, ha="right")
    ax.set_ylabel("RETITC Claims ($M)", fontsize=12, color=BODY)
    ax.set_ylim(0, y_max * 1.30)
    _clean_ax(ax)
    leg = ax.legend(loc="upper left", fontsize=12, frameon=False)
    for t in leg.get_texts():
        t.set_color(BODY)

    _footer_caption(fig,
        "Source: DOTAX — Tax Credits Claimed by Hawaiʻi Taxpayers (Tax Year 2018–2022 "
        "actuals; Tax Year 2023 Dec 2025 publication). \"Other\" = Total − Individual − "
        "Corporate so disclosure-suppressed cells appear in the stack.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_agi_dist(pdf, agi_bins: list, ind_total_M: float):
    """Page 4 — Individual RETITC claims by income bracket (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle

    labels = [b[0] for b in agi_bins]
    claims = [b[1] for b in agi_bins]

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    top_share = claims[-1] / ind_total_M * 100
    _header_strip(fig,
        kicker="Who claims the RETITC?",
        headline="Individual RETITC claims by income bracket",
        subtitle="Tax Year 2023  ·  DOTAX individual filers",
    )

    ax = fig.add_axes([0.24, 0.36, 0.70, 0.46])

    ys = np.arange(len(labels))
    # Graded blue — darker for higher-income brackets
    shades = [PRIMARY_PALE, PRIMARY_LIGHT, PRIMARY_LIGHT, PRIMARY, PRIMARY, PRIMARY_DARK]
    bar_colors = [shades[min(i, len(shades) - 1)] for i in range(len(labels))]
    ax.barh(ys, claims, height=0.62, color=bar_colors, edgecolor="white", lw=0.8)

    x_max = max(claims)
    for i, c in enumerate(claims):
        ax.text(c + x_max * 0.02, i,
                f"${c:.1f}M   ({c / ind_total_M * 100:.0f}%)",
                va="center", ha="left", fontsize=12, color=INK, fontweight="bold")

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=12, color=INK, fontweight="bold")
    ax.set_xlabel("Individual RETITC Claims ($M, Tax Year 2023)", fontsize=12, color=BODY)
    ax.set_ylabel("")
    ax.set_xlim(0, x_max * 1.30)
    ax.invert_yaxis()
    _clean_ax(ax, grid_axis="x")

    # Explainer box below the chart
    ax_info = fig.add_axes([0, 0, 1, 1])
    ax_info.axis("off"); ax_info.set_xlim(0, 1); ax_info.set_ylim(0, 1)
    ax_info.add_patch(Rectangle((0.07, 0.105), 0.86, 0.165,
                                facecolor=BG_SOFT, edgecolor=LINE, lw=0.8,
                                transform=ax_info.transAxes, clip_on=False))
    ax_info.add_patch(Rectangle((0.07, 0.105), 0.004, 0.165,
                                facecolor=PRIMARY, transform=ax_info.transAxes, clip_on=False))
    ax_info.text(0.085, 0.252, "READING THIS CHART",
                 ha="left", va="center", fontsize=12, color=PRIMARY,
                 fontweight="bold", transform=ax_info.transAxes)
    explain = textwrap.fill(
        f"The RETITC is claimed disproportionately by higher-income households. "
        f"The top bracket alone ($200K+) accounts for {top_share:.0f}% of all "
        f"individual claims, while the bottom three brackets (under $60K combined) "
        f"account for less than one-fifth. This reflects who can afford to install "
        f"solar — and who has enough tax liability to use a nonrefundable credit. "
        f"The next page translates this into the tax-burden change by income group.",
        width=104, break_long_words=False)
    ax_info.text(0.085, 0.232, explain,
                 ha="left", va="top", fontsize=12, color=BODY, linespacing=1.5,
                 transform=ax_info.transAxes)

    _footer_caption(fig,
        "Source: DOTAX Table A-5 (Tax Year 2023 individual RETITC by AGI bin). "
        "AGI = Adjusted Gross Income; brackets are individual-filer AGI ranges.")
    pdf.savefig(fig)
    plt.close(fig)


# Hawaiʻi household quintile income boundaries (2026-anchored, household income).
# Source: forecast_sb3125_static_quintile.py — weighted equal-population breaks.
# Re-anchored to the analysis year at render time (see _page_quintile_burden).
_HH_QUINTILE_BREAKS = [28_336, 60_915, 100_510, 168_638]

# Total Hawaiʻi households (ACS 2023 1-yr ≈ 471K) → ~94.2K per quintile.
_HI_HOUSEHOLDS = 471_000

# DOTAX AGI bin edges ($) — aligned by index to RETITC_INDIVIDUAL_BY_AGI_BIN
# (<$10K, $10-30K, $30-60K, $60-100K, $100-200K, $200K+).
_AGI_BIN_EDGES = [0, 10_000, 30_000, 60_000, 100_000, 200_000, float("inf")]

# Pareto shape for the $200K+ income tail (repo convention — see PARETO_ALPHA
# in forecast_sb3125_static_quintile.py).
_PARETO_ALPHA = 1.5


def _growth_for_year(results_df, year: int) -> float:
    """Nominal Hawaiʻi individual income-growth factor (base TY2023) for `year`.

    Read straight from the credit model's own ``growth_individual`` return
    value, so the report needs no separate income-projection import and stays
    self-contained.
    """
    sub = results_df[
        (results_df["scenario"] == "OBBBA Mid") & (results_df["tax_year"] == year)
    ]
    if not sub.empty and "growth_individual" in sub.columns:
        g = sub.iloc[0]["growth_individual"]
        if g == g and g > 0:          # not-NaN and positive
            return float(g)
    return 1.0


def _agi_bin_quintile_weights(quintile_breaks) -> list:
    """Fractional crosswalk — distribute each DOTAX AGI bin's claim dollars
    across the 5 household-income quintiles by income-range overlap.

    Replaces a hard integer bin->quintile assignment. Within each closed AGI
    bin, RETITC dollars are assumed uniform across the bin's income range, so a
    bin straddling a quintile break is split in proportion to the overlap.
    The open-ended $200K+ bin is assigned to the quintile containing its lower
    edge (the top quintile — the Q4/Q5 break is well below $200K).

    Returns 6 rows (one per bin) x 5 quintile weights, each row summing to 1.0.
    """
    b = list(quintile_breaks)
    q_ranges = [
        (0.0, b[0]), (b[0], b[1]), (b[1], b[2]), (b[2], b[3]),
        (b[3], float("inf")),
    ]
    weights = []
    for i in range(len(_AGI_BIN_EDGES) - 1):
        lo, hi = _AGI_BIN_EDGES[i], _AGI_BIN_EDGES[i + 1]
        row = [0.0] * 5
        if hi == float("inf"):
            q = next((qi for qi, (ql, qh) in enumerate(q_ranges)
                      if ql <= lo < qh), 4)
            row[q] = 1.0
        else:
            span = hi - lo
            for qi, (ql, qh) in enumerate(q_ranges):
                overlap = max(0.0, min(hi, qh) - max(lo, ql))
                row[qi] = overlap / span if span > 0 else 0.0
            s = sum(row)
            if s > 0:
                row = [w / s for w in row]
        weights.append(row)
    return weights


def _aged_agi_eligibility(agi_bins: list, growth_factor: float) -> float:
    """Individual RETITC AGI-eligibility share for a year whose nominal income
    growth (base TY2023) is ``growth_factor``.

    The §235-12.5(a) thresholds ($175K single / $350K joint) are NOT indexed
    to inflation, so as nominal incomes rise more filers cross above them and
    the eligible dollar-share declines. Each TY2023 bin's eligibility is
    re-aged: its TY2023 eligible fraction implies a blended nominal threshold
    (uniform-within-bin for closed bins; a Pareto tail for the open $200K+
    bin), which is divided by ``growth_factor`` to express the TY-of-claim
    cutoff in TY2023-income space. ``growth_factor == 1.0`` reproduces the
    static TY2023 share exactly.
    """
    g = max(float(growth_factor), 1e-9)
    elig_dollars = 0.0
    total_dollars = 0.0
    for i, (_, claim_M, e0) in enumerate(agi_bins):
        lo, hi = _AGI_BIN_EDGES[i], _AGI_BIN_EDGES[i + 1]
        if e0 >= 1.0:
            e_aged = 1.0                          # whole bin below threshold
        elif hi == float("inf"):
            # Pareto tail F(x) = 1 - (lo/x)**alpha; e0 = F(T) => solve for T.
            thresh = lo / ((1.0 - e0) ** (1.0 / _PARETO_ALPHA))
            cutoff = thresh / g
            e_aged = 1.0 - (lo / cutoff) ** _PARETO_ALPHA if cutoff > lo else 0.0
        else:
            # Uniform within bin: e0 = (T - lo)/(hi - lo) => solve for T.
            thresh = lo + e0 * (hi - lo)
            cutoff = thresh / g
            e_aged = min(1.0, max(0.0, (cutoff - lo) / (hi - lo)))
        elig_dollars += claim_M * e_aged
        total_dollars += claim_M
    return elig_dollars / total_dollars if total_dollars > 0 else 1.0


def _compute_savings_band(results_df, cd: str, compute_fn, agi_bins: list):
    """Per-year (lo, hi) RETITC-savings envelope for the page-6 sensitivity band.

    Two uncertainty dimensions:
      * Demand — min/max of ``reec_savings_$M`` across the three OBBBA
        scenarios already in ``results_df`` (free; no extra model calls).
      * Parametric — an endpoint sweep on the OBBBA-Mid scenario over the
        pro-rata demand-suppression elasticity (behavioral response to the
        cap), the carryforward utilization rate, and AGI-eligibility erosion
        (unindexed thresholds). ~25 extra compute_credit_overlay calls.
    """
    import pandas as pd

    years = sorted(results_df["tax_year"].unique())
    band = {y: [] for y in years}

    # Demand dimension — reuse the three scenarios already computed.
    for y in years:
        for v in results_df[results_df["tax_year"] == y]["reec_savings_$M"]:
            if v == v:
                band[y].append(float(v))

    # Parametric dimension — endpoint sweep on OBBBA Mid.
    for year in years:
        elig_aged = _aged_agi_eligibility(agi_bins, _growth_for_year(results_df, year))
        combos = [(e, u, None, "obbba_mid") for e in (0.0, 0.6) for u in (0.55, 0.75)]
        combos.append((0.0, UTIL_RATE, {year: elig_aged}, "obbba_mid"))  # eligibility erosion
        # Safe-harbor overhang — elevated 2026-2027 demand from §25D
        # safe-harbor projects (commenced construction ≤ 12/31/2025,
        # completing installation in 2026-2027). Interpretation A confirmed;
        # this scenario extends the band's upper bound.
        combos.append((0.0, UTIL_RATE, None, "safe_harbor_mid"))
        for elast, util, elig, scen in combos:
            kw = dict(
                reec_demand_scenario=scen,
                corp_subject_to_agi_limit=False,
                reec_effective_claim_share=util,
                cgec_annual_growth=0.030,
                model_carryforward_pool=True,
                pro_rata_elasticity=elast,
            )
            if cd == "2":
                kw["interpretation"] = _CD2_KNOBS["interpretation"]
                kw["dynamic_refundable_share"] = _CD2_KNOBS["dynamic_refundable_share"]
            else:
                kw["interpretation"] = "A"
            if elig is not None:
                kw["agi_eligibility_by_year"] = elig
            v = compute_fn(year, **kw).get("reec_savings_$M")
            if v is not None and v == v:
                band[year].append(float(v))

    return pd.DataFrame([
        {"tax_year": y,
         "savings_lo": min(band[y]) if band[y] else 0.0,
         "savings_hi": max(band[y]) if band[y] else 0.0}
        for y in years
    ])


def _page_quintile_burden(pdf, results_df, agi_bins: list, cd: str):
    """Page 5 — RETITC tax-burden increase by household income quintile (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle

    # --- TY2027 OBBBA Mid figures (first year of the AGI limit + $40M cap) ----
    row = results_df[
        (results_df["scenario"] == "OBBBA Mid") & (results_df["tax_year"] == 2027)
    ]
    pro_rata = 1.0
    ind_share_of_total = 58.294 / 100.075   # TY2023 individual share of all RETITC
    scale = 1.0
    corp_other_2027 = 0.0
    if not row.empty:
        r = row.iloc[0]
        if "reec_pro_rata_factor" in r and r["reec_pro_rata_factor"] == r["reec_pro_rata_factor"]:
            pro_rata = float(r["reec_pro_rata_factor"])
        if "reec_baseline_$M" in r and r["reec_baseline_$M"] > 0:
            ind_baseline_2027 = r["reec_baseline_$M"] * ind_share_of_total
            scale = ind_baseline_2027 / sum(c for _, c, _ in agi_bins)
        corp_other_2027 = (
            float(r.get("reec_corporate_$M", 0.0) or 0.0)
            + float(r.get("reec_other_$M", 0.0) or 0.0)
        )

    # Re-anchor the (2026-anchored) quintile breaks to the TY2027 analysis year
    # using one year of nominal income growth derived from the model's own
    # base-TY2023 growth factor (TY2023->2027 spans 4 years).
    annual_growth = _growth_for_year(results_df, 2027) ** (1.0 / 4.0)
    breaks = [bk * annual_growth for bk in _HH_QUINTILE_BREAKS]

    # --- Distribute RETITC loss across quintiles (fractional crosswalk) ---------
    n_q = 5
    q_income_limit = [0.0] * n_q   # lost because AGI over $175K/$350K
    q_cap_loss     = [0.0] * n_q   # lost to the $40M pro-rata cap
    q_claims       = [0.0] * n_q   # scaled TY2027 claims
    bin_weights = _agi_bin_quintile_weights(breaks)

    for idx, (_, claim_2023, elig) in enumerate(agi_bins):
        claim = claim_2023 * scale
        income_limit_loss  = claim * (1.0 - elig)
        eligible_remaining = claim * elig
        cap_loss           = eligible_remaining * (1.0 - pro_rata)
        for q in range(n_q):
            w = bin_weights[idx][q]
            q_claims[q]       += claim * w
            q_income_limit[q] += income_limit_loss * w
            q_cap_loss[q]     += cap_loss * w

    q_total_loss = [il + cl for il, cl in zip(q_income_limit, q_cap_loss)]
    hh_per_q     = _HI_HOUSEHOLDS / n_q
    q_avg_per_hh = [loss * 1e6 / hh_per_q for loss in q_total_loss]

    # Quintile income-range labels (TY2027-anchored)
    b = breaks
    q_ranges = [
        f"under ${b[0] / 1000:.0f}K",
        f"${b[0] / 1000:.0f}K – ${b[1] / 1000:.0f}K",
        f"${b[1] / 1000:.0f}K – ${b[2] / 1000:.0f}K",
        f"${b[2] / 1000:.0f}K – ${b[3] / 1000:.0f}K",
        f"${b[3] / 1000:.0f}K and up",
    ]

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Who pays?",
        headline="RETITC tax-burden increase by income group",
        subtitle="Tax Year 2027  ·  OBBBA Mid  ·  individual RETITC",
    )

    # --- Chart: stacked horizontal bars — avg tax increase per household ------
    ax = fig.add_axes([0.26, 0.515, 0.68, 0.30])
    ys = np.arange(n_q)
    il_per_hh = [il * 1e6 / hh_per_q for il in q_income_limit]
    cl_per_hh = [cl * 1e6 / hh_per_q for cl in q_cap_loss]

    ax.barh(ys, il_per_hh, height=0.60, color=PRIMARY_DARK, edgecolor="white", lw=0.8,
            label="Lost to income limit")
    ax.barh(ys, cl_per_hh, height=0.60, color=PRIMARY_LIGHT, edgecolor="white", lw=0.8,
            left=il_per_hh, label="Lost to the $40M cap")

    x_max = max(q_avg_per_hh) if max(q_avg_per_hh) > 0 else 1.0
    for i, v in enumerate(q_avg_per_hh):
        ax.text(v + x_max * 0.025, i, f"+${v:,.0f}",
                va="center", ha="left", fontsize=12, color=INK, fontweight="bold")

    ax.set_yticks(ys)
    ax.set_yticklabels(q_ranges, fontsize=12, color=INK, fontweight="bold")
    ax.set_xlabel("Average tax increase per household ($/yr, Tax Year 2027)",
                  fontsize=12, color=BODY)
    ax.set_xlim(0, x_max * 1.30)
    ax.invert_yaxis()
    _clean_ax(ax, grid_axis="x")
    leg = ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.03),
                    fontsize=12, frameon=False, ncol=2,
                    handlelength=1.5, columnspacing=1.5)
    for t in leg.get_texts():
        t.set_color(BODY)

    # --- Table: quintile detail ----------------------------------------------
    ax_t = fig.add_axes([0.07, 0.27, 0.86, 0.19])
    ax_t.axis("off")
    col_labels = ["Household income", "RETITC lost ($M)",
                  "Avg tax increase / household"]
    rows = []
    for i in range(n_q):
        rows.append([
            q_ranges[i],
            f"${q_total_loss[i]:.1f}M",
            f"+${q_avg_per_hh[i]:,.0f} / yr",
        ])
    tbl = ax_t.table(cellText=rows, colLabels=col_labels,
                     colWidths=[0.38, 0.24, 0.38],
                     loc="upper left", bbox=[0, 0, 1, 1.0], cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(12)
    n_cols = len(col_labels)
    for j in range(n_cols):
        c = tbl[(0, j)]
        c.set_facecolor(PRIMARY)
        c.set_text_props(color="white", fontweight="bold")
        c.set_edgecolor(PRIMARY)
    for r in range(1, n_q + 1):
        is_top = r == n_q
        for j in range(n_cols):
            c = tbl[(r, j)]
            c.set_facecolor(PILL if is_top else ("white" if r % 2 == 0 else BG_SOFT))
            c.set_edgecolor(LINE)
            if j == 0:
                c.set_text_props(color=INK, fontweight="bold")
            if is_top:
                c.set_text_props(color=PRIMARY_DARK, fontweight="bold")

    # --- Bottom: two callout boxes (how-to-read + corporate incidence) -------
    ax_n = fig.add_axes([0, 0, 1, 1])
    ax_n.axis("off"); ax_n.set_xlim(0, 1); ax_n.set_ylim(0, 1)
    box_y, box_h = 0.072, 0.168
    for bx, accent in ((0.07, WARM), (0.525, PRIMARY)):
        ax_n.add_patch(Rectangle((bx, box_y), 0.405, box_h,
                                 facecolor=BG_SOFT, edgecolor=LINE, lw=0.8,
                                 transform=ax_n.transAxes, clip_on=False))
        ax_n.add_patch(Rectangle((bx, box_y), 0.004, box_h,
                                 facecolor=accent, transform=ax_n.transAxes,
                                 clip_on=False))

    ax_n.text(0.090, box_y + box_h - 0.022, "HOW TO READ THIS",
              ha="left", va="center", fontsize=12, color=WARM,
              fontweight="bold", transform=ax_n.transAxes)
    ax_n.text(0.090, box_y + box_h - 0.048, textwrap.fill(
        "Each group's lost RETITC credits, divided by its number of "
        "households. Claims cluster at the top — so the burden falls "
        "hardest on high earners.",
        width=38, break_long_words=False),
        ha="left", va="top", fontsize=12, color=BODY, linespacing=1.45,
        transform=ax_n.transAxes)

    ax_n.text(0.545, box_y + box_h - 0.022, "CORPORATE & OTHER CLAIMS",
              ha="left", va="center", fontsize=12, color=PRIMARY,
              fontweight="bold", transform=ax_n.transAxes)
    ax_n.text(0.545, box_y + box_h - 0.048, textwrap.fill(
        f"Corporate and trust / estate filers hold ~42% of RETITC "
        f"(≈${corp_other_2027:.0f}M in 2027). Those credits flow to "
        f"shareholders and ratepayers — the chart shows individual "
        f"filers only.",
        width=38, break_long_words=False),
        ha="left", va="top", fontsize=12, color=BODY, linespacing=1.45,
        transform=ax_n.transAxes)

    _footer_caption(fig,
        "Individual RETITC loss distributed across DOTAX Tax Year 2023 AGI bins via fractional "
        f"income-range overlap onto TY2027-anchored household quintiles "
        f"(${breaks[0]/1000:.0f}K / ${breaks[1]/1000:.0f}K / ${breaks[2]/1000:.0f}K / "
        f"${breaks[3]/1000:.0f}K breaks). Hawaiʻi households ≈ {_HI_HOUSEHOLDS/1000:.0f}K "
        "(ACS 2023). Static incidence — no behavioral response modeled.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_demand(pdf, demand_df):
    """Page 5 — Line chart: baseline demand projections (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np

    scen_styles = {
        "Pre-OBBBA":    dict(color=PRIMARY_LIGHT, linestyle="--", lw=2.0, marker="o", ms=5),
        "OBBBA Mid":    dict(color=PRIMARY,       linestyle="-",  lw=2.6, marker="s", ms=6),
        "OBBBA Severe": dict(color=WARM,          linestyle="--", lw=2.0, marker="^", ms=5),
    }

    years = sorted(demand_df["tax_year"].unique())
    xs    = np.arange(len(years))

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Projection",
        headline="RETITC baseline demand projections",
        subtitle="No bill effect  ·  Tax Year 2024 – Tax Year 2031  ·  three post-OBBBA scenarios",
    )

    ax = fig.add_axes([0.11, 0.13, 0.84, 0.66])

    series: dict = {}
    for scen in SCENARIO_SHORT:
        sub  = demand_df[demand_df["scenario"] == scen].sort_values("tax_year")
        vals = sub["baseline_$M"].values
        series[scen] = vals
        ax.plot(xs, vals, label=scen, **scen_styles[scen])

    ax.fill_between(xs, series["OBBBA Severe"], series["Pre-OBBBA"],
                    alpha=0.07, color=PRIMARY)

    cap_idx = [i for i, y in enumerate(years) if 2027 <= y <= 2030]
    if cap_idx:
        ax.plot([min(cap_idx), max(cap_idx)], [40.0, 40.0],
                color=ROSE, linestyle="-.", linewidth=2.0, alpha=0.9, zorder=5)
        ax.text(max(cap_idx) + 0.10, 41.5,
                "SB 3125 cap · $40M",
                fontsize=12, color=ROSE, fontweight="bold", va="bottom")

    if 2031 in years:
        ti = years.index(2031)
        ax.axvline(ti - 0.5, color=INK, linestyle=":", alpha=0.28, lw=1.2)
        top_val = max(v.max() for v in series.values())
        ax.text(ti - 0.42, top_val * 0.88,
                "RETITC sunsets\nTax Year 2031+",
                fontsize=12, color=INK, alpha=0.55, style="italic", va="top")

    ax.set_xticks(xs)
    ax.set_xticklabels([f"Tax Year {y}" for y in years], fontsize=12, color=BODY,
                       rotation=28, ha="right")
    ax.set_ylabel("RETITC Baseline Demand ($M)", fontsize=12, color=BODY)
    _clean_ax(ax)
    leg = ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02),
                    fontsize=12, frameon=False, ncol=3,
                    handlelength=2.2, columnspacing=1.5)
    for t in leg.get_texts():
        t.set_color(BODY)

    _footer_caption(fig,
        "Hawaiʻi nominal income growth (individual) + business-sector growth (commercial) "
        "applied to Tax Year 2023 DOTAX baseline. Post-OBBBA scenarios reflect federal §25D "
        "termination (PL 119-21, July 2025). Shaded band spans Severe – Pre-OBBBA.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_impact_summary(pdf, results_df, cd: str, band_df):
    """Page 6 — Bill impact: RETITC savings chart + OBBBA Mid fiscal table (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle

    years = sorted(results_df["tax_year"].unique())
    xs    = np.arange(len(years))

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Bill impact",
        headline=f"Projected RETITC savings under SB 3125 CD{cd}",
        subtitle="OBBBA Mid scenario  ·  Savings vs. no-bill baseline  ·  Tax Year 2027–2031",
    )

    # --- Top: savings chart with sensitivity whiskers (OBBBA Mid) -----------
    ax = fig.add_axes([0.12, 0.595, 0.82, 0.205])
    mid_sub = results_df[results_df["scenario"] == "OBBBA Mid"].sort_values("tax_year")
    mid_vals = mid_sub["reec_savings_$M"].values
    band = band_df.set_index("tax_year")
    lo = np.array([float(band.loc[y, "savings_lo"]) for y in years])
    hi = np.array([float(band.loc[y, "savings_hi"]) for y in years])

    ax.bar(xs, mid_vals, width=0.52, color=PRIMARY, edgecolor="white", lw=1.2,
           zorder=3, label="Central estimate")
    lower_err = np.clip(mid_vals - lo, 0, None)
    upper_err = np.clip(hi - mid_vals, 0, None)
    ax.errorbar(xs, mid_vals, yerr=[lower_err, upper_err], fmt="none",
                ecolor=PRIMARY_DARK, elinewidth=1.5, capsize=5, capthick=1.5,
                zorder=6, label="Range of plausible outcomes")

    y_top = max(float(mid_vals.max()) if len(mid_vals) else 1.0, float(hi.max()))
    ax.set_ylim(0, y_top * 1.35)
    ax.axvspan(len(years) - 1 - 0.5, len(years) - 0.5, alpha=0.05, color=INK, zorder=0)
    ax.text(len(years) - 1, y_top * 1.24, "Sunset\n(Tax Year 2031)",
            ha="center", va="center", fontsize=12, color=INK, alpha=0.6,
            style="italic", linespacing=1.3)
    for i, v in enumerate(mid_vals):
        if v > 2.0:
            ax.text(xs[i], max(v, hi[i]) + y_top * 0.035, f"${v:.1f}M",
                    ha="center", va="bottom", fontsize=12,
                    color=PRIMARY_DARK, fontweight="bold")

    ax.set_xticks(xs)
    ax.set_xticklabels([f"Tax Year {y}" for y in years], fontsize=12, color=BODY)
    ax.set_ylabel("RETITC Revenue Savings ($M)", fontsize=12, color=BODY)
    _clean_ax(ax)

    # --- Revenue-context callout ---------------------------------------------
    ax_o = fig.add_axes([0, 0, 1, 1])
    ax_o.axis("off"); ax_o.set_xlim(0, 1); ax_o.set_ylim(0, 1)

    # --- Chart legend strip — sits just above the chart (chart top=0.800),
    #     below the header subtitle badges (~0.892).
    #     Two horizontal items: bar proxy + whisker proxy.
    _leg_y  = 0.822   # vertical center of the strip
    _leg_h  = 0.013   # half-height of the visual proxies
    _leg_fs = 12     # label font size
    # Item 1 — filled rectangle (bar proxy)
    _bar_x = 0.30
    ax_o.add_patch(Rectangle((_bar_x, _leg_y - _leg_h), 0.024, _leg_h * 2,
                             facecolor=PRIMARY, edgecolor="none",
                             transform=ax_o.transAxes, clip_on=False, zorder=6))
    ax_o.text(_bar_x + 0.031, _leg_y, "Central estimate",
              ha="left", va="center", fontsize=_leg_fs, color=BODY,
              transform=ax_o.transAxes)
    # Item 2 — vertical line with end-caps (whisker proxy)
    _wp_x = 0.545
    ax_o.plot([_wp_x, _wp_x], [_leg_y - _leg_h, _leg_y + _leg_h],
              color=PRIMARY_DARK, lw=1.5, zorder=6)
    ax_o.plot([_wp_x - 0.009, _wp_x + 0.009], [_leg_y - _leg_h, _leg_y - _leg_h],
              color=PRIMARY_DARK, lw=1.5, zorder=6)
    ax_o.plot([_wp_x - 0.009, _wp_x + 0.009], [_leg_y + _leg_h, _leg_y + _leg_h],
              color=PRIMARY_DARK, lw=1.5, zorder=6)
    ax_o.text(_wp_x + 0.018, _leg_y, "Range of plausible outcomes",
              ha="left", va="center", fontsize=_leg_fs, color=BODY,
              transform=ax_o.transAxes)
    mid_res  = results_df[results_df["scenario"] == "OBBBA Mid"]
    reec_cum = mid_res["reec_savings_$M"].sum()
    if "total_credit_savings_$M" in mid_res.columns:
        total_cum = mid_res["total_credit_savings_$M"].sum()
        share_txt = (
            f"SB 3125 reforms three tax credits; RETITC is the largest — "
            f"${reec_cum:.0f}M of ${total_cum:.0f}M in combined savings over "
            f"2027–2031. The bill's income-tax rate increases (≈$670M) are "
            f"separate and not shown here."
        )
    else:
        share_txt = (
            f"Cumulative RETITC savings (OBBBA Mid, 2027–2031): ${reec_cum:.0f}M. "
            f"SB 3125's income-tax rate changes are separate, not shown here."
        )
    _rc_box_bottom = 0.430
    _rc_box_height = 0.125
    ax_o.add_patch(Rectangle((0.07, _rc_box_bottom), 0.86, _rc_box_height,
                             facecolor=GREEN_TINT, edgecolor=LINE, lw=0.8,
                             transform=ax_o.transAxes, clip_on=False))
    ax_o.add_patch(Rectangle((0.07, _rc_box_bottom), 0.004, _rc_box_height,
                             facecolor=GREEN, transform=ax_o.transAxes, clip_on=False))
    ax_o.text(0.090, _rc_box_bottom + _rc_box_height - 0.022,
              "HOW THIS FITS INTO SB 3125",
              ha="left", va="center", fontsize=12, color=GREEN,
              fontweight="bold", transform=ax_o.transAxes)
    ax_o.text(0.090, _rc_box_bottom + _rc_box_height - 0.046,
              textwrap.fill(share_txt, width=86, break_long_words=False),
              ha="left", va="top", fontsize=12, color=INK,
              linespacing=1.45, transform=ax_o.transAxes)

    # --- Bottom: OBBBA Mid fiscal table --------------------------------------
    ax_o.text(0.07, 0.410, "FISCAL DETAIL — OBBBA MID SCENARIO",
              ha="left", va="center", fontsize=12, color=PRIMARY_DARK,
              fontweight="bold", transform=ax_o.transAxes)

    metrics = [
        ("reec_baseline_state_cost_$M", "RETITC cost — without the bill"),
        ("reec_scenario_state_cost_$M", "RETITC cost — under SB 3125"),
        ("reec_savings_$M",             "State savings on RETITC"),
    ]
    ax_tbl = fig.add_axes([0.07, 0.232, 0.86, 0.165])
    ax_tbl.axis("off")
    col_labels = ["Metric"] + [f"Tax Year\n{y}" for y in years] + ["5-Year\nTotal"]
    col_widths = [0.38] + [0.10] * len(years) + [0.12]
    sub = results_df[results_df["scenario"] == "OBBBA Mid"].sort_values("tax_year")
    rows = []
    for col_key, col_label in metrics:
        r = [col_label]
        total = 0.0
        have = False
        for yr in years:
            yr_sub = sub[sub["tax_year"] == yr]
            if yr_sub.empty or col_key not in yr_sub.columns:
                r.append("—")
            else:
                v = float(yr_sub.iloc[0][col_key])
                r.append(f"${v:.1f}M")
                total += v
                have = True
        r.append(f"${total:.1f}M" if have else "—")
        rows.append(r)
    tbl = ax_tbl.table(cellText=rows, colLabels=col_labels, colWidths=col_widths,
                       loc="upper left", bbox=[0, 0, 1, 1.0], cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(12)
    for j in range(len(col_labels)):
        c = tbl[(0, j)]
        c.set_facecolor(PRIMARY)
        c.set_text_props(color="white", fontweight="bold")
        c.set_edgecolor(PRIMARY)
    for rr in range(1, len(rows) + 1):
        is_savings = (rr - 1) == 2   # "State savings on RETITC" — the focal row
        bg = PILL if rr % 2 == 1 else "white"
        for j in range(len(col_labels)):
            c = tbl[(rr, j)]
            if is_savings:
                c.set_facecolor(GREEN_TINT)
                c.set_text_props(color=GREEN if j > 0 else INK, fontweight="bold")
                c.set_edgecolor(GREEN)
            else:
                c.set_facecolor(bg)
                if j == 0:
                    c.set_text_props(color=INK, fontweight="bold")
                c.set_edgecolor(LINE)

    # --- Carryforward explainer ----------------------------------------------
    def _stock(yr):
        s = sub[sub["tax_year"] == yr]
        if not s.empty and "reec_scenario_end_stock_$M" in s.columns:
            v = s.iloc[0]["reec_scenario_end_stock_$M"]
            return float(v) if v == v else None
        return None
    s27, s31 = _stock(2027), _stock(2031)

    nb_y, nb_h = 0.060, 0.150
    ax_o.add_patch(Rectangle((0.07, nb_y), 0.86, nb_h,
                             facecolor=BG_SOFT, edgecolor=LINE, lw=0.8,
                             transform=ax_o.transAxes, clip_on=False))
    ax_o.add_patch(Rectangle((0.07, nb_y), 0.004, nb_h,
                             facecolor=PRIMARY, transform=ax_o.transAxes,
                             clip_on=False))
    ax_o.text(0.090, nb_y + nb_h - 0.022,
              "WHY THE STATE'S RETITC COST CAN EXCEED $40M",
              ha="left", va="center", fontsize=12, color=PRIMARY_DARK,
              fontweight="bold", transform=ax_o.transAxes)
    if s27 is not None and s31 is not None:
        pool_clause = (
            f"This older pool holds ≈${s27:.0f}M at the end of 2027, "
            f"easing to ≈${s31:.0f}M by 2031. "
        )
    else:
        pool_clause = "That older pool keeps drawing down for years. "
    box_txt = (
        "The $40M cap limits only the NEW RETITC credits approved each year "
        "(2027–2030) — not total RETITC dollars. Credits earned earlier but "
        f"not yet claimed draw down on top of it. {pool_clause}That is why "
        "'RETITC cost' in the table above exceeds $40M in the early years."
    )
    ax_o.text(0.090, nb_y + nb_h - 0.046,
              textwrap.fill(box_txt, width=86, break_long_words=False),
              ha="left", va="top", fontsize=12, color=INK, linespacing=1.45,
              transform=ax_o.transAxes)

    cd_note = "CD2 formula" if cd == "2" else "CD1 formula"
    _footer_caption(fig,
        f"'RETITC cost' = income-tax revenue forgone to the RETITC each year, including "
        f"carryforward drawdown. 'State savings' = reduction vs. the no-bill baseline. "
        f"Whiskers show the range of plausible outcomes; installation-timing shifts are "
        f"not modeled. Nominal $M, {cd_note}.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_carryforward(pdf, results_df, cd: str):
    """Page 7 — Dual panel (portrait)."""
    import matplotlib.pyplot as plt
    import numpy as np

    sub  = results_df[results_df["scenario"] == "OBBBA Mid"].sort_values("tax_year")
    yrs  = sub["tax_year"].values
    xs   = np.arange(len(yrs))

    has_pool = "reec_scenario_new_certs_$M" in sub.columns

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Stock dynamics",
        headline="Carryforward pool dynamics",
        subtitle=f"OBBBA Mid scenario  ·  SB 3125 CD{cd}  ·  §235-12.5(j) carryforward",
    )

    # Two panels stacked — left/right margins must leave room for twinx label
    ax_top = fig.add_axes([0.11, 0.51, 0.78, 0.30])
    ax_bot = fig.add_axes([0.11, 0.13, 0.78, 0.30])

    base_col = "reec_baseline_state_cost_$M" if has_pool else "reec_baseline_$M"
    scen_col = "reec_scenario_state_cost_$M" if has_pool else "reec_after_bill_$M"
    base_cost = sub[base_col].values
    scen_cost = sub[scen_col].values
    savings   = sub["reec_savings_$M"].values

    w = 0.34
    ax_top.bar(xs - w / 2, base_cost, width=w, color=PRIMARY_DARK, edgecolor="white", lw=1.0,
               label="Baseline state cost")
    ax_top.bar(xs + w / 2, scen_cost, width=w, color=PRIMARY,      edgecolor="white", lw=1.0,
               label="Bill scenario cost")
    ax_top.plot(xs, savings, color=WARM, linewidth=2.4, marker="D",
                markersize=6, label="Savings", zorder=5)
    for i, s in enumerate(savings):
        if s > 1.0:
            ax_top.text(xs[i], base_cost[i] + 1.4,
                        f"${s:.1f}M",
                        ha="center", va="bottom", fontsize=12,
                        color=WARM, fontweight="bold")

    ax_top.set_ylabel("State Cost ($M)", fontsize=12, color=BODY)
    _clean_ax(ax_top)
    ax_top.set_xticklabels([])
    # Panel title (left) + legend (right) on shared row above the axes
    ax_top.text(0.0, 1.06, "State cost · baseline vs bill",
                transform=ax_top.transAxes, ha="left", va="bottom",
                fontsize=12, fontweight="bold", color=HEADING)
    leg = ax_top.legend(loc="lower right", bbox_to_anchor=(1.0, 1.02),
                        fontsize=12, frameon=False, ncol=3,
                        handlelength=1.6, columnspacing=1.4)
    for t in leg.get_texts():
        t.set_color(BODY)

    if has_pool:
        refund     = sub["reec_scenario_refundable_$M"].values
        nonref_use = sub["reec_scenario_nonref_usage_$M"].values
        end_stock  = sub["reec_scenario_end_stock_$M"].values
        base_stock = sub["reec_baseline_end_stock_$M"].values

        ax_bot.bar(xs - w / 2, refund,     width=w, color=PRIMARY,        edgecolor="white", lw=1.0,
                   label="Refundable claims")
        ax_bot.bar(xs + w / 2, nonref_use, width=w, color=PRIMARY_DARK,   edgecolor="white", lw=1.0,
                   label="Nonref stock usage")

        ax2 = ax_bot.twinx()
        ax2.plot(xs, end_stock, color=WARM, linestyle="-",
                 linewidth=2.4, marker="o", ms=5, label="Year-end stock (bill)")
        ax2.plot(xs, base_stock, color=INK, linestyle=":",
                 linewidth=1.8, marker="s", ms=4, alpha=0.55,
                 label="Year-end stock (baseline)")
        ax2.set_ylabel("Carryforward Stock ($M)", fontsize=12, color=BODY)
        ax2.tick_params(colors=BODY, labelsize=8.5, length=0)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color(LINE)
        lines2, labels2 = ax2.get_legend_handles_labels()
    else:
        ax_bot.bar(xs - w / 2, sub["reec_baseline_$M"].values, width=w, color=PRIMARY_DARK,
                   edgecolor="white", lw=1.0, label="Baseline demand")
        ax_bot.bar(xs + w / 2, sub["reec_after_bill_$M"].values, width=w, color=PRIMARY,
                   edgecolor="white", lw=1.0, label="After bill (cap applied)")
        lines2, labels2 = [], []

    ax_bot.set_ylabel("Annual Credits ($M)", fontsize=12, color=BODY)
    ax_bot.text(0.0, 1.06, "Credit flows & §235-12.5(j) carryforward stock",
                transform=ax_bot.transAxes, ha="left", va="bottom",
                fontsize=12, fontweight="bold", color=HEADING)
    ax_bot.set_xticks(xs)
    ax_bot.set_xticklabels([f"Tax Year {y}" for y in yrs], fontsize=12, color=BODY,
                           rotation=28, ha="right")
    _clean_ax(ax_bot)

    lines1, labels1 = ax_bot.get_legend_handles_labels()
    all_lines  = lines1 + lines2
    all_labels = labels1 + labels2
    leg = ax_bot.legend(all_lines, all_labels,
                        loc="lower right", bbox_to_anchor=(1.0, 1.02),
                        fontsize=12, frameon=False, ncol=len(all_lines),
                        handlelength=1.4, columnspacing=1.2)
    for t in leg.get_texts():
        t.set_color(BODY)

    _footer_caption(fig,
        "Vintage simulation: nonrefundable stock accumulates from Tax Year 2010; util. rate applied "
        "each year to (prior stock + new nonref certs). §235-12.5(j) — carryforward 'until exhausted'.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_summary_table(pdf, results_df, cd: str):
    """Page 8 — Summary table, all scenarios × years (portrait)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    years = sorted(results_df["tax_year"].unique())

    # Key metrics only — plain-English labels, MID scenario only
    col_sample = results_df.columns.tolist()
    metrics = [
        ("reec_baseline_$M",   "Projected RETITC claims (no bill)"),
        ("reec_after_bill_$M", "State cost with SB 3125"),
        ("reec_savings_$M",    "Revenue the State keeps"),
    ]
    SHOW_SCENARIOS = ["OBBBA Mid"]   # focus on mid scenario only

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Summary",
        headline=f"SB 3125 CD{cd} fiscal impact · OBBBA Mid scenario",
        subtitle=(
            f"Tax Year 2027 – Tax Year 2031  ·  all values in nominal $M  ·  "
            f"{'CD2 formula' if cd == '2' else 'CD1 formula'}"
        ),
    )

    n_scen        = len(SHOW_SCENARIOS)
    top, bot, gap = 0.85, 0.08, 0.0
    panel_h       = top - bot   # one panel, full height
    col_labels    = ["Metric"] + [f"Tax Year {y}" for y in years]
    col_widths    = [0.34] + [0.132] * len(years)

    for s_idx, scen in enumerate(SHOW_SCENARIOS):
        y0     = top - s_idx * (panel_h + gap)
        ax_y0  = y0 - panel_h
        ax = fig.add_axes([0.07, ax_y0, 0.86, panel_h])
        ax.axis("off")

        # Scenario header bar — a filled rectangle above the table with the
        # scenario name. This replaces the floating text approach.
        from matplotlib.patches import FancyBboxPatch
        ax.add_patch(FancyBboxPatch(
            (0, 1.0), 1.0, 0.22,
            boxstyle="square,pad=0", facecolor=PILL, edgecolor=LINE, lw=0.8,
            transform=ax.transAxes, clip_on=False,
        ))
        ax.text(0.014, 1.11, scen.upper(),
                transform=ax.transAxes, ha="left", va="center",
                fontsize=12, fontweight="bold", color=PRIMARY_DARK)

        sub  = results_df[results_df["scenario"] == scen].sort_values("tax_year")
        rows = []
        for col_key, col_label in metrics:
            row = [col_label]
            for yr in years:
                yr_sub = sub[sub["tax_year"] == yr]
                if yr_sub.empty or col_key not in yr_sub.columns:
                    row.append("—")
                else:
                    v = yr_sub.iloc[0][col_key]
                    row.append(f"{v:.2f}" if col_key == "reec_pro_rata_factor"
                               else f"${v:.1f}M")
            rows.append(row)

        tbl = ax.table(cellText=rows, colLabels=col_labels,
                       colWidths=col_widths, loc="upper left",
                       bbox=[0, 0, 1, 1.0], cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(12)

        for j in range(len(col_labels)):
            c = tbl[(0, j)]
            c.set_facecolor(PRIMARY)
            c.set_text_props(color="white", fontweight="bold")
            c.set_edgecolor(PRIMARY)
        for r in range(1, len(rows) + 1):
            is_savings = rows[r - 1][0] == "Revenue the State keeps"
            bg = PILL if r % 2 == 1 else "white"
            for j in range(len(col_labels)):
                c = tbl[(r, j)]
                if is_savings:
                    c.set_facecolor(GREEN_TINT)
                    c.set_text_props(color=GREEN if j > 0 else INK, fontweight="bold")
                    c.set_edgecolor(GREEN)
                else:
                    c.set_facecolor(bg)
                    if j == 0:
                        c.set_text_props(color=INK, fontweight="bold")
                    c.set_edgecolor(LINE)

    _footer_caption(fig,
        "All values in nominal $M. 'Revenue the State keeps' = income-tax revenue retained "
        "vs. the no-bill baseline. RETITC: $40M annual cap (Tax Year 2027–2030) + AGI "
        "eligibility filter. Vintage-pool carryforward simulation.")
    pdf.savefig(fig)
    plt.close(fig)


def _page_glossary(pdf, cd: str):
    """Page 9 — Glossary (portrait, 2 columns)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig = plt.figure(figsize=PAGE)
    fig.patch.set_facecolor("white")
    _header_strip(fig,
        kicker="Glossary",
        headline="Key terms and definitions",
        subtitle="Key terms used in this report",
    )
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    terms = [
        ("AGI (Adjusted Gross Income)",
         "Your total annual income minus certain deductions. SB 3125 uses "
         "AGI — not taxable income — to decide who qualifies for the credit. "
         "It is line 11 of federal Form 1040."),
        ("Tax Year",
         "The calendar year income was earned. Tax Year 2027 = income earned "
         "Jan–Dec 2027, reported on a return filed in spring 2028."),
        ("Nonrefundable credit",
         "A credit that can lower your tax bill to zero but no further — if "
         "the credit is larger than the tax you owe, you don't get the "
         "difference back as cash. The unused part carries forward."),
        ("Carryforward",
         "Unused nonrefundable credit that rolls over to future years. "
         "Hawaiʻi law (§235-12.5(j)) lets RETITC balances carry forward with no "
         "expiration date, so a large pool of older credits keeps being "
         "claimed year after year."),
        ("Annual cap — $40M",
         "SB 3125 limits the new RETITC credits approved each year to $40M "
         "(Tax Year 2027–2030). If claims for new credits exceed $40M, each "
         "one is trimmed by the same percentage. The cap does not apply to "
         "the carryforward pool of older credits."),
        ("OBBBA",
         "The federal 'One Big Beautiful Bill Act' (July 2025). It ended the "
         "federal residential solar credit at the end of 2025, which lowers "
         "demand for Hawaiʻi's RETITC — homeowners previously used both credits "
         "together to finance an installation."),
        ("Sensitivity range (whiskers)",
         "The whiskers on the chart show the range the figure could fall in "
         "if real-world conditions—how many people install solar, how the cap "
         "changes behavior — differ."),
    ]

    # 2-column layout. Each entry: term (bold), wrapped definition.
    col_top      = 0.86
    col_x        = [0.07, 0.53]
    entry_gap    = 0.024   # blank space below each entry
    term_offset  = 0.027   # term top → definition top
    line_h_unit  = 0.0206  # figure-coord height per definition line at 12pt
    wrap_chars   = 36      # chars/line — fits a 3.0" column at 12pt

    half = (len(terms) + 1) // 2
    columns = [terms[:half], terms[half:]]

    for col_idx, col_terms in enumerate(columns):
        y = col_top
        x = col_x[col_idx]
        for term, defn in col_terms:
            # Term (bold, primary color)
            ax.text(x, y, term, ha="left", va="top",
                    fontsize=12, color=PRIMARY_DARK, fontweight="bold",
                    transform=ax.transAxes)
            # Wrapped definition
            wrapped = textwrap.fill(defn, width=wrap_chars, break_long_words=False)
            n_lines = wrapped.count("\n") + 1
            ax.text(x, y - term_offset, wrapped,
                    ha="left", va="top",
                    fontsize=12, color=BODY, linespacing=1.35,
                    transform=ax.transAxes)
            y -= term_offset + n_lines * line_h_unit + entry_gap

    _footer_caption(fig,
        f"SB 3125 CD{cd}  ·  §235-12.5 Renewable Energy Technologies Tax Credit  ·  "
        "Statutory language at Hawaiʻi Revised Statutes §235-12.5.")
    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main(cd: str = "1") -> Path:
    sys.path.insert(0, str(REPO / "packages" / "tax_modeler" / "src"))

    try:
        from tax_modeler.scenarios.sb3125_cd1_credits import (
            compute_credit_overlay,
            REEC_INDIVIDUAL_BY_AGI_BIN as RETITC_INDIVIDUAL_BY_AGI_BIN,
            REEC_INDIVIDUAL_TOTAL_M as RETITC_INDIVIDUAL_TOTAL_M,
            _reec_eligible_individual_M,
        )
    except ImportError as exc:
        print(f"ERROR: Could not import tax_modeler: {exc}", file=sys.stderr)
        print("Run `uv sync --all-packages` to install the venv.", file=sys.stderr)
        sys.exit(1)

    import matplotlib
    import matplotlib.font_manager as _fm
    import shutil as _shutil

    # ------------------------------------------------------------------ #
    # Font setup — Source Sans 3, all four weights                        #
    #                                                                      #
    # The OTF files bundled with Acrobat DC are CFF-flavoured (OTTO       #
    # magic / CFF table), which means matplotlib embeds them as CID       #
    # composite fonts rather than TrueType Type 42.  Acrobat's Edit PDF   #
    # mode then can't find the full font for editing (WebResources isn't  #
    # a standard font directory) and substitutes DejaVu Sans.             #
    #                                                                      #
    # Fix: copy the OTF files to ~/Library/Fonts/ on first run so both   #
    # matplotlib *and* Acrobat's editor can find them.  The copy is       #
    # idempotent (skipped if file already exists).                        #
    # ------------------------------------------------------------------ #
    _SS3_SRC = Path(
        "/Library/Application Support/Adobe/Acrobat/DC"
        "/WebResources/Resource1/app1/fonts"
    )
    _SS3_DST = Path.home() / "Library" / "Fonts"
    _SS3_VARIANTS = (
        "SourceSans3-Regular.otf",
        "SourceSans3-Bold.otf",
        "SourceSans3-It.otf",
        "SourceSans3-BoldIt.otf",
    )
    for _variant in _SS3_VARIANTS:
        _src = _SS3_SRC / _variant
        _dst = _SS3_DST / _variant
        if _src.exists() and not _dst.exists():
            _shutil.copy2(str(_src), str(_dst))
            print(f"  Installed font: {_variant} → ~/Library/Fonts/")
        if _dst.exists():
            _fm.fontManager.addfont(str(_dst))
        elif _src.exists():
            _fm.fontManager.addfont(str(_src))   # fallback: source dir only

    _REPORT_FONT = "Source Sans 3"
    matplotlib.rcParams["text.parse_math"] = False
    matplotlib.rcParams["font.family"]      = "sans-serif"
    matplotlib.rcParams["font.sans-serif"]  = [_REPORT_FONT, "DejaVu Sans"]
    matplotlib.rcParams["axes.titleweight"] = "bold"
    matplotlib.rcParams["axes.titlepad"]    = 10
    matplotlib.rcParams["pdf.fonttype"]     = 42   # TrueType embedding → editable in Acrobat
    from matplotlib.backends.backend_pdf import PdfPages

    PDF_OUT = Path(f"/tmp/reec_cd{cd}_analysis_report.pdf")

    print("Loading DOTAX historical data...")
    hist_df    = _load_historical()
    total_ty23 = float(hist_df[hist_df["tax_year"] == 2023]["total_$M"].iloc[0])

    agi_elig_share = _reec_eligible_individual_M() / RETITC_INDIVIDUAL_TOTAL_M
    print(f"  AGI eligibility share (TY 2023 PUMS): {agi_elig_share:.4f}")

    print(f"Running vintage-pool scenarios (TY 2027-2031, CD{cd})...")
    results_df = _compute_scenarios(cd, compute_credit_overlay, agi_elig_share)

    print("Computing sensitivity band (parametric sweep)...")
    band_df = _compute_savings_band(results_df, cd, compute_credit_overlay,
                                    RETITC_INDIVIDUAL_BY_AGI_BIN)

    print(f"Generating PDF: {PDF_OUT}")
    with PdfPages(PDF_OUT) as pdf:
        print("  Page 1 — Cover")
        _page_cover(pdf, cd, total_ty23)
        print("  Page 2 — About the RETITC")
        _page_about(pdf, cd)
        print("  Page 3 — Historical claims")
        _page_historical(pdf, hist_df)
        print("  Page 4 — Who claims the RETITC")
        _page_agi_dist(pdf, RETITC_INDIVIDUAL_BY_AGI_BIN, RETITC_INDIVIDUAL_TOTAL_M)
        print("  Page 5 — RETITC burden by income group")
        _page_quintile_burden(pdf, results_df, RETITC_INDIVIDUAL_BY_AGI_BIN, cd)
        print("  Page 6 — Bill impact & fiscal summary")
        _page_impact_summary(pdf, results_df, cd, band_df)
        print("  Page 7 — Glossary")
        _page_glossary(pdf, cd)

    print(f"\nDone.  Open: file://{PDF_OUT}")
    return PDF_OUT


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    main(cd=args.cd)
