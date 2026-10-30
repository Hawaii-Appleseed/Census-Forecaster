# Prediction Methodology Assessment — ACS / statistical projections
**Date:** 2026-08-21 · **Scope:** the statistical prediction engine (`census_forecaster` ACS + BLS projection, ensemble, calibration) — the counterpart to the revenue-chain assessment of 2026-06-11. All claims verified against current main (`1ff996e`), the shipped calibration (`data/anchors/calibration.json`, schema v5, run 2026-08-06), and the ablation reports in `backtests/results/`.

> **Status update (same day):** Batch 1 landed — A1 (`use_ml` default ON after the
> ship-gate ablation passed 3/3 rules and the ρ sweep put 16/16 indicators in band
> at ρ=0.7; `backtests/results/ml_default_ablation_2026-08-21.md`), A2
> (conformal-primary intervals replace the max(κ, conformal) floor: held-out 92.7%
> coverage at 20.7% mean half-width vs 94.3%/24.0%; two evaluation-pass bugs fixed
> en route), A3 (`project_acs_ensemble` re-aliased to the calibrated
> `project_ensemble_multi`), B4 (per-cell φ gated behind `phi_enabled`, off by
> default; Pass 2 threads φ when enabled so a future v4 re-run is unconfounded).
> Bundled calibration regenerated (`phi_enabled: false`, honest
> `evaluation_coverage` — which now exposes B25077/multi_anchor at 77% on the 2022
> eval anchor, previously masked). B5 also landed the same day: the Kalman
> ablation was re-run (wholesale verdict still HOLD), and the two indicators that
> pass both per-indicator gates — pct_service_occupations (−15.0% RMSE, 90.8%
> cov) and B25071 (−10.6%, 93.8%) — are promoted via
> `ensemble.KALMAN_PROMOTED_INDICATORS` (`use_kalman=None` → auto). Full suite
> 2,013 passed / 3 skipped. Act 24 numbers unmoved (see SB3125_CD1_FORECAST.md
> §ACS engine changes).
>
> **Batch 2 (same day, evidence-gated — see METHODOLOGY §Batch-2 diagnostics and
> §S2301 null):** B1 S2301 mean-reversion — built and evaluated, **NULL** (59%
> RMSE vs incumbents' 33–36%; the ACS print's county idiosyncrasy dominates;
> UI-claims nowcast spun off as its own task). B6 recency-weighted bias —
> **NULL** on the 2022-eval A/B (unweighted generalizes best); mechanism ships
> off by default. B2 log-space intervals — **NULL with inverted diagnosis**:
> the tail imbalance is residual +0.8% eval-fold over-projection, not interval
> shape; log-space would worsen the dominant lower-tail misses. C3 QCEW→B20002
> anchor — **PASS, shipped** (blend RMSE 10.47%→9.12%). C3's B25071
> ratio-anchor idea retired as moot (B25071 is Kalman-promoted, bypassing the
> anchor blend). Still open: blend-level κ/conformal stratum, B25077 anchor
> miscalibration, residual eval-fold bias, 5-year ACS, PEP intake (Batch 3).

**Headline:** the engine's calibration discipline is genuinely strong — stratified κ/bias, split-conformal, publication-lag-honest backtests — but three kinds of headroom exist: (A) **validated improvements already built and benchmarked that never shipped** (the ML member, the conformal sharpness fix), (B) **structural model gaps** the calibration layer papers over with large κ multipliers (unemployment mean-reversion, symmetric-Gaussian intervals on bounded rates, unweighted fits on noisy small-county series), and (C) **untapped data** (ACS 5-year, Census PEP, anchors for 11 of 16 indicators).

Where the current defaults stand (v3 MAPE / CI90, 440 folds per indicator, eval 2021–22, h≤3, from `phi_ablation.md`): income 4.4%/91%, rents 5.4–5.5%/84–86%, home value 6.8%/84%, education 1.2–5.5%/94–95%, poverty 10.6%/91%, **unemployment 40.5%/98.8%**, vacancy 18.0%, in-migration 15.1%.

---

## A. Shipped-but-unused gains (highest leverage, lowest science risk)

### A1. Turn on `ml_trend` (per-indicator, gated) — the single largest available accuracy gain
`use_ml=False` is the default (`acs/ensemble.py:818`) even though:
- The walk-forward ablation (METHODOLOGY.md §Cross-county ML trend) shows **RMSE improvements on all 16 indicators, −5% to −25%**, with production-equivalent calibration applied.
- Stand-alone `ml_trend` is the *only* member with pre-κ CI90 coverage in [85, 95] on all 16 indicators (`phase_c_bps_ablation.md`: 88.4–93.5%).
- Its κ/bias/conformal strata records are already computed and shipped in `calibration.json` — the default path just never consults them.

The documented blockers were two "mechanical follow-ups" (METHODOLOGY.md): an ensemble-level κ stratum for the 3-member blend, and a ρ sweep to keep joint coverage in band. Neither was ever run. Meanwhile the HOLD verdict in `phase_c_bps_ablation.md` rests on a **degenerate comparison table** (the with/without rows are empty; "Rule 1 FAIL 0/0 = 0.0%") — the gate failed on a reporting bug, not on evidence.

**Recommendation:** re-run `compare_ml_ablation` with the comparison table fixed; add the `ensemble_with_ml` κ stratum (one more Pass-2 label in `calibration.py` — the machinery is generic); sweep ρ_inner ∈ {0.5…0.9}. If coverage lands in band (the April ablation says it lands 85–90%), flip the default per-indicator, keeping the classical ensemble where the ML gain is <5%. Effort: ~1–2 days, all in existing harnesses.

### A2. Fix interval sharpness — the shipped default now *over*-covers
The v5 payload's held-out `evaluation_coverage` (conformal floor applied) runs **86.9–100%, median ≈96.6%** against a 90% target. Cause: `_apply_conformal_floor` (`ensemble.py:625-700`) takes `max(κ_half, q·se_pre_κ)` — the max of two *independently calibrated* 90% half-widths, which is systematically wider than either. κ was bisected to hit 90% on its own; flooring it with a second 90% quantile can only add width.
**Recommendation:** calibrate the two jointly — either bisect κ *after* imposing the conformal floor, or use conformal as the primary width and κ only as a small-n fallback. This is pure sharpness: same coverage target, narrower bands. Also revisit `multi_anchor`'s uniform κ=0.715 — its raw SE grows with `h` not `√h` (`anchors.py:430-529`, deliberate), so one multiplicative κ per h-bucket corrects the level but not the growth shape; a per-h κ (the strata already support it) would stop the 98–99% raw over-coverage at its source.

> **Note (2026-09-29):** production did not follow that h-growth. `project_ensemble_multi` divided the calibration RMSE by the target `h`, cancelling the ×h, so its pre-κ SE was flat in `h` while κ was fit on the h-proportional fold SE. Fixed with the shared `ANCHOR_CALIBRATION_HORIZON` (METHODOLOGY.md §4, *Anchor SE convention*); any per-h κ follow-up should be judged on post-fix coverage.

### A3. Point the public API at the calibrated path
`census_forecaster.__init__:47` aliases **`project_acs_ensemble` → the legacy `project_ensemble`** — fixed 0.30 macro weight, no bias correction, no κ strata, no conformal, and `se_forecast=0` on the anchor unless the caller passes `rate_se_log`. Both CLAUDE.md files call this "the primary public API," and Cost-of-Living-Tracker consumes the package as a pinned dependency. Anyone importing the advertised name gets pre-v3 uncertainty.
**Recommendation:** re-alias to `project_ensemble_multi` (or a thin adapter), deprecation-warn the legacy path. Half a day including tests and the downstream-pin note.

---

## B. Structural model gaps (where κ is compensating for misspecification)

### B1. Unemployment (S2301): wrong model class, known since April, never replaced
MAPE 40.5% — 4–8× every other indicator — and κ hits the 2.60 ceiling. METHODOLOGY.md already names the fix ("a dedicated mean-reversion model — AR(1) toward a long-run mean — would be appropriate") and the anchor registry documents why the trend machinery can't be patched (`sources/base.py:391-416`: the rejected CPS rate anchor blew the band invariant). Three assets exist and are unconnected:
1. The **LAUS level anchor** (county-truth, `level_se_floor=1.5pp`) — already the best member but applied as a static level with no reversion dynamics.
2. **`natl_unemp_lvl/chg1/chg2`** ML features — permutation importance +0.243, the strongest feature for S2301, but only reachable via the opt-in ML member.
3. **`HI_UI_CLAIMS`** — the strongest predictive relationship the causal screen has ever found (p≈3e-64, r=+0.82, genuine 1-month lead, 2020-robust) — screened, bundled, and **never promoted to any feature registry** (documented as intentional in METHODOLOGY.md, but the promotion + ablation was never attempted).
**Recommendation:** a dedicated S2301 model: AR(1) partial adjustment toward a LAUS-anchored level, with the national-change and (ablation-gated) UI-claims channels as regressors. Score it against the level-anchor baseline in the existing fold harness. This is the largest single-indicator error in the system and the fix is fully specified in the repo's own docs. Same treatment generalizes to the other mean-reverting/bounded series (vacancy 18%, in_migration 15%).

### B2. Interval shape: symmetric Gaussian in *level* space on models fit in *log* space
`ci_from_se` (`common/moe.py:162`) emits `point ± 1.645·se` in dollars/percentage-points for every method, though the trend, AR(1), anchor, and ML models all operate on log growth. For dollar series this loses the right-skew of multiplicative growth; for bounded rates (unemployment, vacancy, poverty, homeownership) it permits negative or >100% bounds and is part of why κ must stretch to 2.6.
**Recommendation:** form intervals in the model's native space — `point·exp(±z·σ_log)` for dollar series, logit-space for shares — then re-bisect κ. Mechanical; the BLS module already does exactly this (`CI = point·exp(±z·κ·SE_log)`), so it's an internal-consistency fix as much as a statistical one.

### B3. The trend fit ignores measurement error it already knows about
Published MOEs enter only (a) additively as `se_sample` at the end and (b) as the φ-selection input. The fit itself — `fit_damped_trend`, `fit_ar1_log_diff` — weights a CV=0.02 print and a CV=0.35 print identically, so one noisy small-county print steers the level/trend states. This is the textbook small-area-estimation setting (Fay–Herriot: known heteroskedastic sampling variances + a linking model), and the repo already computes per-observation observation variance in `kalman/project.py:47-52` (`_obs_r`) — it's just only used by a member that's off by default.
**Recommendation:** weight the smoothing/AR fits by `1/SE_log²` (or equivalently, promote the Kalman member for noisy cells — see B5). Biggest effect exactly where accuracy is worst: the `small` pop bucket, which also has **no φ strata records at all** in the shipped calibration.

### B4. v4 φ ablation is confounded — fix before concluding φ is a dead end
Production applies per-cell φ (`ensemble.py:940`), but the calibration generator's fold projections use `DEFAULT_PHI=0.85` (`calibration.py:110-123`). Every κ, bias, RMSE, and conformal quantile was therefore fit under a different φ than production runs, and the null φ result (`phi_ablation.md`, +0.9% MAPE) partially measures that mismatch. Cheap fix (thread φ into `_project_trend_only`), and it removes a latent bias affecting all trend-cell calibration records, not just φ.

### B5. Kalman: promote the two indicators that already pass, with MOE-driven R
`phase_d_kalman_ablation.md`'s own next-steps section identifies `pct_service_occupations` (−15% RMSE, 90.8% cov) and `B25071` rent burden (−10.6%, 93.8%) as allowlist candidates, plus per-indicator Q tuning. Combined with B3 (per-observation R from MOE instead of a global default) the Kalman member becomes the principled home for measurement-error weighting rather than a fourth competing trend model.

### B6. Bias correction is a frozen COVID-era estimate
The −7…−9% dollar-series bias (and S1701's +14% clamped cell) is estimated on 2014–2022 anchors and dominated by the 2020–22 inflation-surprise folds. Applied to 2026+ forecasts it assumes that regime persists; as post-2023 anchor years enter the panel the correction should shrink, but nothing downweights stale folds.
**Recommendation:** recency-weight the bias estimate (the repo's own `0.5^(years_back/τ)` convention) or re-estimate on a rolling window, and track `b_raw` drift release-over-release in the refresh workflow. Low effort; prevents yesterday's inflation shock from becoming tomorrow's systematic over-correction.

### B7. Assumed correlations, unexamined since v1
ρ_inner=0.7, ρ_anchor=0.5, cross-anchor ρ=0.6, level-fusion ρ=0.5 are all literals (`ensemble.py:228,317,1168`; `anchors.py:315`). The fold cache contains per-fold residuals for every member — the empirical residual correlations are one groupby away. Estimating them (or at least sweeping, as the June assessment already recommended) directly serves A1's coverage question. Related: `_calibrated_macro_weight` reads only the globally-marginalised RMSE table — the trend↔anchor blend weight is the one v3 quantity not stratified by pop/horizon.

---

## C. Untapped data (the "ACS projections and other stat projections" question)

### C1. ACS 5-year — supported everywhere, used nowhere
`AcsClient` fetches it, `effective_year` places it at `year−2`, `GeographySeries.five_year()` exists — and the calibration panel is 100% 1-year, every truth lookup filters `vintage=="1y"`, and the ML panel explicitly excludes 5y. For small counties (where 1-year data is noisiest or, under-65K population, *doesn't exist*) this is the largest untapped data source in the system. Two honest routes:
- **Fusion:** treat 5y as a second, low-variance, lagged observation of the latent level — natural in the Kalman member (one more observation row with its own R), incoherent in the smoothing fits.
- **Coverage extension:** forecast 5y-only counties the engine currently can't serve at all.
Either way the overlapping-sample autocorrelation (adjacent 5y windows share 4/5 of their sample) must be modeled, not iid-assumed — this is the same defect the June assessment flagged as "patched by a blunt global 1.30 inflator."

### C2. Census PEP + demographic components — absent entirely
No Population Estimates Program, no vintage estimates, no births/deaths/migration components anywhere in the projection path; population is a frozen 2020 bucket label and a static ML feature. PEP county totals and components of change are annual, timely (~6-month lag), administrative-quality, and free. Concrete uses, in ascending ambition:
1. **Level anchor for B01002 median age** (currently anchor-less, trend-only) via PEP age structure.
2. **PEP components (net migration, natural change) as ML features** — the in_migration_rate target (15% MAPE) currently has no administrative signal at all.
3. **Denominator discipline for h=4–5**: rate indicators drift with age structure in ways trend extrapolation can't see; DBEDT's county population projections (already the repo's cited source for the revenue chain's 0.5%/yr scalar) would give the ACS engine the same demographic forcing the June assessment asked for on the tax side (P1.7).
Given Hawaii's fast-aging structure this is the main *economically-motivated* gap, distinct from the statistical ones.

### C3. Anchor coverage: 11 of 16 indicators have none
Only income, rents, home value, poverty, unemployment are anchored. Cheap additions with existing fetch infrastructure:
- **B20002 worker earnings ← QCEW average weekly wage** (the file is already bundled for B19013; earnings is arguably the *better* conceptual match for payroll data).
- **B25071 rent burden ← ratio of anchored forecasts** (gross rent ÷ income — both sides already anchored; the derived indicator machinery exists in the panel builder).
- **S1501 education, B01002 age**: slow-moving, well-covered by trend — low priority, correctly so.

### C4. Promote screened nowcast signals into features
The causal-screen pipeline has produced a disciplined shortlist (BH-FDR + 2020-robustness + sign check) that mostly *isn't wired to anything*: UI claims (B1 above), intl arrivals, price-cut share → SF median. The promotion path (registry entry + ablation gate) is documented and cheap; the screen has done the hard part. Without promotion the screen is a research program with no consumer.

---

## Not recommended (assessed and declined)

- **Re-tuning HGB hyperparameters per indicator** — the code's own comment (`ml_trend.py:84-85`) is right: the sample can't support it.
- **GARCH-class volatility for ACS** — the BLS/markets bake-off already showed monthly cadence is too thin; annual is hopeless.
- **Re-adding national unemployment as an anchor** — rejected on solid evidence (`sources/base.py:391-416`); the feature path is the right home.
- **Deeper φ search as-is** — null result is credible for h≤3 *after* B4's confound is fixed; only revisit if h=4–5 becomes a primary use case.
- **Finer strata (3-bucket horizons, octile populations)** — n_folds ≥ 20 gating already binds; more cells = more fallback, not more signal.

---

## Sequencing

**Batch 1 — unlock what's already validated (≈1 week):**
A3 (API alias), B4 (φ confound fix — invalidates and regenerates calibration, do it before anything else that re-runs calibration), A1 (ML default-on with ensemble κ stratum + ρ sweep), A2 (conformal/κ joint calibration).

**Batch 2 — the misspecification fixes (the real science, ~2–3 weeks):**
B1 (S2301 mean-reversion model + UI-claims promotion C4), B2 (log/logit-space intervals + κ re-bisection), B6 (recency-weighted bias), C3 (QCEW→B20002, ratio→B25071).

**Batch 3 — new data (scoped separately):**
C1 (5-year ACS via Kalman fusion — pairs naturally with B3/B5), C2 (PEP intake: anchor for B01002 first, components-as-features second).

Every item above is gateable by the existing fold-cache/ablation harnesses; none requires new evaluation infrastructure. The standing ship discipline (RMSE gate + coverage ∈ [85,95] + no-regression) applies throughout.
