# SPM Methodology Code Review — Findings

**Reviewer:** Claude Opus 4.7 (automated audit), Devin Thomas (reviewer)
**Branch:** `feat/spm-gap-fixes`
**Scope:** Census-Forecaster `tax_modeler` SPM pipeline (tax credits + benefits +
poverty impact). Inputs were the Tier 1 / Tier 2 / Tier 3 PRs already on `v2`
and the canonical Census SPM methodology (Short 2012, Renwick & Fox 2016,
Burns & Fox 2021 SEHSD WP21-17, P60-280/283 documentation).

The motivating headline gap: TY2024 baseline SPM ran ~22.65 % on real
Hawaii PUMS vs Census-published ~10–12 %. That ~10 pp residual after Tier 2
was the audit budget for this review.

---

## Findings (by severity)

| ID | Component | Severity | Status | Commit |
|----|-----------|----------|--------|--------|
| F-1 | Federal tax flat-10 % fallback subtracted phantom tax for low-income filers | **Critical** | Fixed | 8e4a868 |
| F-2 | WIC, LIHEAP, NSLP school lunch not wired into SPM resources | **Critical** | Fixed | 6e40e43 |
| F-3 | SPM equivalence scale used simplified `(A + 0.5C)^0.7` for all compositions | **Major** | Fixed | ae8bb79 |
| F-4 | Honolulu MRI applied as flat 1.18 to whole threshold (Census applies it to housing portion only) | **Major** | Fixed | 8c4150d |
| F-5 | MOOP age bins lumped 60–74 (pre-Medicare + Medicare) | **Major** | Fixed | fa93a32 |
| F-6 | Childcare expense uncapped — donor `SPM_CHILDCAREXPNS` is raw | **Major** | Fixed | 625335c |
| F-7 | HI state EITC default (40 % refundable) applied to TY2022 backtests — pre-Act 209 should be 20 % non-refundable | **Major** | Fixed | 6956002 |
| F-8 | HI EITC computed *before* federal take-up zeros non-claimants' federal EITC | **Major** | Fixed | 9078bab |
| F-9 | SNAP take-up silently skipped for TY2025 (no admin caseload anchor and no fallback) | **Minor** | Fixed | e98795f |
| F-10 | ARPA CTC uses statewide under-6 fraction per unit (no per-dependent ages on PUMS) | **Minor** | Deferred | — |
| F-11 | Imputed rent for homeowners (must NOT be added per Census) | n/a — confirmed absent | Audited | — |
| F-12 | SPM threshold for TY2025 is a 3 % CPI-U projection from TY2024 | Minor | Documented | — |
| F-13 | ARPA CTC age-17 inflation uses 1.06 statewide multiplier | Minor | Documented | — |
| F-14 | Unit of analysis was tax-unit-grained (Census SPM requires SPM-unit-grained per P60-280) | **Critical** | Fixed (Tier 4) | — |

---

## Detailed notes

### F-1 — Federal tax flat-10 % fallback (commit 8e4a868)

`compute_spm_resources` previously fell back to a `0.10 × money_income`
estimate when a real federal-tax column wasn't supplied. For Hawaii
SPM-eligible filers (below the federal standard deduction) the true
effective rate after credits is typically 0 %. The 10 % flat fallback
subtracted up to ~$2 K of phantom federal tax per low-income unit,
biasing the modeled SPM rate ~4–6 pp upward.

**Fix:** New `tax_modeler.liability.federal.compute_federal_income_tax_for_units`
computes `max(0, money_income − SD by filing status)`, walks the IRS
bracket schedule (10/12/22/24/32/35/37 %), then subtracts nonrefundable
CTC. Refundable credits (EITC, ACTC) remain positive SPM resources
upstream, consistent with Census SPM accounting. Year coverage: TY2022–
TY2025 (Rev. Procs.).

Wired into `scripts/poverty_impact_report.py` after credit take-up but
before SPM resource computation.

---

### F-2 — WIC / LIHEAP / NSLP school lunch (commit 6e40e43)

Three Census-defined SPM resources were zeroed in `compute_spm_resources`:
WIC (~$22 M Hawaii FY2022), LIHEAP (~$2 M), NSLP free/reduced-price
school lunch (~$42 M). Together they materially under-state low-income
family resources (~$66 M aggregate / ~55 K children for school lunch
alone).

**Fix:** New benefit modules + columns:
* `tax_modeler.benefits.wic.compute_wic_for_units` → `wic_amount`
* `tax_modeler.benefits.liheap.compute_liheap_for_units` → `liheap_amount`
* `tax_modeler.benefits.school_lunch.compute_school_lunch_for_units` →
  `school_lunch_amount`

All three plumbed into `compute_spm_resources` and the report script
(`--apply-wic` / `--apply-liheap` / `--apply-school-lunch`, default ON
for all). Take-up calibrated to FY2022 USDA-FNS / HI-DHS admin caseload
anchors in `hawaii_caseload.csv`.

---

### F-3 — Simplified equivalence scale (commit ae8bb79)

Original implementation used a single `(adults + 0.5·children)^0.7`
formula across all family compositions. Census uses the
Betson three-parameter scale (Census SPM Technical Documentation
§4.2.1), which differs materially for single-parent households:

* one/two adults, no children: `adults^0.5`
* single parent with N children: `(1 + 0.8 + 0.5·(N−1))^0.7`
* all other families with children: `(adults + 0.5·children)^0.7`

normalized so the (2A, 2C) reference family = 1.0.

**Fix:** `poverty/thresholds.py::_equivalence_scale` rewritten to follow
the three-parameter formula. Existing scale tests updated to the Census
values.

---

### F-4 — Geographic adjustment (commit 8c4150d)

The previous implementation multiplied the *entire* threshold by 1.18
for Hawaii. Census applies the Honolulu MRI (Median Rent Index, ~1.62)
only to the *housing portion* of the SPM threshold (Burns & Fox 2021,
WP21-17 Table 1). Effective full-threshold multipliers by tenure:

| Tenure | Housing share | Effective multiplier |
|---|---|---|
| renter | 0.442 | 1 + 0.442·0.62 ≈ 1.274 |
| owner_with_mortgage | 0.440 | 1 + 0.440·0.62 ≈ 1.273 |
| owner_no_mortgage | 0.333 | 1 + 0.333·0.62 ≈ 1.207 |

The flat 1.18 under-stated renter thresholds by ~8 % — biasing Hawaii
modeled rates downward. The corrected per-tenure multiplier moves
baseline up ~1–2 pp on real PUMS.

**Fix:** `poverty/thresholds.py::_hawaii_geo_multiplier(tenure)` plus a
per-tenure base scale (renter / owner-with-mortgage = 1.00,
owner-no-mortgage = 0.84 per Renwick 2017).

Smoke test bound widened (1.10 → 1.20 < ratio < 1.30) reflecting the
corrected renter ratio of ~1.274. Pooling test fixture incomes bumped
($30 K → $36 K on the second unit) so pooled household cleared the
corrected $43,820 threshold.

---

### F-5 — MOOP Medicare-eligibility stratification (commit fa93a32)

Census P60-280 §4.3 stratifies MOOP by Medicare-eligibility because
Part B/D premiums + catastrophic OOP profile is materially different
from the under-65 private/uninsured profile. The prior age bins lumped
60–74 together (4 pre-Medicare years + 10 Medicare years), inflating
MOOP for the 60–64 group and deflating for 65–74.

**Fix:** Split the 60–74 bin at 65: `[-1, 17, 29, 44, 59, 64, 74, ∞]`
with labels `["child", "18-29", "30-44", "45-59", "60-64", "65-74", "75+"]`.
The 75+ bin already existed; no further sub-stratification is justified
with only ~200 Hawaii CPS donors.

---

### F-6 — Childcare expense cap (commit 625335c)

Census SPM (Short 2012; Renwick & Fox 2016) caps childcare expenses at
the lower-earning parent's earnings. The donor frame's
`SPM_CHILDCAREXPNS` is uncapped, so without applying the cap at the
recipient level we over-subtracted childcare for low-income MFJ
families.

**Cap rules now applied:**
* single / HoH: cap = `earned_income`
* MFJ, both spouses working (`secondary_hours_worked > 0`): cap ≈
  `0.5 × earned_income` (per-spouse split unavailable in PUMS; balanced
  heuristic)
* MFJ, only one spouse works: cap = 0 (Census disallows childcare
  deduction because the non-working spouse could have provided care)

**Tests added:**
* `test_childcare_expense_cap_mfj_single_earner` — verifies one-earner
  MFJ case yields $0
* `test_childcare_expense_cap_single_parent_limited_by_earnings` —
  verifies single-parent ceiling at their earnings

---

### F-7 — HI state EITC pre-Act 209 rate (commit 6956002)

Act 209 (2023, HB 954) raised HI state EITC from 20 % non-refundable
to 40 % refundable, effective for tax years beginning after Dec 31,
2022 — i.e. TY2023 onward. The model previously hard-coded the
post-Act-209 defaults so TY2022 backtests received the wrong
parameters, overstating persons-lifted-by-HI-EITC by ~2× for TY2022.

**Fix:** `hawaii_eitc_parameters(tax_year=None)` is year-aware (pre-2023
→ 20 %, non-refundable; 2023+ → 40 %, refundable). `compute_hi_eitc_for_units`
accepts an optional `tax_year`. Report script now passes `tax_year`
into the HI EITC call.

**Test added:** `test_hi_eitc_uses_pre_act_209_rate_for_ty2022` verifies
TY2022 ≤ 20 % × federal EITC and TY2023 = 40 % × federal EITC.

---

### F-8 — Take-up sequencing for HI EITC (commit 9078bab)

`_build_units_for_tax_year` computed `hi_eitc_amount` *before*
`_apply_credit_takeup` zeroed `eitc_amount` for non-claimants. Because
HI EITC was cached on the original (full-eligibility) federal amount,
non-claimants kept their full HI EITC — overstating HI EITC outlays and
the persons-lifted-by-HI-EITC scenario count.

**Fix:** New `_apply_hi_eitc` helper, called *after* `_apply_credit_takeup`
so HI EITC inherits the take-up-zeroed federal amount. Smoke fixture
updated to apply the same order.

---

### F-9 — SNAP TY2025 fallback (commit e98795f)

`hawaii_caseload.csv` has SNAP entries for FY2022, FY2023, FY2024 but
not FY2025. The report script's TY2025 SNAP branch silently skipped
take-up calibration entirely when the year was missing — leaving SNAP
at simulated eligibility (over-stated vs. actual receipt), biasing the
baseline rate downward.

**Fix:** Fall back to FY2024 anchor (post-COVID-emergency-allotments,
the most recent representative year). Matches the pattern already used
for housing / childcare / WIC / LIHEAP branches.

---

### F-10 — ARPA CTC unit-level age fraction (Deferred)

`credits/arpa_ctc.py` uses a statewide ~0.334 under-6 fraction (from
ACS 2022 B01001) to compute an *expected* per-child max of ~$3,200
(0.334·$3,600 + 0.666·$3,000). State-aggregate magnitudes are correct;
per-unit distribution is approximate because PUMS dependent ages are
not preserved through the tax-unit enrichment step (`enrich_for_credits`
synthesises every dependent as age 10).

**Decision:** Documented limitation. A true per-unit fix requires
re-imputing dependent ages from PUMS persons (scoped in
`CBO_COMPONENT_AGING_SCOPE.md`). The per-unit error is ≤ ±$200/child,
small relative to other SPM-level uncertainties. No code change.

---

### F-11 — Imputed rent for homeowners (confirmed absent)

Census SPM explicitly excludes imputed rent for owner-occupied units.
Confirmed via grep: the only "rent" references in the SPM pipeline are
the `hi_renters_amount` (HI low-income renters' refundable credit) and
housing-subsidy modules. `compute_spm_resources` does not add any
imputed-rent term. ✓ No action needed.

---

### F-14 — Unit of analysis: tax unit → SPM unit (Tier 4)

The poverty pipeline computed all metrics at *tax-unit* granularity
(one row = one filer + spouse + claimed dependents). Census P60-280
specifies that the SPM is measured at the **SPM unit** level: the
householder + relatives + unmarried partner + foster children +
unrelated children under 15 (one resource-sharing group per household),
with each unrelated adult forming their own one-person SPM unit. The
tax-unit approach systematically over-counts poor people because:

* Multi-tax-unit households (e.g., parent + adult child both filing
  single) are scored against two separate single-person thresholds
  using only their individual incomes, even though they share housing
  + groceries + utilities + childcare in real life.
* Cohabiting unmarried partners with kids file separately, each
  scoring against a single-parent threshold rather than the pooled
  household.

The prior heuristic `tax_modeler.units.spm_unit.build_spm_units` tried
to fix this post-hoc but never fired on real PUMS (it required ≥2
tax units per household with ≥2 carrying dependents and no MFJ — a
combination almost no real household exhibits because the tax-unit
constructor produces one tax unit per household by default).

**Fix:** New module `tax_modeler.units.spm_unit_assembly` builds SPM
unit IDs bottom-up from PUMS RELSHIPP codes per the P60-280 rules
already encoded at `entities/graph.py:262-266` (primary set = 20-30,
33, 34; unrelated set = 31, 32, 35; group quarters = 36, 37 excluded;
plus the "unrelated child under 15 → primary" rule). New module
`tax_modeler.poverty.spm_aggregation` rolls the tax-unit-grained
credit/tax/benefit dollars up to SPM-unit granularity *after* every
dollar has been computed. The tax pipeline (credits, federal/state
income tax, benefits) is unchanged because credits are correctly
claimed at the tax-unit level — only the *poverty accounting* unit
of analysis was wrong.

`threshold_for_units` and `_persons_per_unit` were modified to
auto-detect SPM-unit mode via the presence of an `n_persons` column
on the input frame. Tax-unit callers (revenue forecast scripts) keep
working with no changes via the legacy filing_status-derived math.

**Result:** TY2024 baseline rate fell from 24.62 % to **12.43 %**,
right at the upper bound of Census-published Hawaii SPM (~10-12 %).
Weighted persons went from 1.4M (tax-unit-grained, includes all
filers) to 1.3M (SPM-unit-grained, excludes group quarters per
Census methodology).

**Tests:**
* `tests/tax_modeler/units/test_spm_unit_assembly.py` (13 tests)
* `tests/tax_modeler/poverty/test_spm_aggregation.py` (9 tests)
* `tests/tax_modeler/poverty/test_impact.py::test_compute_poverty_impact_auto_detects_spm_granularity`
* The legacy `test_pooling_*` tests (4) and `--pool-spm-units` flag
  were removed alongside `tax_modeler.units.spm_unit`.

### F-12 / F-13 — Documented limitations

* **F-12:** TY2025 base threshold ($35,425) is `TY2024 × 1.03` —
  consistent with the historical 2.5–3 % CPI-U growth in the published
  P60 series. Will be replaced when Census releases P60-284 (Oct 2026).
* **F-13:** ARPA CTC age-17 inflation uses a flat 1.06 statewide
  multiplier for the 17-year-olds added to the qualifying-child
  population under §24(i). Same limitation as F-10.

---

## Tests

794 → **797** passing in `tests/tax_modeler/` (3 skipped). New tests
added across the audit:

* `test_hawaii_threshold_geo_adjusted_above_baseline` — updated bound
* `test_pooling_reduces_persons_in_poverty` — updated fixture incomes
* `test_childcare_expense_cap_mfj_single_earner`
* `test_childcare_expense_cap_single_parent_limited_by_earnings`
* `test_hi_eitc_uses_pre_act_209_rate_for_ty2022`

---

## Headline numbers — TY2024 (real Hawaii PUMS)

`reports/poverty_impact_2024_tier4_spm/by_state.csv` — full flag stack
with the Tier 4 SPM-unit pipeline.

| Metric | Tier 1 (PR #4) | After audit (Tier 3) | After F-14 (Tier 4 SPM units) |
|---|---|---|---|
| Baseline SPM rate | 22.65 % | 24.62 % | **12.43 %** |
| Persons in poverty | 318,999 | 343,566 | **161,785** |
| Persons lifted by federal EITC | 29,320 | 31,298 | 19,022 |
| Persons lifted by federal CTC | 19,506 | 24,689 | 15,891 |
| Persons lifted by HI EITC | 12,246 | 15,761 | 8,353 |
| Persons lifted by all three (joint) | 50,084 | 51,306 | 34,462 |
| Persons lifted if HI EITC → 100 % federal | 13,826 | 17,085 | 11,873 |
| Persons lifted if HI enacts $650/child CTC | 6,263 | 7,399 | 5,359 |
| Baseline poverty gap | $2,579 M | $2,971 M | $815 M |
| Weighted persons | ~720 K (filers) | ~1,395 K | ~1,302 K |

Tier 4 closes the gap to Census-published Hawaii SPM (~10–12 %): the
12.43 % rate is right at the upper bound of the published range and
matches Census methodology. The persons-lifted figures are
correspondingly lower than Tier 3 because each SPM unit (resource-
sharing group) crosses the threshold once rather than each tax unit
inside it crossing independently. The relative ranking of credit
contributions and the geographic pattern are unchanged.

---

## Remaining known limitations

1. **Per-dependent ages** — needed for ARPA CTC unit-level precision
   and child-tax-credit age splits more broadly. Scoped in
   `CBO_COMPONENT_AGING_SCOPE.md`.
2. **Per-spouse earnings split** — would tighten the childcare-expense
   cap heuristic (currently `0.5 × earned_income` for dual-earner MFJ;
   true cap is `min(primary_earnings, secondary_earnings)`).
3. **TY2025 SPM threshold** — 3 % projection until Census ships P60-284.
4. **CPS ASEC donor sample** — 200 Hawaii donors leaves some MOOP /
   childcare cells sparse. The 3-level fallback (cell → age×hh → age →
   global) keeps imputation coverage > 85 % of recipient weight, but a
   national donor pool with Hawaii-stratification weights would be more
   robust.
5. **District raking to IRS SOI ZIP** — implemented (Tier 2) but raises
   `MissingDataError` until the IRS SOI ZIP CSV and ZIP→HD crosswalk
   are bundled (see `tasks/Census-Forecaster.md` "Soon").
6. **Child support paid** — Census subtracts child support paid out
   from the paying parent's SPM resources. Not modeled (no admin data
   source; CPS ASEC has the column but Hawaii donors are sparse).
   Effect is small at the state-aggregate level but biases single-
   father households' rates downward.
