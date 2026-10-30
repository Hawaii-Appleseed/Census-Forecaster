# Archive

Finished scoping docs, audits and reviews, moved out of the repo root 2026-10-30. They are history: do not edit them, and do not read them as a description of the current code. The date is the commit that last touched the file (`git log -1 --format=%as`).

- `REVIEW_FINDINGS.md` — SPM methodology code review (findings table with fix commits); a point-in-time review, its findings applied. Last touched 2026-05-19.
- `PIPELINE_AUDIT_2026-06-10.md` — audit of tax-unit/household construction and the forecasting engine, written against branch `v2`; a point-in-time audit. Last touched 2026-06-10.
- `PIPELINE_AUDIT_ROUND2_2026-06-10.md` — second pass: unit-construction internals and how forecasts feed the microsim; a point-in-time audit. Last touched 2026-06-10.
- `FORECAST_ASSESSMENT_2026-06-11.md` — end-to-end assessment of the revenue forecast chain, verified against main at `661ca38`; a point-in-time assessment. Last touched 2026-06-11.
- `ACS_PREDICTION_ASSESSMENT_2026-08-21.md` — assessment of the ACS/statistical projection engine; its recommendations shipped as the v3 calibrated ensemble (see `packages/census_forecaster/CLAUDE.md`). Last touched 2026-09-29.
- `SOI_DIRECT_ANCHOR_SCOPE.md` — scope for anchoring the $1M+ tail directly on IRS SOI Table 1.4; shipped as `tax_modeler.calibration.soi_top_anchor`. Last touched 2026-05-05.
- `REGISTRY_MIGRATION_SCOPE.md` — registry-pattern migration of the county series (candidate 1 done, candidate 2 declined); the recipe is cited from `METHODOLOGY.md`. Last touched 2026-07-16.
- `EITC_CTC_GEO_PLAN.md` — plan for the CBPP-comparable EITC/CTC report by state/county/district; built as `scripts/eitc_ctc_geo_report.py`. Last touched 2026-05-15.
- `DASHBOARD_PIPELINE_SCOPE.md` — results-pipeline consolidation (manifested `runs/`, scenario registry, `scripts/run_scenario.py`, reporting layer, dashboard); phases 1-4 done, publishing now goes through `scripts/publish.py` and `scripts/build_site.py`. Last touched 2026-08-04.
- `CBO_COMPONENT_AGING_SCOPE.md` — scope for CBO per-component income aging; shipped, `--cbo-aging` is on by default in `scripts/poverty_impact_report.py` and the chain uses `use_cbo_aging=True`. Last touched 2026-09-30.
