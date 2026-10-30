#!/usr/bin/env python3
"""Build the static Hawaiʻi Appleseed poverty-impact dashboard.

Reads the latest tier reports/ CSVs and emits a self-contained HTML file
at ``dashboard/dist/index.html`` (plus assets).

Stack: vanilla HTML + Chart.js (CDN-loaded once; pinned), inline CSS.
No npm/Next.js — keeps the build single-step and reproducible.

Usage:
    .venv/bin/python scripts/build_poverty_dashboard.py [--tier-dir reports/poverty_impact_2024_tier3]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "dashboard" / "dist"


# Hawaiʻi Appleseed brand palette — single-sourced so the dashboard and
# the poverty brief cannot drift apart.
from tax_modeler.reporting.palette import (  # noqa: E402
    CHARCOAL as COLOR_CHARCOAL,
)
from tax_modeler.reporting.palette import (
    GOLD as COLOR_GOLD,
)
from tax_modeler.reporting.palette import (
    LIGHT_GRAY as COLOR_LIGHT,
)
from tax_modeler.reporting.palette import (
    LIGHT_TEAL as COLOR_CALLOUT,
)
from tax_modeler.reporting.palette import (
    SLATE as COLOR_SLATE,
)
from tax_modeler.reporting.palette import (
    TEAL as COLOR_TEAL,
)


def _autodetect_tier_dir() -> Path:
    """Pick the most recent reports/poverty_impact_<year>_tier*/ directory."""
    candidates = sorted(
        (REPO_ROOT / "reports").glob("poverty_impact_*_tier*"),
        key=lambda p: p.name,
        reverse=True,
    )
    for c in candidates:
        if (c / "by_state.csv").exists():
            return c
    raise FileNotFoundError(
        "No reports/poverty_impact_*_tier*/ directory with by_state.csv found."
    )


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def _num(s, default=0.0) -> float:
    if s is None or s == "":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _money_short(n: float) -> str:
    """Format dollars short — $1.5M, $250K, $1.2B."""
    a = abs(n)
    if a >= 1e9:
        return f"${n / 1e9:.1f}B"
    if a >= 1e6:
        return f"${n / 1e6:.0f}M"
    if a >= 1e3:
        return f"${n / 1e3:.0f}K"
    return f"${n:.0f}"


def _persons(n: float) -> str:
    return f"{int(round(n)):,}"


def _tax_year_from_dir(p: Path) -> int:
    for token in p.name.split("_"):
        if token.isdigit() and len(token) == 4:
            return int(token)
    return 0


def _extract_data(tier_dir: Path) -> dict:
    by_state = _load_csv(tier_dir / "by_state.csv")
    by_county = _load_csv(tier_dir / "by_county.csv")
    by_house = _load_csv(tier_dir / "by_house_district.csv")
    by_senate = _load_csv(tier_dir / "by_senate_district.csv")
    by_hh = _load_csv(tier_dir / "by_household_type.csv")

    state = by_state[0] if by_state else {}

    # Counties sorted by population (state-level numerator from by_county sum)
    counties = []
    for row in by_county:
        if not row.get("county") or row["county"] in {"", "None", "nan"}:
            continue
        counties.append({
            "county": row["county"],
            "weighted_persons": _num(row.get("weighted_persons")),
            "poverty_rate_baseline": _num(row.get("poverty_rate_baseline")),
            "persons_in_poverty_baseline": _num(row.get("persons_in_poverty_baseline")),
            "persons_lifted_no_eitc": _num(row.get("persons_lifted_no_eitc")),
            "persons_lifted_no_ctc": _num(row.get("persons_lifted_no_ctc")),
            "persons_lifted_no_hi_eitc": _num(row.get("persons_lifted_no_hi_eitc")),
            "persons_lifted_no_credits": _num(row.get("persons_lifted_no_credits")),
            "persons_lifted_hi_eitc_100pct": _num(row.get("persons_lifted_hi_eitc_100pct")),
            "persons_lifted_hi_ctc_650": _num(row.get("persons_lifted_hi_ctc_650")),
        })
    counties.sort(key=lambda r: r["weighted_persons"], reverse=True)

    def _normalize_district_rows(rows, id_col):
        out = []
        for row in rows:
            geo = row.get(id_col)
            if not geo or str(geo).lower() in {"none", "nan", ""}:
                continue
            try:
                geo = int(float(geo))
            except (TypeError, ValueError):
                continue
            out.append({
                "id": geo,
                "weighted_persons": _num(row.get("weighted_persons")),
                "persons_in_poverty_baseline": _num(row.get("persons_in_poverty_baseline")),
                "poverty_rate_baseline": _num(row.get("poverty_rate_baseline")),
                "persons_lifted_no_credits": _num(row.get("persons_lifted_no_credits")),
                "persons_lifted_hi_ctc_650": _num(row.get("persons_lifted_hi_ctc_650")),
                "gap_closed_no_credits_$": _num(row.get("gap_closed_no_credits_$")),
            })
        return sorted(out, key=lambda r: r["id"])

    house = _normalize_district_rows(by_house, "house_district")
    senate = _normalize_district_rows(by_senate, "senate_district")

    # by_household_type (HoH = single-mother proxy)
    hh_types = {}
    for row in by_hh:
        fs = row.get("filing_status")
        if not fs:
            continue
        hh_types[fs] = {
            "weighted_persons": _num(row.get("weighted_persons")),
            "persons_in_poverty_baseline": _num(row.get("persons_in_poverty_baseline")),
            "poverty_rate_baseline": _num(row.get("poverty_rate_baseline")),
            "persons_lifted_no_credits": _num(row.get("persons_lifted_no_credits")),
            "persons_lifted_hi_eitc_100pct": _num(row.get("persons_lifted_hi_eitc_100pct")),
            "persons_lifted_hi_ctc_650": _num(row.get("persons_lifted_hi_ctc_650")),
        }

    return {
        "tax_year": _tax_year_from_dir(tier_dir),
        "tier_dir_name": tier_dir.name,
        "state": {
            "weighted_persons": _num(state.get("weighted_persons")),
            "poverty_rate_baseline": _num(state.get("poverty_rate_baseline")),
            "persons_in_poverty_baseline": _num(state.get("persons_in_poverty_baseline")),
            "poverty_gap_baseline_$": _num(state.get("poverty_gap_baseline_$")),
            "persons_lifted_no_eitc": _num(state.get("persons_lifted_no_eitc")),
            "persons_lifted_no_ctc": _num(state.get("persons_lifted_no_ctc")),
            "persons_lifted_no_hi_eitc": _num(state.get("persons_lifted_no_hi_eitc")),
            "persons_lifted_no_credits": _num(state.get("persons_lifted_no_credits")),
            "gap_closed_no_credits_$": _num(state.get("gap_closed_no_credits_$")),
            "persons_lifted_expanded_ctc_2021": _num(state.get("persons_lifted_expanded_ctc_2021")),
            "persons_lifted_hi_eitc_100pct": _num(state.get("persons_lifted_hi_eitc_100pct")),
            "persons_lifted_hi_ctc_650": _num(state.get("persons_lifted_hi_ctc_650")),
            "gap_closed_hi_eitc_100pct_$": _num(state.get("gap_closed_hi_eitc_100pct_$")),
            "gap_closed_hi_ctc_650_$": _num(state.get("gap_closed_hi_ctc_650_$")),
        },
        "counties": counties,
        "house_districts": house,
        "senate_districts": senate,
        "household_types": hh_types,
    }


def _render_html(data: dict) -> str:
    state = data["state"]
    counties = data["counties"]
    hh = data["household_types"]
    hoh = hh.get("head_of_household", {})

    payload = json.dumps(data, indent=2)

    # Build the senate district table rows
    senate_rows_html = "\n".join(
        f'<tr><td>SD {r["id"]}</td>'
        f'<td>{_persons(r["weighted_persons"])}</td>'
        f'<td>{r["poverty_rate_baseline"]*100:.1f}%</td>'
        f'<td>{_persons(r["persons_in_poverty_baseline"])}</td>'
        f'<td>{_persons(r["persons_lifted_no_credits"])}</td></tr>'
        for r in data["senate_districts"]
    )

    house_rows_html = "\n".join(
        f'<tr><td>HD {r["id"]}</td>'
        f'<td>{_persons(r["weighted_persons"])}</td>'
        f'<td>{r["poverty_rate_baseline"]*100:.1f}%</td>'
        f'<td>{_persons(r["persons_in_poverty_baseline"])}</td>'
        f'<td>{_persons(r["persons_lifted_no_credits"])}</td></tr>'
        for r in data["house_districts"]
    )

    rxkids_section_html = ""
    if hoh:
        rxkids_section_html = f"""
      <div class="callout-grid">
        <div class="callout">
          <div class="big-num">{hoh['poverty_rate_baseline']*100:.1f}%</div>
          <div class="big-label">Single-parent household poverty rate (TY {data['tax_year']})</div>
        </div>
        <div class="callout">
          <div class="big-num">{_persons(hoh['persons_in_poverty_baseline'])}</div>
          <div class="big-label">People in single-parent households living below the SPM threshold</div>
        </div>
        <div class="callout">
          <div class="big-num">{_persons(hoh['persons_lifted_no_credits'])}</div>
          <div class="big-label">Single-parent persons lifted by federal EITC, CTC, and HI EITC combined</div>
        </div>
      </div>
      <p>
        Head-of-household tax units serve as the closest PUMS proxy for single-parent households —
        primarily single mothers in Hawaiʻi. The combined federal + state credit stack reduces
        single-parent poverty by <strong>{(hoh['persons_lifted_no_credits']/max(1,hoh['persons_in_poverty_baseline']))*100:.0f}%</strong>
        of the baseline persons-in-poverty count. Hypothetical policy expansions could push this
        further:
      </p>
      <ul>
        <li>Raising HI EITC from 40% → 100% of federal: <strong>+{_persons(hoh.get('persons_lifted_hi_eitc_100pct', 0))} additional single-parent persons lifted</strong></li>
        <li>New $650/child HI state CTC: <strong>+{_persons(hoh.get('persons_lifted_hi_ctc_650', 0))} additional single-parent persons lifted</strong></li>
      </ul>
      <p class="note">A modeled RxKids Hawaiʻi program (prenatal + postnatal cash prescriptions for Medicaid-enrolled families) is in scope as a Tier 4 follow-up; see <code>RXKIDS_METHODOLOGY.md</code>. RxKids-specific lift numbers will appear here when the scenario is wired into the production report.</p>
"""
    else:
        rxkids_section_html = '<p class="placeholder">Single-parent household disaggregation pending — re-run report with PR #7 / RxKids submodule active.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hawaiʻi Poverty Impact — TY{data['tax_year']}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@600;700&family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --teal: {COLOR_TEAL};
      --gold: {COLOR_GOLD};
      --slate: {COLOR_SLATE};
      --charcoal: {COLOR_CHARCOAL};
      --light: {COLOR_LIGHT};
      --callout: {COLOR_CALLOUT};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{
      font-family: 'Poppins', system-ui, -apple-system, sans-serif;
      color: var(--charcoal);
      background: #fff;
      line-height: 1.5;
    }}
    h1, h2, h3 {{
      font-family: 'Manrope', system-ui, -apple-system, sans-serif;
      font-weight: 700;
      color: var(--teal);
      line-height: 1.2;
    }}
    h1 {{ font-size: 2.5rem; margin: 0 0 0.5rem; }}
    h2 {{ font-size: 1.75rem; margin: 0 0 1rem; }}
    h3 {{ font-size: 1.25rem; margin: 0 0 0.75rem; }}
    .eyebrow {{
      font-family: 'Manrope', system-ui, sans-serif;
      font-size: 0.85rem;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--gold);
      margin-bottom: 0.5rem;
    }}
    header {{
      background: var(--teal);
      color: #fff;
      padding: 3.5rem 2rem 2.5rem;
    }}
    header .container {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    header h1 {{ color: #fff; }}
    header .eyebrow {{ color: var(--gold); }}
    header .subtitle {{
      font-size: 1.1rem;
      max-width: 750px;
      opacity: 0.92;
      margin-top: 0.5rem;
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 2.5rem 2rem;
    }}
    section {{ margin-bottom: 3.5rem; }}
    .callout-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin: 1.5rem 0;
    }}
    .callout {{
      background: var(--callout);
      border-left: 4px solid var(--teal);
      padding: 1.5rem 1.25rem;
      border-radius: 4px;
    }}
    .big-num {{
      font-family: 'Manrope', sans-serif;
      font-size: 2.25rem;
      font-weight: 700;
      color: var(--teal);
      line-height: 1.1;
    }}
    .big-label {{
      font-size: 0.85rem;
      color: var(--slate);
      margin-top: 0.5rem;
      line-height: 1.4;
    }}
    .chart-wrap {{
      background: #fff;
      padding: 1rem;
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      margin: 1rem 0;
    }}
    .chart-wrap canvas {{ max-height: 360px; }}
    .tabs {{
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1rem;
      border-bottom: 2px solid var(--light);
    }}
    .tab-btn {{
      background: none;
      border: none;
      padding: 0.75rem 1.25rem;
      font-family: 'Manrope', sans-serif;
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--slate);
      cursor: pointer;
      border-bottom: 3px solid transparent;
      margin-bottom: -2px;
    }}
    .tab-btn.active {{ color: var(--teal); border-bottom-color: var(--teal); }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      margin: 1rem 0;
    }}
    th, td {{
      text-align: left;
      padding: 0.5rem 0.75rem;
      border-bottom: 1px solid var(--light);
    }}
    th {{
      font-family: 'Manrope', sans-serif;
      background: var(--light);
      color: var(--slate);
      font-weight: 600;
      cursor: pointer;
      user-select: none;
    }}
    th:hover {{ background: #ebeef3; }}
    tbody tr:hover {{ background: #fafbfc; }}
    .filter-bar {{
      display: flex;
      gap: 1rem;
      margin-bottom: 0.5rem;
      align-items: center;
    }}
    .filter-bar input, .filter-bar select {{
      padding: 0.4rem 0.6rem;
      border: 1px solid #d1d5db;
      border-radius: 4px;
      font-family: inherit;
      font-size: 0.9rem;
    }}
    p.note {{
      font-size: 0.85rem;
      color: var(--slate);
      font-style: italic;
    }}
    p.placeholder {{
      padding: 1.25rem;
      background: var(--light);
      border-left: 4px solid var(--gold);
      font-style: italic;
      color: var(--slate);
    }}
    code {{
      font-family: ui-monospace, monospace;
      font-size: 0.85em;
      background: var(--light);
      padding: 0.1em 0.35em;
      border-radius: 3px;
    }}
    details {{
      background: var(--light);
      padding: 1rem 1.25rem;
      border-radius: 6px;
      margin: 1rem 0;
    }}
    details summary {{
      cursor: pointer;
      font-family: 'Manrope', sans-serif;
      font-weight: 600;
      color: var(--teal);
    }}
    footer {{
      background: var(--charcoal);
      color: #ddd;
      padding: 2.5rem 2rem;
      font-size: 0.9rem;
    }}
    footer .container {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    footer a {{ color: var(--gold); text-decoration: none; }}
    footer a:hover {{ text-decoration: underline; }}
    .table-toggle {{
      margin-top: 0.5rem;
      font-size: 0.85rem;
    }}
    .table-toggle button {{
      background: none;
      border: 1px solid var(--slate);
      color: var(--slate);
      padding: 0.3rem 0.7rem;
      border-radius: 3px;
      cursor: pointer;
      font-family: inherit;
      font-size: 0.8rem;
    }}
  </style>
</head>
<body>
  <header>
    <div class="container">
      <div class="eyebrow">Hawaiʻi Appleseed Center for Law &amp; Economic Justice</div>
      <h1>Keeping Hawaiʻi's Families Out of Poverty</h1>
      <p class="subtitle">
        How federal and state tax credits lift tens of thousands of local residents
        above the poverty line — and what expanding them would do. Tax year {data['tax_year']}.
      </p>
    </div>
  </header>

  <main>

    <!-- ================== HERO / EXECUTIVE SUMMARY ================== -->
    <section id="summary">
      <div class="eyebrow">Executive Summary</div>
      <h2>The combined impact of federal &amp; state tax credits</h2>
      <div class="callout-grid">
        <div class="callout">
          <div class="big-num">{_persons(state['persons_lifted_no_credits'])}</div>
          <div class="big-label">People kept out of poverty every year by the federal EITC, federal CTC, and Hawaiʻi state EITC</div>
        </div>
        <div class="callout">
          <div class="big-num">{_money_short(state['gap_closed_no_credits_$'])}</div>
          <div class="big-label">Annual poverty gap closed by the three credits combined</div>
        </div>
        <div class="callout">
          <div class="big-num">+{_persons(state['persons_lifted_hi_ctc_650'])}</div>
          <div class="big-label">Additional people who would be lifted if Hawaiʻi enacted a $650-per-child state CTC</div>
        </div>
      </div>
      <p>
        Tax credits give working Hawaiʻi families the money they need for food, rent,
        and medical care — without the application overhead of programs like SNAP.
        This dashboard reports the modeled anti-poverty impact of every major federal
        and state credit, broken down by county and legislative district.
      </p>
      <p>
        At the headline level, the federal EITC alone lifts roughly
        <strong>{_persons(state['persons_lifted_no_eitc'])}</strong> Hawaiʻi residents out of
        poverty each year; the federal CTC adds <strong>{_persons(state['persons_lifted_no_ctc'])}</strong>;
        and Hawaiʻi's 40%-of-federal state EITC contributes another <strong>{_persons(state['persons_lifted_no_hi_eitc'])}</strong>.
        The three together close a <strong>{_money_short(state['gap_closed_no_credits_$'])}</strong>
        annual poverty gap.
      </p>
    </section>

    <!-- ================== BASELINE POVERTY BY COUNTY ================== -->
    <section id="baseline">
      <div class="eyebrow">Section 1</div>
      <h2>Supplemental Poverty Measure rates by county</h2>
      <p>
        The Supplemental Poverty Measure (SPM) is a more complete picture of poverty
        than the official measure — it counts the value of food, housing, and medical
        benefits as resources, and subtracts out-of-pocket medical, child care, and
        work expenses. Hawaiʻi's high cost of living drives a higher threshold than
        the contiguous-US baseline.
      </p>
      <div class="chart-wrap">
        <canvas id="county-rate-chart" aria-label="County SPM poverty rates"></canvas>
      </div>
    </section>

    <!-- ================== CREDIT-BY-CREDIT IMPACT ================== -->
    <section id="credits">
      <div class="eyebrow">Section 2</div>
      <h2>People lifted out of poverty by each credit</h2>
      <div class="tabs" role="tablist">
        <button class="tab-btn active" data-target="eitc-tab">Federal EITC</button>
        <button class="tab-btn" data-target="ctc-tab">Federal CTC</button>
        <button class="tab-btn" data-target="hi-eitc-tab">Hawaiʻi EITC</button>
      </div>
      <div id="eitc-tab" class="tab-panel active">
        <div class="chart-wrap"><canvas id="eitc-chart" aria-label="EITC persons lifted by county"></canvas></div>
      </div>
      <div id="ctc-tab" class="tab-panel">
        <div class="chart-wrap"><canvas id="ctc-chart" aria-label="CTC persons lifted by county"></canvas></div>
      </div>
      <div id="hi-eitc-tab" class="tab-panel">
        <div class="chart-wrap"><canvas id="hi-eitc-chart" aria-label="HI EITC persons lifted by county"></canvas></div>
      </div>
    </section>

    <!-- ================== LEGISLATIVE DISTRICT DRILL-DOWN ================== -->
    <section id="districts">
      <div class="eyebrow">Section 3</div>
      <h2>Legislative district drill-down</h2>
      <p>
        Per-district persons-in-poverty and combined-credit lift counts. Use these
        for legislator-specific one-pagers. District-level point estimates carry
        roughly ±20% uncertainty until IRS SOI ZIP raking is enabled.
      </p>
      <div class="filter-bar">
        <label>View:
          <select id="district-chamber">
            <option value="senate">Senate Districts</option>
            <option value="house">House Districts</option>
          </select>
        </label>
        <input type="text" id="district-filter" placeholder="Filter by ID…">
      </div>
      <table id="district-table">
        <thead>
          <tr>
            <th data-sort="0">District</th>
            <th data-sort="1">Residents</th>
            <th data-sort="2">SPM rate</th>
            <th data-sort="3">In poverty</th>
            <th data-sort="4">Lifted by all 3 credits</th>
          </tr>
        </thead>
        <tbody id="senate-rows">{senate_rows_html}</tbody>
        <tbody id="house-rows" style="display:none">{house_rows_html}</tbody>
      </table>
    </section>

    <!-- ================== EXPANSION SCENARIOS ================== -->
    <section id="expansions">
      <div class="eyebrow">Section 4</div>
      <h2>What if Hawaiʻi expanded its credits?</h2>
      <p>
        Two policy proposals modeled: raising Hawaiʻi's state EITC from its current
        40% of the federal credit to 100%, and creating a new $650-per-qualifying-child
        state CTC (refundable). Cost estimates are from legislative analysis.
      </p>
      <div class="chart-wrap"><canvas id="expansion-chart" aria-label="Expansion scenarios"></canvas></div>
      <table>
        <thead>
          <tr><th>Policy</th><th>Additional people lifted</th><th>Additional gap closed</th><th>Est. annual cost</th></tr>
        </thead>
        <tbody>
          <tr>
            <td>HI EITC → 100% of federal</td>
            <td>{_persons(state['persons_lifted_hi_eitc_100pct'])}</td>
            <td>{_money_short(state['gap_closed_hi_eitc_100pct_$'])}</td>
            <td>~$30M <span class="note">(legislative est.)</span></td>
          </tr>
          <tr>
            <td>$650/child HI state CTC</td>
            <td>{_persons(state['persons_lifted_hi_ctc_650'])}</td>
            <td>{_money_short(state['gap_closed_hi_ctc_650_$'])}</td>
            <td>~$83M <span class="note">(legislative est.)</span></td>
          </tr>
          <tr>
            <td>Both combined</td>
            <td>{_persons(state['persons_lifted_hi_eitc_100pct'] + state['persons_lifted_hi_ctc_650'])} <span class="note">(non-additive — see methodology)</span></td>
            <td>{_money_short(state['gap_closed_hi_eitc_100pct_$'] + state['gap_closed_hi_ctc_650_$'])}</td>
            <td>~$113M</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ================== SINGLE-PARENT (RXKIDS PROXY) ================== -->
    <section id="rxkids">
      <div class="eyebrow">Section 5</div>
      <h2>Single-parent household poverty &amp; RxKids Hawaiʻi</h2>
      {rxkids_section_html}
    </section>

    <!-- ================== METHODOLOGY ================== -->
    <section id="methodology">
      <div class="eyebrow">Section 6</div>
      <h2>Methodology &amp; data sources</h2>
      <details>
        <summary>What is the Supplemental Poverty Measure?</summary>
        <p>
          Unlike the official poverty measure, the SPM accounts for in-kind benefits
          (SNAP, housing assistance, school lunch), tax credits (EITC, CTC), and
          subtracts non-discretionary expenses (medical out-of-pocket, child care,
          work expenses, taxes). Hawaiʻi's SPM threshold is adjusted upward for the
          Honolulu Median Rent Index applied to the housing share of the threshold.
          Source: Census Bureau P60-280/283; Burns &amp; Fox 2021 SEHSD WP21-17.
        </p>
      </details>
      <details>
        <summary>Data sources</summary>
        <ul>
          <li><strong>Microdata:</strong> US Census Bureau 5-year ACS PUMS 2018-2022, Hawaiʻi sample</li>
          <li><strong>Tax credit calibration:</strong> IRS SOI ZIP-code data, TY 2022 Hawaiʻi anchors</li>
          <li><strong>SNAP / WIC / LIHEAP take-up:</strong> USDA-FNS and HI DHS BESSD administrative caseloads</li>
          <li><strong>MOOP / childcare / work expense:</strong> CPS ASEC Hawaiʻi donor records</li>
          <li><strong>Equivalence scale:</strong> Census Betson 3-parameter (Burns &amp; Fox 2021)</li>
          <li><strong>Geographic adjustment:</strong> Honolulu MSA Median Rent Index applied to per-tenure housing share</li>
        </ul>
      </details>
      <details>
        <summary>Known limitations</summary>
        <ul>
          <li>Static counterfactual: no modeled labor supply / marriage / fertility / filing response</li>
          <li>Unit of analysis: SPM unit (per Census P60-280). Tax-unit-grained calculations are also available via <code>--by-tax-unit</code> for diagnostic comparisons.</li>
          <li>District assignment: within-PUMA HD/SD via deterministic SERIALNO hash; ±20% point-estimate uncertainty until IRS SOI ZIP raking enabled</li>
          <li>Marginal attribution is non-additive: sum of single-credit lifts ≠ combined-credit lift due to phase-out interactions</li>
        </ul>
      </details>
    </section>

  </main>

  <footer>
    <div class="container">
      <p>
        <strong>Source:</strong> U.S. Census Bureau 5-Year ACS PUMS 2018-2022; Census-Forecaster tax simulation model,
        Hawaiʻi Appleseed 2026. Generated from <code>{data['tier_dir_name']}</code>.
        <a href="https://github.com/Hawaii-Appleseed/Census-Forecaster">Source code</a>.
      </p>
      <p>
        Hawaiʻi Appleseed Center for Law &amp; Economic Justice |
        <a href="https://hiappleseed.org">hiappleseed.org</a>
      </p>
    </div>
  </footer>

  <script>
    const DATA = {payload};
    const COLORS = {{
      teal: '{COLOR_TEAL}',
      gold: '{COLOR_GOLD}',
      slate: '{COLOR_SLATE}',
      charcoal: '{COLOR_CHARCOAL}',
    }};
    const fmt = n => Number(n).toLocaleString('en-US', {{maximumFractionDigits: 0}});
    const pct = n => (Number(n) * 100).toFixed(1) + '%';
    const baseOpts = {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
      }},
      scales: {{
        x: {{ grid: {{ display: false }} }},
        y: {{ grid: {{ color: '#eee' }}, beginAtZero: true }},
      }},
    }};

    // -- County baseline rate (horizontal bars) --
    const counties = DATA.counties;
    new Chart(document.getElementById('county-rate-chart'), {{
      type: 'bar',
      data: {{
        labels: counties.map(c => c.county),
        datasets: [{{
          data: counties.map(c => (c.poverty_rate_baseline * 100).toFixed(1)),
          backgroundColor: COLORS.teal,
        }}],
      }},
      options: {{
        ...baseOpts,
        indexAxis: 'y',
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              afterLabel: (ctx) => 'Persons in poverty: ' + fmt(counties[ctx.dataIndex].persons_in_poverty_baseline),
            }},
          }},
        }},
        scales: {{
          x: {{ ticks: {{ callback: v => v + '%' }}, beginAtZero: true }},
          y: {{ grid: {{ display: false }} }},
        }},
      }},
    }});

    const creditConfig = (canvasId, key, color) => {{
      new Chart(document.getElementById(canvasId), {{
        type: 'bar',
        data: {{
          labels: counties.map(c => c.county),
          datasets: [{{
            data: counties.map(c => Math.round(c[key])),
            backgroundColor: color,
          }}],
        }},
        options: {{
          ...baseOpts,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: {{ label: ctx => 'Lifted: ' + fmt(ctx.raw) }} }},
          }},
        }},
      }});
    }};
    creditConfig('eitc-chart', 'persons_lifted_no_eitc', COLORS.teal);
    creditConfig('ctc-chart', 'persons_lifted_no_ctc', COLORS.gold);
    creditConfig('hi-eitc-chart', 'persons_lifted_no_hi_eitc', COLORS.slate);

    // -- Tabs --
    document.querySelectorAll('.tab-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const target = btn.dataset.target;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === target));
      }});
    }});

    // -- District chamber toggle + filter --
    const chamberSel = document.getElementById('district-chamber');
    const filterInput = document.getElementById('district-filter');
    const senateBody = document.getElementById('senate-rows');
    const houseBody = document.getElementById('house-rows');
    function syncDistrictView() {{
      const showSenate = chamberSel.value === 'senate';
      senateBody.style.display = showSenate ? '' : 'none';
      houseBody.style.display = showSenate ? 'none' : '';
      const q = filterInput.value.trim().toLowerCase();
      const body = showSenate ? senateBody : houseBody;
      body.querySelectorAll('tr').forEach(r => {{
        const txt = r.cells[0].textContent.toLowerCase();
        r.style.display = (!q || txt.includes(q)) ? '' : 'none';
      }});
    }}
    chamberSel.addEventListener('change', syncDistrictView);
    filterInput.addEventListener('input', syncDistrictView);

    // -- Column sort --
    document.querySelectorAll('#district-table th').forEach(th => {{
      th.addEventListener('click', () => {{
        const idx = parseInt(th.dataset.sort);
        const showSenate = chamberSel.value === 'senate';
        const body = showSenate ? senateBody : houseBody;
        const rows = Array.from(body.querySelectorAll('tr'));
        const parseVal = (s) => {{
          const n = parseFloat(s.replace(/[,%$]/g, ''));
          return isNaN(n) ? s : n;
        }};
        const dir = th.dataset.dir === 'asc' ? -1 : 1;
        rows.sort((a, b) => {{
          const va = parseVal(a.cells[idx].textContent);
          const vb = parseVal(b.cells[idx].textContent);
          if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
          return String(va).localeCompare(String(vb)) * dir;
        }});
        th.dataset.dir = dir === 1 ? 'asc' : 'desc';
        rows.forEach(r => body.appendChild(r));
      }});
    }});

    // -- Expansion scenarios chart --
    new Chart(document.getElementById('expansion-chart'), {{
      type: 'bar',
      data: {{
        labels: ['HI EITC → 100% of federal', '$650/child HI state CTC'],
        datasets: [{{
          label: 'Additional persons lifted',
          data: [
            Math.round(DATA.state.persons_lifted_hi_eitc_100pct),
            Math.round(DATA.state.persons_lifted_hi_ctc_650),
          ],
          backgroundColor: [COLORS.teal, COLORS.gold],
        }}],
      }},
      options: {{
        ...baseOpts,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: ctx => 'Lifted: ' + fmt(ctx.raw) }} }},
        }},
      }},
    }});
  </script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier-dir", type=Path, default=None,
                    help="Source directory under reports/. Default: latest tier auto-detected.")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    tier_dir = args.tier_dir or _autodetect_tier_dir()
    if not tier_dir.is_absolute():
        tier_dir = (REPO_ROOT / tier_dir).resolve()
    print(f"Loading data from: {tier_dir.relative_to(REPO_ROOT)}")

    data = _extract_data(tier_dir)
    html = _render_html(data)

    out_dir = args.out_dir if args.out_dir.is_absolute() else (REPO_ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)
    print(f"Wrote: {out_path.relative_to(REPO_ROOT)} ({len(html):,} bytes)")
    print(f"  Tax year:    {data['tax_year']}")
    print(f"  Counties:    {len(data['counties'])}")
    print(f"  Senate dist: {len(data['senate_districts'])}")
    print(f"  House dist:  {len(data['house_districts'])}")
    print(f"  HH types:    {len(data['household_types'])}")
    print(f"\nPreview: open file://{out_path}")


if __name__ == "__main__":
    main()
