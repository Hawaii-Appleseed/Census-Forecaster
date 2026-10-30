"""Chart generation for the poverty-impact brief (matplotlib → PNG files)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .data import (
    CHARCOAL,
    GOLD,
    SLATE,
    TEAL,
    BriefData,
)

# ---------------------------------------------------------------------------
# Global style
# ---------------------------------------------------------------------------

def _set_chart_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Manrope",
                "Poppins",
                "Inter",
                "Helvetica",
                "Arial",
                "DejaVu Sans",
            ],
            "axes.edgecolor": CHARCOAL,
            "axes.labelcolor": CHARCOAL,
            "axes.titlecolor": CHARCOAL,
            "axes.titleweight": "bold",
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "xtick.color": CHARCOAL,
            "ytick.color": CHARCOAL,
            "axes.grid": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )


# ---------------------------------------------------------------------------
# Chart primitives
# ---------------------------------------------------------------------------

def _horizontal_bar(
    labels: list[str],
    values: list[float],
    out_path: Path,
    color: str,
    title: str,
    value_fmt: str = "{:.1f}%",
    xlabel: str = "",
    width: float = 7.5,
    height: float = 4.0,
) -> None:
    fig, ax = plt.subplots(figsize=(width, height))
    y_pos = list(range(len(labels)))
    bars = ax.barh(y_pos, values, color=color, edgecolor="white", height=0.65)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    if xlabel:
        ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left", pad=12)
    max_v = max(values) if values else 1.0
    for bar, v in zip(bars, values):
        ax.text(
            bar.get_width() + max_v * 0.01,
            bar.get_y() + bar.get_height() / 2,
            value_fmt.format(v),
            va="center",
            fontsize=8,
            color=CHARCOAL,
        )
    ax.set_xlim(0, max_v * 1.18)
    ax.tick_params(axis="x", length=0)
    plt.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _vertical_bar(
    labels: list[str],
    values: list[float],
    out_path: Path,
    color: str,
    title: str,
    value_fmt: str = "{:,.0f}",
    ylabel: str = "",
    width: float = 7.5,
    height: float = 4.0,
) -> None:
    fig, ax = plt.subplots(figsize=(width, height))
    x_pos = list(range(len(labels)))
    bars = ax.bar(x_pos, values, color=color, edgecolor="white", width=0.65)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", pad=12)
    max_v = max(values) if values else 1.0
    for bar, v in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max_v * 0.02,
            value_fmt.format(v),
            ha="center",
            va="bottom",
            fontsize=8,
            color=CHARCOAL,
        )
    ax.set_ylim(0, max_v * 1.18)
    ax.tick_params(axis="y", length=0)
    plt.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _stacked_vertical_bar(
    group_labels: list[str],
    series: dict[str, list[float]],
    colors: dict[str, str],
    out_path: Path,
    title: str,
    value_fmt: str = "{:,.0f}",
    ylabel: str = "",
    width: float = 7.5,
    height: float = 4.2,
) -> None:
    fig, ax = plt.subplots(figsize=(width, height))
    x = list(range(len(group_labels)))
    bottoms = [0.0] * len(group_labels)
    series_items = list(series.items())
    totals = [sum(s[i] for _, s in series_items) for i in range(len(group_labels))]
    max_total = max(totals) if totals else 1.0
    for label, values in series_items:
        bars = ax.bar(
            x,
            values,
            bottom=bottoms,
            color=colors.get(label, TEAL),
            edgecolor="white",
            label=label,
            width=0.55,
        )
        for bar, v in zip(bars, values):
            if v < max_total * 0.04:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_y() + bar.get_height() / 2,
                value_fmt.format(v),
                ha="center",
                va="center",
                fontsize=8,
                color="white",
                fontweight="bold",
            )
        bottoms = [b + v for b, v in zip(bottoms, values)]
    # Totals on top
    for xi, total in zip(x, totals):
        ax.text(
            xi,
            total + max_total * 0.02,
            value_fmt.format(total),
            ha="center",
            va="bottom",
            fontsize=9,
            color=CHARCOAL,
            fontweight="bold",
        )
    ax.set_xticks(x)
    ax.set_xticklabels(group_labels)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", pad=12)
    ax.set_ylim(0, max_total * 1.20)
    ax.tick_params(axis="y", length=0)
    ax.legend(frameon=False, loc="upper right", fontsize=8)
    plt.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grouped_vertical_bar(
    group_labels: list[str],
    series: dict[str, list[float]],
    colors: dict[str, str],
    out_path: Path,
    title: str,
    value_fmt: str = "{:,.0f}",
    ylabel: str = "",
    width: float = 7.5,
    height: float = 4.0,
) -> None:
    fig, ax = plt.subplots(figsize=(width, height))
    n_groups = len(group_labels)
    n_series = len(series)
    bar_width = 0.8 / max(n_series, 1)
    x = list(range(n_groups))
    max_v = max((max(v) for v in series.values()), default=1.0)
    for i, (label, values) in enumerate(series.items()):
        offsets = [xi - 0.4 + bar_width * (i + 0.5) for xi in x]
        bars = ax.bar(
            offsets,
            values,
            width=bar_width,
            color=colors.get(label, TEAL),
            label=label,
            edgecolor="white",
        )
        for bar, v in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_v * 0.02,
                value_fmt.format(v),
                ha="center",
                va="bottom",
                fontsize=8,
                color=CHARCOAL,
            )
    ax.set_xticks(x)
    ax.set_xticklabels(group_labels)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", pad=12)
    ax.set_ylim(0, max_v * 1.22)
    ax.tick_params(axis="y", length=0)
    ax.legend(frameon=False, loc="upper right", fontsize=8)
    plt.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure orchestrator — public alias: make_figures
# ---------------------------------------------------------------------------

def _make_figures(data: BriefData, charts_dir: Path) -> dict[str, Path]:
    _set_chart_style()
    charts_dir.mkdir(parents=True, exist_ok=True)

    counties = data.counties.copy()
    counties = counties.sort_values("poverty_rate_baseline", ascending=False)

    fig1 = charts_dir / "fig1_poverty_rate_by_county.png"
    state_rate = float(data.state["poverty_rate_baseline"]) * 100
    labels = list(counties["county"]) + ["Hawaiʻi (statewide)"]
    values = list(counties["poverty_rate_baseline"] * 100) + [state_rate]
    _horizontal_bar(
        labels,
        values,
        fig1,
        TEAL,
        f"Figure 1. Supplemental Poverty Measure rates by county, Hawaiʻi (TY{data.tax_year})",
        value_fmt="{:.1f}%",
    )

    fig2 = charts_dir / "fig2_eitc_stacked_by_county.png"
    counties_sorted_eitc = counties.assign(
        _eitc_total=counties["persons_lifted_no_eitc"]
        + counties["persons_lifted_no_hi_eitc"]
    ).sort_values("_eitc_total", ascending=False)
    eitc_labels = list(counties_sorted_eitc["county"]) + ["Statewide"]
    _stacked_vertical_bar(
        eitc_labels,
        {
            "Federal EITC": list(counties_sorted_eitc["persons_lifted_no_eitc"])
            + [float(data.state["persons_lifted_no_eitc"])],
            "Hawaiʻi state EITC": list(
                counties_sorted_eitc["persons_lifted_no_hi_eitc"]
            )
            + [float(data.state["persons_lifted_no_hi_eitc"])],
        },
        {"Federal EITC": TEAL, "Hawaiʻi state EITC": GOLD},
        fig2,
        f"Figure 2. People lifted out of poverty by EITC (federal + state), by county (TY{data.tax_year})",
        value_fmt="{:,.0f}",
        ylabel="People lifted out of poverty",
    )

    fig3 = charts_dir / "fig3_ctc_by_county.png"
    counties_sorted_ctc = counties.sort_values(
        "persons_lifted_no_ctc", ascending=False
    )
    _vertical_bar(
        list(counties_sorted_ctc["county"]),
        list(counties_sorted_ctc["persons_lifted_no_ctc"]),
        fig3,
        SLATE,
        f"Figure 3. People lifted out of poverty by federal Child Tax Credit, by county (TY{data.tax_year})",
        value_fmt="{:,.0f}",
        ylabel="People lifted out of poverty",
    )

    fig4 = charts_dir / "fig4_rxkids_impact.png"
    if data.rxkids_state is not None:
        rx = data.rxkids_state
        rx_lifted = float(rx["persons_lifted_rxkids_hi"])
        hi_eitc_100 = float(data.state.get("persons_lifted_hi_eitc_100pct", 0))
        hi_ctc_650 = float(data.state.get("persons_lifted_hi_ctc_650", 0))
        _vertical_bar(
            ["RxKids Hawaiʻi\n($1,500 prenatal\n+ $500×6 postnatal)",
             "HI EITC raised\nto 100% of federal",
             "New $650/child\nHI state CTC"],
            [rx_lifted, hi_eitc_100, hi_ctc_650],
            fig4,
            GOLD,
            f"Figure 4. Additional people lifted out of poverty by proposed expansions (TY{data.tax_year})",
            value_fmt="{:,.0f}",
            ylabel="Additional people lifted",
            width=7.5,
            height=4.2,
        )
    else:
        fig4 = None

    sd_top = (
        data.senate_districts.sort_values("persons_lifted_no_credits", ascending=False)
        .head(10)
        .copy()
    )
    sd_labels = [f"SD {int(s)}" for s in sd_top["senate_district"]]
    fig5 = charts_dir / "fig5_combined_top_senate.png"
    _horizontal_bar(
        sd_labels,
        list(sd_top["persons_lifted_no_credits"]),
        fig5,
        TEAL,
        f"Figure 5. People kept out of poverty by all three credits, top 10 senate districts (TY{data.tax_year})",
        value_fmt="{:,.0f}",
        xlabel="People lifted out of poverty",
    )

    fig6 = charts_dir / "fig6_expansion_scenarios.png"
    scen_series_6: dict[str, list[float]] = {
        "HI EITC at 100% of federal": [
            float(data.state["persons_lifted_hi_eitc_100pct"])
        ],
        "New $650/child HI state CTC": [
            float(data.state["persons_lifted_hi_ctc_650"])
        ],
    }
    colors_6: dict[str, str] = {
        "HI EITC at 100% of federal": TEAL,
        "New $650/child HI state CTC": GOLD,
    }
    if data.rxkids_state is not None:
        scen_series_6["RxKids Hawaiʻi"] = [
            float(data.rxkids_state["persons_lifted_rxkids_hi"])
        ]
        colors_6["RxKids Hawaiʻi"] = SLATE
    _stacked_vertical_bar(
        [f"Hawaiʻi statewide (TY{data.tax_year})"],
        scen_series_6,
        colors_6,
        fig6,
        f"Figure 6. Additional people lifted by proposed expansions — additive (TY{data.tax_year})",
        value_fmt="{:,.0f}",
        ylabel="Additional people lifted out of poverty",
    )

    # Background section charts
    fig_bg1 = None
    if data.household_types is not None:
        hht = data.household_types.copy()
        label_map = {
            "head_of_household": "Single parent\n(head of household)",
            "single": "Single adult\n(no dependents)",
            "married_filing_jointly": "Married couple",
            "married_filing_separately": "Married filing\nseparately",
        }
        hht["label"] = hht["filing_status"].map(label_map)
        hht = hht[hht["label"].notna()].sort_values("poverty_rate_baseline", ascending=True)
        state_rate = float(data.state["poverty_rate_baseline"]) * 100
        labels_bg = list(hht["label"]) + ["Hawaiʻi\n(statewide)"]
        values_bg = list(hht["poverty_rate_baseline"] * 100) + [state_rate]
        colors_bg = [TEAL] * len(hht) + [GOLD]
        fig_bg1 = charts_dir / "fig_bg1_poverty_by_household_type.png"
        fig_b, ax_b = plt.subplots(figsize=(7.5, 3.8))
        bars = ax_b.barh(labels_bg, values_bg, color=colors_bg, edgecolor="white")
        for bar, v in zip(bars, values_bg):
            ax_b.text(v + 0.3, bar.get_y() + bar.get_height() / 2, f"{v:.1f}%",
                      va="center", fontsize=9, color=CHARCOAL)
        ax_b.set_xlabel("SPM Poverty Rate (%)")
        ax_b.set_xlim(0, max(values_bg) * 1.2)
        ax_b.tick_params(axis="x", length=0)
        ax_b.set_title(
            f"Figure B1. SPM poverty rate by household type, Hawaiʻi (TY{data.tax_year})",
            loc="left", pad=10)
        plt.tight_layout()
        fig_b.savefig(fig_bg1, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig_b)

    fig_bg2 = None
    if data.racial_stats is not None:
        rs = data.racial_stats.sort_values("poverty_rate", ascending=True).copy()
        fig_bg2 = charts_dir / "fig_bg2_poverty_by_race.png"
        fig_r, ax_r = plt.subplots(figsize=(7.5, 3.8))
        bar_colors = [
            GOLD if "Hawaiian" in r or "Pacific" in r or "NHPI" in r else TEAL
            for r in rs["race"]
        ]
        bars_r = ax_r.barh(list(rs["race"]), list(rs["poverty_rate"] * 100),
                           color=bar_colors, edgecolor="white")
        for bar, v in zip(bars_r, rs["poverty_rate"] * 100):
            ax_r.text(v + 0.3, bar.get_y() + bar.get_height() / 2, f"{v:.1f}%",
                      va="center", fontsize=9, color=CHARCOAL)
        ax_r.set_xlabel("Official Poverty Rate (%)")
        ax_r.set_xlim(0, max(rs["poverty_rate"] * 100) * 1.25)
        ax_r.tick_params(axis="x", length=0)
        rs_vintage = rs["vintage"].iloc[0] if "vintage" in rs.columns else "ACS PUMS"
        ax_r.set_title(
            f"Figure B2. Official poverty rate by race/ethnicity, Hawaiʻi ({rs_vintage})",
            loc="left", pad=10)
        plt.tight_layout()
        fig_r.savefig(fig_bg2, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig_r)

    out = {
        "fig1": fig1,
        "fig2": fig2,
        "fig3": fig3,
        "fig5": fig5,
        "fig6": fig6,
    }
    if fig4 is not None:
        out["fig4"] = fig4
    if fig_bg1 is not None:
        out["fig_bg1"] = fig_bg1
    if fig_bg2 is not None:
        out["fig_bg2"] = fig_bg2
    return out


# Public alias
make_figures = _make_figures
