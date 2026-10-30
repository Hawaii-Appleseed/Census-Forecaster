#!/usr/bin/env python3
"""Generate SB 3125 CD2 distributional analysis HTML report.

Reads pre-computed CSVs (produced by forecast_sb3125_cd2_vs_fy26base.py and
forecast_sb3125_cd2_enhanced.py) and writes a self-contained Chart.js HTML
matching the ITEP-style report format, with an added REEC credit section.

Usage:
    python3 analyses/generate_itep_report.py [--output /path/to/output.html]

Dependencies: pandas only (no matplotlib, no reportlab).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Input / output paths — canonical location is the manifested runs/ store;
# fall back to the legacy /tmp mirrors during the transition.
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parent.parent


def _prefer_runs(runs_path: Path, tmp_path: Path) -> Path:
    return runs_path if runs_path.exists() else tmp_path


QUINTILE_CSV = _prefer_runs(
    _REPO / "runs" / "sb3125_cd2_fy26base" / "quintile.csv",
    Path("/tmp/cd2_vs_fy26base_quintile_mid_2027_2031.csv"),
)
ENHANCED_CSV = _prefer_runs(
    _REPO / "runs" / "sb3125_cd2_enhanced" / "enhanced.csv",
    Path("/tmp/sb3125_cd2_enhanced_2027_2031.csv"),
)
DEFAULT_OUT  = Path("/tmp/SB3125_CD_analysis_charts.html")

# ---------------------------------------------------------------------------
# Static REEC AGI-bin data (DOTAX TY2023 actuals, §235-12.5 eligibility screen)
# Tuple: (label, individual_claim_$M, eligibility_share_under_AGI_limit)
# ---------------------------------------------------------------------------
REEC_AGI_BINS = [
    ("<$10K",       4.731, 1.000),
    ("$10K–$30K",   2.522, 1.000),
    ("$30K–$60K",   3.121, 1.000),
    ("$60K–$100K",  5.752, 1.000),
    ("$100K–$200K", 16.150, 0.972),
    ("$200K+",      26.018, 0.561),
]

# Income-range labels for our 5 quintiles (from Hawaii DOTAX/PUMS breakpoints)
QUINTILE_META = [
    ("Q1 (bottom 20%)", "Lowest 20%",  "under $28K"),
    ("Q2",              "Second 20%",  "$28K–$57K"),
    ("Q3",              "Middle 20%",  "$57K–$98K"),
    ("Q4",              "Fourth 20%",  "$98K–$152K"),
    ("Q5 (top 20%)",    "Top 20%",     "$152K+"),
]

QUINTILE_ORDER = [m[0] for m in QUINTILE_META]
TARGET_YEARS   = [2027, 2028, 2029, 2030, 2031]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: float, prefix: str = "$", decimals: int = 0) -> str:
    """Format with sign, comma-thousands, prefix. Negative → em-dash minus."""
    abs_v = abs(round(v, decimals))
    s = f"{abs_v:,.{decimals}f}"
    sign = "−" if v < 0 else "+"
    return f"{sign}{prefix}{s}"


def _js_list(vals: list) -> str:
    return "[" + ", ".join(str(v) for v in vals) + "]"


def _js_str_list(vals: list[str]) -> str:
    escaped = [v.replace("'", "\\'") for v in vals]
    return "[" + ", ".join(f"'{v}'" for v in escaped) + "]"


def _load_quintile(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["_order"] = df["quintile"].map({k: i for i, k in enumerate(QUINTILE_ORDER)})
    df = df.sort_values(["tax_year", "_order"]).drop(columns="_order")
    return df


def _load_enhanced(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Chart data builders
# ---------------------------------------------------------------------------

def _quintile_row(q_df: pd.DataFrame, year: int) -> list[int]:
    sub = q_df[q_df["tax_year"] == year]
    sub = sub.set_index("quintile").reindex(QUINTILE_ORDER)
    return [int(round(v)) for v in sub["avg_total_change"].tolist()]


def _trend_table_rows(q_df: pd.DataFrame) -> list[tuple]:
    """Returns list of (display_label, income_range, {year: formatted_str, ...})."""
    rows = []
    for csv_key, label, income_range in QUINTILE_META:
        year_vals = {}
        for yr in TARGET_YEARS:
            sub = q_df[(q_df["tax_year"] == yr) & (q_df["quintile"] == csv_key)]
            v = sub["avg_total_change"].iloc[0] if len(sub) else 0.0
            year_vals[yr] = (v, _fmt(v))
        rows.append((label, income_range, year_vals))
    return rows


def _reec_incidence_data(e_df: pd.DataFrame) -> dict:
    """Build per-AGI-bin incidence data for MID TY2027."""
    mid27 = e_df[(e_df["scenario"] == "MID") & (e_df["tax_year"] == 2027)].iloc[0]
    pro_rata     = float(mid27["reec_pro_rata_factor"])
    elig_share   = float(mid27["reec_eligibility_share"])

    labels, claimed, lost_cap, lost_agi = [], [], [], []
    for lbl, claim_m, elig in REEC_AGI_BINS:
        eligible_m = claim_m * elig
        ineligible_m = claim_m * (1.0 - elig)
        # Apply pro-rata only to eligible portion (capped year)
        claimed_m  = eligible_m * pro_rata
        cap_loss_m = eligible_m * (1.0 - pro_rata)

        labels.append(lbl)
        claimed.append(round(claimed_m, 2))
        lost_cap.append(round(cap_loss_m, 2))
        lost_agi.append(round(ineligible_m, 2))

    total_base    = sum(b[1] for b in REEC_AGI_BINS)
    total_claimed = sum(claimed)
    total_savings = round(float(mid27["reec_savings_$M"]), 1)

    return {
        "labels":        labels,
        "claimed":       claimed,
        "lost_cap":      lost_cap,
        "lost_agi":      lost_agi,
        "pro_rata":      round(pro_rata, 3),
        "elig_share":    round(elig_share, 3),
        "total_base_m":  round(total_base, 1),
        "total_claim_m": round(total_claimed, 1),
        "total_save_m":  total_savings,
    }


def _reec_trajectory_data(e_df: pd.DataFrame) -> dict:
    """Build scenario trajectory data for the REEC savings time-series chart."""
    scenarios = ["LOW", "MID", "HIGH"]
    by_scen   = {}
    for sc in scenarios:
        sub = e_df[e_df["scenario"] == sc].sort_values("tax_year")
        by_scen[sc] = [round(float(v), 1) for v in sub["reec_savings_$M"].tolist()]

    mid = e_df[e_df["scenario"] == "MID"].sort_values("tax_year")
    base_costs = [round(float(v), 1) for v in mid["reec_base_state_cost_$M"].tolist()]
    scen_costs = [round(float(v), 1) for v in mid["reec_scen_state_cost_$M"].tolist()]
    five_yr    = {sc: round(sum(by_scen[sc]), 1) for sc in scenarios}

    return {
        "years":       TARGET_YEARS,
        "low":         by_scen["LOW"],
        "mid":         by_scen["MID"],
        "high":        by_scen["HIGH"],
        "base_costs":  base_costs,
        "scen_costs":  scen_costs,
        "five_yr":     five_yr,
    }


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_html(q_df: pd.DataFrame, e_df: pd.DataFrame) -> str:

    # ---------- quintile distributional ----------
    data_2027 = _quintile_row(q_df, 2027)
    data_2031 = _quintile_row(q_df, 2031)
    trend_rows = _trend_table_rows(q_df)

    # KPI values
    q4_idx = QUINTILE_ORDER.index("Q4")
    kpi_q4_cut = data_2027[q4_idx]
    kpi_q4_str = _fmt(kpi_q4_cut)

    # bottom 80% max pct paying more
    sub27 = q_df[q_df["tax_year"] == 2027].set_index("quintile").reindex(QUINTILE_ORDER[:4])
    bot80_max_more = float(sub27["pct_pay_more"].max())
    kpi_bot80_str  = f"{bot80_max_more:.1f}%"

    # 5-yr MID total from enhanced CSV
    mid_5yr = e_df[e_df["scenario"] == "MID"]["total_impact_$M"].sum()
    kpi_5yr_str = f"${mid_5yr / 1000:.2f}B"

    # ---------- REEC data ----------
    reec_inc  = _reec_incidence_data(e_df)
    reec_traj = _reec_trajectory_data(e_df)

    # ---------- Trend table HTML ----------
    trend_html_rows = ""
    for label, income_range, year_vals in trend_rows:
        cells = ""
        for yr in TARGET_YEARS:
            v, s = year_vals[yr]
            cls = "val-cut" if v < 0 else ("val-more" if v > 0 else "")
            cells += f'<td class="{cls}">{s}</td>'
        trend_html_rows += f"<tr><td>{label}<br><span style='font-weight:400;color:#5A6478;font-size:11px;'>{income_range}</span></td>{cells}</tr>\n"

    # ---------- REEC incidence table HTML ----------
    reec_inc_rows = ""
    for i, (lbl, claimed, lost_cap, lost_agi) in enumerate(
        zip(reec_inc["labels"], reec_inc["claimed"], reec_inc["lost_cap"], reec_inc["lost_agi"])
    ):
        total_bin = claimed + lost_cap + lost_agi
        reec_inc_rows += (
            f"<tr>"
            f"<td>{lbl}</td>"
            f"<td>${total_bin:.1f}M</td>"
            f"<td class='val-cut'>${claimed:.1f}M</td>"
            f"<td style='color:#E07B00;font-weight:600;'>${lost_cap:.1f}M</td>"
            f"<td style='color:#c0392b;font-weight:600;'>${lost_agi:.1f}M</td>"
            f"</tr>\n"
        )

    # JS data
    q_labels_js = _js_str_list([m[1] for m in QUINTILE_META])
    q_ranges_js = _js_str_list([m[2] for m in QUINTILE_META])
    data27_js   = _js_list(data_2027)
    data31_js   = _js_list(data_2031)

    reec_bins_js    = _js_str_list(reec_inc["labels"])
    reec_claimed_js = _js_list(reec_inc["claimed"])
    reec_lcap_js    = _js_list(reec_inc["lost_cap"])
    reec_lagi_js    = _js_list(reec_inc["lost_agi"])

    reec_years_js = _js_list(reec_traj["years"])
    reec_low_js   = _js_list(reec_traj["low"])
    reec_mid_js   = _js_list(reec_traj["mid"])
    reec_high_js  = _js_list(reec_traj["high"])
    reec_base_js  = _js_list(reec_traj["base_costs"])
    reec_scen_js  = _js_list(reec_traj["scen_costs"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SB 3125 CD2 — Tax & REEC Analysis</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,400;1,700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>

  <style>
    *, *::before, *::after {{
      box-sizing: border-box; margin: 0; padding: 0;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
      color-adjust: exact !important;
    }}
    :root {{
      --blue-deep:   #1a3564;
      --blue:        #3c73ba;
      --teal:        #5ca8d4;
      --teal-light:  #d4ecf7;
      --green:       #2a9d8f;
      --green-light: #d0f2ef;
      --amber:       #E07B00;
      --amber-light: #FFF3DC;
      --red:         #c0392b;
      --red-light:   #fde8e6;
      --gold:        #F4A261;
      --bg:          #FFFFFF;
      --card:        #FFFFFF;
      --text:        #1A1A2E;
      --text-muted:  #5A6478;
      --border:      #E2E8F0;
    }}
    body {{ font-family:'Inter',sans-serif; background:var(--bg); color:var(--text); font-size:14px; line-height:1.5; }}
    h1,h2,h3,h4,h5 {{ font-family:'Poppins',sans-serif; }}
    .page {{ max-width:1100px; margin:0 auto; padding:0 0 48px; }}

    /* HEADER */
    .header {{ background:linear-gradient(135deg,var(--blue-deep) 0%,#0353A4 60%,var(--blue) 100%); color:white; padding:36px 48px 32px; }}
    .header-inner {{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; }}
    .header-left {{ flex:1; }}
    .header-eyebrow {{ font-family:'Poppins',sans-serif; font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:rgba(255,255,255,.65); margin-bottom:8px; }}
    .header-title {{ font-family:'Poppins',sans-serif; font-size:28px; font-weight:800; line-height:1.2; margin-bottom:10px; }}
    .header-subtitle {{ font-size:15px; color:rgba(255,255,255,.82); line-height:1.5; max-width:620px; }}
    .header-meta {{ margin-top:16px; font-size:11px; color:rgba(255,255,255,.5); }}
    .header-badge {{ flex-shrink:0; background:rgba(255,255,255,.12); border:1.5px solid rgba(255,255,255,.3); border-radius:12px; padding:14px 18px; text-align:center; }}
    .badge-label {{ font-size:10px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:rgba(255,255,255,.6); margin-bottom:4px; }}
    .badge-val {{ font-family:'Poppins',sans-serif; font-size:26px; font-weight:800; color:white; line-height:1; }}
    .badge-sub {{ font-size:11px; color:rgba(255,255,255,.65); margin-top:4px; }}

    /* STATS STRIP */
    .stats-strip {{ display:grid; grid-template-columns:repeat(3,1fr); background:#f0f5ff; border-bottom:1px solid var(--border); }}
    .stat-cell {{ padding:18px 24px; border-right:1px solid var(--border); text-align:center; }}
    .stat-cell:last-child {{ border-right:none; }}
    .stat-num {{ font-family:'Poppins',sans-serif; font-size:24px; font-weight:800; line-height:1; margin-bottom:4px; }}
    .stat-num.green {{ color:var(--green); }}
    .stat-num.red   {{ color:var(--red); }}
    .stat-num.blue  {{ color:var(--blue-deep); }}
    .stat-label {{ font-size:12px; color:var(--text-muted); line-height:1.4; }}
    .stat-label strong {{ color:var(--text); display:block; }}

    /* SECTION */
    .section {{ padding:28px 48px 0; }}
    .section-title {{ font-family:'Poppins',sans-serif; font-size:13px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted); border-left:3px solid var(--blue); padding-left:10px; margin-bottom:16px; }}

    /* CHART CARD */
    .chart-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px 24px 16px; }}
    .chart-title {{ font-family:'Poppins',sans-serif; font-size:14px; font-weight:700; color:var(--text); margin-bottom:4px; }}
    .chart-sub {{ font-size:11px; color:var(--text-muted); margin-bottom:12px; }}
    .chart-legend {{ display:flex; gap:20px; font-size:12px; color:var(--text-muted); flex-wrap:wrap; }}
    .legend-item {{ display:flex; align-items:center; gap:6px; }}
    .legend-dot {{ width:10px; height:10px; border-radius:3px; flex-shrink:0; }}
    .chart-canvas-wrap {{ position:relative; height:260px; width:100%; }}
    .chart-canvas-wrap.tall {{ height:320px; }}
    .chart-canvas-wrap canvas {{ display:block; width:100% !important; height:100% !important; }}

    /* NOTE */
    .baseline-note {{ background:#fffbf0; border:1px solid #f4e0a0; border-radius:8px; padding:12px 16px; font-size:12px; color:#6b5700; margin-top:16px; line-height:1.5; }}
    .baseline-note strong {{ color:#4a3a00; }}
    .reec-note {{ background:#f0f7ff; border:1px solid #bcd4f0; border-radius:8px; padding:12px 16px; font-size:12px; color:#1a3564; margin-top:16px; line-height:1.5; }}
    .reec-note strong {{ color:#0d2040; }}

    /* SECTION DIVIDER */
    .section-divider {{ margin:36px 48px 0; border:none; border-top:2px solid var(--border); }}
    .section-header-band {{ background:linear-gradient(90deg,var(--blue-deep),#1a5090); color:white; padding:16px 48px; margin-top:4px; }}
    .section-header-band h2 {{ font-family:'Poppins',sans-serif; font-size:16px; font-weight:700; margin-bottom:2px; }}
    .section-header-band p {{ font-size:12px; color:rgba(255,255,255,.75); margin:0; }}

    /* TREND TABLE */
    .trend-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    .trend-table th {{ background:var(--blue-deep); color:white; font-family:'Poppins',sans-serif; font-weight:600; font-size:11px; padding:8px 12px; text-align:left; }}
    .trend-table th:first-child {{ border-radius:8px 0 0 0; }}
    .trend-table th:last-child  {{ border-radius:0 8px 0 0; }}
    .trend-table th:not(:first-child) {{ text-align:right; }}
    .trend-table td {{ padding:7px 12px; border-bottom:1px solid var(--border); text-align:right; }}
    .trend-table td:first-child {{ text-align:left; font-weight:600; color:var(--text); }}
    .trend-table tr:last-child td {{ border-bottom:none; }}
    .trend-table tr:nth-child(even) td {{ background:#f8f9fa; }}
    .val-cut  {{ color:var(--green); font-weight:600; }}
    .val-more {{ color:var(--red);   font-weight:700; }}

    /* REEC INCIDENCE TABLE */
    .reec-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    .reec-table th {{ background:#1a3564; color:white; font-family:'Poppins',sans-serif; font-weight:600; font-size:11px; padding:8px 12px; }}
    .reec-table th:first-child {{ border-radius:8px 0 0 0; text-align:left; }}
    .reec-table th:last-child  {{ border-radius:0 8px 0 0; }}
    .reec-table th:not(:first-child) {{ text-align:right; }}
    .reec-table td {{ padding:7px 12px; border-bottom:1px solid var(--border); text-align:right; }}
    .reec-table td:first-child {{ text-align:left; font-weight:600; color:var(--text); }}
    .reec-table tr:last-child td {{ border-bottom:none; }}
    .reec-table tr:nth-child(even) td {{ background:#f8f9fa; }}

    /* GRID */
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}

    /* PRINT */
    @media print {{
      body {{ font-size:12px; }}
      .page {{ max-width:100%; }}
      .header {{ padding:14px 36px 12px; }}
      .header-title {{ font-size:20px; }}
      .header-subtitle {{ font-size:12px; }}
      .badge-val {{ font-size:22px; }}
      .stat-cell {{ padding:7px 12px; }}
      .stat-num {{ font-size:18px; }}
      .section {{ padding:10px 36px 0; }}
      .section-title {{ margin-bottom:8px; font-size:11px; }}
      .chart-card {{ break-inside:avoid; page-break-inside:avoid; padding:10px 18px 8px; }}
      .chart-canvas-wrap {{ height:230px !important; }}
      .chart-canvas-wrap.tall {{ height:270px !important; }}
      .section-table {{ break-before:page; page-break-before:always; }}
      .section-reec  {{ break-before:page; page-break-before:always; }}
      .trend-table {{ font-size:11px; }}
      .two-col {{ grid-template-columns:1fr 1fr; gap:10px; }}
    }}
  </style>
</head>
<body>
<div class="page">

  <!-- HEADER -->
  <div class="header">
    <div class="header-inner">
      <div class="header-left">
        <div class="header-eyebrow">Hawaii Tax Analysis · ITEP, May 2026</div>
        <div class="header-title">SB 3125 Conference Agreement</div>
        <div class="header-subtitle">
          Average annual tax change by income group, compared to TY2026 law.
          Negative values represent tax reductions; positive values represent tax increases.
        </div>
        <div class="header-meta">Source: Hawaii DOTAX SOI microsimulation · May 2026</div>
      </div>
      <div class="header-badge">
        <div class="badge-label">Baseline</div>
        <div class="badge-val">TY2026</div>
        <div class="badge-sub">Current law (Act 46<br>scheduled cuts)</div>
      </div>
    </div>
  </div>

  <!-- STATS STRIP -->
  <div class="stats-strip">
    <div class="stat-cell">
      <div class="stat-num green">{kpi_q4_str}</div>
      <div class="stat-label">
        <strong>Fourth 20% Avg. Annual Cut (TY2027)</strong>
        Households earning $98K–$152K
      </div>
    </div>
    <div class="stat-cell">
      <div class="stat-num green">{kpi_bot80_str}</div>
      <div class="stat-label">
        <strong>Bottom 80% — Share Paying More (TY2027)</strong>
        Lowest through Fourth quintile face no net increase
      </div>
    </div>
    <div class="stat-cell">
      <div class="stat-num blue">{kpi_5yr_str}</div>
      <div class="stat-label">
        <strong>5-Year Total Fiscal Impact (MID scenario)</strong>
        TY2027–TY2031 combined (brackets + credits)
      </div>
    </div>
  </div>

  <!-- DISTRIBUTIONAL CHARTS -->
  <div class="section" style="margin-top:24px;">
    <div class="section-title">Avg. Tax Change vs. 2026 Law — by Income Group</div>
    <div style="display:flex; flex-direction:column; gap:12px;">

      <div class="chart-card">
        <div style="display:grid; grid-template-columns:1fr auto 1fr; align-items:center; margin-bottom:8px;">
          <div class="chart-title" style="margin:0;">Tax Year 2027</div>
          <div class="chart-legend" style="margin:0; justify-self:center;">
            <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Tax cut vs. 2026</div>
            <div class="legend-item"><div class="legend-dot" style="background:var(--red)"></div> Tax increase vs. 2026</div>
          </div>
          <div></div>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart2027"></canvas></div>
      </div>

      <div class="chart-card">
        <div style="display:grid; grid-template-columns:1fr auto 1fr; align-items:center; margin-bottom:8px;">
          <div class="chart-title" style="margin:0;">Tax Year 2031</div>
          <div class="chart-legend" style="margin:0; justify-self:center;">
            <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Tax cut vs. 2026</div>
            <div class="legend-item"><div class="legend-dot" style="background:var(--red)"></div> Tax increase vs. 2026</div>
          </div>
          <div></div>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart2031"></canvas></div>
      </div>

    </div>
    <div class="baseline-note">
      <strong>How to read this:</strong> Compared to TY2026 law, SB 3125 CD preserves Act 46's scheduled income tax
      reductions for the vast majority of residents — meaning most households pay <em>less</em> than under 2026 law
      (negative bars). Values shown are averages across <em>all</em> residents in each quintile, including those with
      no tax liability. The Top 20% bar represents all households above $152K; within this group, the top 1% (earners
      above ~$783K) pay more on average while the 80th–99th percentile pay less.
    </div>
  </div>

  <!-- MULTI-YEAR TREND TABLE -->
  <div class="section section-table" style="margin-top:28px;">
    <div class="section-title">Average Tax Change — All Years (TY2027–TY2031)</div>
    <div class="chart-card" style="padding:0; overflow:hidden;">
      <table class="trend-table">
        <thead>
          <tr>
            <th>Income Group</th>
            <th>TY2027</th><th>TY2028</th><th>TY2029</th><th>TY2030</th><th>TY2031</th>
          </tr>
        </thead>
        <tbody>
          {trend_html_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- ════════════════ REEC SECTION ════════════════ -->
  <hr class="section-divider">
  <div class="section-header-band section-reec">
    <h2>Renewable Energy Tax Credit (REEC) — Detailed Analysis</h2>
    <p>§235-12.5 — How SB 3125 CD2's cap and AGI screen affect credit claims by income group · MID scenario</p>
  </div>

  <!-- REEC KPI strip -->
  <div class="stats-strip" style="background:#f5f9ff;">
    <div class="stat-cell">
      <div class="stat-num blue">${reec_inc['total_base_m']:.1f}M</div>
      <div class="stat-label">
        <strong>Individual REEC Baseline (TY2023)</strong>
        Total individual credits before cap or AGI limit
      </div>
    </div>
    <div class="stat-cell">
      <div class="stat-num green">${reec_inc['total_claim_m']:.1f}M</div>
      <div class="stat-label">
        <strong>Estimated Claims Under Bill (TY2027)</strong>
        After $40M cap (pro-rata {reec_inc['pro_rata']:.1%}) + AGI screen
      </div>
    </div>
    <div class="stat-cell">
      <div class="stat-num red">${reec_inc['total_save_m']:.1f}M</div>
      <div class="stat-label">
        <strong>TY2027 State Savings (MID)</strong>
        Individual + corporate combined vs. uncapped baseline
      </div>
    </div>
  </div>

  <div class="section" style="margin-top:24px;">
    <div class="section-title">REEC Credit Disposition by AGI Group — TY2027 (MID Scenario)</div>
    <div class="two-col">
      <div class="chart-card">
        <div class="chart-title">Stacked Credit Disposition by Income</div>
        <div class="chart-sub">Individual REEC claims ($M) · Green = claimed, Amber = lost to pro-rata cap, Red = lost to AGI screen</div>
        <div class="chart-legend" style="margin-bottom:10px;">
          <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Claimed under bill</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--amber)"></div> Lost to cap (pro-rata)</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--red)"></div> Lost to AGI screen</div>
        </div>
        <div class="chart-canvas-wrap tall"><canvas id="reecIncidenceChart"></canvas></div>
      </div>

      <div class="chart-card">
        <div class="chart-title">Credit by AGI Bin — Actuals vs. Cap</div>
        <div class="chart-sub">DOTAX TY2023 individual claims · Eligibility % under §235-12.5(a) AGI limit</div>
        <div style="overflow:auto; margin-top:4px;">
          <table class="reec-table">
            <thead>
              <tr>
                <th>AGI Range</th>
                <th>Baseline</th>
                <th>Claimed</th>
                <th>Lost/Cap</th>
                <th>Lost/AGI</th>
              </tr>
            </thead>
            <tbody>
              {reec_inc_rows}
              <tr style="border-top:2px solid var(--border); background:#f0f5ff !important;">
                <td style="font-weight:700;">Total</td>
                <td style="font-weight:700;">${reec_inc['total_base_m']:.1f}M</td>
                <td class="val-cut" style="font-weight:700;">${reec_inc['total_claim_m']:.1f}M</td>
                <td style="font-weight:700; color:var(--amber);">${round(sum(reec_inc['lost_cap']), 1):.1f}M</td>
                <td style="font-weight:700; color:var(--red);">${round(sum(reec_inc['lost_agi']), 1):.1f}M</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="reec-note" style="margin-top:12px; font-size:11px;">
          <strong>Pro-rata factor (MID TY2027):</strong> {reec_inc['pro_rata']:.3f} — because total eligible demand
          exceeds the $40M cap, each claimant receives {reec_inc['pro_rata']*100:.1f}¢ per $1 of credit requested.
          AGI screen removes filers above $175K single / $350K MFJ (§235-12.5(a)).
        </div>
      </div>
    </div>
  </div>

  <!-- REEC Time Series -->
  <div class="section" style="margin-top:24px;">
    <div class="section-title">REEC State Savings Trajectory — TY2027–TY2031</div>
    <div class="two-col">
      <div class="chart-card">
        <div class="chart-title">Annual REEC Savings by Scenario ($M)</div>
        <div class="chart-sub">State budget savings vs. uncapped baseline · LOW / MID / HIGH demand assumptions</div>
        <div class="chart-legend" style="margin-bottom:10px;">
          <div class="legend-item"><div class="legend-dot" style="background:#6c9ec4"></div> LOW</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--blue-deep)"></div> MID</div>
          <div class="legend-item"><div class="legend-dot" style="background:#2a9d8f"></div> HIGH</div>
        </div>
        <div class="chart-canvas-wrap"><canvas id="reecSavingsChart"></canvas></div>
      </div>

      <div class="chart-card">
        <div class="chart-title">REEC State Cost: Baseline vs. Bill (MID, $M)</div>
        <div class="chart-sub">Annual state cost of administering REEC credits · TY2030+ bill = $0 (§235-12.5(p) sunset)</div>
        <div class="chart-legend" style="margin-bottom:10px;">
          <div class="legend-item"><div class="legend-dot" style="background:#c0392b; opacity:.6;"></div> Baseline (uncapped)</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Under bill</div>
        </div>
        <div class="chart-canvas-wrap"><canvas id="reecCostChart"></canvas></div>
      </div>
    </div>

    <!-- REEC 5-yr summary -->
    <div style="margin-top:16px; display:grid; grid-template-columns:repeat(3,1fr); gap:12px;">
      <div class="chart-card" style="text-align:center; padding:16px;">
        <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.08em; font-weight:600; margin-bottom:6px;">LOW Scenario</div>
        <div style="font-family:'Poppins',sans-serif; font-size:22px; font-weight:800; color:#6c9ec4;">${reec_traj['five_yr']['LOW']:.1f}M</div>
        <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">5-year cumulative savings</div>
      </div>
      <div class="chart-card" style="text-align:center; padding:16px; border-color:var(--blue);">
        <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.08em; font-weight:600; margin-bottom:6px;">MID Scenario</div>
        <div style="font-family:'Poppins',sans-serif; font-size:22px; font-weight:800; color:var(--blue-deep);">${reec_traj['five_yr']['MID']:.1f}M</div>
        <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">5-year cumulative savings</div>
      </div>
      <div class="chart-card" style="text-align:center; padding:16px;">
        <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.08em; font-weight:600; margin-bottom:6px;">HIGH Scenario</div>
        <div style="font-family:'Poppins',sans-serif; font-size:22px; font-weight:800; color:var(--green);">${reec_traj['five_yr']['HIGH']:.1f}M</div>
        <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">5-year cumulative savings</div>
      </div>
    </div>

    <div class="reec-note" style="margin-top:16px;">
      <strong>Why do savings jump in TY2030–2031?</strong> §235-12.5(p) (added by SB 3125 CD2) sunsets the REEC
      for new installations effective TY2030 — so the bill scenario has $0 in new certifications while the baseline
      continues at ~$99M/yr. Prior-year carryforward stock still draws down in both scenarios, so savings are the
      difference in drawdown rates, not the full baseline amount.
    </div>
  </div>

</div><!-- end .page -->

<script>
  Chart.register(ChartDataLabels);
  Chart.defaults.font.family = "'Inter', sans-serif";
  Chart.defaults.color = '#5A6478';

  const CUT_COLOR  = '#2a9d8f';
  const MORE_COLOR = '#c0392b';

  const QUINTILE_LABELS = {q_labels_js};
  const QUINTILE_RANGES = {q_ranges_js};

  function makePillPlugin(id) {{
    return {{
      id: id,
      afterDraw(chart) {{
        const yAxis = chart.scales.y;
        const ctx   = chart.ctx;
        yAxis.ticks.forEach((tick, i) => {{
          const y     = yAxis.getPixelForTick(i);
          const group = QUINTILE_LABELS[i];
          const range = QUINTILE_RANGES[i];

          ctx.save();
          ctx.font = '700 12px Poppins, sans-serif';
          const textW = ctx.measureText(group).width;
          const pillW = textW + 14;
          const pillH = 21;
          const pillX = yAxis.right - pillW - 20;
          const pillY = y - 14;

          ctx.fillStyle = '#e8eef7';
          ctx.beginPath();
          ctx.roundRect(pillX, pillY, pillW, pillH, 5);
          ctx.fill();

          ctx.fillStyle = '#1a3564';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(group, pillX + pillW / 2, pillY + pillH / 2);

          ctx.font = '500 11px Inter, sans-serif';
          ctx.fillStyle = '#5A6478';
          ctx.fillText(range, pillX + pillW / 2, y + 14);
          ctx.restore();
        }});
      }}
    }};
  }}

  function makeDistribConfig(data, pluginId) {{
    return {{
      type: 'bar',
      plugins: [makePillPlugin(pluginId)],
      data: {{
        labels: QUINTILE_LABELS,
        datasets: [{{
          data: data,
          backgroundColor: data.map(v => v >= 0 ? MORE_COLOR : CUT_COLOR),
          borderRadius: 4,
          borderSkipped: false,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        layout: {{ padding: {{ left: 70, right: 90 }} }},
        plugins: {{
          legend: {{ display: false }},
          datalabels: {{
            anchor: 'end',
            align: 'end',
            font: {{ size: 13, weight: '700' }},
            color: '#1A1A2E',
            formatter: (value) => {{
              if (value === 0) return '';
              const abs = Math.abs(Math.round(value)).toLocaleString();
              return (value < 0 ? '−' : '+') + '$' + abs;
            }}
          }},
          tooltip: {{
            callbacks: {{
              label: ctx => {{
                const v = Math.round(ctx.parsed.x);
                const abs = Math.abs(v).toLocaleString();
                return ' ' + (v < 0 ? 'Tax cut: −$' : 'Tax increase: +$') + abs + '/yr avg';
              }}
            }}
          }}
        }},
        scales: {{
          x: {{ grid: {{ color: '#E2E8F0' }}, ticks: {{ display: false }} }},
          y: {{
            grid: {{ display: false }},
            afterFit: function(axis) {{ axis.width = 185; }},
            ticks: {{ display: false }}
          }}
        }}
      }}
    }};
  }}

  new Chart(document.getElementById('chart2027'), makeDistribConfig({data27_js}, 'pills2027'));
  new Chart(document.getElementById('chart2031'), makeDistribConfig({data31_js}, 'pills2031'));

  // ── REEC Incidence Chart (stacked horizontal bar by AGI bin)
  const reecBins    = {reec_bins_js};
  const reecClaimed = {reec_claimed_js};
  const reecLostCap = {reec_lcap_js};
  const reecLostAGI = {reec_lagi_js};

  new Chart(document.getElementById('reecIncidenceChart'), {{
    type: 'bar',
    data: {{
      labels: reecBins,
      datasets: [
        {{ label: 'Claimed under bill', data: reecClaimed, backgroundColor: '#2a9d8f', borderRadius: 0 }},
        {{ label: 'Lost to pro-rata cap', data: reecLostCap, backgroundColor: '#E07B00', borderRadius: 0 }},
        {{ label: 'Lost to AGI screen',  data: reecLostAGI, backgroundColor: '#c0392b', borderRadius: [0,4,4,0] }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      layout: {{ padding: {{ right: 50 }} }},
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{
          display: ctx => ctx.datasetIndex === 2 && (reecClaimed[ctx.dataIndex] + reecLostCap[ctx.dataIndex] + reecLostAGI[ctx.dataIndex]) > 0.5,
          anchor: 'end',
          align: 'end',
          font: {{ size: 11, weight: '600' }},
          color: '#1A1A2E',
          formatter: (v, ctx) => {{
            const total = reecClaimed[ctx.dataIndex] + reecLostCap[ctx.dataIndex] + reecLostAGI[ctx.dataIndex];
            return '$' + total.toFixed(1) + 'M';
          }}
        }},
        tooltip: {{
          callbacks: {{
            label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.parsed.x.toFixed(2) + 'M'
          }}
        }}
      }},
      scales: {{
        x: {{ stacked: true, grid: {{ color: '#E2E8F0' }}, ticks: {{ callback: v => '$' + v + 'M' }} }},
        y: {{ stacked: true, grid: {{ display: false }}, ticks: {{ font: {{ size: 12 }} }} }}
      }}
    }}
  }});

  // ── REEC Savings Trajectory
  const reecYears = {reec_years_js};
  const reecLow   = {reec_low_js};
  const reecMid   = {reec_mid_js};
  const reecHigh  = {reec_high_js};

  new Chart(document.getElementById('reecSavingsChart'), {{
    type: 'bar',
    data: {{
      labels: reecYears,
      datasets: [
        {{ label: 'LOW',  data: reecLow,  backgroundColor: '#6c9ec4', borderRadius: 3 }},
        {{ label: 'MID',  data: reecMid,  backgroundColor: '#1a3564', borderRadius: 3 }},
        {{ label: 'HIGH', data: reecHigh, backgroundColor: '#2a9d8f', borderRadius: 3 }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{
          display: ctx => ctx.datasetIndex === 1,
          anchor: 'end',
          align: 'end',
          font: {{ size: 10, weight: '600' }},
          color: '#1a3564',
          formatter: v => '$' + v.toFixed(0) + 'M'
        }},
        tooltip: {{ callbacks: {{ label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(1) + 'M' }} }}
      }},
      scales: {{
        x: {{ grid: {{ display: false }} }},
        y: {{ grid: {{ color: '#E2E8F0' }}, ticks: {{ callback: v => '$' + v + 'M' }} }}
      }}
    }}
  }});

  // ── REEC Cost Baseline vs. Bill
  const reecBase = {reec_base_js};
  const reecScen = {reec_scen_js};

  new Chart(document.getElementById('reecCostChart'), {{
    type: 'bar',
    data: {{
      labels: reecYears,
      datasets: [
        {{ label: 'Baseline (uncapped)', data: reecBase, backgroundColor: 'rgba(192,57,43,0.45)', borderRadius: 3 }},
        {{ label: 'Under bill',          data: reecScen, backgroundColor: '#2a9d8f', borderRadius: 3 }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{ display: false }},
        tooltip: {{ callbacks: {{ label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(1) + 'M' }} }}
      }},
      scales: {{
        x: {{ grid: {{ display: false }} }},
        y: {{ grid: {{ color: '#E2E8F0' }}, ticks: {{ callback: v => '$' + v + 'M' }} }}
      }}
    }}
  }});
</script>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT,
                   help="Output HTML path (default: %(default)s)")
    p.add_argument("--quintile-csv", type=Path, default=QUINTILE_CSV)
    p.add_argument("--enhanced-csv", type=Path, default=ENHANCED_CSV)
    args = p.parse_args()

    missing = [str(f) for f in [args.quintile_csv, args.enhanced_csv] if not f.exists()]
    if missing:
        print(f"ERROR — missing input file(s):\n  " + "\n  ".join(missing), file=sys.stderr)
        print("\nRun these first:", file=sys.stderr)
        print("  python3 chain/forecast_sb3125_vs_fy26base.py --cd 2", file=sys.stderr)
        print("  python3 chain/forecast_sb3125_enhanced.py --cd 2", file=sys.stderr)
        sys.exit(1)

    q_df = _load_quintile(args.quintile_csv)
    e_df = _load_enhanced(args.enhanced_csv)

    html = build_html(q_df, e_df)
    args.output.write_text(html, encoding="utf-8")
    print(f"Written → {args.output}")


if __name__ == "__main__":
    main()
