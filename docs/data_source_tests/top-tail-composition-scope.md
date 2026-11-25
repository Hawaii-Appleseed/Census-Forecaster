# Scoping: the synthetic $1M+ tail's income composition and deductions

Branch `data/synthetic-puf-top-tail`. Analysis only: no model code, default, data file, `site/` or `runs/` changed. Run 2026-11-25 on the Mac mini, one small script at a time (none longer than 20 s). Follows `synthetic-puf-top-tail.md`, whose fallback comparison found (1) the synthetic $1M+ rows carry only "wages" (68.5% of AGI) and capital gains (31.5%), where national SOI has wages 27.6%, pass-through 26.4%, dividends+interest 8.5%, gains 31.9% (Hawaii SOI Table 2: wages 20.9%), and (2) the model's effective deduction is about 18% of AGI at $200-500K and 9.7% above $1M against 4.7% and 5.8% in national SOI.

Scripts (`docs/data_source_tests/synthetic_puf_top_tail/`, all read only, run from the worktree root with `uv run python -W ignore`):

| Script | What it does |
|---|---|
| `scope_build_units.py` | `open_calibrated(shared=False)`, `units(alpha=1.5, top_premium=0.01)`, saves the frames to `/tmp/pufwork` |
| `scope_recalibrate.py` | recalibrates the base in memory with the current code from the raw unit cache (nothing written under `data/`), builds the same units |
| `scope_composition.py` | question A: tax invariance, federal AGI, CBO re-aging and Act 24 static delta under four labellings, TY given on the command line |
| `scope_deductions.py` | question B: DOTAX vs model deduction share, AGI and taxable income by class (argument: the parquet to read) |
| `scope_tail_deductions.py` | question B: the tail's AGI when its deduction level changes (tail calibration re-run with scaled deduction params) |

Labels used throughout: **[measured]** came out of a run above; **[read]** is from reading code, not run.

## Short answer

- **A. The composition lumping moves no published number.** Hawaii tax reads only `income` and `synthetic_cg_share`; the other income columns on the synthetic rows are never read by the Act 24 chain's scoring, ETI, migration or PTE (capture is 0 in every scenario). The one place the labels do bite, CBO component aging, is not on the MID chain's path, and on the path that does use it the $1M+ rows are discarded and replaced by SOI-anchored rows. Relabelling to a Hawaii/national mix changes the Act 24 bracket delta by under 5% of the delta, and only in a counterfactual where the chain used CBO aging. There is a latent defect worth knowing about: as built, the rows double count the gains dollars (they sit in `primary_intp` as "interest" and in `synthetic_cg_share`), so any path that ages the as-built rows by CBO components grows the tail 36% too fast.
- **B. The model's deductions are higher than Hawaii's as a share of AGI, the national SOI figure is the wrong benchmark, and calibration pins the result anyway.** DOTAX (Hawaii law, Hawaii returns) shows deductions plus exemptions of 10.3% of AGI at $200-300K, 7.7% at $300-400K, 5-7% at $400K-$1M and 8.5% above $1M (TY2023); the model has 18.5-19% at $200-400K, 12-16% at $400K-$1M, 9.8% above $1M. But calibration fixes counts and tax by class, so taxable income lands on DOTAX's (within 0.1-2.5% from $50K to $400K) while model AGI runs 4-11% high. The deduction share is not pinned; taxable income and tax are. The class AGI overage is the mirror image, a PUMS income property the calibrator's own docstring documents.
- **C. Recommendation: do nothing to the model; document (this file).** Fix the `primary_intp` double count and relabel only when something starts to read composition (PTE capture above 0, the MID chain moving to CBO aging, federal-side reporting on the tail). One real finding to act on separately: the cached calibrated base on this machine is the 2026-10-09 calibration, which predates the bounded raking (2026-11-20).

## Caveat on "the cached calibrated base"

`open_calibrated()` loads `data/artifacts/sb3125_calibrated_base.pkl` (identical size and mtime in the main checkout: Oct 8). Its metadata is `git_sha 493ef76`, created 2026-10-09, and carries **no `content_key`**, which every `save_calibrated_base` since that field was added writes. So no run since then has rewritten it. It predates `act24_calibration.py` (bounded raking, 2026-11-20), which is the current `calibrate()`. Consequence **[measured]**: on this pickle the class counts are 12-19% over DOTAX at $75K-$400K and tax by class up to 10% over, exactly the "compromise fixed-point" the `act24_calibration.py` docstring describes.

To separate that from the question, B reports both: the pickle as asked, and a recalibration in memory with the current code (`scope_recalibrate.py`) from the raw tax-unit cache (`tax_units_cache.parquet`, built 2026-10-30; its `code_key` is not the current one, so tax-unit construction may differ slightly from what a fresh rebuild gives; nothing was written). The recalibrated base reproduces DOTAX counts and tax by class to under 1% (see the second table in B), which is what the docstring promises, so it is the better stand-in for what `publish.py` produces. The synthetic tail is the same in both: `tail_k` = 1.35144 either way. A should be insensitive to the choice (it uses only the 24 tail rows); I ran it on the pickle.

Something to raise with whoever runs the chain: if the pickle on a worktree is Oct 9, a script that reads it directly (the `analyses/` scripts do) is on the old calibration. The chain's own step 1 recalibrates and, if the content differs, rewrites it.

## A. Does the composition lumping matter for a published number?

### What the synthetic rows carry [measured, `scope_build_units.py`]

24 rows (6 tiers x 4 filing statuses), weight 1,633, AGI $6.82B, weighted gains share 31.5%. Non-zero numeric columns include `primary_wagp` = (1 - s) x AGI, **`primary_intp` = s x AGI** (the gains dollars), `earned_income` = (1 - s) x AGI, `investment_income` = s x AGI, `synthetic_cg_share` = s. No `primary_semp`, `primary_div`, `primary_retp`, nothing on the secondary person. (`top_income_synthesis.py`, Override 3.)

### Path by path

| Path | Do synthetic rows pass through it? | Reads which columns | Effect of relabelling the non-gains income |
|---|---|---|---|
| Hawaii tax (`liability/hawaii.py`, `calculate_hawaii_tax_for_units`) | yes | `income`, `filing_status`, `num_dependents`, `synthetic_cg_share` (gains cap, §235-51(f)) | **None** [measured]: tail tax before credits $580.729M under all four labellings, difference $0.00 (script part 1). All non-gains income is taxed identically; only the gains share matters. |
| Gains anchoring (`cg_anchor.anchor_nltcg`) and `impute_capital_gains_from_soi` | yes | `income`, `synthetic_cg_share` | None [read]. |
| Act 24 chain growth (`act24_population.project_units`) | yes | `project_tax_units_forward` without `cbo_aging`: county B19013 scalar, then the top premium, both on `income` | None [read]; composition is not an input to this aging. This is the path every Act 24 number comes from (`chain/forecast_sb3125_enhanced.py`). |
| CBO component aging (`cbo_aging.age_filers_with_components`) | In `recalibrated_population.project` (vs-FY26 and capital-gains steps) the as-built tail goes through it before the SOI anchor, then is replaced; in the poverty/working-family builders there is no tail | `primary_wagp`, `primary_semp`, `primary_intp`, `primary_div`, `primary_retp`, `synthetic_cg_share` | See below. The as-built labels are wrong here (double count); but in the one path that ages the tail, `year_recalibrator` step 5b zeroes every row with `agi >= $1M` and substitutes `synthesize_top_filers_from_soi`, whose tier averages are aged from SOI Table 1.4's full component mix (`_aged_tier_avg_agi`). So the Pareto rows' labels never reach a published figure there [read, not run end to end]. |
| ETI (`apply_eti_response`) | yes | `income` only; the factor scales all income, gains included, by the bracket marginal rate | None from composition [read]. (Side note, not pursued: it prices gains at the ordinary bracket rate, though §235-51(f) caps the gains rate at 7.25%. That is a separate question about the ETI channel, not about labels.) |
| Migration (`apply_migration_response`) | yes | `income` (tier thresholds), `weight` | None [read]. |
| Realization (`apply_realization_response`) | yes | `synthetic_cg_share` | None from the non-gains split [read]. |
| PTE election (`estimate_pte_election_shift_M`) | yes | `income` x (1 - `synthetic_cg_share`) x a flat `PASS_THROUGH_SHARE = 0.20` | None today: `pte_capture` is 0 in LOW, MID and HIGH (Act 58 addback). The flat 20% does not read the rows' composition at all; it is a separate assumption (its comment cites 12.6% of income for Hawaii $200K+ filers). If capture is ever set above 0, this is the first place a relabel would matter, since the national $1M+ pass-through share (26.4% of AGI, 38.8% of non-gains income) is about double the 20%. The `SB3125_CD1_FORECAST.md` section 5c table (MID 70% capture, -$185M) is a historical description, not the current setting. |
| Federal side (`add_federal_income_columns`, EITC, CTC, `federal_tax_before_credits`) | yes (columns rebuilt on the tail rows) | `primary_wagp` (x 0.87 W-2 factor), `earned_income` | Changes the tail's `federal_agi` but nothing published reads it: EITC/CTC are zero at $1M+, the IRS household rake and family margins run on the base before the tail exists, `agreement.py` is a diagnostic [read]. Measured: tail `federal_agi` $6,214M as built (91.1% of income), $6,575M under the national mix, $6,636M under the Hawaii mix [measured]. |
| Calibration targets | no | The IPF/bounded rake runs on the base before synthesis; `forward_agi_targets` uses its own tabulated SOI compositions (`_BRACKET_COMPOSITIONS`, `_SOI_TIER_COMPOSITIONS`), not the rows; the tail is calibrated to DOTAX's $1M+ **tax** (`tail_k`), which is composition-blind | None [read]. |

So: for every published number, composition is read only through `synthetic_cg_share`. The lumping into "wages" is cosmetic on the paths that matter.

### The counterfactual, run [measured, `scope_composition.py 2027 2031`]

If the MID chain aged the tail by CBO components instead of the county scalar (it does not), what would the labelling do? I re-aged the 24 rows with `age_filers_with_components` (CBO Feb 2026, Hawaii factors; `base_year` 2024) under four labellings, applied the same top premium, replaced the tail's income and gains share in the projected TY population, refreshed tax (`refresh_stale_hawaii_tax`) and scored Act 24 (CD2 system) against Act 46 with `compare_systems`, static (no behavioral response, no gains-anchor factors, stacked gains shortcut: **so the dollar levels are not the published ones, only the differences mean anything**).

Labellings (the gains share per row is the model's own in all four):
- `asbuilt`: wages (1 - s), "interest" = the gain dollars (the current columns).
- `twobucket`: wages (1 - s), nothing else (what the label intends).
- `national`: the non-gains income split wages 27.6 / pass-through 26.4 / dividends+interest 8.5 / other 5.6 (national SOI $1M+, normalised over the non-gains 68.1%).
- `hawaii`: wages fixed at 20.9% of AGI (Hawaii SOI Table 2), the rest of the non-gains income split pass-through / dividends+interest / other in the national ratios. Only wages is a Hawaii number; the split of the remainder is an assumption.

| TY | Tail growth 2024 -> TY, ex premium | Tail AGI vs chain | Act 24 static delta | Change |
|---|---|---|---|---|
| 2027 chain (county scalar) | 1.1328 | | $48.2M | |
| 2027 CBO, as built | 1.5429 | +36.2% | $95.4M | +$47.2M |
| 2027 CBO, two bucket | 1.1549 | +1.9% | $46.7M | -$1.5M |
| 2027 CBO, national mix | 1.1636 | +2.7% | $47.9M | -$0.4M |
| 2027 CBO, Hawaii-wage mix | 1.1650 | +2.8% | $48.1M | -$0.2M |
| 2031 chain (county scalar) | 1.2900 | | $78.9M | |
| 2031 CBO, as built | 1.7369 | +34.6% | $144.5M | +$65.6M |
| 2031 CBO, two bucket | 1.2526 | -2.9% | $78.6M | -$0.3M |
| 2031 CBO, national mix | 1.2744 | -1.2% | $81.6M | +$2.7M |
| 2031 CBO, Hawaii-wage mix | 1.2781 | -0.9% | $82.1M | +$3.2M |

Reading it:
- **Relabel, same engine** (two bucket -> national or Hawaii mix): tail AGI +0.8% (TY2027) to +1.7-1.9% (TY2031); Act 24 static bracket delta +$1.2-1.4M (+2.6-3.0%) in TY2027 and +$3.0-3.5M (+3.8-4.5%) in TY2031. Direction: up, because dividends and interest (CBO factor with Hawaii ratio 1.00) and pass-through (0.90) outgrow wages (0.85) and gains (0.85). Order of magnitude: a few percent of a bracket delta that is itself about $50-80M a year, on a path the chain does not use. [measured, but only for this counterfactual]
- **Engine, not labels, is the larger lever**: switching from the county scalar to CBO aging moves the tail by +2.7% to -1.2% (TY2027, TY2031), similar in size to the relabel.
- **As built the rows are wrong under CBO aging**: the same dollars are counted as gains and as interest, so the component sum is 1 + s of income (`cbo_aging` logs a decomposition warning; the `other` floor hides it) and the tail grows 36% too fast (1.54 vs 1.15 by TY2027). Nothing published goes through this path with the Pareto rows retained: the MID chain does not use CBO aging, and the recalibrated path replaces them. It would bite the first analysis that does both. [measured for the effect; "nothing published reaches it" is read.]
- Act 46 revenue moves by the same tail growth (+$8-15M in TY2027 for the labelled mixes); not shown in the delta.

### Mid-tail composition (not the $1M+ rows)

The $500K-$1M and $200K-$500K rows are PUMS units with real wage/self-employment/interest/dividend columns; the earlier test's table (PUMS-based wages 64-65%, SE 7-16%) describes them. Their gains share comes from `impute_capital_gains_from_soi`. Nothing here changes that.

## B. Deductions: wrong, or the wrong benchmark?

### How the model sets deductions for the top

- `compute_base_tax(units, deduction_params=ded_params, tax_year=2023)` scores every unit on the **expected** itemized deduction, `max(standard, E[itemized])` (`liability/hawaii.py::_expected_itemized_deductions`): mortgage interest (homeownership probability x an AGI-tiered base, x 1.3 for joint filers), charitable (claim rate 0.727 x AGI x an AGI-tiered giving rate, 6% to 15% of AGI above $300K), medical above 7.5% of AGI, real-estate tax by AGI tier, then Hawaii's Pease limitation (3% of AGI over $166,800, capped at 80% of the total, `HRS §235-2.4(f)`). Nobody is stochastic; **every unit gets the expected itemized figure, so the model itemizes 100% of units** (the params file's own note: actual Hawaii itemization is 61% overall, 67-78% at $200K+). No state income tax is deducted (Hawaii disallows it), so the SALT line is real-estate tax only.
- The "deduction basis" on `CalibratedBase` is `ded_params` plus `tax_year`: the canonical `packages/tax_modeler/config/deduction_params.json` (fingerprint `ab6b63a25f3fa23b`) and TY2023. The tail calibration, `age_synthetic_tail` and the projections re-score on it (projections on `scale_deduction_params_for_target_year`, which scales the mortgage tiers).
- `data/policy/baseline_2022_deductions.json` is **not read by any code**: the only reference in the repo is a mention in `packages/tax_modeler/README.md`. It plays no part.

### DOTAX deductions as a share of AGI [measured, from the bundled JSON]

(AGI - taxable income) / AGI, which for DOTAX is deductions plus personal exemptions; the model's `hi_agi - hi_taxable_income` is the same quantity. Table A-1 (resident taxable returns), TY2019 / 2021 / 2022 / 2023:

| AGI class | 2019 | 2021 | 2022 | 2023 |
|---|---|---|---|---|
| $100-150K | 18.5% | 16.8% | 16.9% | 16.6% |
| $150-200K | 17.6% | 15.6% | 16.0% | 16.0% |
| $200-300K | 10.9% | 9.7% | 9.9% | 10.3% |
| $300-400K | 8.2% | 6.9% | 7.4% | 7.7% |
| $400K+ | 11.1% | 7.0% | 5.3% | 7.5% |

Table A-8 (`1 - etr_agi/etr_ti`, rates printed to 0.1 point so +-1 point) for the top classes: $400-500K 7.2 / 6.1 / 6.0 / 7.1%, $500-750K 6.8 / 4.6 / 4.6 / 5.6%, $750K-$1M 5.4 / 4.4 / 4.4 / 5.4%, $1M+ 13.8 / 7.5 / 6.1 / 8.5% (same year order). The $1M+ class is volatile (2019's 13.8% sits on a $413M class with 1,163 returns).

Hawaii's standard deduction is $2,200 / $4,400, so DOTAX's figure above $200K is itemized deductions plus exemptions (about 1-2% of AGI at $200-300K); it is the right benchmark. The national SOI figure in the earlier doc (4.7% / 4.9% / 5.8%) is the bundled `itemized_M` over AGI: it counts itemized deductions only (not the federal standard deduction, about 9% of AGI at $300K) and includes the state income tax deduction Hawaii disallows. It does not measure what Hawaii's taxable income is built from. **The national comparison is the wrong benchmark;** comparing with DOTAX says the model is high at $200K-$1M, and not far off above $1M.

### Model vs DOTAX, by class, TY2023 [measured, `scope_deductions.py`]

Recalibrated base (current calibration; the pickle in parentheses), model `(AGI - TI)/AGI` on the same classes:

| Class | DOTAX | Model, recalibrated (pickle) | AGI vs DOTAX | Taxable income vs DOTAX | Returns vs DOTAX |
|---|---|---|---|---|---|
| $100-150K | 16.6% | 19.6% (20.3%) | +3.8% (+16.5%) | +0.1% (+11.2%) | +0.2% (+16.3%) |
| $150-200K | 16.0% | 20.9% (21.8%) | +5.9% (+19.1%) | -0.2% (+11.0%) | +0.2% (+19.1%) |
| $200-300K | 10.3% | 18.5% (19.8%) | +8.8% (+12.2%) | -1.1% (+0.2%) | +0.2% (+11.9%) |
| $300-400K | 7.7% | 18.8% (20.4%) | +10.8% (+14.3%) | -2.5% (-1.4%) | +0.9% (+14.5%) |
| $400-500K (A-8) | 7.1% | 15.2% (14.2%) | +7.9% (+2.3%) | -1.5% (-5.5%) | |
| $500-750K (A-8) | 5.6% | 15.7% (16.7%) | +7.8% (+10.3%) | -3.7% (-2.6%) | |
| $750K-$1M (A-8) | 5.4% | 12.7% (12.2%) | +5.0% (+3.2%) | -3.1% (-4.2%) | |
| $1M+ (A-8) | 8.5% (TY2022 6.1%) | 9.8% (9.8%) | see below | see below | |

(Under $100K the gap is 0-5 points in both; the $0-10K class is not comparable, the model's expected itemized deduction exceeds AGI there.)

Tax before credits by class on the recalibrated base against the A-8 targets: $100K+ $466M vs $467M, $150K+ $318M vs $319M, $200K+ $345M vs $349M, $300K+ $163M vs $166M, $400K+ $101M vs $103M, $500K+ $149M vs $157M, $750K+ $83M vs $87M. On the pickle: $512M, $350M, $345M, $164M, $101M, $149M, $83M, with counts 12-19% over at $100-200K.

### Does calibration pin it? Partly, and not the share.

Read from `act24_calibration.py`: the bounded rake's targets are filer counts by A-8 class, filing status, **tax before credits by class**, and family margins; AGI by class is optional (`agi_margin=True`, off: it is infeasible at the default bounds, because "the model's units carry too much AGI per filer above $200,000", and the docstring puts the overage at 11-23% above $200K). Taxable income is not a target. Measured, the effect is what that implies: with counts and tax pinned by class and a deduction that is too large, the AGI is too large by about the same dollar amount, and **taxable income, the thing the brackets act on, ends within 0.1-2.5% of DOTAX from $50K to $400K** (and -1.5% to -3.7% at $400K-$1M, from A-8's rounded rates). So deductions as a share of AGI are not pinned and are about 2x Hawaii's at $200K-$1M, but because AGI is high by the same dollars, taxable income and tax are right. The two errors offset by construction; they are one error in the PUMS income that the rake compensates for.

What this leaves exposed: anything that scales on AGI rather than taxable income. The top-income premium and ETI act on `income` (AGI), so a given percentage change moves the larger AGI by more dollars than DOTAX's AGI would, while the bracket sees the same taxable base: the response is slightly overstated in dollars at $200K-$1M, and the AGI-share statistics in the earlier doc inherit the overage. I did not size those effects.

### The $1M+ class: what the tail does with deductions

DOTAX's printed $1M+ class is $441M (TY2023), but the model's target is the TY2019-2023 window's mean share, $562.3M and 1,633 returns (`dotax_base.top_class_anchor`), so a direct AGI comparison with the printed class is misleading (model $6.82B vs the A-8-implied $5.13B, +33%; at the window's $562.3M the implied AGI is about $6.5B, so +4%). The effective rate on AGI is the like-for-like check: model 8.5% vs DOTAX A-8 8.6% (TY2023; 9.3% TY2022, 7.5% TY2019). Deduction share above $1M: model 9.8%, DOTAX 6.1-8.5% (TY2021-23).

Tail AGI as a function of the deduction level [measured, `scope_tail_deductions.py`]: the tail is calibrated to the tax target, so a smaller deduction is paid for in AGI. Scaling the mortgage, real-estate and charitable amounts by f and re-running the tail calibration:

| f | tail_k | tail AGI | (AGI - TI)/AGI | tail tax |
|---|---|---|---|---|
| 1.0 (as is) | 1.3514 | $6,621M | 9.9% | $562.3M |
| 0.8 | 1.3120 | $6,427M | 7.5% | $562.3M |
| 0.6 | 1.2741 | $6,242M | 5.1% | $562.3M |
| 0.4 | 1.2376 | $6,063M | 2.6% | $562.3M |

At DOTAX-like deductions (6-8%) the tail's AGI would be 3-5% lower and its taxable income about 0.7% lower (5,966 -> 5,924 at f = 0.6). The published $1M+ tax is unchanged by construction (it is the target). The Act 24 bracket delta works on taxable income above $350-400K, so it should move by about the 0.7% TI change at most. [TI arithmetic measured; the Act 24 consequence is read, not run, because projection re-scores on the canonical params, so a scaled-param counterfactual through `projected()` would be inconsistent.] This is not a lever worth pulling.

## C. What, if anything, should change

| Option | What it touches | Published figures that move | Fixtures and keys | Verdict |
|---|---|---|---|---|
| **1. Do nothing; document (this file)** | nothing in code | none | none | **Recommended now** |
| 2. Relabel the tail's non-gains income by an SOI mix (national tier shares from `soi_top_anchor.load_soi_top_anchors`, wages pinned to Hawaii's 20.9%) and drop the gains from `primary_intp`/`investment_income` | `scenarios/top_income_synthesis.py` (Override 3 and `_TAIL_INCOME_COLUMNS`, which must scale any new columns or the rows go inconsistent after `tail_k` and the aging), `soi_top_anchor.py` rows (lines 415-418), `adjustments/ultra_high_income_synthesizer_v2.py` (`SYNTHETIC_CG_SHARES` unchanged) | None expected: Hawaii tax, ETI, migration and PTE (capture 0) are blind to it, so outputs should stay byte-identical; `test_headline_figures` would confirm. Tail `federal_agi` changes (unpublished). | `tax_modeler.base` is in `construction_code_key()`'s closure, so the unit cache rebuilds (about 2.5 min) and the population store and simulator golden fixtures re-key; unit tests on the rows' columns change | Worth doing only when something reads composition (below). Fixes the double count. |
| 3. Add full component columns (`primary_semp`, `primary_div`, `primary_retp`) and switch the MID chain to CBO aging | as 2, plus `project_units` (`cbo_aging=CboAging()`), and `tax_unit_projector` | Yes: the tail ages at a different rate; counterfactual above: tail AGI +2.7% (TY2027) to -1.2% (TY2031), Act 24 static bracket delta within about +-5%; every Act 24 page, the simulator and the headline test | Everything in 2 plus all published runs | Not recommended: it is a change of aging method (an assumption) bundled with a labelling clean-up, and the data cannot validate the finer split (Hawaii SOI Table 2 gives wages only). |

Recommendation: option 1 now. Concretely:
1. Leave the synthesis, `tail_k` and the deduction params as they are. Composition does not move any number, and deductions at $1M+ are within 1-4 points of DOTAX with the tax pinned.
2. When a change makes composition live (PTE capture above 0, the MID chain on CBO aging, federal reporting on the tail), do option 2 first: it is small, and its first step is removing the gain dollars from `primary_intp`. Until then a guard is cheap: a test that `cbo_aging._component_amounts_for_filer` sums to `income` on the synthetic rows would have caught the double count.
3. Do not try to bring the model's deduction share to DOTAX's by tuning the params: AGI above $200K is high by 4-11% for PUMS reasons, taxable income is right, and lowering deductions without lowering AGI would raise tax above DOTAX's.
4. Separately: the cached calibrated base here is the 2026-10-09 pickle. Anyone reading it directly (analysis scripts, ad hoc work) is on the pre-bounded calibration; the chain's step 1 refreshes it.

## Limits

- The pickle is stale (see the caveat). B's DOTAX comparison is shown on both bases; A is insensitive. The recalibrated base uses the 2026-10-30 raw cache, whose code key is not the current one.
- The Act 24 deltas are static, TY2027 and TY2031 only, CD2 system, without the gains anchor factors and with the stacked gains shortcut; the dollar levels are not the published ones. After the relabel I recomputed the income and gains share of the 24 rows and refreshed tax, but did not re-run `calibrate_synthetic_tail_to_tax_target` (it is composition-blind, so `tail_k` does not change).
- The Hawaii mix fixes only wages (Hawaii SOI Table 2). Pass-through, dividends, interest and "other" use national ratios; the Hawaii truth for those is not in the bundled data.
- "Nothing published reaches the as-built CBO aging" is a code reading (the recalibrated path zeroes every aged row at or above $1M and substitutes SOI rows); I did not run the vs-FY26 chain.
- DOTAX A-8 deduction shares have rounding error of about +-1 point (rates printed to 0.1); A-1 shares do not.
- The ETI-on-gains note and the income-versus-taxable-income exposure of the premium and ETI are flagged, not sized.
