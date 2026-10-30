# Results pipeline consolidation → dashboard — scope

**Status (2026-08-03): scoped, not started.** Goal: make the forecast
families produce results the same way, so a single static dashboard can
present all of them without per-scenario code. Written after the
2026-08-03 audit (script dedup findings) and the PUMS loader fix.

## Problem

Five output conventions coexist, and nothing downstream can enumerate
"what results exist":

| Family | Producer | Lands in | Format |
|---|---|---|---|
| SB3125/HB2306 fiscal | `forecast_sb3125_enhanced.py` | **`/tmp/*.csv`** | long (scenario, tax_year, 25 metric cols) |
| SB3125 distributional | `forecast_sb3125_vs_fy26base.py` | **`/tmp/*.csv`** | long (tax_year, quintile, metric cols) |
| Poverty impact | `scripts/poverty_impact_report.py` | `reports/poverty_impact_<yr>_<tier>/` | **wide** — 43 cols, scenario names baked into headers (`poverty_rate_no_eitc`, …) |
| RxKids | `forecast_rxkids_2028.py` | `reports/rxkids_2028*/` | per-file tables + pdf + xlsx |
| HI-EITC revert | `forecast_hi_eitc_revert_20.py` | `reports/eitc_revert_20_*/` | per-file tables |

Renderers are equally scattered: `generate_itep_report.py` (HTML),
four copies of the quintile PDF renderer, `generate_reec_report.py`,
`scripts/brief/{charts,html_renderer,pdf_renderer}.py`, and
`scripts/build_poverty_dashboard.py`.

Specific frictions:

1. **`/tmp` is the inter-stage medium** for the Makefile pipeline
   (`ENHANCED_CSV := /tmp/…`). Results vanish on reboot; no provenance.
2. **No manifests.** 25 `reports/` dirs, no record of when/what
   parameters/what code produced each. (`tax_modeler/artifacts.py`
   already has `params_fingerprint` + sidecar machinery — used only for
   the tax-units cache.)
3. **Wide poverty schema.** Scenario names live in *column headers*, so
   every new scenario is a schema change and the dashboard builder must
   be edited in lockstep.
4. **Dashboard reads one family.** `build_poverty_dashboard.py`
   autodetects "latest" by reverse name-sort of
   `reports/poverty_impact_*_tier*/` — fragile, and blind to the other
   four families.
5. **Scenario knowledge is code, not data.** `TaxSystemRegistry` is 15
   classmethod getters; the bill list can't be enumerated by a runner
   or a dashboard without importing and hardcoding.

## Target architecture

```
scenario registry (data: slug → system, overlay, params, years)
    │
    ▼
one runner CLI  ──►  runs/<family>/<slug>/
                        ├── manifest.json   (params fingerprint, git SHA,
                        │                    timestamp, input cache sidecar)
                        ├── fiscal.csv       (scenario, year, metric, value)
                        ├── distribution.csv (scenario, year, quintile, metric, value)
                        ├── geo.csv          (scenario, year, geo_level, geo_id, metric, value)
                        └── poverty.csv      (scenario, year, metric, value)
    │
    ▼
one reporting layer (tables → charts → HTML/PDF)
    │
    ▼
dashboard builder: scans runs/*/manifest.json → index.json →
static site (vanilla + Chart.js, Appleseed palette — same stack as
build_poverty_dashboard.py; no npm)
```

Key design decisions:

- **Tidy long tables, one row per (scenario, year, [dimension], metric).**
  This single change is what makes a generic dashboard possible — new
  scenarios become new *rows*, not new columns.
- **Manifests reuse `artifacts.py`**, not a new mechanism.
- **`TaxSystemRegistry` classmethods stay.** Add a thin `SCENARIOS`
  spec dict on top (slug → getter + credit overlay + behavioral params
  + year span) mirroring the proven `COUNTY_SERIES` recipe from
  REGISTRY_MIGRATION_SCOPE.md.
- **Static dashboard, no framework.** Extend the existing
  build_poverty_dashboard stack; a scenario picker reads the generated
  `index.json`.

## Phases (each independently shippable)

### Phase 0 — dedup prerequisites (from the 2026-08-03 audit)
1. Delete `generate_quintile_pdf.py` (572 lines, zero references,
   docstring points at renamed files).
2. Extract the quintile PDF renderer (4 copies, ~1,100 lines) into
   `tax_modeler/reporting/quintile_pdf.py`. There is currently no PDF
   module anywhere in `packages/` — that absence is why it forked.
3. Merge `forecast_hb2306_quintile.py` + `forecast_sb3125_quintile.py`
   (~157 lines byte-identical) into one `--bill` script.
4. Fold `forecast_sb3125_sensitivity.py` into `_enhanced.py` as
   `--mode sensitivity` (its SCENARIOS is a strict subset).
5. Pick ONE writer for `data/artifacts/tax_units_cache.parquet`
   (`forecast_sb3125.py` vs `pipeline_run.py` currently race with
   conflicting `built_by` sidecars).
6. `_common.py` for `TARGET_YEARS` (11 copies), `DATA_DIR`,
   `_parse_args`, warnings/logging preamble.

**Verification:** byte-compare regenerated PDFs/CSVs against current
outputs (the golden-first recipe from REGISTRY_MIGRATION_SCOPE.md).

### Phase 1 — results store
- Add `runs/` with manifest writing (wrap `artifacts.py`).
- Point the Makefile at `runs/sb3125_cd2/` instead of `/tmp`.
- Emit tidy tables *alongside* existing formats (additive; nothing
  breaks). Convert `by_state.csv` wide → long in
  `poverty_impact_report.py`, keeping the wide file during transition.

### Phase 2 — scenario registry + runner ✅ DONE (2026-08-04)
- `tax_modeler.scenarios.registry`: one `ScenarioSpec` per slug (9
  scenarios) carrying domain facts only — system getter, credit-overlay
  mode (`none`/`standard`/`vintage`), baseline slug, year support.
  `system_for(year)` hides the split between year-parameterized getters
  and the TY2027-only ones that every runner used to hardcode.
- `run_scenario.py --list` enumerates scenarios x runners; `--slug X
  --runner Y` dispatches. Deliberately a **dispatcher, not a
  re-implementation** — it shells out to the existing scripts, so
  routing through it cannot move any number.
- `forecast_bill_quintile.py` now takes systems/overlay/baseline from
  the registry and keeps only presentation locally.

**Deviation from the original plan:** root scripts were NOT collapsed
into thin wrappers of a single `run_scenario(slug)` function. Each
carries genuinely different machinery (dynamic vs static scoring,
behavioral response, ProcessPool fan-out), and folding them into one
entry point would have meant rewriting model code for cosmetic
uniformity — exactly the risk REGISTRY_MIGRATION_SCOPE.md's "not
candidates" section warns about. The registry + dispatcher delivers the
enumerable surface Phase 4 needs without touching model math.

**Not yet wired to any runner** (registry-defined, dispatcher reports
them as such): `act46_rollback_targeted`, `millionaire_tax`,
`sb3125_original`, `hb2306_orig`. These were modeled ad hoc before; they
now at least appear in `--list` instead of being invisible.

### Phase 3 — reporting layer ✅ DONE (2026-08-04)
- `scripts/brief/` promoted to `tax_modeler.reporting.brief`. It was
  already a clean data/charts/pdf/html split but lived in `scripts/`,
  reachable only via a `sys.path.insert` hack — nothing else could
  import it. Now installed alongside `reporting.quintile_pdf`, so
  Phase 4 can reuse its chart helpers.
- `tax_modeler.reporting.palette`: the Appleseed brand hexes were
  duplicated verbatim (under two naming conventions) in
  `brief/data.py` and `build_poverty_dashboard.py`. Both now import
  one module, so a brand-guide change is a one-line edit.

**Deviation — `generate_itep_report.py` was NOT ported.** The scope
above assumed its chart blocks should move onto the shared layer. They
can't: it runs on **system python3** by design (`make report` uses
`PYTHON_PLAIN`, and the Makefile documents it as "only needs pandas"),
and it imports nothing from the workspace. Making it import
`tax_modeler.reporting` would force a venv on the one deliverable
deliberately built to run without one. Its palette also differs from the
brand palette on purpose (ITEP-comparable design language). Left alone.

`generate_reec_report.py` likewise keeps its own blue-tinted design
language — a separate deliverable, not drift.

**Gotcha fixed en route:** `brief/data.py` resolved `REPO_ROOT` by
counting parents (`parents[2]`, correct under `scripts/brief/`). The
move made that point inside the package; it is now `parents[6]` and
pinned by `tests/tax_modeler/test_brief_paths.py`, so a future move
fails loudly rather than silently resolving to the wrong directory.

### Phase 4 — dashboard ✅ DONE (2026-08-04)
- `scripts/build_dashboard.py` scans `runs/` manifests + poverty tier
  reports, writes `dashboard/dist/index.json`, and renders
  `dashboard/dist/dashboard.html` — four tabs (Fiscal, Distribution,
  Poverty, Catalog), vanilla HTML + Chart.js, brand palette from
  `reporting.palette`.
- **The builder contains no scenario knowledge.** It reads manifests and
  tidy long tables, resolving run → registry slug → label. Proven in
  practice: the Distribution tab was an empty state until
  `run_scenario.py --slug sb3125_cd2 --runner fy26base` was run, then
  populated on the next build with zero code change.
- The Catalog tab lists all 9 registry scenarios and marks which have a
  run, so unmodeled scenarios are visible rather than absent.

**Deviation:** the existing `build_poverty_dashboard.py` was left intact
rather than folded in as a tab. It is a polished, purpose-built
deliverable (`dist/index.html`); the new builder writes alongside it
(`dist/dashboard.html`) and was verified not to change its output. Merging
them is a presentation decision for later, not a pipeline requirement.

**Still wide, not tidy:** RxKids and EITC-revert reports write per-file
tables without a tidy long form, so they do not yet appear. Adding them
is now a matter of emitting `*_tidy.csv` in those scripts — no dashboard
change needed.

## Status

Phases 0-4 complete. The pipeline the scope set out to build exists:
scenario registry → dispatcher → manifested runs → tidy tables → shared
reporting → dashboard. What remains is incremental: tidy-ify the
remaining report families, and decide whether the two dashboards merge.

## Not in scope

- `census_forecaster` projection internals (Kalman, anchors, ML
  features) — cherry-picked by Housing-Affordability-Tracker; the
  dashboard consumes outputs only.
- `forecast_rxkids_2028.py` internals (77 KB) — wrap its outputs in a
  manifest; do not refactor the model.
- Live/served dashboard — static files only, matching current
  deployment habits.

## Effort

| Phase | Size | Risk |
|---|---|---|
| 0 | ~1 day | low — golden-verified dedup |
| 1 | ~½ day | low — additive |
| 2 | ~1 day | medium — touches all runners |
| 3 | ~1 day | low |
| 4 | ~1–2 days | low — pure consumer |

## Housekeeping to fold in opportunistically

- `CLAUDE.md` says "185/185 tests" (now 1772) and mandates updating
  `SB3125_CD2_FORECAST.md` — the file is `SB3125_CD1_FORECAST.md`.
- Move finished audit/scope docs (`PIPELINE_AUDIT_*`,
  `REVIEW_FINDINGS.md`, `CBO_COMPONENT_AGING_SCOPE.md`,
  `SOI_DIRECT_ANCHOR_SCOPE.md`, `EITC_CTC_GEO_PLAN.md`,
  `FORECAST_ASSESSMENT_*.md`) to `docs/archive/`.
- Superseded `reports/` tiers (2022/2024 tier1/tier4-legacy, review
  dirs): archive or untrack once manifests exist; keep the latest per
  family.
