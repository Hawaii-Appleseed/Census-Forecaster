# Forecasting Logic Assessment — Revenue Focus
**Date:** 2026-06-11 · **Scope:** revenue forecast chain end-to-end, plus the statistical engine feeding it. All claims verified against current main (post-`661ca38`).

---

## Architecture (as-built)

```
PUMS 2020-24 5yr (2024$)                      COR Mar-2026 forecast (FY, hardcoded dict)
        │                                                  │
  build units → calibrate (DOTAX TY2022)                   │
        │                                                  │
  ① income aging ─ CBO components × HI factors (base 2024, vintage 2025-01)
        │            [or county-B19013 scalar / BLS path — mutually exclusive]
  ② forward targets ─ DOTAX TY2022 counts × hardcoded growth dials → rescaled to COR
  ③ Phase 1 IPF rake ─ weights → forward filer-count + filing-status margins
  ④ top premium ─ +1%/yr income ≥$500K, compounded from 2024
  ⑤ $1M+ resynthesis ─ SOI Table 1.4 tiers (TY2022) + optional CBO tier aging
  ⑥ Phase 2 ─ per-bracket multiplier on hi_state_tax → COR aggregate
        │
  scenario scoring (forecast_sb3125_* scripts): statutory recompute per config → deltas
```

**Key structural fact:** Phase 2 anchors the baseline *level* to COR, so the model's actual product is (a) the distribution — who pays — and (b) reform deltas. Issues are prioritized below by their effect on deltas, not levels.

---

## P0 — Correctness bugs in current numbers

### 1. C3 (live): calibrated pickle rescored without deduction params
`forecast_sb3125_vs_fy26base.py:114-116` — pickle built with `CAL_DED_PARAMS` (`forecast_sb3125_enhanced.py:453-461`), but the post-synthesis rescores call `_compute_base_tax(units)` bare → SD-only liabilities. `rescale_synthetic_tail_to_tax_target` (line 115) then derives `tail_k` from those wrong liabilities — high-income filers are itemizers, so the $663M tail anchor is rescaled against inflated tax, **under-weighting the synthetic tail**. That weight error flows into every quintile/bracket delta downstream.
Same pattern: `forecast_hb2306_quintile.py:84-86`, `forecast_sb3125_quintile.py:116-118`, `forecast_sb3125_sensitivity.py:68`.
**Fix:** thread `CAL_DED_PARAMS` (better: serialize params *inside* the pickle and have consumers read them — kills the divergence class entirely).

### 2. Phase 2 COR anchoring never reaches scored output
Verified in `forecast_sb3125_vs_fy26base.py:148-180`: after `project_and_recalibrate`, scenarios are scored by **statutory recompute** (`per_unit_tax` on income under each config). The Phase-2-scaled `hi_state_tax` column is discarded. Consequences:
- The COR anchor constrains nothing the user sees; reported baselines are statutory-recompute aggregates, and the statute-vs-COR wedge (whatever Phase 2's multipliers absorbed, bounded only by the 0.3–5.0 clip) silently re-enters levels.
- Conversely, if Phase 2 *were* carried into scoring it would be worse — scaling the tax column breaks statutory consistency for reform deltas.
**Fix (short-term):** report the wedge — `Σ(statutory tax) / COR target` per year — as an explicit calibration residual in every output. **Fix (long-term):** move the anchor to the weight side (extend Phase 1 with bracket AGI-mass margins, which the code deliberately avoids today for composition reasons — revisit now that B2–B5 improved unit construction) so statutory recompute *is* COR-consistent.

### 3. /tmp cache: no invalidation, no versioning, no locking
`/tmp/sb3125_calibrated_base.pkl` + `/tmp/tax_units_cache.parquet` shared by ≥4 scripts. Existence check only — code changes, PUMS vintage changes, deduction-param changes all serve stale bases silently. Concurrent runs can corrupt.
**Fix:** content-keyed artifact (hash of PUMS vintage + params JSON + constructor git sha) under `data/artifacts/`, params embedded, refuse mismatched loads.

---

## P1 — Methodology: biases in deltas

### 4. F5: federal EITC/CTC params clamp at TY2025 while incomes grow to 2031
`tax_unit_projector.py:568-583`. 2025 phase-out thresholds applied to 2031 nominal incomes → credits phase out at ~15% lower real income by 2031. HI EITC = 40% of federal, so it propagates straight into reform deltas (and poverty estimates). Direction: overstates revenue / understates credits, growing with horizon.
**Fix:** CPI-index the IRS Rev. Proc. parameters past 2025 (chained-CPI formula is mechanical) instead of clamping. ~Half-day.

### 5. Top-income growth: three stacked dials, never jointly backtested
- CBO component aging already grows cap-gains-heavy (top-skewed) income faster than wages
- +1%/yr premium ≥$500K compounded from 2024 (`top_premium_pct`)
- +2.5%/yr top bracket differential ≥$200K in forward count migration (`forecast_sb3125_vs_fy26base.py:144` overrides default 1.0% → 2.5%)
- +0.5%/yr `rate_drift` on per-bracket effective rates

Each is individually defensible (Auten/Gee/Turner cohort persistence is cited); their *sum* is an undocumented top-share drift assumption of roughly 3.5–4%/yr above average growth at $200K+ — compounded 7 years. Phase 2 used to launder the consequence into multipliers; since finding #2 shows Phase 2 doesn't reach output, this stack directly drives reported deltas.
**Fix:** collapse to one named, documented top-share drift parameter; backtest against DOTAX TY2023/TY2024 bracket tables when published (TY2023 should be available now). This is the single highest-leverage methodological validation available.

### 6. forward_targets growth constants are point guesses with no provenance refresh
`forward_targets.py:290-293`: 2.5%/yr low-bracket growth, 0.5%/yr population, 0.5%/yr rate drift, 2.5%/yr COR extrapolation beyond 2031. The 0.5% pop figure cites DBEDT but is hardcoded; no sensitivity is run on any of them in production scripts (sensitivity script only sweeps Pareto α / REEC).
**Fix:** source pop growth from a DBEDT series file; add these dials to the sensitivity sweep. Cheap.

### 7. F6: no demographic aging of weights
Population enters only as a uniform 0.5%/yr scalar on filer counts. Hawaii's age structure is shifting old fast — retirement-income share up (untaxed pension exclusion in HI!), wage share down. By 2031 this is a first-order composition effect on both revenue and distribution that uniform scaling cannot capture.
**Fix (incremental):** age-band the Phase 1 rake margins using DBEDT age-cohort projections. Bigger lift, flag for v3.

### 8. TY/FY conflation
COR publishes fiscal years (Jul–Jun); `forward_targets.py:50-58` maps FY→TY by label comment only; output tables print TY labels described as "fiscal-note style." A legislative reader will be off by one year. No TY-receipts→FY-collections timing model exists (withholding vs estimated vs final payments).
**Fix:** pick and document one convention; relabel outputs. Timing model optional later.

---

## P2 — Uncertainty: computed, then discarded

### 9. F9: the production chain is point-only — but the SDR fix changed the calculus
- `tax_unit_projector.py:466-501` computes `income_ci90_*` columns → zero consumers.
- `revenue_projection.py:316-405` has a real delta-method SE path (`revenue_se = |∂tax/∂income| × income_se`) → not called by any sb3125 script.
- The only uncertainty users see is LOW/MID/HIGH/RECESSION scenarios — parameter scenarios, not statistical intervals.
- `project_and_recalibrate` itself: zero variance propagation (grep confirms no SE/CI anywhere in the file).

**The opportunity:** A1/B1 fixes mean the 80 SDR replicate weights now propagate correctly through unit construction. Running scenario scoring per replicate weight (vectorized — `per_unit_tax` is per-unit, only the weighted sums change) gives **sampling CIs on reform deltas almost for free**. Combine with the existing scenario grid for parameter uncertainty and you have a defensible two-component uncertainty statement. This is the standout improvement of the whole assessment: high credibility gain, low cost, builds directly on work just landed.
Caveat: Phase 1 raking should be re-run per replicate (rake-per-replicate, already noted as deferred in the SDR work) — first-order shortcut (rake once, score replicates) is acceptable and labeled as such.

### 10. Statistical engine (census_forecaster) — secondary for revenue
Production revenue runs use `use_cbo_aging=True`, so these only bind the county-scalar/ensemble path and ACS indicator forecasts:
- κ variance calibration fits and evaluates on the same anchors (`calibration.py:303-348`); honest 3-way conformal split exists but `include_conformal=False` default (`:1025`). **Flip the default.**
- Overlapping 5-yr ACS samples treated iid, patched by a blunt global `EMPIRICAL_SE_INFLATOR=1.30` (`projection.py:78-89`) — calibrated, but not indicator- or horizon-specific.
- `h = max(1, int(round(h_years)))` collapses fractional horizons (`projection.py:239`).
- Ensemble correlation ρ=0.7 / macro-anchor ρ=0.5 assumed, not estimated (`ensemble.py:216-234, 1128-1133`).
- Kalman Q hardcoded (`kalman/filter.py:29-31`).
These are real but second-order for revenue. Worth fixing when the engine's CIs start feeding the revenue uncertainty story (item 9).

---

## P3 — Robustness / hygiene

11. **Silent degradation everywhere:** AGI-target build failure → warn + proceed without cohort correction (`forward_targets.py:382-396`); Phase 1 non-convergence → warn + return partial (`simultaneous_calibrator.py:277-280`); tier rake clamped [0.5,2.0] and Phase 2 clamped [0.3,5.0] with log-only notice. **Fix:** return a structured `CalibrationReport` (converged?, residuals per margin, clip events, statute-vs-COR wedge) and print it in every script's output. One object kills the whole class.
12. **F8:** `forecast_combined_reform.py` applies TY2027 law to un-aged fixture incomes — fine as a demo, but nothing marks it illustrative. Label it or wire it to `project_and_recalibrate`.
13. **COR vintage hardcoded** (`forward_targets.py:50-58`): move to a data file with vintage metadata; warn when more than one COR cycle stale. SOI Table 1.4 (TY2022) and DOTAX A8 same treatment.

---

## Recommended sequencing

**Week 1 (quick wins, all low-risk):**
1. C3 fix across 4 scripts + params-in-pickle (P0.1)
2. Statute-vs-COR wedge reported in outputs (P0.2 short form)
3. Cache versioning (P0.3)
4. EITC/CTC CPI indexing past 2025 (P1.4)
5. TY/FY relabel (P1.8)

**Sprint 2 (the value-add):**
6. Replicate-weight CIs on reform deltas (P2.9) — headline improvement
7. Top-growth dial consolidation + DOTAX TY2023 backtest (P1.5)
8. `CalibrationReport` object (P3.11)

**Later / v3:**
9. Weight-side COR anchoring (P0.2 long form), demographic aging (P1.7), engine fixes incl. conformal default flip (P2.10).
