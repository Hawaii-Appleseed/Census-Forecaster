#!/usr/bin/env python3
"""Build the multi-family Census-Forecaster dashboard (Phase 4).

Discovers every manifested run under ``runs/`` plus the poverty-impact
tier reports, writes a machine-readable ``index.json``, and renders a
self-contained static site with one tab per result family.

This builder knows nothing about individual scenarios. It reads the
manifests written by ``tax_modeler.runs`` and the tidy long tables
produced in Phase 1, so a new scenario shows up as new *rows* rather
than requiring a code change here — the property the whole pipeline
consolidation was for.

Stack matches build_poverty_dashboard.py: vanilla HTML + Chart.js
(CDN-pinned), inline CSS, no npm. Brand colors come from
tax_modeler.reporting.palette.

Usage:
    .venv/bin/python scripts/build_dashboard.py
    .venv/bin/python scripts/build_dashboard.py --out-dir dashboard/dist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from tax_modeler.reporting.palette import CSS_VARS
from tax_modeler.runs import list_runs
from tax_modeler.scenarios.registry import SCENARIO_SPECS

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"
REPORTS_DIR = REPO_ROOT / "reports"
DEFAULT_OUT = REPO_ROOT / "dashboard" / "dist"

CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"

# Poverty tier directories, best first — same preference order the brief uses.
TIER_PREFERENCE = [
    "poverty_impact_2025_tier4_spm",
    "poverty_impact_2025_tier3",
    "poverty_impact_2024_tier4_spm",
    "poverty_impact_2024_tier3",
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _read_tidy(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pd.read_csv(path).to_dict(orient="records")


def collect_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Manifested runs, each with its tidy tables inlined."""
    out = []
    for manifest in list_runs(runs_dir):
        run_dir = runs_dir / manifest["run_dir"]
        entry = {
            "id": manifest["run_dir"],
            "script": manifest.get("script"),
            "created_at": manifest.get("created_at"),
            "git_sha": manifest.get("git_sha"),
            "params_fingerprint": manifest.get("params_fingerprint"),
            "outputs": manifest.get("outputs", []),
            "fiscal": _read_tidy(run_dir / "fiscal_tidy.csv"),
            "distribution": _read_tidy(run_dir / "distribution_tidy.csv"),
        }
        params = manifest.get("params") or {}
        slug = params.get("slug") or _slug_from_run_id(manifest["run_dir"])
        entry["slug"] = slug
        spec = SCENARIO_SPECS.get(slug)
        entry["label"] = spec.label if spec else (params.get("label") or slug or entry["id"])
        entry["overlay"] = spec.overlay if spec else params.get("overlay")
        out.append(entry)
    return out


def _slug_from_run_id(run_id: str) -> str | None:
    """`sb3125_cd2_enhanced` -> `sb3125_cd2` (longest matching registry slug)."""
    for slug in sorted(SCENARIO_SPECS, key=len, reverse=True):
        if run_id.startswith(slug):
            return slug
    return None


def collect_poverty(reports_dir: Path) -> dict[str, Any] | None:
    """Latest poverty tier report that has a tidy long table."""
    candidates = [reports_dir / n for n in TIER_PREFERENCE]
    candidates += sorted(reports_dir.glob("poverty_impact_*"), reverse=True)
    for d in candidates:
        long_path = d / "by_state_long.csv"
        if long_path.exists():
            return {"id": d.name, "rows": _read_tidy(long_path)}
    return None


def build_index(runs_dir: Path, reports_dir: Path) -> dict[str, Any]:
    runs = collect_runs(runs_dir)
    poverty = collect_poverty(reports_dir)
    return {
        "runs": runs,
        "poverty": poverty,
        "scenarios": [
            {"slug": s.slug, "label": s.label, "family": s.family,
             "overlay": s.overlay, "statute": s.statute}
            for s in SCENARIO_SPECS.values()
        ],
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_html(index: dict[str, Any]) -> str:
    payload = json.dumps(index, indent=None, separators=(",", ":"))
    css_vars = "\n      ".join(f"--{k}: {v};" for k, v in CSS_VARS.items())
    n_runs = len(index["runs"])
    n_scen = len(index["scenarios"])
    pov_id = index["poverty"]["id"] if index["poverty"] else "none"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Census-Forecaster — Results Dashboard</title>
<script src="{CHART_JS}"></script>
<style>
  :root {{
      {css_vars}
      --rule: #dfe3e8;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      color: var(--charcoal); background: #fff; line-height: 1.5;
  }}
  header {{
      background: var(--teal); color: #fff; padding: 22px 28px;
  }}
  header h1 {{ margin: 0; font-size: 21px; font-weight: 700; }}
  header p {{ margin: 5px 0 0; font-size: 13px; opacity: .9; }}
  nav {{
      display: flex; gap: 2px; background: var(--light);
      border-bottom: 1px solid var(--rule); padding: 0 20px; flex-wrap: wrap;
  }}
  nav button {{
      border: 0; background: transparent; padding: 12px 18px; cursor: pointer;
      font-size: 14px; font-weight: 600; color: var(--slate);
      border-bottom: 3px solid transparent;
  }}
  nav button[aria-selected="true"] {{
      color: var(--teal); border-bottom-color: var(--gold); background: #fff;
  }}
  main {{ padding: 22px 28px 60px; max-width: 1100px; }}
  section[hidden] {{ display: none; }}
  h2 {{ font-size: 17px; margin: 0 0 4px; color: var(--teal); }}
  .sub {{ color: var(--slate); font-size: 13px; margin: 0 0 18px; }}
  .card {{
      border: 1px solid var(--rule); border-radius: 8px; padding: 16px;
      margin-bottom: 18px; background: #fff; overflow-x: auto;
  }}
  .chart-wrap {{ position: relative; height: 340px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--rule); }}
  th:first-child, td:first-child {{ text-align: left; }}
  thead th {{ background: var(--light); color: var(--teal); font-weight: 700; }}
  .meta {{ font-size: 12px; color: var(--slate); }}
  .meta code {{ background: var(--light); padding: 1px 5px; border-radius: 3px; }}
  .pill {{
      display: inline-block; background: var(--callout); color: var(--teal);
      border-radius: 999px; padding: 2px 10px; font-size: 12px; font-weight: 600;
      margin-right: 6px;
  }}
  .empty {{ color: var(--slate); font-style: italic; }}
  select {{
      font-size: 13px; padding: 6px 9px; border: 1px solid var(--rule);
      border-radius: 6px; margin-bottom: 14px; background: #fff;
  }}
</style>
</head>
<body>
<header>
  <h1>Census-Forecaster — Results Dashboard</h1>
  <p>{n_runs} manifested run(s) · {n_scen} registered scenarios · poverty source: {pov_id}</p>
</header>

<nav id="tabs">
  <button data-tab="fiscal"  aria-selected="true">Fiscal impact</button>
  <button data-tab="dist"    aria-selected="false">Distribution</button>
  <button data-tab="poverty" aria-selected="false">Poverty impact</button>
  <button data-tab="catalog" aria-selected="false">Catalog</button>
</nav>

<main>
  <section id="tab-fiscal">
    <h2>Fiscal impact by scenario</h2>
    <p class="sub">Total revenue impact vs the Act 46 baseline, by tax year.
       Source: manifested runs under <code>runs/</code>.</p>
    <div id="fiscal-body"></div>
  </section>

  <section id="tab-dist" hidden>
    <h2>Distributional impact</h2>
    <p class="sub">Average tax change per household by income quintile.</p>
    <div id="dist-body"></div>
  </section>

  <section id="tab-poverty" hidden>
    <h2>Poverty impact</h2>
    <p class="sub">Persons lifted above the SPM poverty line, by scenario.</p>
    <div id="poverty-body"></div>
  </section>

  <section id="tab-catalog" hidden>
    <h2>Scenario catalog</h2>
    <p class="sub">Every scenario in <code>tax_modeler.scenarios.registry</code>.
       Run one with <code>run_scenario.py --slug &lt;slug&gt; --runner &lt;runner&gt;</code>.</p>
    <div id="catalog-body"></div>
  </section>
</main>

<script id="payload" type="application/json">{payload}</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);
const C = getComputedStyle(document.documentElement);
const COLORS = ['teal','gold','slate','charcoal'].map(n => C.getPropertyValue('--' + n).trim());
const fmtM = v => (v >= 0 ? '+' : '\\u2212') + '$' + Math.abs(v).toFixed(1) + 'M';
const esc = s => String(s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));

// --- tabs -----------------------------------------------------------------
document.getElementById('tabs').addEventListener('click', e => {{
  const btn = e.target.closest('button'); if (!btn) return;
  document.querySelectorAll('#tabs button').forEach(b =>
    b.setAttribute('aria-selected', String(b === btn)));
  ['fiscal','dist','poverty','catalog'].forEach(t =>
    document.getElementById('tab-' + t).hidden = (t !== btn.dataset.tab));
}});

function card(inner) {{ return '<div class="card">' + inner + '</div>'; }}
function empty(msg) {{ return card('<p class="empty">' + esc(msg) + '</p>'); }}

function metaLine(run) {{
  const bits = [];
  if (run.script) bits.push('<code>' + esc(run.script) + '</code>');
  if (run.created_at) bits.push(esc(run.created_at));
  if (run.git_sha) bits.push('git ' + esc(run.git_sha));
  if (run.params_fingerprint) bits.push('params ' + esc(run.params_fingerprint.slice(0, 8)));
  return '<p class="meta">' + bits.join(' &middot; ') + '</p>';
}}

// Pull one metric out of a tidy long table -> {{year: value}}
function pick(rows, metric) {{
  const out = {{}};
  for (const r of rows) if (r.metric === metric) {{
    const k = r.tax_year, s = r.scenario || '_';
    (out[s] = out[s] || {{}})[k] = r.value;
  }}
  return out;
}}

function lineChart(el, seriesMap, yLabel) {{
  const years = [...new Set(Object.values(seriesMap).flatMap(o => Object.keys(o)))]
      .map(Number).sort();
  const datasets = Object.entries(seriesMap).map(([name, obj], i) => ({{
    label: name, data: years.map(y => obj[y] ?? null),
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length],
    tension: .25, pointRadius: 3, borderWidth: 2, spanGaps: true,
  }}));
  new Chart(el, {{
    type: 'line',
    data: {{ labels: years.map(y => 'TY' + y), datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'bottom' }} }},
      scales: {{ y: {{ title: {{ display: !!yLabel, text: yLabel }} }} }},
    }},
  }});
}}

function barChart(el, labels, values, label) {{
  new Chart(el, {{
    type: 'bar',
    data: {{ labels, datasets: [{{ label, data: values, backgroundColor: COLORS[0] }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {{ legend: {{ display: false }} }},
    }},
  }});
}}

// --- fiscal tab -----------------------------------------------------------
(function () {{
  const runs = DATA.runs.filter(r => r.fiscal && r.fiscal.length);
  const host = document.getElementById('fiscal-body');
  if (!runs.length) {{
    host.innerHTML = empty('No fiscal runs found. Generate one with: '
      + 'run_scenario.py --slug sb3125_cd2 --runner enhanced');
    return;
  }}
  runs.forEach((run, idx) => {{
    const id = 'fx' + idx;
    const series = pick(run.fiscal, 'total_impact_$M');
    const rows = Object.entries(series).map(([scen, byYear]) => {{
      const years = Object.keys(byYear).map(Number).sort();
      const cum = years.reduce((a, y) => a + (byYear[y] || 0), 0);
      return '<tr><td>' + esc(scen) + '</td>'
        + years.map(y => '<td>' + fmtM(byYear[y]) + '</td>').join('')
        + '<td><strong>' + fmtM(cum) + '</strong></td></tr>';
    }}).join('');
    const years = [...new Set(run.fiscal.map(r => r.tax_year))].sort();
    host.insertAdjacentHTML('beforeend', card(
      '<h3 style="margin:0 0 2px;font-size:15px;">' + esc(run.label)
        + ' <span class="pill">' + esc(run.id) + '</span></h3>'
      + metaLine(run)
      + '<div class="chart-wrap"><canvas id="' + id + '"></canvas></div>'
      + '<table><thead><tr><th>Scenario</th>'
      + years.map(y => '<th>TY' + y + '</th>').join('')
      + '<th>5-yr total</th></tr></thead><tbody>' + rows + '</tbody></table>'
    ));
    lineChart(document.getElementById(id), series, 'Total impact ($M)');
  }});
}})();

// --- distribution tab -----------------------------------------------------
(function () {{
  const runs = DATA.runs.filter(r => r.distribution && r.distribution.length);
  const host = document.getElementById('dist-body');
  if (!runs.length) {{
    host.innerHTML = empty('No distributional runs found. Generate one with: '
      + 'run_scenario.py --slug sb3125_cd2 --runner fy26base');
    return;
  }}
  runs.forEach((run, idx) => {{
    const id = 'dx' + idx;
    const metric = 'avg_per_hh_total_change';
    const rows = run.distribution.filter(r => r.metric === metric);
    const years = [...new Set(rows.map(r => r.tax_year))].sort();
    const year = years[0];
    const sel = years.map(y =>
      '<option value="' + y + '">TY' + y + '</option>').join('');
    host.insertAdjacentHTML('beforeend', card(
      '<h3 style="margin:0 0 2px;font-size:15px;">' + esc(run.label)
        + ' <span class="pill">' + esc(run.id) + '</span></h3>'
      + metaLine(run)
      + '<select id="' + id + '-sel">' + sel + '</select>'
      + '<div class="chart-wrap"><canvas id="' + id + '"></canvas></div>'
    ));
    let chart = null;
    const draw = (y) => {{
      const sub = rows.filter(r => String(r.tax_year) === String(y));
      if (chart) chart.destroy();
      chart = new Chart(document.getElementById(id), {{
        type: 'bar',
        data: {{
          labels: sub.map(r => r.quintile),
          datasets: [{{ label: 'Avg change per household ($)',
                       data: sub.map(r => r.value), backgroundColor: COLORS[0] }}],
        }},
        options: {{
          responsive: true, maintainAspectRatio: false, indexAxis: 'y',
          plugins: {{ legend: {{ display: false }} }},
        }},
      }});
    }};
    draw(year);
    document.getElementById(id + '-sel').addEventListener('change', e => draw(e.target.value));
  }});
}})();

// --- poverty tab ----------------------------------------------------------
(function () {{
  const host = document.getElementById('poverty-body');
  const pov = DATA.poverty;
  if (!pov || !pov.rows.length) {{
    host.innerHTML = empty('No poverty tier report with a tidy table found.');
    return;
  }}
  const rows = pov.rows.filter(r =>
    r.metric === 'persons_lifted' && r.population === 'all');
  if (!rows.length) {{ host.innerHTML = empty('No persons_lifted rows.'); return; }}
  host.insertAdjacentHTML('beforeend', card(
    '<h3 style="margin:0 0 2px;font-size:15px;">Persons lifted above the poverty line'
      + ' <span class="pill">' + esc(pov.id) + '</span></h3>'
    + '<p class="meta">Tidy source: <code>by_state_long.csv</code></p>'
    + '<div class="chart-wrap"><canvas id="pov"></canvas></div>'
  ));
  barChart(document.getElementById('pov'),
           rows.map(r => r.scenario),
           rows.map(r => r.value), 'Persons lifted');
}})();

// --- catalog tab ----------------------------------------------------------
(function () {{
  const wired = new Set(DATA.runs.map(r => r.slug).filter(Boolean));
  const body = DATA.scenarios.map(s =>
    '<tr><td><code>' + esc(s.slug) + '</code></td><td>' + esc(s.label)
    + '</td><td>' + esc(s.family) + '</td><td>' + esc(s.overlay)
    + '</td><td>' + (wired.has(s.slug) ? 'yes' : '&mdash;')
    + '</td><td style="text-align:left">' + esc(s.statute || '') + '</td></tr>').join('');
  document.getElementById('catalog-body').innerHTML = card(
    '<table><thead><tr><th>Slug</th><th>Label</th><th>Family</th>'
    + '<th>Overlay</th><th>Has run</th><th>Statute</th></tr></thead><tbody>'
    + body + '</tbody></table>');
}})();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    index = build_index(args.runs_dir, args.reports_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    (args.out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    out = args.out_dir / "dashboard.html"
    out.write_text(render_html(index))

    print(f"[dashboard] runs: {len(index['runs'])}, "
          f"scenarios: {len(index['scenarios'])}, "
          f"poverty: {index['poverty']['id'] if index['poverty'] else 'none'}")
    print(f"[dashboard] wrote {out}")
    print(f"[dashboard] wrote {args.out_dir / 'index.json'}")
    print(f"\nPreview: open file://{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
