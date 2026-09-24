# Methodology

This document records the non-obvious design choices in Census Forecaster
that aren't fully captured by source-code docstrings. It supplements
(not replaces) the README and module docstrings.

---

## v3 stratified calibration (April 2026)

The v3 calibration layer extends v2 along three axes: population
stratification, multi-horizon evaluation, and geometric bias correction.
The motivation for each, the parameter choices, and the fallback
behavior are below.

### 1. Population stratification

**Why stratify by population at all?** Counties differ enormously in
ACS sample sizes — Honolulu (1M people) gets thousands of survey
responses per year; rural counties under 50K get a few hundred. The
sampling-error component of forecast SE scales with the inverse square
root of sample size, so a single global SE inflator κ either overcalibrates
the noisy small-county tail or undercalibrates the precise large-county
head. Stratifying lets each population bucket receive its own κ.

**Bucket boundaries** are based on 2020 ACS B01003_001E (5-year vintage):

| Bucket | Range | Approximate count (US counties) |
|---|---|---|
| `small`  | < 50K            | ~1,800 |
| `medium` | 50K–200K          | ~700 |
| `large`  | 200K–1M           | ~280 |
| `xlarge` | ≥ 1M              | ~50 |

Boundaries are coarse (4 levels) to keep cells statistically reliable on
the ~150-county v3 calibration panel. Finer buckets (e.g. octiles) would
fragment the small-county tail below n=20.

**Frozen at 2020.** Bucket assignments are computed once per calibration
vintage and don't drift between runs. Re-running the build script with
the same seed produces identical bucket assignments. When the 2030 ACS
becomes the dominant vintage, re-freeze.

Why frozen rather than year-by-year: bucket-shifting would create a
moving-target stratification — a county that grew from medium to large
between 2015 and 2024 would appear in different cells across folds,
contaminating per-cell calibration.

### 2. Horizon stratification

Exponential smoothing models systematically under-cover at longer
horizons because the variance formula compounds smoothing-noise across
steps. A single κ calibrated at h=2 over-widens 1-year forecasts and
still under-widens 5-year ones.

**Bucket scheme: 2 levels.**

| Bucket | Horizons |
|---|---|
| `short` | h ∈ {1, 2} |
| `long`  | h ∈ {3, 4, 5} |

A 3-bucket scheme (medium = {3, 4}) was considered but fragments
`(small × long)` cells below n≈30 once 2020-1y-ACS holes are accounted
for. 2-bucket retains the dominant horizon-coverage signal — κ at
`long` is materially larger than at `short` — without overfitting.

The h-bucket assignment includes clamping: horizons beyond the longest
defined bucket clamp to `long` so extrapolation stays well-defined.
Horizons ≤ 0 (passthrough projections, target ≤ anchor) skip the bucket
classification entirely; the lookup falls through to the
globally-marginalised cell.

### 3. Geometric bias correction

**Formulation.** For each calibration cell, compute
`b = mean(log(point_raw / actual))`. Apply
`point_corrected = point_raw / exp(b)`. SE components and CI bounds
rescale by the same factor (in log-space, a level shift is multiplicative
on dollar-space SEs by the delta method).

**Why geometric, not arithmetic.** ACS dollar series grow multiplicatively
(income, rent, value all compound). Arithmetic bias correction
(`point − b̂`) doesn't generalize cleanly across counties of different
magnitudes — a $2K bias is a different fraction at $50K vs $500K.
Geometric correction handles all magnitudes uniformly.

**Clamp: ±10% multiplicative.** The applied bias is clamped to
`[log(0.9), log(1.10)]` — i.e. the multiplicative correction stays
within ±10%. Larger bias estimates almost always indicate panel
contamination (pathological growth in the synthetic data, suspended
1-year ACS revisions, or unrepresentative sampling) rather than
genuine systematic miscalibration. Clamping is the safety net.

The unclamped bias is preserved in the `extra["b_raw"]` field of each
calibration record so an analyst can identify which cells hit the clamp
and investigate.

**n-threshold: n ≥ 20 folds.** Bias and κ are gated at this minimum
fold count. Below n=20, the cell receives no calibrated correction
(b=0, κ=base_inflator); the consumer's fallback chain finds a
more-aggregated cell instead.

The 20-fold threshold derives from the standard error on a binomial
coverage estimator: at n=20, SE on a 90% coverage probability is
sqrt(0.9 × 0.1 / 20) ≈ 6.7%. Smaller n produces coverage estimates
with SEs comparable to or larger than the in-band tolerance (90% ±
5pp = ±5 percentage points), at which point κ calibration is fitting
noise. A tighter threshold (n=50) would reject too many small-county
cells; looser (n=10) admits noise.

### 4. Order of operations

**Bias first, then κ.** During calibration the order is fixed:

1. Pass A: estimate `b` per cell from raw fold residuals.
2. Pass A.5: apply bias to fold residuals in memory
   (`point' = point / exp(b)`).
3. Pass B: bisect κ per cell against the **bias-corrected** residuals
   to bring CI90 coverage into [85%, 95%].

Reversing the order would double-count level miscalibration in the κ
because the bisection's residual variance estimate would include both
random forecast error and the systematic bias.

Consumers must follow the same order: `_apply_bias_correction` first,
then `_apply_se_override`. Both are applied **pre-blend** to each
component (`trend_ensemble`, `multi_anchor`) — the v3 calibration
records are keyed by component method, not by the blended
`ensemble_multi_anchor` label.

### 5. Fallback chain

The lookup for both `b` and κ walks four levels. At each level, a
strata record is *qualified* if its `n_folds ≥ n_threshold`:

1. **Exact cell** `(indicator, method, pop_bucket, h_bucket)`.
2. **h-marginalised cell** `(indicator, method, pop_bucket, "*")`.
3. **Globally marginalised cell** `(indicator, method, "*", "*")`.
4. **v2 single-key fallback** `se_inflator_override_by_indicator_method[indicator][method]`.
5. **Floor**: κ = `EMPIRICAL_SE_INFLATOR` (1.30) or `b = 0`.

The "*" sentinel marks records pre-aggregated by the calibration
generator (the marginalisation isn't computed at lookup time, keeping
lookups O(1)). Records with insufficient n are *kept* in the index
but skipped at lookup time — diagnostic round-trips stay lossless.

### 6. Computational note

The v3 calibration replaces the v2 verify-pass approach (re-projecting
the entire panel inside the bisection iteration) with **cached fold
residuals**. The bisection applies a multiplicative SE factor to the
cached `(point, se_total, ci_low, ci_high)` tuples directly,
recomputing coverage in O(folds) per iteration without re-running any
projection model.

On the multi-state, 5-horizon panel (~50K folds across ~96 cells), this
is roughly 100× faster than the v2 approach. The bisection runs in
milliseconds per cell; total calibration runtime is dominated by the
initial Pass-2 fold construction, not by the iterative override search.

### 8. Multi-state calibration panel (April 2026)

The v3 ACS calibration now runs on a **147-county, 8-indicator, 15-year
panel** rather than the original 4-county Hawaii fixture.

**Panel composition (seed=42, frozen):**

| Bucket | Selection | Count |
|---|---|---|
| `xlarge` (≥ 1M) | Top 50 US counties by 2020 population, deterministic | 50 |
| `medium` (50K–200K) | Random sample, seed-stable | 50 |
| `small` (<50K) | Random sample, seed-stable | 50 |
| Hawaii always-include | 15001 / 15003 / 15007 / 15009 | 4 (overlap with above) |

Total: 147 distinct counties across 35 states. The `large` bucket (200K–1M)
is not sampled separately — it receives counties that naturally land there
(Honolulu 15003, plus any xlarge-overflow).

**Indicators (v0.4 expands to 13):**

| Code | Description | Table type |
|---|---|---|
| `B19013_001E` | Median household income | Detail (B*) |
| `B25058_001E` | Median contract rent | Detail |
| `B25064_001E` | Median gross rent | Detail |
| `B25077_001E` | Median home value | Detail |
| `B25071_001E` | Median gross rent as % of household income | Detail |
| `B20002_001E` | Median earnings for workers 16+ | Detail |
| `B01002_001E` | Median age | Detail |
| `S1501_C02_014E` | % age 25+ with HS diploma or higher | Subject (S*) |
| `S1501_C02_015E` | % age 25+ with bachelor's or higher | Subject |
| `S1701_C03_001E` | % below poverty (all people) | Subject |
| `S2301_C04_001E` | Unemployment rate (%) | Subject |
| `homeownership_rate` | Owner-occupied / total occupied units | Derived (B25003) |
| `vacancy_rate` | Vacant / total housing units | Derived (B25002) |

Subject-table indicators use the `/acs/acs1/subject` Census API endpoint.
The `AcsClient` routes automatically based on the `S`-prefix. Derived
indicators are computed post-fetch as numerator ÷ denominator (see
`scripts/build_calibration_panel.py::DERIVED_INDICATORS`).

**Note on `S0101_C02_030E` (% population 65+):** This code was tested but
rejected — the Census S-table schema was restructured around 2016–2018 and
the column assignment for C02/row 30 changed between vintages, producing
~35–40% values pre-2019 vs. correct ~18–22% values post-2019. `B01002_001E`
(median age) is the stable B-table substitute with the same demographic
signal and no structural break.

**Observed κ values from the multi-state calibration (anchors 2014–2022):**

| Indicator / method | κ range | CI90 coverage |
|---|---|---|
| Income (`B19013`) / trend_ensemble | 1.01–1.30 | 90.5% |
| Income (`B19013`) / multi_anchor | 0.35–1.30 | 97.7% |
| Worker earnings (`B20002`) / trend_ensemble | — | 90.3% |
| Rent (`B25058`, `B25064`) / trend_ensemble | **1.30–2.60** | 82–84% |
| Rent (`B25058`, `B25064`) / multi_anchor | 0.42–1.30 | 98–99% |
| Home value (`B25077`) / trend_ensemble | 1.30–1.95 | 89.9% |
| Median age (`B01002`) / trend_ensemble | — | 87.5% |
| HS+ % / trend_ensemble | 0.72–1.30 | 94.9% |
| BA+ % / trend_ensemble | 0.72–1.30 | 96.0% |
| Poverty % / trend_ensemble | 1.01–1.95 | 88.9% |
| Unemployment (`S2301`) / trend_ensemble | — | **79.7%** |
| Homeownership rate / trend_ensemble | — | 88.7% |
| Vacancy rate / trend_ensemble | — | 84.6% |

Key patterns:
- **Rent is the most uncertain series** — trend_ensemble κ up to 2.60 on
  long-horizon, small-county cells (heterogeneous rent dynamics nationwide).
- **Multi-anchor CIs are inherently conservative** — bisection correctly
  deflates κ below 1.0 for income/rent/home-value multi_anchor cells.
  The model's blended uncertainty already exceeds actual error.
- **Educational attainment is stable** — κ < 1 on some cells; near-zero
  bias; series is well-specified by the trend model alone.
- **Unemployment is under-covered at 79.7%.** This is a known model
  misspecification, not a data issue. Unemployment is mean-reverting
  (3%→15%→3% over a recession cycle) whereas the trend model assumes
  continuity. Even with the bisected κ applied, the CI *shape* is wrong
  for recession shocks — no multiplier can make a Gaussian interval capture
  a sudden spike. For applications that require calibrated unemployment
  uncertainty, a dedicated mean-reversion model (AR(1) toward a long-run
  mean) would be appropriate. For the current use case — contextual signal
  in housing affordability tracking — the point estimate is reliable and
  the under-coverage is acceptable.

**Observed bias values (post-clamp, expressed as % level shift):**

| Indicator | Typical bias | Interpretation |
|---|---|---|
| Dollar series (income, rent, value) | −7% to −9% | Model under-projected the 2020–2022 inflation surge |
| Poverty rate / multi_anchor | +6% to +10% | Model over-predicted poverty reduction from income growth |
| Education attainment | −0.5% to +4% | Essentially unbiased |

The negative bias on dollar series is systematic: the pre-2023 anchor
sources (CPI, QCEW, PCE) didn't anticipate the post-COVID inflation spike,
so the ensemble consistently projected below actual. Bias correction shifts
all dollar forecasts up ~7–9%, reducing the systematic over-optimism.

**Rebuilding the panel:**

```bash
CENSUS_API_KEY=<key> python3 -m census_forecaster.scripts.build_calibration_panel
python3 -m census_forecaster.scripts.run_acs_calibration
```

The panel build issues ~3,050 Census API calls (~5–10 minutes with a key).
The calibration regeneration reads the bundled panel and needs no key
(~30 seconds). Both are idempotent.

### 7. 2020 1-year ACS hole

---

### 8. v4 phi calibration — implementation and null result (May 2026)

**What was built.** The v4 calibration layer adds a Pass 0 that derives a
per-cell damping constant (phi) from the ACS series' own margin-of-error
data, rather than using the global default `phi=0.85` everywhere. The idea:
counties with high sampling noise relative to real economic signal should be
damped more aggressively; precise large counties less so.

Noise/signal is estimated by decomposing the observed year-over-year variance
into a sampling component (derived from published ACS MOEs via
`SE = MOE / 1.645`, then `var_sampling ≈ SE_log(t)² + SE_log(t-1)²`) and a
residual signal component (`var_signal = max(0, var_total − var_sampling)`).
The noise share maps to phi via an affine function bounded in `[0.70, 0.95]`:
`phi = 0.70 + 0.25 × noise_share`.

Published ACS MOEs are used rather than PUMS replicate weights — they are
mathematically equivalent (Census Bureau computes MOEs internally from the
same 80 replicates) and avoid a 16+ hour data fetch for a 50-state panel.

**Walk-forward evaluation.** A held-out ablation was run on the 147-county
multi-state panel: train on anchors 2014–2020, evaluate on 2021–2022,
h ∈ {1, 2, 3}. Results:

| Metric | v3 (φ = 0.85) | v4 (per-cell φ) |
|---|---|---|
| Median MAPE | 5.59% | 5.64% |
| Any CI90 coverage < 80% | no | no |

Acceptance bar (median MAPE drop ≥ 3% relative) was not met.

**Why phi doesn't help at short horizons.** The damped trend model uses phi
to shrink the trend component toward zero over time: the trend contribution
at horizon h scales as `Σᵢ₌₁ʰ φⁱ`. At h = 1–3, the level component
dominates the forecast and the trend's cumulative contribution is small.
Varying phi in `[0.70, 0.95]` changes that contribution by less than 25%,
which is not enough to move MAPE materially. The calibrated phi values
further clustered near the default (median = 0.87, stdev = 0.07), so most
cells were already close to `phi = 0.85` anyway.

Phi would become a meaningful lever at h = 4–5, where the trend compounds
across more steps and damping choices diverge. The current primary use case
(revenue forecasting at 1–3 year horizons) sits in the insensitive range.

**Status.** The phi infrastructure is implemented and ships in v4
(`acs/acs_volatility.py`, Pass 0 in `acs/calibration.py`, `_lookup_phi` in
`acs/ensemble.py`). It is included in the calibration output but has no
practical effect at h ≤ 3. It is **not the default** — the bundled
calibration index remains v3. Re-enable and re-evaluate if longer-horizon
forecasting becomes a requirement.

**Drift correction (2026-08-21).** The "not the default" decision above
had silently stopped being true: the monthly refresh workflow regenerates
the bundled calibration with phi records included, and `_lookup_phi`
applied whatever records the payload carried — so production was running
per-cell phi while every κ/bias/conformal record was fit on Pass 2 folds
projected at φ=0.85 (`_project_trend_only` never received phi; its
"Pass 2 uses calibrated phi values" comment described plumbing that was
never written). Production was therefore applying corrections calibrated
for a different trend model than the one it ran.

Fixed by an explicit gate: `_lookup_phi` now applies per-cell phi only
when the payload carries `phi_enabled: true`, which
`run_stratified_calibration(enable_phi=True)` (CLI `--enable-phi`) sets —
and under that flag Pass 2 folds are also projected at the per-cell phi,
so a future v4 re-evaluation is internally consistent. Default remains
off on both sides: folds and production both run at DEFAULT_PHI=0.85,
matching the configuration the phi ablation actually validated. phi
records continue to be emitted as diagnostics either way. Note the MAPE
comparison in the ablation above was already fair (phi moves the point
forecast directly); what the confound corrupted was production *interval*
calibration, not the ablation's point-accuracy verdict.

### 8b. Conformal-primary intervals (August 2026)

The v5 split-conformal layer originally applied as a **floor**:
`final_half = max(κ_half, q·se_pre_κ)`. That policy stacks two
half-widths that were each independently calibrated to the same 90%
target — κ by bisection on the tuning folds, q as a finite-sample
quantile on the calibration fold — so their max can only over-cover.
The shipped payload's held-out evaluation confirmed it: coverage
94–100% (median ≈96.6%) against the 90% target. (The evaluation pass
itself had two bugs, fixed at the same time: its "κ half-width" never
actually applied κ, and its conformal half used the non-bias-corrected
SE — so the reported `evaluation_coverage` wasn't measuring the
production interval at all.)

Three policies were compared on the held-out 2022 anchor (publication
as-of, bias→κ chain reproduced exactly, 6,912 folds across 16
indicators × all default-path methods + ml_trend):

| policy | coverage | mean relative half-width |
|---|---:|---:|
| κ only | 90.9% — but with an under-coverage tail (cells at 56%, 71%, 81–84%) | 22.6% |
| **conformal-primary** | **92.7%, tail eliminated** | **20.7%** |
| max(κ, conformal) — old | 94.3% | 24.0% |

Conformal-primary strictly dominates the max policy (better centered
AND sharper) and removes κ-only's dangerous cells; it is also what the
split-conformal guarantee actually licenses — the guarantee attaches to
the conformal interval itself, not to flooring another interval with it.
**Production now replaces the κ CI with `point ± q·se_pre_κ` wherever a
stratum record exists** (`ensemble._apply_conformal_interval`, renamed
from `_apply_conformal_floor`); the κ-based CI stands for strata without
records (n < 20 calibration folds, or pre-v5 payloads). κ itself is
unchanged and still corrects `se_total` — the SE fields remain
κ-calibrated moments; only the CI is the conformal product.

Residual over-coverage under the new policy concentrates in the
mean-reverting series (S2301 97–100%, S1701 93–97%) where no symmetric
rescaling can fix the interval *shape* — the known model
misspecification documented under "Unemployment is under-covered" above,
which conformal can widen for but not reshape.

Note: `tax_modeler.revenue`'s separate `apply_revenue_conformal_floor`
still uses the max policy — it has its own records and its own
evaluation surface, and switching it should be gated on the same
three-policy comparison run on revenue folds, not inherited from this
result.

### 9. 2020 1-year ACS hole

The 2020 1-year ACS was suspended due to COVID-19 data quality issues.
Folds whose target year would be 2020 (e.g. anchor=2018 with h=2;
anchor=2015 with h=5) are dropped silently by the calibration generator.
This means cells in the `long` horizon bucket have systematically fewer
folds than `short` cells in any vintage that spans 2020 — a confound
between the bucket variable and the data-availability structure. Track
per-cell `n_folds` carefully when interpreting calibration output;
the manifest exposes counts at all granularities.

---

---

## v3 stratified calibration — BLS side (April 2026)

The BLS CPI projection layer received the same stratification treatment
as ACS, with three structural differences reflecting the different
data geometry.

### 1. Three-axis stratification

ACS uses (indicator, method, pop_bucket, h_bucket). BLS uses
**(series_id, h_bucket, vol_regime)**:

* `series_id` — the BLS series (e.g. `CUURS49FSA0`, `CUUR0000SEHA`).
* `h_bucket` — `short` for h ∈ {1–5} months, `long` for h ∈ {6+} months.
* `vol_regime` — `low` if the rolling 24-month return SD ≤ per-series
  median; `high` otherwise.

The volatility regime axis captures the 2021–2023 inflation spike vs
the 2010s calm period. Rent CPI's median 24-month SD is ~0.001/mo;
gasoline's is ~0.05/mo (50× larger). A single κ over both regimes
either over-pads the calm or under-pads the spike.

### 2. Log-space CI rescaling

ACS rescales SE in dollar space (`new_half = factor × old_half`). BLS
rescales SE in **log space** because the projection is log-space
multiplicative:

```
CI_new = point × exp(±Z · factor · SE_log)
```

This matters for the bisection: changing `factor` produces a
**multiplicative** widening/narrowing of the CI, not additive. The
shared bisection algorithm in `acs/calibration_common.py` is
parameterised over a `coverage_fn(factor)` so both modules use the same
search procedure.

### 3. Multi-MSA panel

The v2 calibration ran on 5 "Honolulu" series (see §5 — those IDs were
actually Los Angeles). v3 now runs on **60 series**: 12 areas (national
+ Urban Hawaii + 10 large MSAs) × 5 CPI subindexes (all-items,
food-at-home, rent, housing/shelter, gasoline). The panel is bundled at
`data/bls_panel/cpi_panel.json` and refreshed via
`python -m census_forecaster.scripts.refresh_bls_panel` with a
`BLS_API_KEY`.

Empirical κ values from the v3 calibration on this panel:

| Cell | Approx. κ |
|---|---:|
| Rent, short horizon, low vol | 1.5 |
| Rent, long horizon, low vol | 3.0 |
| All-items, long, low | 2.0–3.0 |
| Housing, long, high vol | 4.0 |
| Gasoline, short | 1.5–2.0 |
| Gasoline, long | 2.0 |

The legacy global κ=1.50 was severely under-padding long-horizon CIs
(true κ at h=long is 2.0–4.0 across most series) and modestly
over-padding short-horizon rent (κ at h=short for rent is 1.5).

### 4. BEA anchor integration

The macro-anchor pool gained three BEA Regional series in v3:

| File | BEA Identifier | Anchors |
|---|---|---|
| `bea_hi_percapita_income.json` | SAINC1 LineCode=3, GeoFips=15000 | B19013, S1701 |
| `bea_honolulu_rpp_all.json` | MARPP LineCode=1, GeoFips=46520 | B19013 |
| `bea_hi_rpp_housing.json` | SARPP LineCode=3, GeoFips=15000 | B25058, B25064 |

After registration, `B19013_001E` has 5 anchor sources (was 3); rent
indicators have 3 (was 2); poverty rate (`S1701_C03_001E`) has its
first anchor (was 0).

On the Hawaii calibration, BEA per-capita personal income gets the
**highest weight** in the income anchor ensemble (~25%), beating QCEW
wages (~23%), the Cleveland Fed-style CPI all-items (~19%), the PCE
deflator (~19%), and Honolulu metro RPP (~13%).

### 5. Series-identity correction + YoY trend estimator (July 2026)

**Identity correction (2026-07-27).** The series the repo had labelled
"Urban Honolulu, HI" — `CUURS49ASA0`, the production Hawaiʻi income
deflator — is actually **Los Angeles-Long Beach-Anaheim, CA** per the
BLS API catalog. Five of eleven panel area labels were shifted; no
Hawaiʻi series was in the panel at all. Corrections:

* Panel areas fixed and **`S49F` "Urban Hawaii"** added (bimonthly,
  published from 2017). `bls/panel.py` documents the audit; the cadence
  tell is that BLS publishes SA0 monthly for exactly four areas (US,
  NY, Chicago, LA) — a monthly "Honolulu" series is mislabelled.
* `_HONOLULU_BLS_SERIES_ID` → `CUURS49FSA0` (tax_modeler income path).
* The annual anchor files `cpi_honolulu_allitems.json` /
  `cpi_honolulu_rent.json` declared BLS series IDs that do not exist;
  both were rebuilt from the genuine semiannual Urban Hawaii series
  (`CUUSS49FSA0` / `CUUSS49FSEHA`), 2017–2025 observed, 2010–2016
  rate-chained backward from the legacy files' year-over-year rates
  (flagged in each file's limitations).
* LA ran ~0.34 pp/yr hotter than Urban Hawaii over 2018–2025 (CAGR
  3.687% vs 3.350%), so LA-based Hawaiʻi deflators were overstated
  ~2.6% compounded over a 2023→2031 window.

**YoY trend estimator (2026-07-27).** The pairwise smoother in
`bls/projection.py` weighted the newest print pair at ~50% and read
consecutive-month changes of NSA series (seasonality) as trend — a
jackknife (drop the newest print) moved its implied annual rate by
~4 pp on both Urban Hawaii and LA. `smoothed_monthly_rate` now uses
**year-over-year log changes** (same-calendar-month differencing
cancels seasonality; unaffected by cadence or isolated holes) blended
with a **time-based recency weight** `0.5^(months_back / 8.3)` — decay
per calendar month, so bimonthly and monthly series with the same path
get the same trend. The 8.3-month half-life mirrors φ=0.92/month's
half-life but is an independent, sweepable constant. Series with <4 YoY
observations fall back to the legacy pairwise smoother (its 2-point
behavior is a public contract). Validation on the corrected panel:
implied trends land within ~0.15 pp/yr of 24-month CAGRs and jackknife
swings drop from ~4 pp to ≤0.3 pp. The v3 calibration was re-derived on
the corrected panel + new estimator (42,908 folds, 421 strata cells —
`backtests/results/bls_v3_calibration_2026-07-30.md`).

**Uncertainty plumbing (2026-07-30).** The κ-rescaled CPI projection SE
now propagates to consumers: `RealGrowthDetail`
(tax_modeler `projection/income_forecast.py`) combines the ACS-forecast
log-SE with the κ-rescaled BLS log-SE under an independence assumption,
and `compute_credit_overlay` (SB 3125) reruns itself at the real-growth
CI90 bounds to emit savings bands. See the SB3125_CD1_FORECAST.md
"Prediction-interval plumbing" section for the decomposition and the
channels deliberately held at point.

### Auto-refresh

A monthly GitHub Actions workflow (`.github/workflows/refresh-data.yml`)
re-fetches the BLS panel, BEA anchors, and regenerates both the BLS v3
and ACS v3 calibrations. The workflow auto-commits with `[skip ci]`
so the test workflow doesn't recurse on data-only changes. The ACS
calibration regeneration reads the already-bundled panel and needs no
`CENSUS_API_KEY` — only the BLS/BEA refresh steps need their respective
secrets.

---

## Cross-county ML trend (experimental, April 2026)

The ACS forecaster ships an optional third ensemble member —
`ml_trend` — alongside the classical `trend_ensemble` (damped log trend
+ AR(1)) and the multi-source `multi_anchor`. It is **opt-in** via
`project_ensemble_multi(..., use_ml=True, ml_series_by_key=...,
ml_populations=...)` and is not on the default projection path.

### Why ML, why now

The classical trend models operate on a single (geoid, indicator) series
in isolation. With the multi-state calibration panel (147 counties × 16
indicators × 15 years = 19,638 observations) we now have enough cross-
county and cross-indicator structure for a tree-based learner to add
signal the per-series models cannot see — county-size effects, regional
patterns, and cross-indicator dependencies (e.g. rent ↔ wages,
unemployment ↔ educational attainment).

### Architecture

`ml_trend` is sklearn's `HistGradientBoostingRegressor` (histogram-based
gradient boosting, same algorithmic family as LightGBM but bundled in
sklearn so no libomp/OpenMP runtime dependency). One model is trained
per `(indicator, cutoff_year)`; cutoff_year defaults to the most recent
observed year. Features per row:

* Lagged target levels in log space at anchor, anchor-1, anchor-2.
* YoY log-growth diffs and 3-yr trailing mean diff.
* County metadata: log 2020 population, pop-bucket one-hot, state FIPS.
* **Cross-indicator panel**: same county's log values for every *other*
  indicator at the anchor year (15 columns). This is the genuinely new
  signal not exposed to the classical models or the macro anchor.
* Horizon as a feature (h ∈ 1..5) so a single model serves all horizons
  per indicator.

Target: `log(y[target_year] / y[anchor_year])` — log-growth from anchor.
Walk-forward training filter: `target_year ≤ cutoff_year` strictly,
matching the back-test discipline used elsewhere in this package.

### Ensemble integration

When `use_ml=True`, the combiner uses a two-stage Bates-Granger:

1. **Inner (target-side)**: `trend_ensemble` + `ml_trend` at ρ=0.7 (both
   consume the same series so high correlation is appropriate).
2. **Outer (target vs macro)**: inner + `multi_anchor` at ρ=0.5,
   blended at the calibrated `_calibrated_macro_weight` per indicator.

Each member is bias-corrected and SE-overridden via the v3 stratified
κ table before combination — the same pre-blend correction order used
for the existing two-member ensemble.

### Walk-forward ablation (April 2026 panel)

Run on the bundled 147-county / 16-indicator panel, anchors 2014-2022,
horizons 1-5. Both calibrations apply v3 bias + κ corrections to each
member before combining; the ensemble metrics below are
production-equivalent.

| Indicator | RMSE Δ | Cov90 (no-ml → with-ml) |
|---|---:|:---:|
| B19013_001E (median income) | -5.0% | 88.9% → 88.9% |
| B25058_001E (median rent, contract) | -5.8% | 86.2% → 85.0% |
| B25064_001E (gross rent) | -5.7% | 87.4% → 86.3% |
| B25077_001E (median home value) | -4.7% | 88.7% → 87.5% |
| B20002_001E (worker earnings) | -10.7% | 89.1% → 88.3% |
| B01002_001E (median age) | -16.3% | 91.0% → 88.6% |
| S1501 (BA+, HS+) | -25.5% / -17.1% | 91% → 88-90% |
| S1701 (poverty rate) | -5.3% | 86.3% → 85.6% |
| homeownership_rate | -18.1% | 88.7% → 87.4% |
| in_migration_rate | -20.9% | 92.8% → 90.4% |
| pct_professional, pct_service | -16% / -12% | 91-92% → 89-90% |
| vacancy_rate | -10.6% | 92.2% → 88.0% |

Point accuracy improves on every indicator (5-25% RMSE drop). The
stand-alone `ml_trend` method has CI90 coverage of 89-94% on all 16
indicators — the model itself is well-calibrated.

### Why opt-in (not default) — RESOLVED 2026-08-21: default is now ON

After v3 calibration the ensemble-level CI90 coverage with ml_trend is
85-90% on every indicator, with one indicator (B25058) at exactly 85.0%
(below the lower bound by less than 1 fold out of 2,984). The classical
ensemble's coverage is uniformly 86-93% — slightly more conservative.
The conservative call was to ship the ML member as opt-in until we had
either:

1. A "ensemble_with_ml"-level κ stratum in the v3 calibration (so the
   joint CI gets its own bisection-on-coverage rather than relying on
   the per-method κ to compose well under inverse-variance combination), or
2. A cross-correlation parameter sweep showing ρ values that put the
   ensemble coverage uniformly inside [85%, 95%].

**Condition (2) was satisfied on 2026-08-21**
(`backtests/results/ml_default_ablation_2026-08-21.md`): the re-run
ship gate passed all three rules — 13/16 indicators improve RMSE ≥5%
(up to −25%; the other three improve 3.2–4.2%), blended CI90 coverage
in [85%, 95%] on all 16 indicators, zero regressions — and the ρ_inner
sweep shows **all 16 indicators in band at the production ρ=0.7**
(15/16 at ρ=0.5–0.6; 16/16 at 0.7/0.8/0.9). ρ_inner stays 0.7.
`use_ml` now defaults to True; when the caller supplies no panel, the
bundled calibration panel is lazily loaded with a shared process-level
model cache (first call per (indicator, cutoff) trains for ~2s, later
calls are cache hits). Geoids absent from the panel degrade gracefully
to the classical ensemble (no feature row → no ML member).

History note: the default was first flipped to True in April 2026
(Phase C) citing a SHIP verdict, then reverted 2026-05-30 to match this
section's opt-in stance — the committed `phase_c_bps_ablation.md` had a
degenerate (empty) comparison table, so the flip stood on unverifiable
evidence. The 2026-08-21 report supersedes it with the comparison table
populated. Note tax_modeler's income/supplement forecast call sites
pass `use_ml=False` explicitly — Act 24 / SB 3125 numbers are pinned to
the classical ensemble until that choice is revisited deliberately.

Follow-up that remains open: condition (1), a blend-level κ/conformal
stratum. Today the final blended CI is Gaussian from the blended
κ-corrected `se_total`; per-member conformal intervals apply only when
a single member is returned (which is the common path — 11/16
indicators have no macro anchor). A post-blend calibration stratum
would give anchored indicators the same treatment.

### How to enable

```python
from census_forecaster.acs import project_ensemble_multi, build_panel_index
from census_forecaster.scripts.load_calibration_panel import load_panel

series, populations, _ = load_panel()
panel = build_panel_index(series)
ml_cache: dict = {}  # reuse across many forecasts at same cutoff

fp = project_ensemble_multi(
    series_observations=obs_for_one_county_indicator,
    target_year=2026,
    populations=populations,
    use_ml=True,
    ml_series_by_key=series,
    ml_populations=populations,
    ml_model_cache=ml_cache,
    ml_panel=panel,
)
```

The training cost is ~2 seconds per `(indicator, cutoff_year)` model
fit; the cache amortises that across many forecasts at the same cutoff.

### Reproducing the ablation

```bash
python -m census_forecaster.scripts.compare_ml_ablation --output report.md
```

Runs the v3 calibration twice (with `include_ml=True` and `False`),
applies v3 corrections to each method's residuals, synthesises the
two-stage ensemble, and writes a per-indicator comparison table. Total
runtime: ~6-8 minutes (the ML half adds ~5 min of HGB training).

---

## S2301 mean-reversion model — informative null (August 2026)

The long-prescribed fix for unemployment's misspecification ("a
dedicated mean-reversion model — AR(1) toward a long-run mean", §8
above) was built and evaluated: `acs/mean_reversion.py` forecasts the
ACS S2301 print as ``delta_c + mu_c + phi^h·(u_LAUS − mu_c)`` — pooled
AR(1) phi across the 90-county LAUS panel, county long-run mean, and a
county-specific ACS↔LAUS offset (the raw level anchor's missing piece).
Walk-forward on the calibration panel (anchors 2014–2022, h 1–5,
n=2,937 folds):

| Design | RMSE-pct | h=1 → h=5 |
|---|---:|---|
| trend_ensemble (incumbent) | **36.1%** | 36.2% → 39.1% |
| ml_trend (incumbent) | **33.4%** | — |
| level_anchor (raw LAUS carry) | 52.3% | 38.2% → 43.1% |
| mean_reversion (full-history mean) | 59.0% | 52.6% → 84.8% |
| … mu recency-weighted (hl=4y) | 66.8%* | 48.2% → 78.5% |
| … mu = last-3-year mean, recency offset | 59.0%* | 46.1% → 62.5% |
| … pure LAUS carry + recency offset | 63.7%* | 45.0% → 51.9% |

(*variant sweep in the standalone harness; same folds.)

**Verdict: NULL — every LAUS-driven design loses to the ACS-trained
incumbents by 20+ RMSE points, at every horizon.** The reading is not
that unemployment fails to mean-revert; it's that the *forecast target
is the ACS print*, and at county level the print's variance is
dominated by its own idiosyncratic measurement structure (1-year ACS
sampling noise, universe/definitional gaps vs the LAUS model) rather
than by the underlying labor market. Predictors trained on the ACS
series' own history model that idiosyncrasy; LAUS-state predictors
cannot, no matter how the reversion target is chosen. This is the same
lesson as the rejected national CPS rate anchor and the level anchor's
52%-vs-33% deficit, now established directly against the prescribed
mean-reversion design.

Practical consequences: (1) the S2301 accuracy gain available today is
the ML member (−3.5% blended RMSE, raw coverage 90.5%), which is now
default-on; (2) the model ships as calibration Pass 2e diagnostic
records only (`method="mean_reversion"`) — deliberately NOT an ensemble
member — so the null is reproducible; (3) the still-open S2301 idea is
a *nowcast* channel (UI claims → Hawaii counties), which needs a
Hawaii-restricted evaluation design, not a panel-wide one.

*Resolved 2026-08-22.* The dilemma dissolved rather than being solved:
ETA-539 publishes all 50 states, so the channel was built **per-state**
(each county reads its own state's claims) and both evaluations became
available at once — a panel-wide ablation with real signal in all 90
counties, plus the Hawaii-restricted read this paragraph asked for. It
passes the no-regression gate in both, and the accuracy gain is ~0.5%
relative on S2301 — i.e. this section's central finding survives the
promotion intact: state-driven labour predictors do not move the ACS
county print much, however strong they look on the monthly grid. See
§"State-level UI claims channel" at the end of this document.

---

## Batch-2 diagnostics — two nulls and a pass (August 2026)

Three assessment items were taken to evidence on the calibration panel
(anchors 2014–2022, tuning 2014–2020, held-out eval 2022), in one
diagnostics run over the shared fold cache:

**QCEW → B20002 anchor: PASS, shipped.** Worker earnings (B20002) had
no macro anchor. Registering it on the existing QCEW wage file (the
conceptually closest cell to a payroll-tax wage base) improves the
blend RMSE 10.47% → 9.12% on 2,977 folds, with the anchor member alone
at 6.91% vs trend 7.97%. Raw blend coverage 95.6% — mild over-coverage
of exactly the kind κ already deflates for B19013. Registry:
`sources/base.py` (affinity extended).

**Recency-weighted bias: NULL — keep the unweighted estimator.** The
hypothesis was that the frozen COVID-era dollar bias (−7…−9%) should
decay via recency weighting. The A/B on held-out 2022 folds says
otherwise: mean per-cell |residual bias| is 3.95% unweighted vs 5.15% /
4.70% / 4.35% at half-lives 2/3/5 years — down-weighting old folds
costs more estimation variance than the regime drift it removes, at
this panel size. The mechanism ships as
`run_stratified_calibration(bias_half_life_years=...)` (default None =
unweighted) for re-evaluation once post-surge anchors (2023+) enter the
panel — the concern is about *future* regime turnover this A/B cannot
yet see. Monitor `b_raw` drift release-over-release.

**Log-space intervals: NULL — the asymmetry is bias, not shape.** The
assessment presumed symmetric level-space CIs on log-space models lose
the right-skew of multiplicative growth. The tails ARE imbalanced on
eval folds (dollar series: 5.4% of actuals miss below the band vs 2.0%
above; rates/shares near-balanced at 4.0%/3.2%) — but in the *opposite*
direction the log-normal story predicts: a log-space interval has a
HIGHER lower bound than the level-space one, so it would increase the
dominant (lower-tail) miss rate. The imbalance traces to a residual
+0.8% mean over-projection on 2022 eval folds that tuning-fold bias
correction under-corrects — a location problem, not a shape problem.
Symmetric intervals stay; the open question is the residual eval-fold
bias, which recency weighting (above) also fails to fix.

---

## Kalman per-indicator promotion (August 2026)

The Kalman state-space member (`kalman/`, opt-in since Phase D) remains
**HOLD as a wholesale replacement** — the 2026-08-21 re-run of
`compare_kalman_ablation` on the current calibration machinery fails all
three ship-gate rules, same as the original (8/16 improve, 7 regress,
10 coverage violations from 66% to 99.8%). But the report's own
per-indicator reading identifies two indicators that pass BOTH gates
(RMSE ≥5% better than the best existing baseline AND raw CI90 coverage
in [85%, 95%]):

| Indicator | RMSE vs best baseline | Cov90 (raw) |
|---|---:|---:|
| `pct_service_occupations` | **−15.0%** | 90.8% |
| `B25071_001E` (rent burden) | **−10.6%** | 93.8% |

These two are promoted via `ensemble.KALMAN_PROMOTED_INDICATORS`:
`use_kalman` is now tri-state (`None` default = auto by allowlist;
`True`/`False` force). Both are trend-only indicators (no macro anchor),
so Kalman replaces the classical trend ensemble exactly where it
demonstrated the largest gains.

**Correction-chain note.** The bundled payload carries no
`kalman_state_space` strata records (the refresh workflow does not pass
`--include-kalman`), so the production Kalman path receives zero net
correction (fallback κ = base inflator → factor 1.0, b = 0, no
conformal) — i.e. production runs the exact raw intervals the ablation
validated. **Do not add `--include-kalman` to the refresh workflow
without first re-running the ablation with corrections applied**: records
in the payload would silently shift promoted-indicator behavior from
raw-as-validated to corrected-but-unvalidated — the same drift class as
the v4 phi incident above. Near-miss candidates for future promotion
(S1501_C02_015E −11.2%/96.3%, S1701 −11.7%/84.7%) each fail one gate by
1–2 points; per-indicator process-noise (Q) tuning is the named lever.

---

## Backwards compatibility

A v2 calibration payload (no `strata_records` key) loads and runs
through every v3-aware consumer unchanged. The legacy single-key
`se_inflator_override_by_indicator_method` lookup is the v2 fallback at
level 4 of the chain; bias correction is a no-op when no strata records
are present.

The v3 generator additionally writes marginalised v2-style tables at
the top level of the JSON payload (`rmse_by_indicator_method`,
`ci90_coverage_by_indicator_method`, etc.) so any v2-only consumer
sees exactly the keys it expects.

---

## Federal EITC / CTC by geography (May 2026)

Year-keyed federal credit calculators (`tax_modeler.credits.eitc`,
`tax_modeler.credits.ctc`) carry IRS Rev. Proc. parameters for TY
2022 → TY 2025 — max-credit, phase-in completion income, single-
and joint-filer phaseout starts, ACTC refundable cap. Statutory rates
(phase-in / phase-out percentages, $200K/$400K phaseout thresholds,
$2,000/child max under TCJA) do not move year-to-year and live in
shared constants.

### Parameter vintages

| Year | EITC max (2 kids) | ACTC refundable cap | IRS Rev. Proc. |
|-----:|------------------:|--------------------:|----------------|
| 2022 |           $6,164 |              $1,500 |        2021-45 |
| 2023 |           $6,604 |              $1,600 |        2022-38 |
| 2024 |           $6,960 |              $1,700 |        2023-34 |
| 2025 |           $7,152 |              $1,700 |        2024-40 |

`project_tax_units_forward(target_year=Y)` plumbs `Y` into both
`_recalculate_ctc` and `calculate_eitc_for_tax_units` so projected
nominal incomes are credited against `Y`'s statutory parameters
(this matches the IRS chained-CPI inflation-indexing treatment).
For `target_year` beyond the latest published Rev. Proc., the
projector clamps to the most recent supported year and logs the
substitution — older callers projecting to 2026+ continue to work.

### Take-up imputation

Eligibility ≠ claim. PUMS-derived eligible amounts overstate IRS
take-up. The `tax_modeler.pipeline.apply_credit_takeup` helper
(opt-in via `run_pipeline(apply_credit_takeup=True)`) ranks eligible
filers by their eligible credit dollars descending and zeroes credits
on non-claimants until weighted recipient counts match the IRS
state-total benchmark. Anchors (IRS SOI ZIP=00000 row, TY 2022):

* **EITC**: 84,010 returns / $184.7M
* **CTC** (nonref + ACTC): 154,580 returns / $469.5M
* **ACTC alone**: 60,600 returns / $117.8M

The take-up *rate* is treated as behavioral and constant across
forward projection; only the target count is anchored at the 2022
vintage. Re-run when newer IRS SOI Hawaii state-totals publish.

### CBPP comparison caveats

`scripts/eitc_ctc_geo_report.py --compare-cbpp` produces a senate-
district delta table against CBPP table 367 (TY 2022). Two systematic
caveats apply:

1. **Geographic resolution**: CBPP uses IRS SOI ZIP-code claims data
   raked to 2024 SLDs. The modeler uses PUMA-level signal hashed to
   HD/SD via the bundled crosswalk, which preserves PUMA-level
   variance but smears within-PUMA. A future enhancement would
   ingest IRS SOI ZIP data and rake the modeler's LD column to it.
2. **Suppression and AGI<$1**: CBPP suppresses small-cell districts
   and excludes AGI<$1 returns; the modeler does neither. Expect
   some absolute discrepancy on small-population SDs even with
   perfect raking.

---

## References

* **Damped trend, ETS variance:** Hyndman, R., Koehler, A., Ord, J.,
  Snyder, R. (2008). *Forecasting with Exponential Smoothing: The State
  Space Approach.* Springer.
* **Small-area discipline:** Wilson, T., Grossman, I., Alexander, M.,
  et al. (2021). "Methods for Small Area Population Forecasts: State
  of the Art." *Population Research and Policy Review.*
* **Inverse-variance combination:** Granger, C., Ramanathan, R. (1984).
  "Improved methods of combining forecasts." *Journal of Forecasting.*
* **Bates-Granger optimal weight:** Bates, J., Granger, C. (1969). "The
  combination of forecasts." *OR Quarterly.*
* **Multi-model ensemble correlation:** Tebaldi, C., Knutti, R. (2007).
  "The use of the multi-model ensemble in probabilistic climate
  projections." *Phil. Trans. R. Soc. A.*
* **ACS MOE conversions:** US Census Bureau (2018). "ACS General
  Handbook, Chapter 8: Calculating Measures of Error for Derived
  Estimates."
* **Cleveland Fed rent blending:** Adams, B., Hertz, B., Verbrugge, R.
  (2022). "New-Tenant Repeat Rent Inflation." *Cleveland Fed
  Working Paper 22-38r.*

---

## SPM unit of analysis (Tier 4, May 2026)

The poverty pipeline reports the Supplemental Poverty Measure at the
**SPM-unit** level per Census P60-280, not at the tax-unit level.
Tax-unit-grained calculations remain available for revenue-side
forecasts and for backward-compatibility checks (the
`--by-tax-unit` flag on `scripts/poverty_impact_report.py`).

### SPM unit construction

SPM units are built bottom-up from PUMS persons using the RELSHIPP
relationship-to-householder code:

* **Primary SPM unit per household** (`{SERIALNO}_primary`): the
  householder + all relatives (RELSHIPP 20–30) + unmarried partner
  (33) + foster children (34) + *any unrelated child under 15*
  (RELSHIPP 31, 32, or 35 with AGEP < 15, per the standard P60-280
  exception).
* **Unrelated adult SPM units** (`{SERIALNO}_unrel_{SPORDER}`):
  roomers/boarders/other non-relatives aged 15+ (RELSHIPP 31, 32, 35
  with AGEP ≥ 15) — one one-person SPM unit each.
* **Group quarters** (RELSHIPP 36, 37): excluded from SPM accounting
  entirely (per P60-280; SPM is defined for the household population).

Implementation: `tax_modeler.units.spm_unit_assembly.build_spm_unit_assignment`.

### Tax-unit → SPM-unit aggregation

After all tax/credit/benefit dollars have been computed at tax-unit
granularity (the methodologically correct level for tax-return-based
quantities), the pipeline rolls them up to SPM-unit granularity
before threshold comparison:

* **Sum across tax units in the same SPM unit**: every SPM-resource
  component — `total_cash_income`, `eitc_amount`, `ctc_refundable`,
  `hi_eitc_amount`, all benefit imputations (SNAP, housing subsidy,
  WIC, LIHEAP, CCSP, school lunch), all tax liabilities (federal,
  state, payroll), MOOP, childcare expense, work expense, RxKids
  (if enabled).
* **Representative columns** (constant within a household): SERIALNO,
  PUMA, county, house district, senate district, tenure — taken
  from the highest-income constituent tax unit (deterministic
  tiebreak: lowest filer_id).
* **Composition counts** (`n_adults`, `n_children`, `n_persons`)
  computed from PUMS person ages restricted to members of the SPM
  unit, used both for the threshold lookup and for the
  persons-in-poverty numerator.
* **Weight**: raw household `WGTP` from PUMS, one value per SPM
  unit. The tax-unit hybrid weight (calibrated to DOTAX filer
  totals via per-filing-status multipliers) is dropped because it
  is a tax-filer concept, not a person concept; SPM-unit weighted
  persons hits the ACS person count (~1.3M for Hawaii) directly.

Implementation: `tax_modeler.poverty.spm_aggregation.aggregate_to_spm_units`.

### Threshold lookup

`tax_modeler.poverty.thresholds.threshold_for_units` auto-detects
SPM-unit input via the presence of `n_adults` and `n_children`
columns and uses them directly when present, applying the Betson
(1996) three-parameter equivalence scale to the published Hawaii
2A2C reference threshold. When the composition columns are absent
(tax-unit fallback path used by `forecast_*.py` revenue scripts),
the function derives `n_adults = 1 + (filing_status == MFJ)` and
`n_children = num_dependents` for backward compatibility.

### Result (TY2024, real Hawaii PUMS)

The Tier 4 SPM-unit pipeline produces a baseline rate of **12.43 %**
on real PUMS, matching the upper bound of Census-published Hawaii
SPM (~10–12 %). The Tier 3 tax-unit baseline of 24.62 % was
inflated by the well-known unit-of-analysis bias: multi-tax-unit
households were each scored against single-person thresholds with
only their individual income, missing the resource-pooling that
happens in real households.

### References

* Short, K. (2012). *The Research Supplemental Poverty Measure:
  2010.* Census P60-241.
* Renwick, T., & Fox, L. (2016). *The Supplemental Poverty Measure:
  2015.* Census P60-258.
* US Census Bureau (annual). *The Supplemental Poverty Measure:
  P60-280 series.*

---

## Margin of error on the poverty-impact pipeline

`scripts/poverty_impact_report.py` historically returned point
estimates (`persons_lifted_<scenario>`, `poverty_rate_<scenario>`,
`gap_closed_<scenario>_$`) with no margin of error. Two dominant
sources of uncertainty are now quantified — and reported as
**separate sibling columns** rather than combined in quadrature, so
the source of any uncertainty is legible to reviewers.

### Source 1 — PUMS sampling variance (SDR)

The Census Bureau ships 80 replicate weights with every PUMS vintage
(`PWGTP1`–`PWGTP80` on persons, `WGTP1`–`WGTP80` on households),
generated via the successive-difference perturbation method (Fay's
modified BRR with `k = 0.5`). The standard variance estimator is

    V(θ) = (4 / R) · Σ_{r=1..R} (θ_r − θ_0)²

where θ_0 is the headline estimate on the main weight and θ_r is the
same estimate recomputed under replicate weight r. The leading factor
of 4 comes from `1 / k²` (with `k = 0.5`). `R = 80` is the default;
truncating to fewer replicates inflates the SE estimate and is
intended for smoke testing only.

**Implementation:** `tax_modeler.poverty.impact.compute_poverty_impact_with_se`
wraps the headline function. After computing the main estimate, it
swaps the SPM-unit `weight` column for each `weight_r{01..80}` in
turn and re-runs the `_aggregate` step (NOT the upstream credit
calculation, which is weight-invariant). The function emits
`<col>_se` siblings for every persons-lifted, poverty-rate, and
gap-closed cell on every aggregated DataFrame.

**Calibration interaction.** This codebase calibrates *tax-unit*
weights via IPF in `tax_modeler.calibration.*` to match DOTAX
filer-status totals. The SPM aggregator
(`aggregate_to_spm_units`) discards the calibrated tax-unit weight
and uses raw household `WGTP` for the SPM-unit weight, because
person-level poverty accounting needs a person-grained denominator
that matches the ACS published count (~1.3M for Hawaii). The
replicate weights ride the same raw-WGTP path; *no per-replicate
IPF re-run is performed*. Doing so would multiply runtime by 80×
without changing the SE materially: sampling variance dominates the
residual calibration variance by roughly an order of magnitude on
state-level poverty counts. This is the standard post-calibration
approximation used by ACS estimation guides.

**CLI:** pass `--replicate-weights` (and optionally `--n-replicates N`)
to `scripts/poverty_impact_report.py`. The flag is OFF by default
because the supporting columns (`WGTP1`..`WGTP80`) are not present in
the bundled synthetic fixture; production PUMS parquet/CSV does ship
them.

### Source 2 — Parameter sensitivity sweep

Four parameters in the poverty-impact pipeline are bare scalars in
the codebase but are empirically uncertain:

| Parameter | Default | Low / Mid / High | Empirical anchor |
|---|---|---|---|
| `hi_ctc_takeup_rate` | 0.70 | 0.65 / 0.70 / 0.75 | **Hawaii-empirical**: federal-EITC take-up in HI 2022 = 84,010 admin claims / 120,535 PUMS-eligible filers ≈ 0.697 (±5pp judgment band). See `tax_modeler.calibration.hi_eitc_takeup_estimate` for the SDR-bootstrap variant. |
| `hi_eitc_100pct_takeup_rate` | 0.98 | 0.95 / 0.98 / 1.00 | **Conditional take-up given existing HI EITC claim**. HI EITC auto-computes as fixed percentage of federal on Form N-15 (Act 209, 2023); conditional rate is near-perfect (0.02 captures rare elect-out cases). Composite friction (non-claimers of federal EITC) is upstream in `hi_eitc_amount`. |
| `arpa_ctc_takeup_rate` | 0.94 | 0.92 / 0.94 / 0.95 | **Empirical mean** of Karpman et al. (Urban Institute, 2022) and US Treasury reports on actual ARPA monthly-CTC participation among eligible families. Band: 0.92–0.95. |
| `eitc_poverty_alpha` | 0.5 | 0.3 / 0.5 / 0.7 | Half-elasticity convention from Tax Policy Center / ITEP state models. **Empirical fit attempted (2026-Q2)**: 5-year Hawaii panel (IRS SOI Historic Table 2 TY 2015–2022 + ACS 1-year Honolulu B19013/S1701) yielded α ≈ 0.71 with RMSE 0.03, but n=2 year-pairs after excluding 2019→20 (COVID), 2020→21 (ARPA up), 2021→22 (ARPA expiration). Per-pair α swings −0.43 to 1.01, so the OLS fit is statistically unidentified. Keep 0.5 as production default; 0.71 lies inside the 0.3/0.5/0.7 sweep band so the existing band brackets the empirical range. Module shipped as infrastructure at `tax_modeler.calibration.eitc_alpha_calibration` — re-run after IRS publishes TY 2023 or when pre-2015 IRS files become accessible. |

The per-child HI CTC dollar amount (formerly a swept parameter at
$300 / $650 / $1000) is no longer in the sweep — it is a
**policy-design counterfactual**, not parameter uncertainty.
Different bills ask different things. The pipeline now ships three
named scenarios (`hi_ctc_300`, `hi_ctc_650`, `hi_ctc_1000`) in the
default scenario menu; each gets its own SE and parameter range.
Ad-hoc dollar amounts for amendment analysis are supported via the
scenario name (e.g. `hi_ctc_500`).

#### Policy-choice axes vs parameter uncertainty

We distinguish two stakeholder questions:

* "Given SB 3125 specifies $650, what's the estimated poverty effect
  ± behavioral uncertainty?" — the **parameter MOE**, surfaced in
  `<col>_param_min` / `<col>_param_max` columns.
* "What if SB 3125 had specified $300 instead?" — a **scenario**,
  surfaced as a separate named row (`persons_lifted_hi_ctc_300`).

Mixing the two conflates "uncertainty about the bill as drafted"
with "uncertainty about which bill to draft." Splitting them by
construction tightens the parameter MOE on `persons_lifted_hi_ctc_650`
from ±37 % (when the per-child amount was in the sweep) to ~±10 %.

`scripts/poverty_impact_sweep.py` runs all four parameters in a
one-at-a-time (OAT) sweep around the mid point by default (9 unique
cells after de-duplication of the shared mid-point), or as a full
factorial of 3^4 = 81 cells with `--factorial`. Each cell invokes
`poverty_impact_report.py` in-process via its `main(argv)` entry
point, collates the state-level `by_state.csv` into `summary.csv`,
and emits `param_ranges.csv` (per-column min / max / median across
all cells).

**Merging back into the headline report:** pass `--merge-sweep
<path>/param_ranges.csv` to `poverty_impact_report.py`. Every
`persons_lifted_*` and `poverty_rate_*` column gains three siblings:
`<col>_param_min`, `<col>_param_max`, `<col>_param_median`. These are
populated only for state-level rows; other geographies receive
NaN-filled siblings for schema parity until per-district sweeps land.

### Why separate columns instead of a combined band

Quadrature combination (`SE_total² = SE_sampling² + SE_param²`)
implicitly assumes independence of the two sources, which is not
true here: a take-up rate move correlates with the credit-amount
columns that the replicate-weighted aggregation reads. More
importantly, **decomposition is informative**. A reviewer can see
whether the band around "persons lifted by EITC" is dominated by
sampling (n=80 is a small sub-state count) or by take-up
uncertainty (a policy assumption that future enactment could
verify). A combined headline band would hide that.

A reader who wants a single conservative envelope can take

    headline ± 1.65 · √(_se² + ((_param_max − _param_min) / 3.29)²)

with the caveat that this treats the parameter range as a 90 %
band and assumes the two sources are independent — a defensible
back-of-envelope but not a rigorous CI.

### Federal-tax fallback removal (2026-Q2 hardening)

`compute_spm_resources` previously used a 10 % flat effective rate
on positive money income as a federal-tax stand-in when no
`federal_tax_liability` column was supplied. For SPM-eligible
filers (low income, standard deduction wipes out taxable income),
the true federal liability is typically $0 — the 10 % fallback was
subtracting $1,000–$3,000 of phantom federal tax per return,
biasing the modeled Hawaiʻi SPM poverty rate ~4–6 pp upward.

The fallback is now wired to the real federal-tax calculator at
`tax_modeler.liability.federal.compute_federal_income_tax_for_units`
(TY 2022–2025 brackets + standard deduction per IRS Rev. Procs.).
The 10 % flat path remains only as a last resort when neither the
`federal_tax_liability` column nor a `tax_year` argument is
supplied; it now logs a warning so direct callers know to upgrade.
The `SPMResourceMeta.federal_tax_source` field reports which path
fired (`"column" | "computed" | "fallback_rate" | "zero"`).

### What's still uncalibrated

* **Behavioral response** — `compute_poverty_impact` is a static
  counterfactual. EITC repeal would reduce single-mother LFP by
  2–7 pp per Eissa-Liebman / Meyer-Rosenbaum; no elasticity
  knob is exposed yet. This bounds removal-scenario estimates
  *above* and expansion-scenario estimates *below*.
* **`eitc_poverty_alpha` empirical calibration** — currently a
  literature half-elasticity (0.5); backtest spec documented in
  `adjustments/eitc_poverty_scaling.py`. Deferred pending a
  multi-year ACS S1701 × IRS SOI panel build.
* **HI Renters Credit take-up** — `compute_hi_renters_for_units`
  uses an unanchored 0.30 default. Not currently invoked in the
  poverty-impact pipeline (the `hi_renters_amount` column is not
  populated upstream of `compute_spm_resources` in the report);
  affects only revenue-forecast outputs. TODO to anchor empirically
  is in the credit module's docstring.
* **Replicate-weight propagation through tax-unit constructor**
  (2026-Q2 closed) — `weight_r01..weight_r80` now ride from PUMS
  households → persons → tax units → SPM aggregator via the
  `include_replicate_weights: bool = True` flag on
  `TaxUnitConstructor`. Each replicate inherits the same hybrid-weight
  + per-filing-status calibration formula as the main `weight` column.
  This unlocks the SDR bootstrap on `τ_HI_EITC` at the tax-unit level
  (the `estimate_hi_eitc_takeup` function now returns a real SE
  instead of the 2-replicate judgment band when run post-constructor).
* **HI Renters Credit** (2026-Q2 wired) — `_apply_hi_renters` now runs
  in `scripts/poverty_impact_report.py` after `_apply_hi_eitc`, so
  `hi_renters_amount` flows into `compute_spm_resources` as a positive
  SPM resource. On real Hawaii PUMS 2024, ~15K tax units qualify with
  aggregate disbursement ~$10.7M; SPM rate shifts by <0.1 pp. The
  underlying take-up rate stays at the 0.30 placeholder pending a
  DOTAX caseload pull (documented TODO in `credits/hi_renters.py`).
* **District-level uncertainty** — within-PUMA HD/SD assignment
  is a deterministic SERIALNO hash. The `--rake-to-irs-zip`
  flag is wired but the supporting IRS SOI ZIP + crosswalk data
  is not bundled, so district point estimates carry roughly
  ±20% uncertainty that the SDR SE alone does not capture.
* **SPM threshold MRI / tenure factors** — fixed constants per
  Renwick (2015) / Burns-Fox (2021); not swept.

### CLI quick reference

    # SDR SE only
    python scripts/poverty_impact_report.py --tax-year 2024 \
        --replicate-weights --n-replicates 80 \
        --out reports/poverty_impact_2024_with_se/

    # Sensitivity sweep (OAT)
    python scripts/poverty_impact_sweep.py --tax-year 2024 \
        --out reports/poverty_sweep_2024/

    # Both together: per-cell SDR SE + cross-cell param ranges
    python scripts/poverty_impact_sweep.py --tax-year 2024 \
        --replicate-weights \
        --out reports/poverty_sweep_2024/
    python scripts/poverty_impact_report.py --tax-year 2024 \
        --replicate-weights \
        --merge-sweep reports/poverty_sweep_2024/param_ranges.csv \
        --out reports/poverty_impact_2024_full/

### References

* US Census Bureau (2024). *PUMS Accuracy of the Data (2018–2022)*,
  §3 (Variance Estimation via Replicate Weights). The
  successive-difference replicate-weight construction and the
  `(4 / R) · Σ(θ_r − θ_0)²` formula are documented there.
* Fay, R.E. (1989). *Theory and application of replicate weighting
  for variance calculations.* Proc. ASA Survey Research Methods.
* Eissa, N., & Liebman, J. (1996). *Labor Supply Response to the EITC.*
  Quarterly Journal of Economics 111(2). (Source of the LFP
  elasticity caveat above.)

---

## Market signals (July 2026)

The `census_forecaster.markets` subpackage tracks a pre-registered
universe of 12 exchange-traded securities and screens them as *leading
indicators* for the Hawaii series the forecaster consumes. It is NOT
part of the Housing-Affordability-Tracker cherry-pick.

### Data

Monthly adjusted closes, fetched keylessly (`markets/client.py`):
yfinance when installed → Yahoo v8 chart API via stdlib → Stooq CSV
(Stooq now serves a JS proof-of-work challenge to headless clients, so
the stdlib Yahoo path is the effective fallback). The incomplete
current calendar month is dropped at fetch time. Bundled panels live in
`data/markets/` (schema mirrors `data/bls_panel/`); the refresh script
(`scripts/refresh_market_panel.py`) runs in the monthly `refresh-data`
workflow with keep-last-committed per-ticker failure tolerance.

The universe (`markets/universe.py`) is three tiers — broad (SPY, QQQ,
VTI), sector (JETS, XLRE, VNQ, XLF, XLE), Hawaii-listed (BOH, FHB, HE,
MATX) — each ticker carrying an explicit *hypothesis* naming the
economic mechanism and the ACS cells it plausibly leads. ALEX
(Alexander & Baldwin, the natural B25077 candidate) was taken private
2026-03-12 and free sources purge delisted histories.

### Ticker trend forecasts (`markets/trend.py`)

Tracker-grade context only. Point = damped drift via
`bls.projection.project_forward_full` at the repo's monthly φ=0.92
(§2.3.1 cadence rule — never a new φ). The honest content is the band:
`point × exp(±z·σ_m·√h)` with σ_m the trailing-36-month return SD and
`z` the **empirical** 90% quantile of walk-forward standardised
absolute errors (`calibrate_band_multiplier`) — the direct empirical
analogue of a z-score, per the repo's calibrated-not-analytical PI
discipline. Calibrated multipliers on the real universe land at
1.8–2.6 (vs the Gaussian 1.645): equity returns are fat-tailed and the
calibration prices that in. Equities are near-random-walks; these
forecasts are context for the tracker report, never trading advice and
never census-forecaster inputs.

### Causal screen (`markets/screen.py`)

**Granger causality is not causation** — a pass means the ticker's
past adds predictive content for the target beyond the target's own
past. Confounders survive this screen by design; the Phase-3
forecaster ablation (walk-forward RMSE + CI90 coverage ∈ [85%, 95%])
is the final arbiter of whether a signal touches any forecast.

Design, in the order the guards bind:

1. **Pre-registration.** Only the ticker→target pairs in
   `HYPOTHESIS_PAIRS` (16 pairs derived from the universe's hypothesis
   map) are tested. This caps the multiple-testing burden before any
   data is seen.
2. **Monthly cadence for inference.** Targets: HI/US unemployment
   (levels, first-differenced), Honolulu ZHVI/ZORI (log-differenced),
   Honolulu CPI (bimonthly; differences computed against the nearest
   previous print within 3 months and scaled to per-month rates).
   Annual pairs (ACS anchors, n≈10–15) are reported as descriptive
   lead-lag correlations only, labelled "no test".
3. **Test.** OLS restricted-vs-unrestricted Granger F
   (numpy lstsq + `scipy.stats.f`; statsmodels is not a dependency) on
   monthly log-returns at lags 3/6/12, refused entirely below
   8 observations per parameter. 12-month momentum is screened by
   cross-correlation only — its overlapping windows induce serial
   correlation that invalidates the F-test's residual assumptions.
4. **FDR control.** Benjamini–Hochberg at q=0.10 across ALL Granger
   tests actually run in a screen invocation.
5. **Regime sensitivity.** The screen re-runs with 2020 excluded;
   `selected_signals.json` records `robust_to_2020_exclusion` and the
   review doc shows both tables. A signal that exists only because of
   the COVID crash is a one-event artifact.

Outputs: `backtests/results/market_signal_screen_<date>.md` (human
review is a gate before Phase-3 integration) and
`data/markets/selected_signals.json` (machine-readable survivors).

First run (2026-07-14, unemployment targets pending the CI BLS key):
16 tests, 9 BH passes, 8 robust to 2020 exclusion. The survivors are
the economically prior-backed mechanisms — XLE→Honolulu CPI (imported
energy), MATX→CPI (shipping), XLRE/VNQ→ZHVI/ZORI (forward rent/value
pricing) — which is what a screen behaving honestly should find.

### Known limitations

- Granger ≠ causation (worth stating twice).
- Ticker inceptions truncate samples: JETS/XLRE 2015, FHB 2016,
  ZORI 2015.
- Annual-cadence relationships are structurally untestable at n≈10–15;
  anything cited from the annual table is descriptive.
- National/sector tickers are geoid-constant: in the pooled ML panel
  they act as year-effects and are near-collinear with
  `anchor_year_norm`. The Phase-3 ablation must check permutation
  importance before trusting them.
- **The genuine Urban Hawaii CPI (bimonthly) cannot be a Granger
  target** — the all-lags-present rule needs monthly cadence. The old
  XLE→CPI screen passes existed only because the mislabelled target was
  secretly monthly Los Angeles (see the July 2026 correction). CPI-
  directed hypotheses are xcorr-descriptive. **Resolved for the energy
  channel 2026-08-05** — see "EIA Hawaii electricity" below.

### EIA Hawaii electricity — screen target correction (2026-08-05)

Two defects, one fix.

**1. The screen was still on the Los Angeles series.** The 2026-07-27
identity audit corrected `_HONOLULU_BLS_SERIES_ID` on the tax_modeler
income path, but `markets/screen.py`'s `MONTHLY_TARGETS["HONOLULU_CPI"]`
was missed and still read `CUURS49ASA0`. Every historical
`XLE → HONOLULU_CPI` / `MATX → HONOLULU_CPI` pass therefore described
**LA** inflation, not Hawaii's. Now `CUURS49FSA0` (genuine Urban
Hawaii). Being bimonthly it yields `None` from `granger_f_test` rather
than a p-value — the honest outcome the limitation above predicted, and
the reason those four passes disappear.

**2. The missing monthly Hawaii price proxy now exists.** EIA's
state-level retail electricity price is genuinely monthly, genuinely
Hawaii, 2001-01 → present at ~3-month lag
(`scripts/refresh_eia_hawaii.py`; free key via `$EIA_API_KEY` or
`~/.eia_api_key`, never committed; merged additively into
`macro_monthly.json`). Hawaii generates most of its power from imported
oil, so this measures the imported-energy → local-price channel
**directly** instead of inferring it from an energy-sector equity price.
EIA's gasoline product (`petroleum/pri/gnd`) covers 29 metro/PADD areas
and excludes Hawaii, so electricity is the only Hawaii-specific monthly
price this source offers.

**Net screen effect** (63 → 68 candidates; robust survivors 11 → 11):

| | pair | evidence |
|---|---|---|
| **dropped** | XLE / MATX → HONOLULU_CPI (lags 3, 6) | were LA-based |
| **gained** | XLE → HI_ELECTRICITY (lags 3, 6, 12) | p≈6e-14, 3-mo lead, r=+0.402 |
| **gained** | MATX → HI_ELECTRICITY (lag 3) | p=1.0e-03, 4-mo lead, r=+0.190 |
| **unchanged** | the 7 rate / REIT / JOLTS survivors | untouched |

The replacement evidence is *stronger* than what it replaced (XLE's
xcorr r rose from +0.209 against LA-CPI to +0.402 against Hawaii
electricity), and all three `mkt_*` feature channels still emit — but
they now rest on Hawaii data. Downstream feature values change, so the
ML ablation should be re-run before leaning on the energy channel's
permutation-importance numbers.

**Registered null: `HE → HI_ELECTRICITY` gets zero BH passes.** Kept
registered because the null is informative. Hawaiian Electric's equity
price tracks regulatory and wildfire-liability risk (the 2023 Maui-fire
crash) while its tariff is PUC-set from fuel costs — different drivers,
so the miss is economically coherent and does *not* impugn the target.
The channel's evidence rests on XLE.

### HTA visitor arrivals — testing the JETS mechanism (2026-08-05)

The `JETS` hypothesis was *"airline equity prices embed forward bookings
→ Hawaii tourism employment follows visitor arrivals"*, but arrivals
themselves were never in the panel, so only the endpoints could be
tested and the mechanism had to be assumed. HTA's historical workbook
(Table 6, *Visitor Arrivals by Island and Month*) supplies **420 months,
1990-2024**, and its island rows map onto counties — `O'AHU`→15003,
`MAUI CTY`→15009, `KAUA'I`→15007, `HAWAI'I ISLAND`→15001 — so these are
genuinely county-level monthly indicators, unlike the geoid-constant
market/national channels. `scripts/refresh_hta_visitors.py`.

Both legs were pre-registered and run. **The chain splits:**

| leg | pair | result |
|---|---|---|
| 1 | JETS → HI_VISITORS | p=1.0e-08, r=+0.492, **not 2020-robust** |
| 2 | HI_VISITORS_ARRIVALS → HI_UNEMPLOYMENT (lag 3) | p=1.2e-04, r=−0.883, **robust** |

So the *second* leg is real — arrivals carry predictive content for
local slack beyond unemployment's own past, at a strikingly strong
correlation — while the *first* leg is another COVID coincidence:
airline equities and Hawaii arrivals both collapsed in 2020, and
excluding it kills the relationship. JETS therefore earns **no new
feature channel**, and the `mkt_*` channels are unchanged. Robust
survivors 11 → 12 (the arrivals→unemployment pair).

Read leg 2 carefully before promoting it: `best_xcorr_lead = 0` months,
i.e. arrivals and unemployment move *contemporaneously* — tourism is the
Hawaii labour market rather than a precursor to it — so the lag-3
Granger pass is predictive content, not a long runway. LAUS is also
itself partly modelled and annually benchmarked, so some of the
correlation is definitional. Ablation-gate before it touches a forecast.

**Source limitations.** No API (UHERO's warehouse has one but needs a
Bearer token, breaking keyless discipline); files sit behind rotating
`/media/<id>/` URLs, so the fetcher scrapes the listing page rather than
hardcoding a link. The workbook ends at 2024 — the current-year file is
a differently-shaped workbook whose 2026 sheet had genuine mid-year gaps
when checked (Jan/Feb/May present, Mar/Apr absent), so it is
deliberately not merged; a clean backbone beats a ragged edge. Revised
vintages publish as strings (`'2006*'`, `'2010R'`, `'2014R'`, `'2017R'`)
and a naive numeric header check silently drops those four years — the
parser handles both forms and a test pins it.

### Current-indicator intake batch (2026-08-05, round 2)

Three sources added after a fetchability survey (every candidate
curl-verified before integration; ruled out this round: Census HPS
(discontinued), state JOLTS (~7-month effective lag), Indeed Hiring Lab
(starts 2020-02 — the 2020-exclusion gate would gut it), Redfin county
tracker (241 MB, deferred), BFS/PEP/IRS-SOI (slower than what's wired).

**1. DBEDT MEI county workbooks** (`refresh_dbedt_mei.py`, keyless,
dated links discovered from the listing page). ~52 monthly series per
county, 1990-01 → current month at ~5-week lag. Taken now:
`DBEDT_ARRIVALS_*` and `DBEDT_PERMITS_*` (monthly county building
permits — the BPS ML channel is annual; bundled for a future cadence
upgrade, not screen-wired: "permits → prices" has no single clean
directional hypothesis). **DBEDT arrivals agree with HTA to within
rounding on all 420 overlap months (max rel diff 0.0004%)**, so the
screen's `HI_VISITORS` target and `HI_VISITORS_ARRIVALS` predictor were
repointed to the DBEDT series (current through 2026-06 vs HTA's 2024
wall); `HTA_VISITORS_*` stays bundled as the archival cross-check.
Maui arrivals genuinely stop at 2026-01 in the source (publication
gap); blanks are skipped, not zeroed.

**2. DOL ETA-539 weekly UI initial claims**
(`refresh_ui_claims.py`, keyless all-states CSV, ~13 MB).
`DOL_HI_INITIAL_CLAIMS` = calendar-month **mean** of weekly initial
claims (mean, not sum, so 4- and 5-week months compare), 1985-03 →
current at ~11-day weekly lag — the fastest-moving labour series in the
panel, and administrative counts rather than survey estimates. Sanity
signatures verified: 2020-04 mean 24,745 (COVID), 2023-08 3,613 (Maui
fires), normal ≈1,000-1,300.

**3. BLS CES Hawaii payrolls** (`SMS15000000000000001`), added to
`refresh_market_panel`'s existing keyless BLS fetch alongside LAUS.
Establishment survey — the hiring-side complement to LAUS's household
survey. 1990-01 → 2026-06.

**Screen effect** (73 → 79 candidates; robust survivors 12 → 12, one
swap):

| | pair | evidence |
|---|---|---|
| **gained** | HI_UI_CLAIMS → HI_UNEMPLOYMENT (lag 6) | **p=3.2e-64, r=+0.821, 1-month lead**, robust — the strongest predictive relationship in the screen, with a genuine lead (unlike arrivals' 0-month) |
| **lost** | HI_VISITORS_ARRIVALS → HI_UNEMPLOYMENT (lag 3) | extending the predictor 18 months (HTA→DBEDT) dropped its 2020-exclusion pass — the borderline robustness the same-day caveat anticipated; still BH-passes, no longer robust |
| **null** | HI_PAYROLLS → HI_UNEMPLOYMENT | zero BH passes at any lag. Coherent: CES and LAUS measure the same labour market from two sides in the same month — co-movement, not precedence. Kept registered. |

Claims' r=+0.821 deserves the same caution as arrivals: initial claims
are mechanically upstream of the unemployment *stock*, so part of the
relationship is definitional. The 1-month xcorr lead and the e-64
Granger p on 480+ aligned months are still the best nowcasting evidence
in the panel — but ablation-gate before any forecast use, per standing
rule. *(Gated and promoted 2026-08-22 via an all-states rebuild of the
channel — see §"State-level UI claims channel". The gate passes; the
forecast gain is a wash.)*

### Ablation re-verified against corrected data (2026-08-05)

`mkt_energy_mom`'s bundled values are byte-identical before and after
the CPI-target fix (it is XLE's own price momentum; what changed was
the target it validates against, not the momentum series itself), so
the prior ship gate was numerically still valid. Re-ran anyway
(`compare_market_ablation.py --skip-anchor`,
`backtests/results/market_ml_ablation_2026-08-05.md`) for a second
reason: the 2026-07-14 report's permutation importances were flagged
mislabelled by a column-ordering bug fixed the *next day*, so its
RMSE/coverage table was trustworthy but its importances never were.

**Verdict: GATE PASSED again** — no RMSE regression >2% on any of the
16 indicators (ensemble-level wash, as before), CI90 coverage stays in
[85%, 95%] throughout.

**The corrected importances change the internal story, though.**
Comparing 07-14 (buggy) → 08-05 (corrected) is not noise — it is
systematic and indicator-specific:

| indicator | direction | typical shrink/growth |
|---|---|---|
| B19013 (income) | **shrank** | to 8-43% of the old value |
| B25077 (home value) | **shrank** | to 11-18% of the old value |
| S2301 (unemployment) | **grew** | to 124-252% of the old value |

The July disposition note's headline claim — *"mkt_reit_mom_lag1 on
B25077: +0.073, the VNQ 12-month Granger lead reproduced as a tree
feature"* — is retracted: the corrected value is **+0.0118**, a real
6x overstatement. The actual strongest signal in the corrected table is
**`mkt_shipping_mom` on S2301 (unemployment): +0.0352** — MATX shipping
costs, not VNQ real estate, and unemployment, not home value. This is
the mechanistically coherent story: shipping/energy costs bite an
import-dependent, tourism-exposed labour market (matching the
JETS/MATX/XLE causal-screen hypotheses above), not median home price.

Disposition stands: **mkt_\* ML features remain SHIPPED**, opt-in
(`use_ml=False` default unchanged), now on both a verified-current data
basis and a verified-correct importance reading.

**HI_UI_CLAIMS is not part of this gate and cannot be**: it exists only
in the causal screen and `macro_monthly.json`, absent from
`NATIONAL_SERIES`, `COUNTY_SERIES`, and market `CHANNELS` — the three
registries `ml_features.py` actually reads. It has passed statistical
discovery but was never wired to a feature. No ablation is possible
until that promotion happens; this is intentional, not an oversight.

### macro_monthly.json write semantics — data-loss bug (2026-08-06)

**`refresh_market_panel` was silently deleting other scripts' series.**
Six scripts now contribute to `data/markets/macro_monthly.json`, but
`build_macro_payload` returned a payload containing only the series
*that script* fetched, and the write was a full overwrite. Because
market-panel runs FIRST in the CI workflow, every downstream script was
re-adding its series to an emptied file — so any series whose downstream
fetch failed that run was **removed from committed history**, not merely
left stale.

Observed, not hypothetical: the 2026-08-06 scheduled run hit transient
FRED read-timeouts on `MORTGAGE30US` and `DGS10`. Their handler logs
`keeping previous` — true of its own merge step, false in effect, since
the upstream write had already deleted the previous values. The loss
removed three 2020-robust rate → home-value findings
(`US_MORTGAGE30 → HONOLULU_ZHVI` at lags 6/12, `US_DGS10` at lag 12)
from the causal screen, which initially looked like a statistical
effect of adding new candidates and was not.

`build_macro_payload` now takes `existing_path` and merges: this run's
series win on overlap, everything else is preserved, and the standard
limitation notes are added once rather than duplicated. Pinned by
`tests/census_forecaster/test_macro_monthly_merge.py`. The two lost
series were restored from `12f8415` and re-fetched.

Not affected: `RRVRUSQ156N` / `RHORUSQ156N` (HVS vacancy and
homeownership) are **quarterly**, and `_SCREEN_CADENCES` deliberately
excludes quarterly from `macro_monthly.json` — they live in
`national_macro.json` as annual feature-channel inputs and were never
part of this file.

### Hawaii indicator intake, round 3 (2026-08-06)

**DBEDT MEI expanded from 2 series to 13 per geography** (65 series
across statewide + 4 counties). The workbook carries ~52 columns and
only arrivals/permits were being read. Added: visitor days, visitor
expenditures, accommodation and food-service payrolls, construction
payrolls, single-family and condo resale counts / median prices /
inventory. Tax-revenue rows were deliberately NOT taken — DOTAX's own
monthly reports cover collections at finer granularity with explicit
revision tracking, and two sources for one quantity invites divergence.

*Parser hazard fixed en route:* MEI reuses one label for two different
series — `Inventory (aver. units on market)` appears at both the
single-family and condo blocks. `SERIES` keys are now
`(fragment, occurrence)` and the column scan iterates **unique**
fragments; iterating raw dict keys double-counted matches
(`[42, 42, 45, 45]`) and collapsed both occurrences onto the first
column, which produced identical SF and condo inventory values.

**Three new sources** (`refresh_hawaii_indicators.py`, all keyless):

| series | what | span |
|---|---|---|
| `HIPHCI` | Philadelphia Fed Hawaii coincident index — the only composite in the panel | 1979-01 → 2026-06 |
| `HIBPPRIV` | Hawaii housing UNITS authorized (counts; DBEDT permits are dollar VALUE) | 1988-01 → 2026-06 |
| `BTS_HNL_PASSENGERS` / `_DEPARTURES` | T-100 origin-airport enplanements/departures from HNL | 2014-01 → 2026-04 |

`HONO115BPPRIV` (Honolulu MSA permits) is the better geographic match
and was the first choice, but it is **discontinued — ends 2013-12**, as
does its SA twin; the statewide series is the only current one. The
Socrata endpoint returns an empty body to requests without a
browser-like User-Agent.

**Screen effect: 79 → 90 tests, robust survivors unchanged at 12.** No
new robust findings; `HI_VISITOR_SPEND → HI_UNEMPLOYMENT` passes BH at
all three lags (p=1.5e-13 at lag 12) but dies on 2020 exclusion, and
`HI_SF_SALES → HONOLULU_ZHVI` passes weakly at lag 12 (p=0.020,
r=+0.087). `HI_AIR_PAX → HI_VISITORS` and
`HI_PERMIT_UNITS → HONOLULU_ZHVI` get zero passes. Informative nulls,
and the data is bundled regardless of screen outcome.

**Two circular pairings were tried and withdrawn.** `HIPHCI` is built
from four inputs *including the state unemployment rate*, so
`HI_COINCIDENT → HI_UNEMPLOYMENT` asks whether a number predicts its own
ingredient; a trial run returned r=−0.934 at lag 0 with a 2020-robust
flag — spectacular-looking and near-meaningless. `HI_JOBS_ACCOM →
HI_UNEMPLOYMENT` has the same defect more mildly (accommodation payrolls
are a component of the employment level the rate is computed against).
Both are documented as deliberately-unregistered in `HYPOTHESIS_PAIRS`
and pinned by tests. If HIPHCI is ever screened, the target must sit
outside its construction.

### Listing-side intake, round 4 (2026-08-06)

**Redfin was the first choice and was rejected: the feed is frozen.**
Its county tracker reaches back to 2012 and carries `months_of_supply`
and `avg_sale_to_list`, which nothing else here has. But every export in
the bucket — national, metro, county, state, zip — returns an identical
`Last-Modified` of 2026-06-02 with data ending 2026-05: no update in
over two months, verified 2026-08-06. A source that has stopped
advancing cannot do the nowcast job this intake exists for. Its Honolulu
single-family sale counts match DBEDT's to within ~2% where they overlap
(264/259, 279/276, 262/261 for recent months), so it is a sound archival
cross-check if the 2012-2016 backfill is ever wanted — it is just not
worth a 241 MB monthly download to re-fetch a series that no longer
moves. Recorded here so the next person does not re-research it.

**Realtor.com replaced it** (`refresh_realtor_inventory.py`, 36 series:
9 metrics × 4 counties, monthly 2016-07 →, ~1-month lag). Refreshed
2026-08-04 with 2026-07 data — currently the freshest housing series in
the panel, ahead of DBEDT's 2026-06. Keyless. Kalawao (15005, pop ~80)
is unpublished; the other four counties are 121/121 complete on every
metric taken.

Why it matters: every housing series already here — DBEDT resale counts
and medians, ZHVI, permit units — is recorded at or after closing, which
in Hawaii is 30-60 days after the price was agreed. These are measured
while the home is still listed, so they are the panel's only candidates
for genuinely *leading* prices rather than re-describing them.

**A model-free price target was added alongside them.**
`HONOLULU_SF_MEDIAN` (`DBEDT_SF_MEDIAN_HONOLULU`) is the median of
prices actually recorded on closed transactions. It exists because
`HONOLULU_ZHVI` is computed from Zestimates, and Zillow documents
on-market data — list price and days on market among it — as Zestimate
inputs. Screening listing-derived predictors against ZHVI therefore
risks the HIPHCI defect in diluted form. The dilution is real (ZHVI
values every home, ~97% of which are unlisted in a given month) and this
is nothing like HIPHCI's arithmetic circularity — but "diluted" is not
"absent", so the uncontaminated control was built before the screen ran.

**The control immediately earned its keep.**

| pair | full run | 2020 excluded | robust |
|---|---|---|---|
| `HI_PRICE_CUTS → HONOLULU_SF_MEDIAN` | p=0.0085 (lag 3), 0.0017 (lag 6) | p=0.0168 (lag 3) | **yes** |
| `HI_DOM → HONOLULU_ZHVI` | p=0.0163 (lag 3), 0.0317 (lag 6) | p=0.0056 (lag 3) | **yes, but see below** |
| `HI_DOM → HONOLULU_SF_MEDIAN` | p=0.219 | p=0.567 | no |
| `HI_PENDING_RATIO → HONOLULU_SF_MEDIAN` | p=0.333 | p=0.734 | no |

`HI_PRICE_CUTS` is a genuine find, and the cross-correlation profile
says so independently of the F-test: against recorded sale prices it is
*positive* at lead 0 (+0.195), crosses over, and peaks negative at lead
3 (−0.272). No contemporaneous relationship, a clean peak at exactly the
lag the mechanism predicts — a listing cut today goes pending in ~30-60
days and closes ~30 after that. The share of sellers cutting their
asking price is measured the day the decision is made; a closed-sale
index only learns about it once the discounted sale settles.

`HI_DOM → HONOLULU_ZHVI` is treated as an **artifact of ZHVI's
construction**, not evidence about housing. It clears BH on ZHVI while
the same predictor against recorded sale prices does not (p=0.567), and
the profiles explain why: against `SF_MEDIAN`, DOM peaks at lead 0
(−0.290) and collapses to +0.021 by lead 1 — coincident with the market,
no predictive content — whereas against ZHVI it peaks at lead 1 and
decays smoothly, which is what a shared input smeared through a smoothed
index looks like. *"`SF_MEDIAN` is merely noisier" does not explain the
asymmetry*: `HI_PRICE_CUTS` cleared BH against that same noisy target,
so it demonstrably has power to detect an effect of this size. The pair
stays registered because the contrast is the informative part and
deleting it would hide the finding, but it carries an explicit
do-not-promote note: it must not become a `signals.py` feature channel
without first reproducing on a model-free target. It cannot reach the
forecast today regardless — `CHANNELS` is keyed on tickers only.

**Deliberately not screened**, all documented inline: `RDC_LIST_PRICE_*`
and `RDC_LIST_PPSF_*` (asking price is an acknowledged Zestimate input,
and against a median *sale* price it is near-tautological — two
measurements of one number separated by the negotiating discount);
`RDC_PRICE_HIKES_*` (`price_increased_share` is exactly 0.0 in 40
county-months, which `log_diff` drops, leaving a gapped series — and in
a softening market the informative direction is cuts); `RDC_ACTIVE_*`,
`RDC_NEW_LISTINGS_*`, `RDC_PENDING_*` levels (multiple-testing budget
only — `RDC_PENDING_RATIO` already carries supply/demand balance
scale-free). All are bundled in `macro_monthly.json` for descriptive use.

### Fuel cost and business formation — two nulls (2026-08-07)

Both added as one-line entries to the keyless FRED fetcher, deliberately
*not* to `refresh_national_macro.py`: that script is driven by
`acs.ml_features.NATIONAL_SERIES`, so registering there would inject an
ungated channel into the ACS feature panel.

**Source audit first.** Hawaii-specific jet fuel is dead — EIA's
state-level refiner price has 34 non-null observations ever, ending
2013-09, and the entire refiner-survey jet fuel family (every state plus
the national total) stops at 2022-03. The live series is the US Gulf
Coast spot benchmark, `MJFUELUSGULF`, monthly 1990-04 →. Identity was
verified **by value, not by title**: all six most recent months match
EIA's `EER_EPJK_PF4_RGC_DPG` exactly. That check is not ceremonial —
this repo shipped `CUURS49ASA0` as "Honolulu CPI" for months when it is
Los Angeles.

Hawaii DCCA publishes business registrations **only as HTML search
forms** — no CSV, no API, no series (`opendata.hawaii.gov` lists 6 DCCA
entries, all search UIs). Census BFS via `BABATOTALSAHI` is therefore
the only route, and it is monthly/SA/2004-07 → current, contradicting an
earlier note in this repo that BFS was too lagged to use.

**Both hypotheses failed, in the same way.**

| pair | full sample | 2020 excluded |
|---|---|---|
| `US_JETFUEL → HI_VISITORS` | p=0.0103 / 0.0138 / 0.0009 (all 3 lags) | p=0.244 / 0.219 / 0.537 (all fail) |
| `HI_BIZ_APPS → HI_UNEMPLOYMENT` | p=0.0045 / 0.0334 / 0.0186 (all 3 lags) | p=0.421 / 0.540 / 0.608 (all fail) |

Textbook one-event artifacts: 2020 collapsed fuel prices, arrivals and
business formation while spiking unemployment, so everything correlates
with everything. This is precisely what the 2020-exclusion gate exists
to catch, and the robust count stayed at **14, unchanged**.

**The fuel pair additionally has the wrong sign**, which is the more
useful lesson. The mechanism predicts negative — fuel up, long-haul
capacity cut, fewer arrivals. The data give **+0.204** at lead 0 on the
full sample and **+0.179** at lead 9 without 2020. Positive is what the
global demand cycle produces (a strong world economy lifts both fuel
prices and travel), so even the full-sample "pass" was never evidence
for the stated channel. **A sign check against the written mechanism is
cheaper than an F-test and would have caught this first** — worth doing
routinely before reading any p-value here.

`HI_BIZ_APPS` keeps the right sign throughout (−0.147 at lead 1: more
formations, less slack), so its mechanism is not contradicted, only
undetectable outside the pandemic. Retest is reasonable later — 2020
exclusion still leaves ~236 usable months, so the binding constraint is
effect size, not sample size, and Hawaii forms businesses below its
population share (0.30% of US applications vs 0.43% of population).

Both pairs stay registered, following the `HE → HI_UNEMPLOYMENT`
precedent: a documented null is worth more than a deleted one, because
the next reader will otherwise re-propose the same idea.

### The sign check is now part of the screen (2026-08-07)

The jet-fuel null above was caught by hand. That is not a control, so
the check now runs automatically: every registered pair declares the
direction its mechanism predicts in `EXPECTED_SIGN`, or declares itself
two-sided in `_AMBIGUOUS_SIGNS` **with a written reason**.
`assert_sign_coverage()` runs before any computation and fails the run
if a pair appears in neither or both — an undeclared pair would
otherwise report `sign="n/a"` and look like it had passed a check that
never ran. Current state: 41 pairs, 38 signed, 3 declared two-sided
(permit units vs home values, mortgage rates vs rents, participation
vs unemployment — each genuinely two-directional in standard theory).

Output: a `sign` column on both result tables, a violations section at
the top of the report, `sign_matches_hypothesis` / `expected_sign` /
`mean_lead_corr` in `selected_signals.json`, and a `::warning::` if a
**robust** signal ever contradicts its mechanism. That last case is not
auto-demoted — what counts as "robust" is a documented methodology
decision, not something a reporting script should redefine — but it
cannot pass quietly. Today: 0 robust signals contradicted (8 sign-ok,
6 with no material direction).

**Two implementation bugs, both of which made the check worse than not
having one.** Recorded because each is easy to reintroduce:

1. *Scoring the peak-|r| lead.* The first version judged direction at
   the largest correlation across leads 0–18. It promptly accused
   `HI_PRICE_CUTS → HONOLULU_SF_MEDIAN` — the most carefully verified
   finding in the screen — of contradicting itself, because the peak
   sits at lead 12 (+0.322), a 12-month offset between two
   non-seasonally-adjusted series, i.e. seasonality. The mechanism's
   horizon, lead 3, reads −0.272 with the predicted sign, and lead 3 is
   where the BH-passing test lives. **Fix:** score the mean correlation
   over leads 1..k for a lag-k test — the window the test actually
   uses. Lead 0 is excluded: co-movement is not prediction.
2. *No materiality floor.* Version two reported a mean r of **+0.009**
   as "contradicting" a negative prediction, flagging 42 combinations
   and burying the real ones. **Fix:** `sign_materiality(n) = 1/√n`
   (~0.05 at n=400, ~0.10 at n=100), so the bar scales with sample size
   instead of being one arbitrary cutoff. Below it the verdict is
   `None` — *no direction*, explicitly not *passed*. Violations fell
   42 → 9.

**What survived the corrected check**, beyond the known jet-fuel case:
`HI_AIR_PAX → HI_VISITORS` clears BH in the 2020-excluded run at lags 3
and 6 with mean r of −0.102 / −0.104 — the *opposite* of its registered
`+1`. A candidate explanation worth testing rather than asserting: the
BTS series is HNL-**origin** traffic, so it misses mainland visitors
flying direct to Kahului, Kona and Līhuʻe, and that share has grown.
Statewide arrivals could then rise while HNL-origin departures fall.
Until someone checks that, the pair is a flagged anomaly, not a finding.

`US_MORTGAGE30`/`US_DGS10 → HONOLULU_ZHVI` flag at lag 3 (mean r
≈ +0.12) but not at the lags 6/12 where they clear BH — economically
coherent, since rates and prices both rise in booms while the
contractionary effect operates over longer horizons. Reading the lag
column matters; a flag is a prompt to look, not a verdict.

### Splitting arrivals: the signal was in the international margin (2026-08-07)

A survey for new sources found the best one was already being fetched:
the DBEDT MEI workbook carries ~40 columns this repo was not reading,
including a **domestic/international split** of both arrivals and
visitor days, in all five workbooks. No new fetcher, no new dependency —
20 series added (4 measures × 5 geographies), monthly, arrivals from
1990-01 and visitor days from 2002-01.

The blended `DBEDT_ARRIVALS_*` averages two markets that behave nothing
alike. International travel (Japan above all) collapsed after 2020 and
has recovered on a completely different path from domestic, and it is
the higher-spend, hotel-concentrated segment. If the tourism→employment
channel is real, averaging the two should weaken it.

**It did.** Against `HI_UNEMPLOYMENT`, all three clear BH on the full
sample; the 2020-exclusion run separates them:

| predictor | lag 3 | lag 6 | lag 12 | robust |
|---|---|---|---|---|
| `HI_VISITORS_ARRIVALS` (blended) | p=0.028 | 0.145 | 0.087 | **no** |
| `HI_VISITORS_DOM` | p=0.061 | 0.421 | 0.220 | **no** |
| `HI_VISITORS_INTL` | **p=0.0014** | 0.105 | 0.186 | **yes (lag 3)** |

The blended pair fails the robustness gate at every lag. Split apart,
the international segment survives at lag 3 — a signal the average was
hiding. Robust count 14 → 15, nothing displaced.

**How far to trust it, stated plainly: not far yet.** The new sign
check declines to endorse this one. Its mean lead-1..3 correlation is
**+0.040** where the mechanism predicts negative — below the
materiality floor (1/√234 ≈ 0.065), so the verdict is *no material
direction* rather than *contradicted*, but it is the wrong side of zero
and it only just escapes being flagged. Granger predictive content and
a clean bivariate lead relationship are different claims; this has the
first and not the second. Treat it as a lead worth watching, not a
validated channel, and do not promote it without a mechanism restated
at a specific horizon.

**Not registered: the segments against their own total.**
`DBEDT_ARRIVALS_*` *is* DOM + INTL — verified on the data rather than
assumed, across 5 geographies × 438 months, worst reconstruction error
1 visitor out of 345,075. Screening a term against its own sum is
circular, and more starkly so than HIPHCI, which at least blended four
inputs. Against `HI_UNEMPLOYMENT` the segments are legitimate, since
the unemployment rate is built from neither.

*Also surveyed and rejected this round*, so nobody re-researches them:
the **DOT Consumer Airfare Report** (DB1B fares) excludes Hawaii *and*
Alaska entirely — 142 city-market values, zero HI/AK, contiguous-48
only; **OpenSky Network** historical flights now returns 403
(authentication required). **FDIC call reports** for the six
Hawaii-domiciled banks are live, keyless and rich (net loans, RE loans,
C&I, deposits, quarterly 1984→2026) and the mechanism is attractive —
local credit is the fundamental under the BOH/FHB *equity* pairs
already screened — but they are **quarterly, and this screen cannot
test quarterly predictors**: `granger_f_test` requires all lags present
and a quarterly series has data only every third month, so it returns
`None` at lags 3, 6 and 12 alike (verified with a synthetic series, not
assumed). Same wall the bimonthly CPI hits. Bundling them would give
descriptive cross-correlations only.

### Sector payrolls, electricity volume, disaster control (2026-08-07)

Closing out the source survey. Three additions, one registered pair,
one more informative null.

**13 MEI sector payroll series** (65 across 5 geographies, monthly
1990-01 →), joining the accommodation/food/construction series already
taken. Two independent validations rather than trusting the parse:
DBEDT counts ÷ 1000 track BLS CES total nonfarm to a **0.61% mean gap
(max 1.87%) across 258 months**, and the 14 private sectors sum
**exactly** to DBEDT's private-sector total in all 438 months — zero
discrepancies, so the sector list is complete.

*Two substring traps, both caught before they shipped.* `state` matches
both "State" payrolls **and** "State general fund tax revenues", so the
federal/state/local government split is skipped entirely rather than
half-taken; `DBEDT_JOBS_GOVT_` carries the total. And `agriculture wage
and salary jobs` is a substring of "Total **NON**-agriculture wage and
salary jobs" — the wanted label sits inside the unwanted one. A test
now pins the general invariant: no fragment may be a substring of
another.

Only one sector was registered — **`HI_JOBS_PROF → HI_UNEMPLOYMENT`**,
because professional & business services contains *temporary help*,
which employers add before permanent hires and cut first. It is a
**null**: p=0.90/0.82/0.88 on the full sample, no BH pass at any lag in
either run. The sign check reports "ok" (−0.213 at lead 4, correct
direction), so this is a no-signal result rather than a wrong-signal
one. The other twelve sectors are bundled, unscreened — without a
written mechanism they would only spend testing budget.

*This registration sits in tension with an earlier decision, deliberately.*
`HI_JOBS_ACCOM → HI_UNEMPLOYMENT` was withdrawn as circular on the
grounds that payrolls are "a component of the employment level the
unemployment rate is computed against". That reasoning does not hold:
payroll counts come from the **establishment** survey, the unemployment
rate from the **household** survey — different instruments, different
universes. The repo already depends on this, since `HI_PAYROLLS`
registers *total* CES nonfarm against the same target unchallenged, and
one sector shares strictly less with the target than the total does.
The `HI_JOBS_ACCOM` withdrawal is nonetheless **left standing**:
reversing a decision made on the record is a human's call, not
something to slip into an unrelated commit.

**EIA electricity SALES volume** (`EIA_HI_ELEC_SALES_*`, 305 months,
2001-01 →) alongside the existing price series, from the same endpoint.
Price is a cost channel; volume is an activity channel, and they can
move in opposite directions. **Bundled but deliberately not screened**:
electricity use is coincident with activity rather than leading it, the
series is not seasonally adjusted, and residential load is driven by
weather. Registering it would spend testing budget on a hypothesis
nobody here believes.

**FEMA major-disaster declarations** (`hi_disasters.json`, 39 Hawaii
declarations back to 1955). **Not a predictor** — a control, the
analogue of `exclude_2020`. Hawaii gets smaller versions of the
same problem on a regular cadence: Lahaina 2023, Kīlauea 2018,
Hurricane Lane, repeated flood declarations, each moving arrivals,
payrolls and prices together for a few months.

Two guards decide whether this is a check or demolition, and the
numbers show why: **60 candidate exclusion months with the defaults,
256 without them.** Hawaii's COVID declaration alone (DR-4510) has a
41-month incident window, 2020-01 → 2023-05; dropping every declared
month would delete three and a half years of panel, and the 2020 gate
already covers that regime, so `Biological` is excluded by default. A
single declaration also contributes at most 6 months, because an
incident window is administrative as much as physical (DR-4201 runs 7
months as lava kept advancing) and the economic shock concentrates at
onset. Both are arguments with documented defaults, not hardcoded rules,
and the written payload records which filters produced its month list.

`run_screen` now accepts `exclude_months=`, defaulting to `None`. **The
committed screen behaviour is unchanged** — a test pins
`excluded_months == 0` on a default run — so the control is available
without a third gate appearing on its own. Making disaster exclusion a
standing part of the robustness definition is a methodology decision
and is left open.

*Process note.* An intermediate check during this work compared the
count of entries in `selected_signals.json` before and after and read
"+2" as "two new robust findings". That file holds **all 67 BH passes**
with a `robust_to_2020_exclusion` flag, not the robust subset — the
correct check is that flag (or the screen's own "N robust" line), not
the array length. The earlier round-4 conclusions were verified against
the report table and the robust count independently and are unaffected.

Source months flagged `quality_flag=1` (thin volume or pandemic-era
disruption; for Honolulu these cluster from 2020-03) are **retained, not
dropped** — silently removing months would change series composition in
a way no downstream consumer could audit, and the screen already re-runs
with 2020 excluded. Net effect on the screen: 98 Granger tests, 61 BH
passes, robust survivors 12 → 14, with **no previously-robust finding
displaced**.

### Experimental: search-attention terms (`markets/attention.py`, July 2026)

Google Trends terms as *demand-side* screen candidates — search embeds
intent (booking, house-shopping, PV-shopping) with zero publication
lag, complementing prices which embed expectations. Four terms are
pre-registered with hypotheses, mirroring the ticker universe's
multiple-testing discipline. A 2026-07 probe using the screen's own
machinery found `flights to hawaii` / `hawaii vacation` →
HI_UNEMPLOYMENT at Granger p ≈ 2e-5..2e-4 (n≈153, lags 3/6)
**with 2020 excluded** — notably the *inverse* of the ticker pattern
(tickers died on 2020-exclusion; here the COVID collapse masks the
relationship). Correlation signs are unstable across terms, so this is
predictive content, not mechanism.

Not wired into the screen registry, no bundled data, no CI: the
endpoints are unofficial (cookie→explore→token dance, breaks at
Google's whim), and values are per-window-normalized *samples* — the
repo's byte-stable bundle discipline is unachievable; promotion would
need pinned-window multi-fetch averaging plus the standard BH /
2020-robustness / ablation gates. Direct ticker-attention terms
("BOH stock") were rejected: Hawaii microcap search volume is below
Trends' privacy thresholds.

### Reverse direction: fundamentals → ticker returns (`markets/fundamentals.py`, July 2026 — null result)

The standing direction is prices → economy because prices embed
expectations before agencies publish; the reverse — census/BLS/Zillow
data informing *stock* forecasts — collides with market efficiency.
The narrow defensible hypothesis (slow information diffusion into the
thinly-followed Hawaii tier — Hong/Lim/Stein 2000, Hou & Moskowitz
2005) was pre-registered as six fundamental→ticker pairs
(HI unemployment → BOH/FHB/HE, ZHVI → BOH/FHB, ZORI → BOH) and run
through the standard gauntlet in reverse.

**Verdict: clean EMH null, both stages.**

* *Screen:* `hi_unemployment→BOH` passes BH-FDR (p=0.001/0.003 at lags
  6/12) **only with 2020 included** and collapses on exclusion
  (p=0.74/0.49) — the exact COVID-coincidence artifact the robustness
  gate exists to catch, and the mirror image of the attention terms
  (which strengthen on exclusion). Zero of six pairs survive.
* *Ablation:* one-step-ahead OLS on availability-lagged fundamentals
  (LAUS/Zillow declare `availability_lag_months`; only
  actually-public-at-forecast-time values are used) beats **neither**
  the predict-zero nor the expanding-mean benchmark for any pair; the
  BOH/unemployment signal is catastrophically worse (+129% RMSE) —
  the regression fits the 2020 outlier and pays for it out of sample.

Consequence: the tracker's damped-drift + calibrated-band forecast
stays as-is, now backed by evidence rather than assumption. Untested
and still plausible: long-horizon fair-value consistency checks (e.g.
BOH's revenue base vs the SB3125 income path) — framed as tracker
context, never trading signals. Revision caveat applies throughout:
screens run on revised LAUS/Zillow histories, so even these nulls are
*optimistic* upper bounds on real-time performance.

### The forecast board + vol bake-off (`markets/forecaster.py`, July 2026)

The markets subpackage now exposes a standalone multi-horizon stock
forecaster (`forecast_board` / `python -m
census_forecaster.markets.forecaster`): damped-drift point + calibrated
90% band per ticker × horizon, with diagnostics per row — 12-month
momentum, a volatility-regime flag, and whether the ticker is itself a
robust *forward*-screen survivor (the direction that works).

Where returns proved unforecastable (above), volatility is not — so the
band's σ was put through the same discipline. Walk-forward bake-off,
3,806 pooled (ticker, anchor, horizon) forecasts, identical damped-drift
points, multipliers calibrated *sequentially* from past standardized
errors (no lookahead), scored by 90% interval score:

| σ estimator | coverage | mean IS |
|---|---:|---:|
| rolling-36 SD (original) | 0.896 | 0.6152 |
| **EWMA λ=0.97 (RiskMetrics monthly)** | 0.898 | **0.6076** |
| GARCH(1,1) via `arch` | 0.898 | 0.6277 |

EWMA wins on sharpness at identical coverage and is dependency-free;
**GARCH lost to the original** — monthly cadence leaves ML vol fitting
too few observations — so the `arch` dependency was evaluated and
rejected on evidence. The board defaults to EWMA;
`forecast_ticker`/`calibrate_band_multiplier` gained a `vol_method`
parameter but keep `rolling` as their default so existing callers'
numbers are unchanged (multiplier and σ must always be calibrated under
the same method). Standing rule restated: the board is tracker context,
not trading advice, and no fundamentals-derived return signal touches
the point forecast (the null above is load-bearing).

### Phase-3 integration (ML features + national anchor)

Two paths, both ablation-gated by
`scripts/compare_market_ablation.py` (report:
`backtests/results/market_ml_ablation_<date>.md`):

1. **ML features.** `markets/signals.py::derive_annual_signals` turns
   the prices panel into annual **June-cutoff** channel momenta
   (`mkt_energy_mom` = XLE, `mkt_shipping_mom` = MATX, `mkt_reit_mom` =
   mean of screen-surviving REIT tickers), written to
   `data/leading_indicators/market_signals.json` by
   `refresh_market_panel --derive-signals`. A channel exists only if
   the causal screen produced a BH survivor for its ticker that is
   robust to 2020 exclusion. The values enter `acs/ml_features.py`
   under the reserved `__national__` pseudo-geoid (sentinels
   `_MKT_*`, excluded from cross-indicator features) as four
   `mkt_*` columns — `energy/shipping/reit` lag-0 plus `reit` lag-1
   (VNQ's Granger lead is ~12 months). June cutoff mirrors
   `truncate_to_anchor`'s no-peeking: a July shock cannot move that
   year's signal (tested). `ml_trend` remains `use_ml=False` by
   default regardless of the ablation outcome.

2. **National unemployment anchor — tried and REJECTED.**
   `bls_national_unemployment.json` (CPS LNS14000000, annual average)
   was registered as a RATE anchor on S2301 (levels don't transfer
   US→county; the hypothesis was that YoY direction might). Verdict on
   the full ablation (market_ml_ablation_2026-07-14.md), recorded
   honestly because the evidence is mixed:

   - Full window (2014–2022 anchors): blended S2301 RMSE *improves*
     −1.2% absolute — but CI90 coverage drops 91.7% → 88.2% and the
     gain concentrates in the 2020–2021 shock years.
   - Recent window (2021–2022 anchors): RMSE *regresses* +2.7%
     absolute vs the 2% gate — on the anchors most like the future,
     the member (standalone RMSE 0.47) dilutes the trend (0.40).
   - Structural: unemployment-rate YoY log-changes violate the
     rate-anchor band invariant (|log(9.3/6.3)| = 0.47 in 2009 vs
     0.30). The rate machinery's SE assumptions were built for
     price/income indexes; recession swings in a *rate* series break
     them — the mechanism behind the coverage loss.

   Rejected on balance: an anchor that buys point accuracy in shock
   years by making intervals overconfident is the wrong trade for this
   repo's calibration-first discipline. The registry row was removed;
   tests pin the removal. The data file is still refreshed (the causal
   screen uses the monthly series) — it just doesn't anchor.

3. **National unemployment reframed as an ML feature (2026-07-15).**
   The rejected anchor's signal was moved to where it belongs — the ML
   leading-indicator path. National unemployment isn't a good
   *contemporaneous* anchor for S2301 (LAUS already owns the county
   level), but national labour markets *lead* local ones, and a tree
   can learn *when* to trust the signal instead of applying it blindly.
   `load_national_unemployment_data()` reads the same
   `bls_national_unemployment.json` (annual-average %) into three
   geoid-constant columns under `__national__`: `natl_unemp_lag0`
   (level at the anchor year) plus `natl_unemp_chg1`/`chg2` (1- and
   2-year percentage-point changes — the change is what leads). This
   sidesteps every anchor failure: no log-space SE (raw % + pp
   deltas), no level-offset (only the change is used directionally),
   and inverse-variance weighting is replaced by the tree deciding
   relevance. No-peeking holds structurally: the anchor-year annual
   average is final by the following January, before the ACS target
   (anchor + h, h ≥ 1) is published. Ablation-gated by
   `scripts/compare_natl_unemp_ablation.py`
   (`backtests/results/natl_unemp_ablation_2026-07-15.md`); ships
   opt-in. Verdict: ensemble-level wash (national geoid-constant signals
   act as year-effects), but permutation importance is decisive and
   theory-consistent — `natl_unemp_lag0` is the single **strongest**
   feature for S2301 unemployment (+0.243) and the 2-year change
   predicts S1701 poverty (+0.033). The vindication of the feature path:
   the same strong predictive content the anchor carried is now weighed
   adaptively by the tree, without the anchor's coverage collapse.

### Feature-column ordering fix (2026-07-15)

While adding the national-unemployment columns, a latent bug surfaced:
`FeatureSpec.column_names` concatenated the auxiliary blocks
(bps/saipe/laus/mkt) *ahead* of the cross-indicator columns, but
`_build_row` emits them *after* — so the name→position map was off by
the cross-column count whenever cross columns existed. The trained
model was unaffected (it is name-blind, and training and inference use
the same builder), but any name-keyed readout — notably permutation
importance — read mislabeled columns, which is why the mkt importance
table in `market_ml_ablation_2026-07-14.md` is unreliable.
`column_names` now interleaves the cross block correctly
(`_BASE_COLUMNS + cross + _AUX_COLUMNS + horizon`), and
`test_column_names_match_actual_row_order` pins a multi-indicator
panel against distinctive aux values so this can't silently return.

### National-macro predictor variables (2026-07-15)

The `natl_unemp` feature generalizes into a **registry-driven national
channel** (`acs/ml_features.NATIONAL_SERIES`) carrying 13 national
Census/BLS/FRED series as opt-in ML leading indicators, feeding both the
census forecaster and the stock causal screen. The registry is the single
source of truth shared by the fetcher (`scripts/refresh_national_macro.py`)
and the feature columns, so the two never drift.

**The 13 series → target mapping.** National CPI subindexes (all-items,
food, housing, rent, gasoline — already fetched for BLS κ-calibration,
here promoted to forecaster inputs at zero fetch cost) → rents / home
value / poverty / commute; average hourly earnings → income; labour-force
participation, employment-population ratio, JOLTS job openings →
unemployment; Census Housing Vacancy Survey national rental-vacancy and
homeownership rates (via the FRED mirror) → the vacancy_rate and
homeownership_rate targets *directly*; 30-yr mortgage rate and 10-yr
Treasury (FRED) → home value / rents (the dominant housing driver). All
fetch paths are keyless (BLS public API, FRED `fredgraph.csv`).

**Column policy (19 columns, deliberately compact).** Geoid-constant
national signals are near-collinear with `anchor_year_norm`, so a naive
3-columns-per-series would add ~40 year-effect columns and invite
overfitting. Instead each series declares a `col_policy`:
- `logchange1` (1 col, `natl_<name>_chg1 = log(v[Y]/v[Y-1])`) for
  index/price series (5 CPIs, AHE) whose *level* is a monotone year proxy;
- `diff1` (1 col, pp change) for the mortgage-rate index;
- `level_diff1` (2 cols, level + change) for mean-reverting rate/ratio
  series (participation, emp-pop, JOLTS, vacancy, homeownership, 10-yr)
  whose level is meaningful.

Total = 6·1 + 1·1 + 6·2 = **19 columns**. The `_build_row` reader is a
single generic loop over the same fixed-order registry tuple as the column
generator, so name↔slot alignment holds by construction (the ordering
invariant and its regression test still apply).

**Aggregation & no-peeking.** Each series is stored as the **calendar-year
mean level** (any cadence — monthly/weekly/daily/quarterly — reduces to the
year mean); the transform is applied at row-build time from years Y and
Y-1. The year-Y mean is complete by year-end, well before the ACS target
at Y+h (h≥1), so no-peeking holds structurally for every cadence. The
current partial year is a mean of available prints (flagged).

**Stock screen (dual use).** The monthly national series
(AHE/LFPR/emp-pop/JOLTS + FRED mortgage/10yr resampled to monthly) are
also merged into the causal screen as pre-registered predictors
(`markets/screen.NATIONAL_PREDICTORS`). Caveat: `log_return` on a rate
level is a rough pp-change proxy — fine for predictive precedence, never a
forecast input. The screen's robust national findings are the theory-backed
housing-rate leads (mortgage/10yr → Honolulu ZHVI) and JOLTS → unemployment
(Beveridge curve); labour-participation "leads" that fail 2020-robustness
are COVID-coincident (xcorr peaks at lag 0), not genuine leads.

**Ship gate.** `scripts/compare_national_macro_ablation.py`
(`national_macro_ablation_2026-07-15.md`): baseline ML vs +national-macro.
**GATE PASSED** — ensemble wash-to-slight-improvement (all |ΔRMSE| ≤
0.0022, largest gains S2301 unemployment and B25071 rent-burden; coverage
in band). Permutation importance is modest but theory-consistent and
spread across families: poverty (S1701) benefits most from national CPI +
rates (cost-of-living → poverty), home value from the mortgage-rate change
channel, income/unemployment from the labour-market series (LFPR, JOLTS,
emp-pop, 10-yr). Ships opt-in (`use_ml=False` default).

### natl_unemp registry migration (2026-07-15, follow-up completed)

The bespoke `natl_unemp_data` channel was folded into the registry the same
day, once a risk-free path was found: a third column policy `level_diff2`
(level + 1-yr + 2-yr pp change) reproduces the exact three columns the
shipped ablation validated — no information dropped. The registry entry is
`("unemp", "BLS_FETCH", "LNS14000000", monthly, mean, level_diff2)`; the
registry is now **14 series → 22 columns**. Migration invariants, each
pinned by a test or re-run:

- **Data equivalence**: `national_macro.json` `unemp` equals the legacy
  anchor file's values within rounding (3dp vs 4dp) — both are annual means
  of monthly LNS14000000 (`test_bundled_unemp_matches_legacy_anchor_file`).
- **Feature equivalence**: `natl_unemp_lvl/chg1/chg2` reproduce the former
  `natl_unemp_lag0/chg1/chg2` values exactly
  (`test_natl_unemp_level_and_changes_match_former_bespoke_values`).
- **Behavioral re-verification**: the ablation re-run in registry form
  (arms = registry with/without the `unemp` key) — see the dated
  `natl_unemp_ablation` report for the verdict and the reproduced
  S2301 importance.

The bespoke param/sentinel/loader (`natl_unemp_data`, `_NATL_UNEMP`,
`load_national_unemployment_data`) were removed from
`ml_features`/`ml_trend`/`calibration`. The legacy anchor data file is still
written by `refresh_market_panel` (reference value; it does not anchor and
no code consumes it). Scope for applying the registry pattern to the
remaining bespoke channels (bps/saipe/laus, market reader):
`REGISTRY_MIGRATION_SCOPE.md`.

### County-channel registry migration (2026-07-16)

The last bespoke aux channels — BPS permits, SAIPE poverty, LAUS
unemployment — were folded into a `COUNTY_SERIES` registry mirroring the
national one. They were structurally identical (per-geoid annual values →
lag0/1/2 + mean-of-valid-lags), differing only in transform and naming, so
two column policies cover them: `log_lags3_mean` (BPS — permit counts are
log-scaled; low-activity counties legitimately print 0, which is
log-undefined) and `level_lags3_mean` (SAIPE/LAUS — raw percentage rates).

Collapsed: three params → one `county_data: {name: {geoid: {year: val}}}`;
three loaders → `load_county_data()`; three injection blocks and three
reader blocks → one generic loop each. **Column names are unchanged** — the
policies were designed around the existing names (`bps_log_lag0…`,
`saipe_lag0…`, `laus_lag0…`), so nothing downstream renames.

**Verification: golden-row equivalence.** 13,174 feature rows × 67 columns
across 4 indicators were captured from the pre-migration code and compared
byte-for-byte afterwards — bit-identical, including NaN placement and row
metadata. This is the strongest form of the discipline used for the
`natl_unemp` migration: the refactor provably changed plumbing only.

**Latent bug fixed en route.** BPS's injection guard was `>= 0` while its
test was named/commented "zero not stored" — and never asserted the zero
case, so it passed either way. The registry unified the guard to `> 0`,
matching the documented intent; the readers NaN'd zeros regardless, so
features are identical (proved by the golden), and the test now asserts it.

The aux block is now fully generated from two registries (county, national)
plus the small hardcoded `mkt_*` block, which stays bespoke by choice: the
market channels are screen-gated (they appear/disappear with
`selected_signals.json`), so their spec is dynamic where a registry is
static. See `REGISTRY_MIGRATION_SCOPE.md` for the reusable recipe.

### State-level UI claims channel (2026-08-22)

The causal screen's strongest-ever finding was finally promoted to a
feature registry — and the honest size of the win is far smaller than its
p-value suggests.

**Why it sat unpromoted for two weeks.** `HI_UI_CLAIMS →
HI_UNEMPLOYMENT` (Granger p=3.2e-64, r=+0.821, genuine 1-month lead,
2020-robust; §"Current-indicator intake batch" above) cleared every
screen gate, but the standing rule is ablation-before-forecast-use and no
ablation was constructible. A Hawaii-only feature is *constant* across 86
of the panel's 90 counties, so a panel-wide ablation is arithmetically
blind to it; and §"S2301 mean-reversion model" had just established the
other half of the trap — that LAUS-state-driven predictors lose
panel-wide to ACS-self-trained ones, so a Hawaii-only channel would have
to be judged Hawaii-restricted, on 4 counties, where fold counts are too
thin to gate on. Neither evaluation could carry a promotion.

**The unlock is that the source was never Hawaii-only.** ETA-539
publishes every state in the same keyless CSV
(`refresh_ui_claims.py` already downloaded all 13 MB and threw 50 states
away). The script now emits a second output —
`data/leading_indicators/ui_claims.json`, per-state calendar-year mean
weekly initial claims, keyed by 2-digit **FIPS** so the feature module
resolves it straight off `geoid[:2]` with no postal-abbreviation table.
51 states, 1984→current.

**A third geographic tier.** `COUNTY_SERIES` varies by geoid; the market
and national-macro registries are geoid-*constant* (pure year-effects,
near-collinear with `anchor_year_norm`, which is why they wash out).
`STATE_SERIES` sits between: every county reads its own state's value, so
the column varies across both the cross-section and time without needing
county-level source data. Values live under a `__state_<FF>__`
pseudo-geoid, the same trick `__national__` already used.

Column policy `log_level_changes3` — 4 columns, matching the county
tier's width, of which **three are deliberately scale-free**:

| column | transform | why |
|---|---|---|
| `ui_claims_log_lag0` | log(v[Y]) | scales with state size; useful only as a state-size interaction, not as signal |
| `ui_claims_chg1` | log(v[Y]/v[Y−1]) | 1-yr change |
| `ui_claims_chg2` | log(v[Y]/v[Y−2]) | 2-yr change |
| `ui_claims_rel3` | log(v[Y]/mean(v[Y−1..Y−3])) | level against the state's **own** recent baseline — "claims are elevated", comparable across states of any size |

Claim levels span two orders of magnitude across states (Hawaii ~1,100
weekly filings, California ~74,000), so a raw-level channel would teach
the tree state size, not labour-market stress. The ratio columns are what
make one pooled rule valid in all 51 states. Columns are appended **last**
in `_AUX_COLUMNS`, so every pre-existing column keeps its slot index and
older permutation-importance tables stay readable.

**Ablation: PASS, but the result is a wash.**
`compare_ui_claims_ablation.py`, two arms both `include_ml=True` carrying
every shipped channel, differing only in `state_data`
(`backtests/results/ui_claims_ablation_2026-08-22.md`):

| cell | RMSE baseline | RMSE +ui_claims | Δ |
|---|---:|---:|---:|
| S2301 panel-wide | 36.87% | 36.69% | −0.18pp (−0.5% rel) |
| S2301 Hawaii-restricted | 46.70% | 46.47% | −0.24pp (−0.5% rel) |
| worst panel-wide regression | — | — | +0.10pp (vacancy_rate) |

Every gate clears — no RMSE regression > 2% absolute panel-wide or on the
Hawaii-restricted S2301 cell, CI90 coverage in band — but a 0.5% relative
improvement is **nowhere near** the 5% relative the ML ship gate demands
of an ensemble member. This is the same ensemble-level wash the
national-macro registry produced, and for a related reason: BLS LAUS
ingests UI claims as one of its own model inputs, and LAUS is *already* a
county-level ML feature. The channel is substantially collinear with
`laus_*` rather than independent of it. A e-64 Granger p on 480 monthly
observations does not translate into annual county-panel forecast
accuracy, because the annual ACS print's variance is dominated by its own
measurement structure — the same lesson §"S2301 mean-reversion model"
records.

**What actually earns the registration** is not the RMSE delta (see also
the Honolulu counter-evidence below):

1. *Permutation importance is real.* `ui_claims_rel3` scores +0.01505 ±
   0.00137 on S2301 — the trees use it. (For scale, `natl_unemp_lvl` is
   +0.243 on the same target: this channel is an order of magnitude
   weaker, not a rival.) The channel is also used off-target —
   `ui_claims_chg2` +0.01010 on S1701 poverty, `ui_claims_log_lag0`
   +0.01030 on B19013 income — consistent with the LAUS note that labour
   state covaries with poverty and in-migration.
2. *It moves the way its mechanism says.* Per the sign-check discipline
   (§"The sign check is now part of the screen"), an RMSE delta is as
   direction-blind as a Granger F-test, so the ablation reports partial
   dependence of predicted S2301 log-growth on `ui_claims_rel3`. The
   curve is monotone with net slope **+0.0621**: counties in states whose
   claims sit below their own 3-year baseline get predicted unemployment
   *declines*, above-baseline get *increases*. The ML fit independently
   reproduces the screen's mechanism on a different estimator, different
   grid and different target. A channel that improved RMSE while moving
   against its mechanism would have been rejected here regardless of the
   gate.

**The one number that argues against it.** Honolulu County (15003) is
the repo's flagship cell and carries its own standing gate in
`CLAUDE.md`. Its pooled `ensemble_with_ml` MAPE across all 16 indicators
moves **5.73% → 5.84%, +0.105pp** (+1.8% relative) — the wrong
direction. It clears every gate (both levels sit well under the
documented 6.76% baseline, and the move is 0.105pp against a 2pp
regression limit), and it coexists with a Hawaii-restricted S2301
*improvement*, so the channel helps the indicator it was built for while
very slightly hurting the 16-indicator pool on that one county. But it is
an increase, it is on the county this project cares most about, and it is
the single strongest argument for keeping the channel out of the default
path. Recorded here rather than left in the report's appendix, because a
"GATE PASSED" banner should not be the only thing a future reader takes
away. *(The 6.76% figure has no script in this repo that emits it, so its
definition is unrecoverable and the absolute levels above are not claimed
comparable to it; the A-vs-B delta on identical folds is the defensible
comparison, and that is what the ablation now reports.)*

**Caveats carried forward.** (a) The definitional caution from the screen
still applies: initial claims are mechanically upstream of the
unemployment *stock*, so part of the relationship is accounting, not
prediction. (b) The partial-dependence grid spans roughly [−0.40, 0.00] —
the panel's 2010–2022 anchors are dominated by the post-2020 claims
decline, so the *positive* side of `rel3` is thinly sampled and the sign
check is better evidence about direction than about magnitude. (c) Five of the six
Hawaii-restricted coverage flags outside [85%, 95%] are **pre-existing in
the baseline arm** — 4 counties, ~272 folds per indicator, κ/bias strata
fit panel-wide — and the report marks them `pre-existing` rather than
letting them read as damage this channel did. Exactly one is genuinely
new: **B19013 Hawaii coverage 85.29% → 84.56%**, a 0.73pp slip across the
85% line on 272 folds, while the same indicator's *panel-wide* coverage
moved the other way (86.46% → 87.06%) on flat RMSE. Noise-scale and not
gated, but recorded rather than rounded away — it is the one cell where
adding this channel made something marginally worse.

**Consistency requirement.** `ensemble.py`'s bundled ML panel and
`calibration.py`'s Pass 2b both load the channel by default, so the
shipped `calibration.json` was regenerated (`run_acs_calibration
--as-of-mode publication --include-ml --include-conformal`) — its κ and
bias records must be fit on the same feature columns the production model
sees, which is the invariant `_load_bundled_ml_panel` documents.

The regeneration's blast radius is itself a check on the claim that this
touches only the ML member: 143 of 410 bias records and 26 of 410 κ
records moved, and **every one of them is a `ml_trend` cell** — the
`trend_ensemble` and `multi_anchor` records are unchanged. Bias moves are
sub-percent (max 0.022 in log space); the κ moves are single steps on the
bisection grid (1.625 / 1.95 / 2.6 / 3.25), i.e. quantisation rather than
drift.
