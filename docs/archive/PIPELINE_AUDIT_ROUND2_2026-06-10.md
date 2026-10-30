# Pipeline Audit — Round 2 (2026-06-10, branch `fix/audit-a1-c2-replicate-poverty-weights`)

Focus: tax-unit construction internals + how forecasts are incorporated into the microsim.
Round 1 findings (`PIPELINE_AUDIT_2026-06-10.md`) are NOT repeated here. Tags:
`[CONFIRMED]` = I re-verified directly (code trace or data inspection);
`[AGENT-VERIFIED]` = sub-agent verified dynamically (ran synthetic households through the constructor);
`[REPORTED]` = code-traced by sub-agent, not independently re-checked.

---

## ROOT CAUSE: the pipeline doesn't know what dollar-year its data is in

**R0. The loaded PUMS is the 2020–2024 5-year file in 2024 dollars; the code assumes 2022 (or 2023). `[CONFIRMED]`**
`~/ctc-and-eitc/data/raw/pums/psam_p15.csv` has the 2024+ `STATE` column and exactly five ADJINC
values `{1015250, 1049470, 1117193, 1193241, 1222017}` — the 2020–2024 5-year vintage, incomes
adjusted to **2024 dollars**. Meanwhile the code hardcodes three different vintages:
- 2022: `tax_unit_projector.py:72` (`_DEFAULT_BASE_YEAR`), `cbo_aging.py:151` (`growth_factor_from_2022`),
  `year_recalibrator.py:213`, `forward_targets.py:352`, `forecast_rxkids_2028.py:64`, `loaders/pums_loader.py`.
- 2023: `config/income_growth.py`, `projection/ensemble.py:36`.
- 2024: the actual data; county-scalar anchor (panel-latest ACS).
Nothing detects vintage at load. Every finding F1–F4 below is a downstream symptom.
**Fix:** add `detect_pums_vintage(person_df)` (ADJINC set + STATE/ST schema → dollar-year), stamp the
units frame with `income_dollar_year`, and require every aging entry point to derive its base from it.

---

## A. Forecast incorporation (the seam)

### HIGH

**F1. CBO aging double-counts 2022→2024 growth (~7–9% income overstatement). `[CONFIRMED]`**
`calibration/cbo_aging.py:339` applies `cbo_rates.factor(comp, target_year)` — **cumulative growth from
2022** — directly to incomes already in **2024 dollars** (R0). Correct factor is
`factor(comp, target)/factor(comp, 2024)`. Affected: all `use_cbo_aging=True` callers
(`forecast_sb3125_quintile.py:154`, `forecast_sb3125_vs_fy26base.py:134`, `forecast_hb2306_quintile.py:120`,
`poverty_impact_report.py` `--cbo-aging` default ON). Revenue scripts are partially rescued by the
DOTAX COR tax-anchoring; **the poverty path has no such anchor**, so incomes are ~9% too high vs
same-year SPM thresholds → poverty rates and reform lift counts understated.
Related: `age_filers_with_components` accepts `base_year` but **never uses it** in factor application
(`cbo_aging.py:276,339,402`) — callers think they control the anchor; they don't. And
`tax_unit_projector.py:~424` passes `_DEFAULT_BASE_YEAR` (2022) into the CBO branch regardless of the
caller's `anchor_year`.
**Fix:** implement re-basing (`factor(target)/factor(data_year)`) with `data_year` from R0; until then,
raise when `base_year != cbo_rates.base_year`.

**F2. EITC dollar anchor clamps projected years to TY2022 nominal dollars. `[CONFIRMED]`**
`takeup_imputation.py:444-450` rescales `eitc_amount` so the weighted aggregate equals the
`hawaii_caseload.csv` 2022 target ($184.7M); `hi_eitc` inherits via the same scalar (451-456).
`poverty_impact_report.py:421-430` wraps the year-specific call in a broad `except Exception` and
**silently falls back to `year=2022`** — which fires for any tax year without caseload rows (2024/2025).
Net: income aging is nullified at the aggregate; federal + HI EITC in projected-year runs are frozen at
2022 nominal dollars. Contradicts the `run_pipeline` docstring ("only the target count is fixed").
**Fix:** skip the dollar rescale when `target.year != tax_year` (or CPI-index the anchor); narrow the
except to the expected ConfigError and log the substituted vintage loudly.

**F3. HI food/excise credit double-counted in SPM resources. `[CONFIRMED]`**
`liability/hawaii.py:509-518`: `hi_tax_liability` is already net of the $110/exemption refundable
low-income credit (HRS §235-55.85). `credits/hi_food_excise.py` models the **same credit** independently,
and `poverty/spm.py:301-311` subtracts `hi_tax_liability` AND adds `hi_food_excise_amount`. Any script
computing both columns double-counts — live in `forecast_combined_reform.py` and any reform touching that
program via `_ensure_baseline_benefits`. (`poverty_impact_report.py` is unaffected only because it never
computes the standalone column.)
**Fix:** one owner. Either remove the credit from `hi_tax_liability` (emit `hi_low_income_credit`
separately), or have `compute_spm_resources` null out `hi_food_excise_col` when liability already nets it.

### MED

- **F4. Mixed growth anchors within one projection run.** `tax_unit_projector.py:378` county rows anchor at
  panel-latest (2024); fallback rows (`:395-405`) anchor at 2022 → same frame, ~2 extra years of growth on
  fallback rows. Resolve one anchor, pass to both branches. `[REPORTED]`
- **F5. Credit params clamp to TY2025 while incomes grow to 2027–2031.** `tax_unit_projector.py:563-579`:
  EITC/CTC phase-in/out thresholds frozen at 2025 against nominally-grown incomes → credits understated,
  bracket creep. Index params by CPI projection beyond 2025 (FPL already does this in `benefits/_fpl.py`);
  log when clamping. `[REPORTED]`
- **F6. Population not aged on the poverty path.** `project_tax_units_forward` scales incomes, never
  weights; revenue path rakes filer counts up at a hardcoded 0.5%/yr (`forward_targets.py:293,352` — no
  provenance). Apply one documented population factor to both paths. `[REPORTED]`
- **F7. rxkids 2028 runs on the legacy county-scalar engine** (`forecast_rxkids_2028.py:506` omits
  `use_cbo_aging` → False) while the headline poverty report defaults CBO ON — two poverty products on
  different growth engines; plus `PUMS_CONSTRUCTION_YEAR=2022` is wrong per R0. `[REPORTED]`
- **F8. `forecast_combined_reform.py`: TY2027 reform on unprojected incomes + hardcoded 2024 SPM threshold**
  (`:126`), no `tax_year` to `compute_spm_resources` → 10% flat federal-tax fallback, with the warning
  suppressed by `warnings.filterwarnings("ignore")` at `:25-26`. Thread `args.year` through; stop
  suppressing warnings. `[REPORTED]` (independently flagged by two lanes)
- **F9. Forecast (model) uncertainty is computed then dropped.** `income_ci90_low/high` populated on the
  scalar path (`tax_unit_projector.py:462-497`) have zero consumers; CBO/BLS paths set them NaN;
  `projection_adapter.py` returns point only. All published CIs are sampling-only (SDR) with no
  aging/forecast variance. Short-term: label every CI "sampling-only"; long-term: outer MC over growth
  draws × inner SDR. `[REPORTED]`
- **F10. Mortgage-deduction tiers under-grown:** params are 2022-vintage but scaled from panel-latest
  anchor, skipping 2022→anchor home-value growth (`itemized_deductions.py:371-419`,
  `acs_supplement_forecast.py:289-339`). `[REPORTED]`

### LOW (hygiene)
Dormant `apply_2026_growth=True` defaults in `units/income.py` (all live callers pass False);
`source_specific_growth.py` hardcodes 2022→2027; dead `EnsembleProjector` with wrong vintage claims;
conflicting inflation constants (`growth_rate_loader.py:57` 1.0681 vs `income_growth.py` 1.1062);
stale `DEFAULT_PUMS_YEAR=2022` comment. All `[REPORTED]`.

---

## B. Tax-unit construction (second pass)

### HIGH

**B1. Replicate weights use a different base than the main weight for HoH / any filer with dependents. `[CONFIRMED]`**
`constructor.py:1311-1321` (`_calculate_hybrid_weight`): main weight uses **WGTP** whenever the unit has
≥2 members (HoH with deps, single with deps, MFJ). `constructor.py:1374-1391`
(`_compute_replicate_weights`): branches on `filing_status in ('single','head_of_household')` → uses
**PWGTPr**. So a HoH unit's main weight is `WGTP×1.88` but its replicates are `PWGTPr×1.88` (agent
measured 150.4 vs 77.1); a dep-less MFS filer has the reverse mismatch. This systematically corrupts SDR
variance — the exact thing this branch exists to fix; our A1 fix made MFJ replicates *exist*, this makes
HoH/dep-carrying replicates *wrong*. Existing test only covers a dep-less single filer where the bases
coincide.
**Fix:** drive the replicate base off the same rule as the main weight (pass member count / use WGTPr
whenever main uses WGTP, PWGTPr only when main used PWGTP). Add HoH-with-dependent and
single-with-dependent replicate tests.

**B2. `_is_student` inverts the PUMS SCH coding. `[CONFIRMED]`**
`dependencies.py:385`: `return person['SCH'] == 1  # 1 = Yes, in school` — in PUMS, SCH=1 means **not**
enrolled; 2/3 = enrolled. Every non-student under 24 passes the student test; every actual student fails.
**Fix:** `person['SCH'] in (2, 3)`.

**B3. HoH qualifying-person logic uses wrong relationship codes. `[AGENT-VERIFIED]`**
`status/hoh.py:139,161,181,202,251,393` test `rel in [22,23,24,25]` (and 34 foster, 27 parent) — RELP+20
ghosts, not 2019+ RELSHIPP. Verified dynamically: bio child (25) → HoH ×1.88, but stepchild (27) /
adopted (26) / grandchild (30) → single ×0.85; while unmarried partner (22) or roommate (34) can qualify
someone for HoH. The correct `_is_qualifying_child` exists in the same file but `is_head_of_household`
never calls it. Same family as round-1 M3 but in a different, higher-impact file.
**Fix:** route through `relshipp_codes` canonical sets.

**B4. Working 18–23-year-olds become dependents with no enrollment/support test. `[AGENT-VERIFIED]`**
`dependencies.py:78-120`: "children" mask is `AGEP<24 & SCHL>=15` — SCHL is *attainment* (16 = HS
diploma), not enrollment — and the guardian-assignment path never invokes `_is_qualifying_child`'s
support/income tests. Verified: a 23-y/o earning $80K, not enrolled, became a dependent; parent got HoH;
unit income $173K. Undercounts single filers, inflates HoH counts and guardian AGI.
**Fix:** gate the 19–23 path on `_is_student` (post-B2) + `_provides_over_half_own_support`.

**B5. Dependents' income is summed into the filer's unit income. `[AGENT-VERIFIED]`**
`constructor.py:1207-1220` includes all dependents in `members_to_include`;
`income.py:99-122` sums everyone → dependent wages land in the claimant's AGI ($90K parent + $80K dep =
$173K unit income). Inflates brackets, kills EITC phase-ins; compounds with B4.
**Fix:** sum filer(s) only; carry dependent income in a separate column if needed for SPM resources.

> **Note on B3+B4+B5 jointly:** these all push the HoH count and HoH/guardian AGI around, and the
> hardcoded `1.88` HoH calibration factor (round-1 M4/A5) is currently papering over the miscount.
> Fixing them requires re-deriving that factor — treat as one workstream.

### MED
- **B6. hoh.py parent-qualifier branch multiplies by raw integer ADJINC** (no /1e6) → the `<3000` income
  test can never pass; elderly-parent HoH qualification is dead in production (`hoh.py:205-212`). `[REPORTED]`
- **B7. Student fallback assigns any under-24 "student" to the householder regardless of relationship**
  (`dependencies.py:177-191`) — lived-together test is just SERIALNO equality; violates the module's own
  no-unrelated-dependents rule. `[REPORTED]`
- **B8. Group quarters silently dropped from the filer universe** (`constructor.py:589-592`, WGTP≤0 skipped
  at debug level). GQ residents file returns; SOI-calibrated filer counts absorb them into wrong cells.
  Construct GQ single-filers on PWGTP or surface the excluded weight. `[REPORTED]`
- **B9. Unclaimed-dependent fallback leaves units inconsistent** (`constructor.py:783-788`): bumps
  `num_dependents` but never updates `dependents_details` (what EITC/CTC reads) nor recomputes income;
  and `_can_claim_dependent` treats RELSHIPP as relative-to-filer when it's relative-to-householder. `[REPORTED]`
- **B10. ADJINC conventions still inconsistent across modules**; test fixtures use `ADJINC=1.0`, which
  `income.py`'s /1e6 turns into ~0 income — invisible because constructor tests never assert income.
  One shared `_adjinc_factor` util. `[REPORTED]`

### LOW
MFS $150K gate uses raw PINCP (no ADJINC) (`constructor.py:1003-1016`); parallel path is
content-identical but order-nondeterministic and pickles the whole constructor per batch (no
parallel==serial test); replicate enumeration `start=1` over present-only columns mispairs on a missing
mid-range WGTPr; `spm_aggregation.py:236-245` weights unrelated one-person SPM units by WGTP not PWGTP,
and 18–23 dependents counted as adults mislabels single-parent units MFJ; dead code
(`is_married_filing_jointly`/`mfs.py` decision fns, both `_calculate_income` copies, phantom `'DIV'`
field — real dividends sit in INTP). All `[REPORTED]`.

---

## C. Orchestration (stage ordering / caching)

### MED
- **C1. `--rake-to-irs-zip` is a no-op**: ZIP raking runs after `aggregate_to_spm_units` already built the
  poverty frame from pre-rake units (`poverty_impact_report.py:1035-1046`). Move before step 6d. `[REPORTED]`
- **C2. `/tmp` caches have no invalidation and silently degrade data**: `pipeline_run.py:34-64` str-coerces
  `dependents_details` before parquet-caching → on reload `enrich_for_credits` silently falls back to
  synthetic age-10 dependents for every unit (corrupts EITC/CTC on any cached rerun). No schema/code
  fingerprint — the cache survives constructor changes (including this branch's). Same for
  `/tmp/sb3125_calibrated_base.pkl`. Add a cache key (code+schema hash) or drop the cache. `[REPORTED]`
- **C3. Calibrated-base reload uses a different liability basis**: `forecast_sb3125_vs_fy26base.py:114-116`
  recomputes base tax with defaults (std deduction, 2023) while the pickle was calibrated under
  `CAL_DED_PARAMS` (itemized). Factor the calibration tax config into a shared constant. `[REPORTED]`
- **C4. Per-program fallback years blend vintages in one run**: `_apply_snap`→2024, `_apply_credit_takeup`→2022
  etc., each behind broad `except Exception`, while `forecast_combined_reform.py` and `pipeline_run.py`
  globally suppress warnings — the signals that would reveal F2/F8 are muted. Emit a run manifest of
  (program → anchor year). `[REPORTED]`

### LOW
Re-enrichment clobbers `dependent_person_ids` (`pipeline.py:187` on already-enriched frames);
`calibrate_benefits` eitc→hi_eitc scalar is dict-order-dependent; `calibrate_via_rake` docstring defaults
drift from signature; legacy `CalibrationOrchestrator._step4` compounds a ×0.85 haircut per outer
iteration (exported, uncalled). All `[REPORTED]`.

### Confirmed-OK this round
Reform mutation discipline (copies everywhere, baseline untouched on second scenario); liability
pre-calibration ordering sound; HI EITC computed after federal take-up (correct, documented);
eitc reweight → take-up ordering keyed on the same column; SPM aggregation's deliberate raw-WGTP choice
documented.

---

## Recommended sequencing

1. **B1** — replicate-base mismatch. In scope for the current branch; without it the A1 fix gives
   wrong (not just missing) MFJ-adjacent SDR variance for HoH/dep-carrying units.
2. **R0 + F1** — vintage detection + CBO re-basing. One workstream; corrects every published 2026+
   poverty number (~9% income overstatement) and the base-year confusion across 6 files.
3. **F2** — EITC 2022-dollar clamp + silent fallback (every projected-year EITC number).
4. **F3** — food/excise double-count (every reform run touching low-income units).
5. **B2–B5 (+re-derive the 1.88 HoH factor)** — construction-correctness workstream; changes filing-status
   mix, so re-run DOTAX calibration validation after.
6. **C2** — cache invalidation (prevents this branch's own fixes from being masked by stale caches).
7. Everything else per severity.
