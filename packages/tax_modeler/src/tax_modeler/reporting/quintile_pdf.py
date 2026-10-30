"""Quintile distributional PDF report renderer.

Single source for the report previously copy-pasted across
forecast_hb2306_quintile.py, forecast_sb3125_quintile.py and
analyses/forecast_sb3125_static_quintile.py (and the deleted
generate_quintile_pdf.py). Layout: one table page (three row-blocks x
years), three horizontal-bar chart pages (first + last chart year), plus
optional caller-supplied extra pages.

matplotlib is imported lazily inside make_quintile_pdf so importing
tax_modeler.reporting does not require it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

# ── Shared constants (previously duplicated in every _make_pdf) ────────────

# ITEP-anchored 2026 household-income quintile boundaries.
HH_BREAKS = [28_336, 60_915, 100_510, 168_638]
Q_NAMES = ["Bottom 20%", "2nd 20%", "3rd 20%", "4th 20%", "Top 20%"]
Q_RANGES = [
    f"under ${HH_BREAKS[0]//1000}K",
    f"${HH_BREAKS[0]//1000}K – ${HH_BREAKS[1]//1000}K",
    f"${HH_BREAKS[1]//1000}K – ${HH_BREAKS[2]//1000}K",
    f"${HH_BREAKS[2]//1000}K – ${HH_BREAKS[3]//1000}K",
    f"${HH_BREAKS[3]//1000}K+",
]

NAVY = "#1e3a5f"
TEAL = "#2c8c87"
ORANGE = "#c05e2b"
PILL_BG = "#e8eef7"
PILL_FG = "#1e3a5f"
TXT_GREY = "#4a5568"
RULE = "#cbd5e0"
GREEN = "#276749"  # HB 2306 accent
BLUE = "#2b6cb0"   # SB 3125 accent


def fmt_value(val: float, fmt: str) -> str:
    """Format a cell/bar label: 'dollar', 'pct', or 'millions'."""
    if fmt == "dollar":
        return f"{'+' if val >= 0 else '-'}${abs(val):,.0f}"
    if fmt == "pct":
        return f"{val:.1f}%"
    if fmt == "millions":
        return f"{'+' if val >= 0 else '-'}${abs(val):,.1f}M"
    return str(val)


def make_quintile_pdf(
    df,
    out_path: Path | str,
    *,
    q_labels: Sequence[str],
    table_title: str,
    table_subtitle: str,
    table_specs: Sequence[tuple[str, str, str]],
    chart_specs: Sequence[tuple[str, str, str, str, str]],
    pdf_meta: dict[str, str],
    table_footnote: str | None = None,
    cor_footnote: str | None = None,
    chart_years: tuple[int, int] = (2027, 2031),
    table_bottom: float = 0.03,
    source_note: str = (
        "Source: Census-Forecaster microsim. Quintiles defined on 2026 "
        "household income."
    ),
    extra_pages: Sequence[Callable] = (),
) -> Path:
    """Render the quintile distributional PDF.

    Args:
        df: long frame with columns ``tax_year``, ``quintile`` (values in
            ``q_labels``) plus every metric column named in the specs.
        q_labels: quintile values as they appear in ``df['quintile']``
            (label case differs between the dynamic and static pipelines).
        table_specs: three ``(block_title, column, fmt)`` row-blocks for
            the table page.
        chart_specs: three ``(title, subtitle, column, fmt, bar_color)``
            chart pages.
        pdf_meta: PDF info dict entries (``Title``/``Author``/…).
        table_footnote: static italic footnote on the table page.
        cor_footnote: second line of the COR-anchored diagnostics footer;
            when set and ``total_change_cor_$M`` exists, per-year raw vs
            COR-anchored totals are printed above it.
        extra_pages: callables ``f(pdf)`` appended after the chart pages
            (e.g. the static CD2 REEC incidence/time-series pages).

    Returns the output path.
    """
    import matplotlib

    matplotlib.rcParams["text.parse_math"] = False
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"]
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.backends.backend_pdf import PdfPages

    out_path = Path(out_path)
    years = sorted(df["tax_year"].unique())
    has_cor = "total_change_cor_$M" in df.columns

    def _cell(yr, ql, col):
        sub = df[(df["tax_year"] == yr) & (df["quintile"] == ql)]
        return float(sub.iloc[0][col]) if not sub.empty else None

    # ── Page 1: tables ──────────────────────────────────────────────────
    def table_page(pdf):
        fig = plt.figure(figsize=(11, 11))
        fig.suptitle(table_title, fontsize=15, fontweight="bold", y=0.97,
                     color=NAVY)
        fig.text(0.5, 0.935, table_subtitle, ha="center", fontsize=10,
                 style="italic", color=TXT_GREY)
        n = len(table_specs)
        top, bot, pad = 0.90, table_bottom, 0.025
        ph = (top - bot - (n - 1) * pad) / n
        for i, (title, col, fmt) in enumerate(table_specs):
            y0 = top - i * (ph + pad)
            ax = fig.add_axes([0.05, y0 - ph, 0.90, ph])
            ax.axis("off")
            ax.text(0, 1.0, title, transform=ax.transAxes,
                    fontsize=11.5, fontweight="bold", color=NAVY, va="top")
            rows = []
            for ql, qn, qr in zip(q_labels, Q_NAMES, Q_RANGES):
                row = [qn, qr]
                for yr in years:
                    v = _cell(yr, ql, col)
                    row.append(fmt_value(v, fmt) if v is not None else "—")
                rows.append(row)
            col_labels = ["Quintile", "Household income"] + [str(y) for y in years]
            col_widths = [0.16, 0.22] + [0.124] * len(years)
            tbl = ax.table(cellText=rows, colLabels=col_labels,
                           colWidths=col_widths, loc="upper left",
                           bbox=[0, 0, 1, 0.85], cellLoc="center")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(9)
            for j in range(len(col_labels)):
                c = tbl[(0, j)]
                c.set_facecolor(NAVY)
                c.set_text_props(color="white", fontweight="bold")
                c.set_edgecolor("white")
            for r in range(1, len(rows) + 1):
                for j in range(len(col_labels)):
                    c = tbl[(r, j)]
                    c.set_edgecolor(RULE)
                    if j == 0:
                        c.set_text_props(fontweight="bold", color=NAVY)
                        c.set_facecolor(PILL_BG)
                    elif j == 1:
                        c.set_text_props(color=TXT_GREY)
                        c.set_facecolor(PILL_BG)
                    if r == len(rows) and j >= 2:
                        c.set_facecolor("#fff5f5")
        if table_footnote:
            fig.text(0.05, 0.025, table_footnote, ha="left", fontsize=7.5,
                     style="italic", color=TXT_GREY)
        if cor_footnote and has_cor:
            diag_lines = []
            for yr in years:
                sub = df[df["tax_year"] == yr]
                raw = sub["total_change_$M"].sum()
                scl = sub["total_change_cor_$M"].sum()
                diag_lines.append(
                    f"TY {yr}: raw ${raw:+,.0f}M / COR-anchored ${scl:+,.0f}M"
                )
            fig.text(0.05, 0.018,
                     "Aggregate impact (raw / COR-anchored statewide):  "
                     + "  |  ".join(diag_lines),
                     ha="left", fontsize=7.5, color=TXT_GREY)
            fig.text(0.05, 0.005, cor_footnote,
                     ha="left", fontsize=7, style="italic", color=TXT_GREY)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    # ── Chart pages ─────────────────────────────────────────────────────
    def _panel(ax, yr, col, fmt, bar_color):
        vals = [(_cell(yr, ql, col) or 0.0) for ql in q_labels]
        yp = np.arange(len(q_labels))[::-1]
        ax.barh(yp, vals, height=0.55, color=bar_color, edgecolor="none")
        vmax = max(abs(min(vals)), abs(max(vals))) or 1.0
        pad = vmax * 0.04
        for y, v in zip(yp, vals):
            lbl = fmt_value(v, fmt)
            if v >= 0:
                ax.text(v + pad, y, lbl, va="center", ha="left",
                        fontsize=10, fontweight="bold", color="#2d3748")
            else:
                ax.text(v - pad, y, lbl, va="center", ha="right",
                        fontsize=10, fontweight="bold", color="#2d3748")
        ax.set_yticks(yp)
        ax.set_yticklabels([])
        for y, name, rng in zip(yp, Q_NAMES, Q_RANGES):
            ax.text(-0.01, y + 0.12, name, transform=ax.get_yaxis_transform(),
                    ha="right", va="center", fontsize=9.5, fontweight="bold",
                    color=PILL_FG,
                    bbox=dict(boxstyle="round,pad=0.35", facecolor=PILL_BG,
                              edgecolor="none"))
            ax.text(-0.01, y - 0.22, rng, transform=ax.get_yaxis_transform(),
                    ha="right", va="center", fontsize=8.5, color=TXT_GREY)
        ax.set_title(f"Tax Year {yr}", loc="left", fontsize=12,
                     fontweight="bold", color=NAVY)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", colors=TXT_GREY, labelsize=8)
        ax.grid(axis="x", linestyle=":", alpha=0.35, color=RULE)
        ax.axvline(0, color=RULE, lw=0.7)
        if min(vals) < 0:
            ax.set_xlim(min(vals) - vmax * 0.30, max(vals) + vmax * 0.30)
        else:
            ax.set_xlim(0, max(vals) * 1.30)

    def chart_page(pdf, title, subtitle, col, fmt, bar_color):
        fig = plt.figure(figsize=(11, 10))
        fig.suptitle(title, fontsize=14, fontweight="bold", y=0.965,
                     color=NAVY)
        fig.text(0.5, 0.93, subtitle, ha="center", fontsize=10,
                 style="italic", color=TXT_GREY)
        for i, yr in enumerate(chart_years):
            ax = fig.add_axes([0.22, 0.50 - i * 0.42, 0.72, 0.36])
            _panel(ax, yr, col, fmt, bar_color)
        fig.text(0.5, 0.02, source_note, ha="center", fontsize=8,
                 style="italic", color=TXT_GREY)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    with PdfPages(out_path) as pdf:
        table_page(pdf)
        for title, subtitle, col, fmt, bar_color in chart_specs:
            chart_page(pdf, title, subtitle, col, fmt, bar_color)
        for page_fn in extra_pages:
            page_fn(pdf)
        m = pdf.infodict()
        for k, v in pdf_meta.items():
            m[k] = v

    print(f"Saved: {out_path}", flush=True)
    return out_path
