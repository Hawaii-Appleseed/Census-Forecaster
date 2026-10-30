# Census-Forecaster Pipeline Audit — 2026-06-10 (branch `v2`)

Scope: tax-unit/household construction + forecasting engine. Findings are tagged
`[VERIFIED]` (I read the code/call-sites and confirmed) or `[REPORTED]` (sub-agent
finding, plausible but not independently re-confirmed line-by-line). Severity reflects
**live blast radius** — several real-looking bugs are in dead/uncalled code and are
flagged as *latent*.

---

## A. Tax-unit & household construction

### HIGH

**A1. MFJ tax units get NO replicate weights → all SDR standard errors biased. `[VERIFIED]`**
`units/constructor.py:1396` (active `_create_joint_filer`) returns without calling
`_compute_replicate_weights`, while `_create_single_filer` (1553→~1696) *does* populate
`weight_r01..weight_r80`. There are two `_create_joint_filer` defs (1183 and 1396); Python
keeps the **second**, which is the one missing the replicate call (the first, 1183, has it).
Consequence: every married-filing-jointly unit lacks replicate weights, so any
successive-difference (SDR) variance — poverty SE, EITC take-up SE — is computed over an
inconsistent population (point weight present, replicates absent/zero for MFJ).
**Fix:** delete the dead duplicate at 1183; add `self._compute_replicate_weights(...)` to the
surviving `_create_joint_filer` (mirror `_create_single_filer`). Add a test asserting every
emitted tax unit has the full `weight_r*` set, and a class-level lint against duplicate method
names.

**A2. `assign_county` mutates the global NumPy RNG and draws once after seeding. `[VERIFIED]`**
`analysis/puma_imputation.py:174-177`: `np.random.seed(seed); ... np.random.random()` per row.
Two problems: (1) reseeding the **global** RNG inside the imputation corrupts determinism of any
other stochastic code in the process; (2) seed-then-draw-once is biased across nearby seeds, so
the Maui/Kauai split for PUMA 0100 won't match the intended 68.9/31.1 target — and the validation
log just reports whatever came out, masking it.
**Fix:** `rng = np.random.default_rng(seed); r = rng.random()`. Never touch the global seed. Add a
test that the empirical split over all PUMA-0100 units matches `base_allocation` within tolerance.

**A3. Biproportional raking: misleading convergence, no weight cap, silent zero-mass cells. `[REPORTED]`**
`analysis/district_raking.py:98-132`. Drift is measured on the last-applied scaling factors
(Step B perturbs the HD constraint after Step A, then exits without re-checking), so it can report
"converged" while HD marginals are still off. No cap on the multiplicative factor → sparse cells
get huge weights (weight explosion, n_eff collapse). `target>0 and current==0` only warns and leaves
the target unmet (silent under-coverage).
**Fix:** measure drift from actual marginal residuals after a full A+B cycle; clip per-iteration
factors (e.g. [0.1, 10]) or cap final/mean weight ratio; escalate unmet zero-mass cells; assert
state-total preservation post-rake. (Mirrors the well-built guards already in
`ipf_orchestrator.calibrate_via_rake` — port them here.)

### MED

**A4. Three inconsistent ADJINC conventions; one duplicate method is a 1.18-million× landmine. `[VERIFIED, latent]`**
`units/constructor.py` has duplicate `_calculate_income` (1155 correct integer-ADJINC handling;
**1271 active** treats raw PUMS ADJINC ~1184371 as a ~1.18 factor → multiplies income by ~1.18M).
Currently **dead** — the hot path uses `income.calculate_tax_unit_income` (correct, divides by 1e6),
and no live `self._calculate_income` caller exists. But the shadowing means any future caller silently
gets the broken version. Separately, `status/mfs.py` only applies ADJINC when `0<adjinc<2`, i.e. it
does **not** adjust real PUMS data, while `dependencies.py` does — so MFS income thresholds ($150K floor)
and QR gross-income tests ($4,400) run on different dollar bases.
**Fix:** one shared `person_income(person)` helper, one ADJINC convention; delete the per-module copies
and the dead duplicate.

**A5. Filing-status calibration factors baked into base weights (HoH ×1.88). `[VERIFIED]`**
`units/constructor.py:1329` (`_calculate_hybrid_weight`): single ×0.85, **HoH ×1.88**, MFS ×1.05,
applied during construction *before* the explicit IPF rake — and applied even when
`use_soi_calibration=False`. This breaks weight-sum preservation (`sum(weight) != sum(WGTP)`) and
double-calibrates against the downstream rake.
**Fix:** gate behind `use_soi_calibration`, or remove and let the IPF step own all calibration.
Assert/log construction weight-sum vs PUMS household-weight total.

**A6. `_can_claim_dependent` uses the pre-2019 RELSHIPP codes. `[REPORTED]`**
`units/constructor.py:~1500`. Maps 22=child…25=grandchild (old scheme), but the rest of the codebase
uses canonical 2019+ RELSHIPP (25/26/27=own child, 30=grandchild, 21/23=spouse). So the unclaimed-
dependent fallback can "claim" a spouse and reject actual children, shifting credit dollars.
**Fix:** use the canonical sets from `relshipp_codes` (`OWN_CHILD`, `GRANDCHILD`, `QUALIFYING_RELATIVE_RELS`).

**A7. `identify_dependents` called without `tax_year` (frozen QR limit). `[REPORTED]`**
`units/constructor.py:604` → defaults to TY2023 QR gross-income limit regardless of run year.
**Fix:** thread the run's `tax_year` through `_process_household → identify_dependents`.

**A8. Phase-2 "relaxed" couple pairing can marry unrelated adults. `[REPORTED]`**
`units/constructor.py:~897-940`. Pairs any two remaining married opposite-sex adults within 20 years
of age, with no SERIALNO/RELSHIPP linkage — can merge two unrelated tax units (e.g. married adult child
+ married lodger) and their incomes into one MFJ return.
**Fix:** require pairing evidence (spouse-present `MAR` pattern, parent/spouse relationship) or drop
Phase 2 and rely on the strict `_are_married` matcher.

### LOW
- **A9** `analysis/geographic.py`: parallel geo path defaults `weight_col='PWGTP'`, silently falls back to
  `weights=1` when absent, `_weighted_median` ignores weights, and ships a toy 5-row crosswalk with wrong
  county assignments. Default to `weight`, warn on missing, implement a real weighted median, delete/mark
  the toy crosswalk. `[REPORTED]`
- **A10** `analysis/district_imputation.py`: `min_similarity=0.7` threshold rarely met → most districts hit a
  silent `confidence=0` fallback with empty SOI. Calibrate the transform; surface fallback as a warning. `[REPORTED]`

---

## B. Forecasting engine (ACS + BLS + Kalman)

### HIGH

**B1. Overlapping ACS 5-year samples treated as independent observations. `[REPORTED]`**
`acs/projection.py:300-307` (`fit_damped_trend`), `:507-521` (`fit_ar1_log_diff`) pool all vintages as
iid points. Adjacent 5yr vintages share 4/5 of their sample (ρ≈0.8). Effects: residual variance biased
low (intervals too tight), AR(1) ρ biased toward +1 (MA artifact), effective-n overstated in the
`n/(n-2)` small-sample correction. The global `EMPIRICAL_SE_INFLATOR=1.30` papers over this but can't fix
the per-series structure — and the back-test that justified 1.30 scores only against 1yr truth, so it may
not exercise the 5yr-overlap regime production actually uses.
**Fix:** don't mix vintages as iid. Cheapest: fit on 1yr when available, use 5yr only as a level anchor.
Better: deflate effective-n by the overlap factor (`n_eff ≈ n_1y + n_5y/5`). Best: GLS with a banded
covariance (vintages k years apart share `max(0,5-k)/5`).

**B2. Interval calibration fits κ and evaluates coverage on the same folds (in-sample leakage). `[REPORTED]`**
`acs/calibration.py:386-485` (v2) and `:1446-1462` (v3): the SE override κ is bisected to bring fold
coverage to ~90%, then coverage is re-scored on the **same** folds; bias correction
`b=mean(log(point/actual))` is likewise fit and applied on the same data. In small strata (floor n=20,
coverage SE ~6.7%) this fits noise and reports optimistic coverage. The split-conformal path (schema 5)
is the correct 3-way-split design but is **opt-in** (`include_conformal=False`).
**Fix:** make the honest tuning/calibration/evaluation split the default; report in-sample κ/coverage as
diagnostics only. Until then, treat shipped `ci90_coverage_post_override` as optimistic, especially in
low-n strata.

**B3. `forecast_se_log` collapses fractional/short horizons to one-step variance. `[REPORTED]`**
`acs/projection.py:237-261`: `h = max(1, int(round(h_years)))`; for `h_years ∈ (0,1.5)` the variance
factor is 1.0, so the interval does **not** widen with extrapolation distance — even though the point
forecast uses the true fractional `h_years` (the dominant 5yr-pivot case projects ~1.5yr point with a
1-step SE). Rounding also makes 2.4→2 vs 2.6→3.
**Fix:** make the ETS variance formula accept fractional h (interpolate the `c_j` sum), or at minimum use
`math.ceil` so the interval never shrinks relative to the point's extrapolation distance.

### MED
- **B4** Macro-anchor Bates-Granger weight uses the ρ=0 formula
  (`RMSE_t²/(RMSE_t²+RMSE_a²)`, `acs/ensemble.py:730`) while the blend variance assumes ρ=0.5 — the two
  forecasts are correlated (both pivot on `latest.estimate`), so the weight is suboptimal (not wrong).
  Use the correlation-aware optimal weight. `[REPORTED]`
- **B5** Kalman process-noise/init variances hardcoded, not calibrated (`q_growth=4e-4`, `q_level=1e-6`,
  obs-R fallback 0.01); growth state collapses to over-confidence and can't track regime changes
  (COVID rent spike). Opt-in path, so MED. Calibrate Q or document. `[REPORTED]`
- **B6** AR(1) RW-on-diffs variance is I(2)-explosive; its huge SE feeds inverse-variance weighting
  (`acs/ensemble.py:65-70`), which silently zeros the noisy AR(1) member instead of widening the combined
  interval — combined with B1's high-ρ bias the ensemble can quietly become "damped trend only."
  Floor/clip the member variance or down-weight without dropping. `[REPORTED]`
- **B7** Kalman discards all non-1yr observations (`kalman/project.py:82-85`) → returns `None` for small
  counties with sparse 1yr data, i.e. unavailable exactly where the stratification needs it. Document the
  coverage gap. `[REPORTED]`

### Verified-correct (no action) — forecasting
MOE→SE divisor `Z=1.645` is correct (`moe.py:32`); damped-trend point + ETS(A,Ad,N) variance coefficients
match Hyndman 2008; hidden-data discipline in calibration (`_truncate` on `effective_year`/`publication_date`,
truth = 1yr print at target); Joseph-form Kalman update keeps covariance PSD; split-conformal quantile uses
the correct finite-sample `⌈(n+1)(1-α)⌉` form; annual-rate cap applied consistently in log space; anchor rate
uncertainty now propagated (`h·se_log_rate`). `[REPORTED]`

---

## C. Cross-cutting (raking / weighting / metrics)

### HIGH
**C1. Canonical `rake()` silently returns un-converged weights. `[REPORTED]`**
`pums_estimator/estimation/rake.py:68-102`: no convergence flag, no log, no exception on hitting
`max_iter`. It's the engine `ipf_orchestrator.calibrate_via_rake` delegates count margins to.
**Fix:** return/raise/log a convergence status; `logger.warning` on `max_delta >= tol` at loop exit.

**C2. `poverty_by_geography` returns a UNIT-weighted poverty rate, not person-weighted. `[VERIFIED live]`**
`metrics/geographic.py:85` uses `weight_col='weight'` (filer/unit weight); the canonical SPM path
(`poverty/impact.py:339-346`) uses `person_weight = weight * persons_per_unit` for rates. Live callers:
`validation/phase9_validation.py:107` and `forecast_combined_reform.py:162` (both use the default).
Larger households (more people, often higher child-poverty exposure) are undercounted; the ~12.03%
person-rate benchmark can't be reproduced through this function.
**Fix:** add a `persons_per_unit`/`person_weight_col` arg; compute the rate on
`weight * persons_per_unit`, keep dollar gaps on the unit weight (mirror `impact._aggregate`).

**C3. EITC reweight mutates only `weight`, not `WGTP1..80` → breaks SDR. `[VERIFIED, latent]`**
`calibration/eitc_reweight.py:150`. After reweighting, the point estimate uses up-weighted EITC units
while every replicate uses raw weights → SDR variance compares mismatched populations. **No production
caller** today (only re-exported in `calibration/__init__.py`), so latent — but it will silently corrupt
any EITC SE/CI the moment it's wired in.
**Fix:** apply the per-bucket factor to all 81 weight columns (calibrate each replicate), or explicitly
bar SDR on EITC-by-children outputs and document `weight` as point-only.

### MED
- **C4** No marginal-consistency assertion: `rake()` only converges if all margins share a grand total;
  `DOTAX_FILER_TARGETS` vs `DOTAX_FILING_STATUS_TARGETS` equality (618,423) is hand-maintained with no
  runtime check. Assert before raking. `[REPORTED]`
- **C5** No weight cap in canonical `rake()` (`rake.py:92-94`) — the newer `_to_weighted_sum` path clips,
  the canonical one doesn't. Add a per-cell scale cap / final weight-ratio trim with a logged count. `[REPORTED]`
- **C6** `district_ctc.py:274-278` quintiles via unweighted `pd.qcut` (every other quantile path is
  weighted); duplicate-edge errors are swallowed by a broad `except`. Use `weighted_ntile_labels`. `[REPORTED]`
- **C7** `metrics/geographic.py:78` `fillna(0)` on resources pulls missing-data units into poverty
  (NaN→$0 < threshold). Drop NaN rows instead of zero-filling. `[REPORTED]`
- **C8** Liability `estimate_num_exemptions` checks `filing_status == 'joint'` while the canonical pipeline
  value is `'married_filing_jointly'` (`deductions/calculator.py:420`). **No callers** → latent; would drop
  the MFJ spouse exemption if called on un-normalized frames. Accept both vocabularies. `[VERIFIED latent]`
- **C9** Hawaii `_apply_schedule` returns wrong tax at *exact* bracket-floor dollars (falls through to the
  top-bracket fallback; verified e.g. taxable=4800→110.80 vs 110.00). Measure-zero for continuous incomes,
  but plainly incorrect and inconsistent with the two correct bracket walkers. Use `lo < taxable <= hi`.
  `[REPORTED]`

### Verified-correct (no action) — metrics/liability
SPM resource signs (credits/transfers added, taxes/MOOP/expenses subtracted; Medicaid excluded; no
double-count); threshold housing-share geo-adjustment; reform application runs baseline+scenario on the same
immutable frame (no shared-state mutation, no double-application); federal bracket math + SD/bracket values
(TY2022-2025) match IRS Rev. Procs; CG cap (§235-16) incremental-stacking correct; `strata.py` and `sdr.py`
are statistically sound. `[REPORTED]`

---

## Recommended fix order (highest live-impact first)
1. **A1** MFJ replicate weights (corrupts every SDR SE) — `[VERIFIED]`
2. **C2** person-weighted poverty rate in `poverty_by_geography` (live, breaks headline rate) — `[VERIFIED]`
3. **B2** make honest 3-way calibration split the default (overstated coverage everywhere) — `[REPORTED]`
4. **B1** ACS overlap correlation (the structural under-coverage the inflator only masks) — `[REPORTED]`
5. **C1 / A3** raking silent non-convergence + weight explosion — `[REPORTED]`
6. **B3** fractional-horizon interval collapse — `[REPORTED]`
7. **A2** global-seed county imputation — `[VERIFIED]`
8. **A5 / A4** weight-factor double-calibration + ADJINC unification — `[VERIFIED]`
9. Latent landmines to neutralize before they're wired in: **C3** (EITC reweight SDR), **C8** (exemption
   string), **A4** dead `_calculate_income` duplicate.
