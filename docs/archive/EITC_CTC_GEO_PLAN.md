# Plan — CBPP-comparable EITC/CTC by state/county/HD/SD, with TY 2025 projection

**Goal**: extend `packages/tax_modeler` to produce a report of federal EITC and CTC (and HI state EITC) totals + recipient counts at state, county, House district, and Senate district — comparable to CBPP table 367 — for two modes: (a) TY 2022 backtest matching CBPP's vintage, (b) TY 2025 projection using the existing `project_tax_units_forward` machinery.

**Branch**: work on `v2` (current). Single feature branch off `v2`: `feat/eitc-ctc-geo`.

**Hard rules** (from `CLAUDE.md`):
- Repo-relative paths only.
- 185/185 tests must pass at every commit boundary.
- Do **not** update `SB3125_CD1_FORECAST.md` — this work is independent of SB 3125.
- Cadence-aware damping unchanged (φ=0.92/mo CPI, φ=0.85/yr ACS).
- If methodology change touches the cherry-picked projection module (`census_forecaster/acs/`), queue a follow-up to re-harmonize `Housing-Affordability-Tracker` per `CLAUDE.md` companion-sync rule.

---

## Background (one-pager)

**Existing capability** (already in repo):
- Federal EITC: `packages/tax_modeler/src/tax_modeler/credits/eitc.py` — TY 2023 params hardcoded.
- Federal CTC/ACTC: `packages/tax_modeler/src/tax_modeler/credits/ctc.py` — TY 2023 params hardcoded.
- HI state EITC: `packages/tax_modeler/src/tax_modeler/credits/hi_eitc.py` — 40% of federal, refundable.
- PUMA→county and PUMA→(HD, SD) crosswalk: `packages/tax_modeler/src/tax_modeler/analysis/puma_crosswalk.py` + `data/crosswalks/hawaii_puma_districts_official_2022.csv`.
- Geographic rollups: `RevenueEstimator.state_summary()`, `.by_county()`, `.by_district('house_district'|'senate_district')` in `packages/tax_modeler/src/tax_modeler/revenue/estimator.py`.
- Forward projection: `packages/tax_modeler/src/tax_modeler/projection/tax_unit_projector.py::project_tax_units_forward` — scales income via county-specific B19013 growth ratio, then recalculates HI tax + CTC + EITC.

**CBPP table 367** (https://apps.cbpp.org/program_participation/#table/367/eitc-and-ctc-claims):
- Tax Year 2022, last updated 2025-02-14.
- Geography: State + State Legislative District (**Upper Chamber only** — Hawaii Senate, 25 SDs).
- Source: IRS ZIP-code claims data → 2024 SLDs via 2020-population crosswalk.
- Metrics: federal EITC claims & dollars; CTC + ACTC dollars (incl. ODC); refundable-only / nonrefundable-only CTC splits.
- Caveat: AGI<$1 excluded; some districts suppressed; SD figures may not sum exactly to state total.

**Gap to close**:
1. EITC/CTC parameters are TY 2023 only — need TY 2022, TY 2024, TY 2025 sets.
2. Project recomputes credits but with TY 2023 params even when projecting to TY 2025.
3. No take-up imputation for EITC/CTC — model gives *eligible* amounts; CBPP shows *claimed* from IRS.
4. No IRS state-level EITC/CTC benchmarks in `data/admin_caseload/hawaii_caseload.csv`.
5. Stale comment at `revenue/estimator.py:23` says Hawaii has no state EITC — wrong.

---

## Execution phases (commit at each phase boundary)

### Phase 1 — Year-parameterized credit calculators

**Files to change**:
- `packages/tax_modeler/src/tax_modeler/credits/eitc.py`
- `packages/tax_modeler/src/tax_modeler/credits/ctc.py`

**Tasks**:
1. Extract the `EITCParameters` and `CTCParameters` defaults into a year-keyed dict factory. Add params for:
   - **TY 2022** (IRS Rev. Proc. 2021-45): EITC max credit `$560 / $3,733 / $6,164 / $6,935` for 0/1/2/3+ kids; CTC `$2,000` w/ refundable `$1,500`/child.
   - **TY 2023** (existing — IRS Rev. Proc. 2022-38): EITC `$600 / $3,995 / $6,604 / $7,430`; CTC `$2,000` w/ refundable `$1,600`.
   - **TY 2024** (IRS Rev. Proc. 2023-34): EITC `$632 / $4,213 / $6,960 / $7,830`; CTC `$2,000` w/ refundable `$1,700`.
   - **TY 2025** (IRS Rev. Proc. 2024-40): EITC `$649 / $4,328 / $7,152 / $8,046`; CTC `$2,000` w/ refundable `$1,700`. Phase-in/phaseout thresholds per published Rev. Proc.
2. Plumb `tax_year` argument through `calculate_eitc`, `calculate_ctc`, the `_for_tax_units` batch wrappers, and the `compute_hi_eitc_for_units` chain (HI EITC just multiplies federal — no separate year params needed).
3. Default `tax_year` remains 2023 to keep all existing call sites working.
4. **Tests**: add `tests/tax_modeler/credits/test_year_params.py` — assert max EITC for 2-child unit at exactly the saturation point matches each year's official IRS table value to the dollar.

**Acceptance**: existing tests pass; new param tests pass; no behavior change at default `tax_year=2023`.

### Phase 2 — Year-aware projection

**Files to change**:
- `packages/tax_modeler/src/tax_modeler/projection/tax_unit_projector.py`

**Tasks**:
1. Find the credit-recalculation step that currently bakes in 2023 params. Pass `tax_year=target_year` through to `calculate_eitc_for_tax_units` and `calculate_ctc_for_tax_units` (now possible after Phase 1).
2. Document the assumption in the projector docstring: nominal incomes are scaled by B19013 ratio, then credits applied with the **target-year's** statutory parameters (this is the IRS inflation-indexed treatment — phase-in / phase-out thresholds move with chained CPI per the Rev. Procs.).
3. **Test**: project a synthetic 2-kid filer at $30k/EI from 2022 → 2025; assert EITC > 2022 baseline (params grew), CTC refundable cap = $1,700.

**Acceptance**: `project_tax_units_forward(base_year_units, target_year=2025)` uses TY 2025 EITC/CTC params; backtest mode at `target_year=2022` uses TY 2022 params.

### Phase 3 — IRS administrative benchmarks for take-up

**Files to change/add**:
- `packages/tax_modeler/src/tax_modeler/data/admin_caseload/hawaii_caseload.csv` — append rows.
- `packages/tax_modeler/src/tax_modeler/calibration/takeup_imputation.py` — extend.

**Tasks**:
1. Pull Hawaii state-level IRS EITC/CTC totals from IRS SOI Historic Table 2 (state, TY 2022). Add rows to `hawaii_caseload.csv`:
   ```
   eitc,2022,return,<N>,<$M>,"IRS SOI Historic Table 2, Hawaii, TY2022"
   ctc,2022,return,<N>,<$M>,"IRS SOI Historic Table 2, Hawaii, TY2022 (refundable + nonrefundable + ODC)"
   actc,2022,return,<N>,<$M>,"IRS SOI Historic Table 2, Hawaii, TY2022 (refundable portion / Additional CTC)"
   ```
2. Extend `TakeupImputer` (or build a thin wrapper) so it handles `program in {'eitc', 'ctc'}` with the same rank-based logic it uses for SNAP/SSI. Eligible filers are ranked by EITC/CTC eligible dollars descending; mark `claimed=True` for the top N until the weighted count hits the IRS target.
3. Apply the take-up adjustment as a post-step in `pipeline.py` AFTER projection, BEFORE `RevenueEstimator`. Add an opt-out flag for callers that want raw eligibility numbers.

**Acceptance**: after take-up imputation, state-level total EITC dollars within ±2% of IRS SOI Hawaii TY 2022; total CTC dollars within ±3%.

### Phase 4 — CBPP comparison + report script

**New file**: `scripts/eitc_ctc_geo_report.py`

**Tasks**:
1. CLI:
   ```
   python scripts/eitc_ctc_geo_report.py --tax-year 2025 --out reports/eitc_ctc_2025/
   python scripts/eitc_ctc_geo_report.py --tax-year 2022 --compare-cbpp --out reports/eitc_ctc_2022_backtest/
   ```
2. Output four CSVs per run:
   - `by_state.csv` — single-row state totals (filers receiving EITC, total EITC $, filers receiving CTC, total CTC $, refundable CTC $, HI EITC $).
   - `by_county.csv` — 4 Hawaii counties × same columns.
   - `by_house_district.csv` — 51 rows × same columns.
   - `by_senate_district.csv` — 25 rows × same columns.
3. When `--compare-cbpp` is set:
   - Download/cache CBPP table 367 senate-district data (the `getSpreadsheetByID&table_id=367` endpoint — try `curl -A "Mozilla/5.0 …"`, fall back to a checked-in copy at `packages/tax_modeler/src/tax_modeler/data/raw/cbpp_table367_ty2022.csv` for reproducibility).
   - Emit `cbpp_vs_modeler_2022.csv` with columns: `senate_district, cbpp_eitc_returns, model_eitc_returns, delta_pct, cbpp_eitc_dollars, model_eitc_dollars, delta_pct, ...` (same for CTC).
4. Log a top-level summary table to stdout with state-level totals + a flag for any SD where delta exceeds ±15%.

**Acceptance**: report runs end-to-end on the existing PUMS panel; state-level deltas within ±5% of CBPP TY 2022.

### Phase 5 — Cleanup + docs

**Files to change**:
- `packages/tax_modeler/src/tax_modeler/revenue/estimator.py:23` — fix stale comment ("Hawaii does not currently have a state CTC or EITC" → "Hawaii has a state EITC at 40% of federal (refundable, HRS §235-55.75); state CTC is not enacted as of 2026.").
- New `packages/tax_modeler/README.md` section "EITC/CTC by geography" — 1 paragraph + example invocation.

**Tasks**:
1. Update stale comment.
2. Add a short methodology note in `METHODOLOGY.md` (new subsection §6 or wherever credits live) covering: parameter vintages, take-up imputation method, CBPP comparison caveats (PUMA→district vs ZIP→district).

**Acceptance**: 185/185 tests pass; ruff clean; `make test` green.

---

## Deliverables (commit log)

```
feat(credits): year-keyed EITC/CTC parameters TY 2022-2025
feat(projection): apply target-year credit params during forward projection
feat(takeup): IRS administrative EITC/CTC anchors + take-up imputation
feat(scripts): eitc_ctc_geo_report.py with CBPP backtest comparison
docs: fix stale HI EITC comment, add EITC/CTC methodology notes
```

Push to `feat/eitc-ctc-geo` and open PR against `v2`.

## Out of scope (flag for follow-up tasks)

- **ZIP→LD raking**: CBPP's true geographic resolution requires IRS SOI ZIP-code claims data. The PUMS approach gives 1 average per PUMA spread across HD/SD via hash — preserves PUMA-level signal but not within-PUMA. If the user later wants tighter LD estimates, ingest IRS SOI ZIP data and rake the modeler's LD column to it.
- **Hawaii state CTC**: Hawaii has no permanent state CTC as of 2026. Leave a `hi_ctc.py` scaffold only if explicitly requested.
- **Companion sync**: if Phase 2 touches `packages/census_forecaster/src/census_forecaster/acs/`, queue a follow-up to re-harmonize `Housing-Affordability-Tracker`'s `census_forecasting/` cherry-pick (last sync `d7cbdf4`, April 2026).

## Working notes for madison

- A related but separate project at `~/repos/ctc-and-eitc/` predates this work and has some tangentially-relevant CTC/EITC code. Treat it as reference only; the home for this work is `~/repos/Census-Forecaster/packages/tax_modeler/`.
- After each phase, run `make test` (or `pytest tests/`) and commit only if 185/185 pass. If a test fails, fix root cause — do not skip.
- All IRS parameter values must cite the originating Rev. Proc. in a code comment.
- Open one PR per phase ideally; if scope demands, one PR for the full feature is fine.
