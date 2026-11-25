# Diagnosis: why the model has too few federal-EITC-eligible units (Hawaii, TY2022)

Diagnosed 2026-11-25 on branch `diag/eitc-eligible-shortfall`. Read-only: no default, calibration, published number, `site/` or `runs/` file changed, nothing implemented.
Script: `analyses/eitc_eligible_shortfall.py` (run from the repo root, about 6 s, under 1 GB; output reproduced below). Inputs: the cached chain units `data/artifacts/working_family_units.pkl` (2024 1-year PUMS, 8,218 units), `packages/data/raw/pums_2024_1yr/psam_p15.parquet`, and IRS SOI Historic Table 2 for Hawaii TY2022 (`22in12hi.xlsx`, curl, HTTP 200, 2026-11-25; the bundled `irs_soi_hi_table2.json` has no EITC-by-AGI rows, so the EITC cells are typed into the script).

"Measured" below means the script recomputed eligibility on the cached units with the change applied. "Reasoned" means argued from code or outside figures, not run. The earlier test's number reproduces exactly: 68,620 raw eligible units, $128.4M.

## Answer in four lines

1. The missing units are families with children: 1-child -17.3K, 2-child -8.6K, 3+ -4.0K against claims/0.833; childless is only -3.4K (and the model is above the IRS claim count, 28.8K vs 26.9K).
2. The cause is mostly **who claims the children**, not who is poor. The constructor hands every grandchild (and every non-student relative/partner's child) to the householder, who is usually the highest earner in the household, so the low-income parent files with no children. Fixing the claimant rules lawfully recovers **+12.2K** (measured). The pool check shows the shortfall is in units that carry the child at all, not in the eligibility screen.
3. The earlier test also scored **2024-dollar incomes under 2022 rules** (PUMS 2024 incomes, no deflation). Putting incomes in 2022 dollars recovers **+7.1K** (CPI) to +12.1K (x0.90) (measured). This is a defect of the TY2022 comparison, not of the production model, which runs TY2024 on 2024 dollars (raw 83,063 vs IRS TY2024 claims 81,800).
4. With both applied, the model has 88.8K eligible (90.3K with an AGI fix), 87-89% of the 102,005 implied by the Census rate and 105% of the 84,970 IRS claims. The remaining 12-13K is within what the benchmark's own definitions and erroneous claims could account for (reasoned). The 0.98 take-up factor is then about 0.94-0.96 of eligible units, not 0.83, because claims would still sit below eligibles.

## 1. Where the missing units are (measured)

Replicate-weight standard error of the raw eligible total: 3,140 (1.645 SE = 5,165). The gap is 10 SEs, so it is not sampling noise; single counterfactuals under about 1-2K are.

**A. By qualifying children** (claims = SOI Table 2 published totals; implied = claims / 0.833):

| kids | model raw | SOI claims | claims/0.833 | model - implied |
|---|---|---|---|---|
| 0 | 28,848 | 26,900 | 32,293 | -3,445 |
| 1 | 16,109 | 27,820 | 33,397 | -17,288 |
| 2 | 13,554 | 18,460 | 22,161 | -8,606 |
| 3+ | 10,109 | 11,790 | 14,154 | -4,045 |
| all | 68,620 | 84,970 | 102,005 | -33,385 |

**B. By filing status x kids** (model eligible; weights include the DOTAX status factors):

| status | 0 | 1 | 2 | 3+ | eligible | all units |
|---|---|---|---|---|---|---|
| single | 26,158 | 569 | 0 | 0 | 26,727 | 389,661 |
| head of household | 55 | 11,050 | 6,237 | 6,214 | 23,556 | 79,131 |
| married joint | 2,635 | 4,490 | 7,317 | 3,895 | 18,337 | 252,299 |
| married separate | 0 | 0 | 0 | 0 | 0 | 18,652 |

HoH holds 23.6K of the 39.8K with-children eligibles. Raw HoH before the 1.30 factor is 79,131/1.30 = 60.9K against SOI's 71,180 HoH returns: the model builds too few HoH units, and the 1.30 factor (fitted to DOTAX status shares, see `scripts/diagnose_filing_status.py`) hides part of it.

**C. By AGI class x kids** (federal_agi, as SOI classes; model minus SOI claims):

| kids | <$1 | $1-10K | $10-25K | $25-50K | $50-75K |
|---|---|---|---|---|---|
| 0 | -600 | +1,100 | +1,437 | 0 | 0 |
| 1 | -130 | -796 | -4,326 | -6,449 | 0 |
| 2 | -80 | -505 | -2,754 | -2,151 | +575 |
| 3+ | -40 | +523 | -15 | -1,766 | -393 |

The shortfall sits in the $10-50K classes for 1- and 2-child families (-15.7K of their combined -25.9K gap to claims/0.833; -10.8K of it for 1 child). The model has too many childless units at the bottom, too few child-bearing units in the working-poor middle.

**D. The pool.** Units with b EITC children, earnings above zero and AGI under $50K, before any eligibility test: 1 kid 19,483; 2 kids 13,007; 3+ 9,062. SOI claims in the same classes: 27,810 / 17,630 / 10,360. The 1-child pool is smaller than the IRS claim count, so the missing units are not lost to the earnings, income or investment screens; the model never builds them. (Eligible within the pool: 16,109 / 12,139 / 9,062.)

**E. By household structure** (eligible units; households classed from RELSHIPP: partner/unrelated = any unmarried partner, roommate or other nonrelative; extended = any parent, grandchild, sibling, in-law or other relative; else nuclear):

| structure | eligible 0 | 1 | 2 | 3+ | total | units with an EITC child | all units |
|---|---|---|---|---|---|---|---|
| nuclear | 8,269 | 9,096 | 5,529 | 4,418 | 27,313 | 85,285 | 382,843 |
| extended | 8,550 | 2,966 | 4,404 | 2,081 | 18,000 | 26,242 | 165,621 |
| partner/unrelated | 12,029 | 4,047 | 3,621 | 3,610 | 23,307 | 25,570 | 191,278 |

After the chained construction fixes (section 2): nuclear 27,707 (+0.4K), extended 25,005 (+7.0K), partner/unrelated 28,075 (+4.8K). The missing units are in complex households, 11.8K of the 12.2K.

**F. Independent check that kids sit in too few returns** (same units vs SOI Table 2, TY2022; model on unaltered 2024-dollar incomes):

| measure | model baseline | model after chain | SOI |
|---|---|---|---|
| returns with nonrefundable CTC | 125,394 | 132,797 | 154,800 |
| returns with ACTC | 41,374 | 54,290 | 61,270 |
| dependents, unit weight x claimed | 375,010 | 377,089 | 307,820 |
| dependents, own PWGTP | 344,907 | | 307,820 |

The model has more dependents than SOI (so the weights do not undercount children) but 19% fewer CTC returns and 32% fewer ACTC returns: the same kids are packed into fewer, higher-income returns. Moving claimants lifts the ACTC count 31% (65% of the way to SOI) with no EITC input, which is the corroboration. (SOI dependents = individuals 1,238,090 - returns 689,960 - joint returns 240,310; model dependents include adult qualifying relatives.)

## 2. Candidate causes, one at a time

Gap to explain: 102,005 - 68,620 = 33,385. Changes are weighted eligible units versus the 68,620 baseline.

| # | Candidate | Basis | Eligible units recovered | By kids 0 / 1 / 2 / 3+ |
|---|---|---|---|---|
| 1a | Grandchildren go to their parent (an adult child or child-in-law of the householder, 15+ years older), only where exactly one candidate parent | measured | +2,568 | -924 / +1,783 / +1,371 / +337 |
| 1a | same, parent claims, best of several candidates (upper end of ambiguity) | measured | +6,543 | -2,368 / +4,460 / +3,662 / +788 |
| 1a | same, family-optimal (householder or parent, whichever gains more) | measured | +7,456 | -2,488 / +5,298 / +3,849 / +798 |
| 1b | Minors of an unmarried partner or other unrelated adult go to that adult (today claimed by no one) | measured | +1,289 | -589 / +1,494 / +373 / +11 |
| 2a | Nieces/nephews/siblings (RELSHIPP 33/28 minors) count, go to a parent-aged relative or the householder | measured | +2,871 | -888 / +2,219 / +554 / +986 |
| 2b | Every minor no unit claims goes to the householder | measured | +426 | 0 / +74 / -368 / +720 |
| 2c | 18-year-old own children (under 19 at year end) stay qualifying children | measured | +320 | 0 / +320 / 0 / 0 |
| 2d | Disabled non-earning adult children qualify at any age (DIS=1 as proxy) | measured | +197 | 0 / +170 / -10 / +37 |
| 1-2 | **Chain of 1a (family-optimal), 1b, 2a-2d** | measured | **+12,167** (80,787) | -3,965 / +9,534 / +4,766 / +1,831 |
| 1e | Any adult relative (grandparent, aunt, older sibling) claims any minor, family-optimal | measured, **unlawful** | +18,512 | -4,092 / +15,105 / +6,474 / +1,026 |
| 3-V | Incomes in 2022 dollars (CPI-U x0.933) | measured | +7,141 | +1,061 / +3,460 / +2,022 / +598 |
| 3-V | Incomes x0.90 (wages grew faster than CPI) | measured | +12,144 | +4,866 / +3,936 / +2,515 / +827 |
| 3a | Drop the investment-income test (ACS INTP also holds rent and royalties) | measured | +476 | 0 / +407 / 0 / +69 |
| 3b | W-2 wage factor 1.00 instead of 0.87 | measured | **-8,740** (the haircut helps) | -3,464 / -1,613 / -2,504 / -1,158 |
| 3c | AGI excludes SSI, public assistance and other income that PINCP includes | measured | +1,569 | +792 / +187 / +367 / +223 |
| 3d | AGI = earned income only (floor of what any return could show) | measured | +6,069 | +2,454 / +1,683 / +1,484 / +447 |
| 4 | MFS units allowed to claim | measured, **invalid** | +2,596 | +1,561 / +216 / +819 / 0 |
| 5 | Remove the DOTAX status weight factors (0.82 / 1.00 / 1.30 / 1.35) | measured | +431 net | **+5,729** / -2,425 / -1,439 / -1,434 |
| 6 | Benchmark definitions and erroneous claims | reasoned | see section 4 | |
| chain | 1-2 chain plus 2022-dollar incomes (CPI) | measured | +20,165 (88,785) | -3,026 / +13,635 / +7,013 / +2,542 |
| chain | ... plus AGI without non-taxable PINCP components | measured | +21,641 (90,261) | -2,268 / +13,701 / +7,497 / +2,711 |

Dollars follow (measured): the full chain moves eligible federal EITC dollars from $128.4M to about $204M (+$76M), against SOI's $185.9M of claims.

Notes on each:

**1. Tax-unit construction (measured).** `identify_dependents` ranks guardians householder first, spouse second, then others by ascending income, but `_is_parent` can only match the householder or the householder's spouse to an own child or **grandchild** (RELSHIPP 25-27, 30), and the foster test; an adult child (25-27) or child-in-law (32) is never the "parent" of a grandchild (30). So every grandchild lands in the householder's unit; the adult child with the kid files alone as childless (this is why d_0 is negative above: those units move out of the childless bucket, they are not lost). Unmarried partner's children (RELSHIPP 36) and other nonrelative minors, plus non-student minor relatives (33, 28), are claimed by nobody: `_find_potential_guardians` only falls back to the householder for **students**, so under-5s and unenrolled kids drop out. They are about 5,300 weighted minors out of roughly 300K, which is why 1b/2b are small. The AGI tie-break matters: a lower-earning grandparent, aunt or older sibling cannot claim a child over a higher-AGI parent who can claim (1e is therefore an unlawful upper bound, shown only to size the stake); moving a child to the **parent** is always lawful. "Family-optimal" assumes a family files in whatever way yields the larger credit, which IRS claims data reflects. The candidate-parent choice is ambiguous in PUMS (no parent pointer; RELSHIPP plus a 15-year age gap only): 2.6K is the households with one candidate, 6.5-7.5K those where several adult children could be the parent. 2a includes cousins (RELSHIPP 33 cannot separate nieces/nephews from cousins), so it is partly an upper bound.

**2. Qualifying-child rules (measured).** Beyond claimant assignment the age/relationship tests cost little: 2c 18-year-olds +0.3K, 2d disabled adults +0.2K. The student test uses `SCHL >= 16` (attainment) on dependents that `identify_dependents` already selected by `SCH` enrollment, so it is generous rather than strict (reasoned); PUMS has no full-time flag. Citizenship screens children with CIT 5, which the EITC does not require of the child (needs an SSN valid for work): not measured, small.

**3. Income measurement.**
- *Dollar year (measured, defect in the test).* PUMS 2024 incomes are in 2024 dollars (`PUMS_INCOME_DOLLAR_YEAR = 2024`); the earlier test's TY2022 branch calls `compute_base_tax(..., tax_year=2022)` with no deflation, so 2024 wages meet 2022 phase-out thresholds. The production TY2024 run is dollar-consistent. Running the same units with TY2024 rules gives 83,063 raw eligible (IRS TY2024 claims 81,800, DOTAX 78,399): participation 98.5%, the same picture as TY2022's 124% raw. With chain fixes TY2024 gives 95,342 against 98,199 implied at 83.3%.
- *Federal AGI (measured).* `income` is PINCP, which includes SSI, public assistance and other non-taxable income; `federal_agi` removes the wage haircut and adds taxable SS but keeps those. Removing them lifts eligibility +1.6K. The phase-out uses `max(earned, AGI)`, so this matters for low earners with benefits.
- *W-2 factor 0.87 (measured, sign opposite to a fix).* Lower federal earnings put more units inside the phase-in/plateau; removing the haircut costs 8.7K. Any rework of income must keep this in mind: it is the biggest silent support for the current count.
- *Investment test (measured).* INTP includes rent and royalties, so some units fail the $10,300 limit wrongly; only +0.5K.
- *Unit vs household income (reasoned).* The unit uses the filer(s)' own income, not household income, which is correct for the credit.
- *Aged year (reasoned and measured above).* For TY2024 and later `project_tax_units_forward` anchors at the 2024 dollar year, so the test is consistent there; the inconsistency is only the TY2022 comparison.

**4. Filing status (measured).** MFS is built only for couples with combined income at or above $150K (`_should_file_separately`), median unit AGI $149,826. Letting those units claim (+2.6K, mostly childless) would credit the low-earning spouse of a rich couple, which is wrong: the lawful recovery is 0. HoH versus single does not change the EITC schedule (same thresholds), so the HoH construction problem matters only through who holds the kids. Joint couples: 2,828 joint units are present; not tested separately.

**5. Weights (measured).** Hybrid weights do not undercount children (section F: unit weight x claimed dependents is 9% above own PWGTP). The status factors do shift the mix: they push 5.7K childless singles (x0.82 pulls them down, so removing the factor adds them) against 5.3K children-bearing units (x1.30), net +0.4K. Without the HoH 1.30 factor the with-children shortfall would be 5.3K larger, so the factor is already compensating for the same construction problem as 1a. The lever-3a reweight sits on top of that.

## 3. Ranking by units explained (gap 33,385)

| rank | cause | units | basis | fixable in model |
|---|---|---|---|---|
| 1 | Claimant assignment: grandchildren, relatives' and partners' children go to the householder or to nobody | +12.2K chained (+2.6K to +7.5K for grandchildren alone, depending on parent ambiguity) | measured | yes, constructor |
| 2 | TY2022 test scored 2024-dollar incomes under 2022 rules | +7.1K (CPI) to +12.1K | measured | test only; production is consistent |
| 3 | Benchmark denominator: Census eligibility rules and erroneous IRS claims | 8-17K of the 102K | reasoned | no |
| 4 | AGI includes non-taxable PINCP components | +1.6K | measured | yes, small |
| 5 | Investment-income test on INTP (rent, royalties) | +0.5K | measured | yes, small |
| 6 | Status weight factors (mix, not total) | +0.4K net, moves 5.7K between childless and kids | measured | via recalibration |
| 7 | MFS | 0 lawful | measured | no |
| - | W-2 wage factor | -8.7K if removed | measured | keep |

The chain 1 + 2 + 4 gives 90.3K, leaving 11.7K (11.5%) to the Census figure and -5.3K (+6%) above the IRS claim count of 84,970, so claims over eligible units would be about 94% (IRS count 84,970 over 90.3K). Components are not strictly additive (the chain is measured, 20.2K, vs 12.2K + 7.1K = 19.3K).

## 4. How much may be definitional (reasoned, not measured)

- **The 102,005 is a constructed number.** It is 84,970 SOI claims / 0.833. The Census rate's numerator is IRS payment records for a different tabulation (the IRS statistics page counts 82K returns processed for TY2022; the earlier test noted 82K vs 84,970), so the Census denominator may be nearer 82/0.833 = 98.4K. Range 98-102K.
- **Erroneous claims.** IRS compliance work puts improper EITC payments in roughly the high-20s to low-30s percent of dollars over recent years (recalled from Treasury/IRS improper-payment reporting; I could not re-fetch the IRS compliance pages this session, both returned 404), concentrated in qualifying-child and income errors. If that share applied to even 15-20% of the 58K with-children claims, 9-12K "eligible" claims would not be eligible under the model's rules. Whether the Census numerator excludes them is not documented on the IRS page, so I treat it as 0 to 12K.
- **Rules and population differences.** SOI counts every return filed from a Hawaii address (military, part-year residents, group-quarters filers) while PUMS covers resident households; Census models eligibility on ACS with its own treatment of filing status, qualifying children and income imputation. I cannot quantify these without their method paper (not fetched).
- Net: definitional room of roughly 8-17K, which is the size of what remains after fixes 1-2 (11.7K). The model is then not demonstrably short.

## 5. What a fix would touch, and what to do first

Recommendation, in order:

1. **Fix the comparison first (cost zero, no model change).** Any participation or take-up reading must use TY2024 model units against TY2024 claims, or deflate to 2022 dollars. Under TY2024 the model raw is 83.1K against 81.8K claims, so the "15% short after reweight" picture partly reflects the vintage error. Tell whoever reads the 0.98 factor as take-up that it is a fitted ratio, now more clearly so.
2. **Fix claimant assignment in the constructor (`packages/tax_modeler/src/tax_modeler/units/dependencies.py`, `identify_dependents`, `_is_parent`, `_find_potential_guardians`; `units/constructor.py` `_process_household`).** Parent-first for grandchildren (adult child or child-in-law 15+ years older, preferring the unit with the lowest AGI when several qualify, which the IRS tie-break allows only for the parent), partners' and relatives' minors to their adult, and fall back to the householder for all unclaimed minors, not only students. This is the largest and the only structural item.
3. **Then re-derive** what the current construction bakes in: the status factors (`_calculate_hybrid_weight`: single 0.82, HoH 1.30, MFS 1.35, fitted to DOTAX shares on the old construction; HoH should rise naturally), the lever-3a by-children reweight (`calibration/eitc_reweight.py`; its up-weights should shrink toward 1), and the take-up factors (`takeup_factors`).
4. Optional, small: AGI excluding non-taxable PINCP components (+1.6K) and an INTP-only split from rent/royalties.

What the construction fix touches: it changes `tax_modeler.base`'s import closure, so the raw-unit parquet cache (`data/artifacts/tax_units_cache.parquet`, code_key), `working_family_units.pkl` and the MID population store all rebuild; every chain step moves. Published figures that would move: the working-family credits page (federal and Hawaii EITC, CTC counts and dollars, take-up factors, which come from a fit), Act 24 / SB 3125 revenue and distribution tables (HoH/single mix, rake weights, credit dollars), the poverty runs (credit resources, child-bearing units), the simulator golden fixtures, and `tests/test_headline_figures.py` until republished. Expect the revenue effect to be small (income is the same, units split differently) but nonzero, and the credit counts to move toward SOI (ACTC and CTC returns by about +30% / +6% per section F). Needs the usual full suite and `publish.py` run on a branch with checks.

## Limits

- One 2024 1-year sample (8,218 units); eligible-total SE 3.1K. Counterfactual deltas under about 1-2K are inside noise; the chained 12K and the 7K dollar-year effect are not.
- The parent of a grandchild is inferred (RELSHIPP plus age gap), so the 1a figure is a range, +2.6K to +7.5K.
- The CPI deflator is a stand-in for 2022-dollar incomes; the real Hawaii wage path between 2022 and 2024 was faster, so +7.1K is the low end (+12.1K at x0.90).
- Family-optimal claiming is an assumption about behavior; the lawful claim is the parent's.
- The IRS improper-payment share is recalled, not fetched; section 4 is argued, not measured.
- Counterfactuals alter dependents lists only; unit weights stay as they were (a unit that gains a child keeps its person-based single weight, though the constructor would give it WGTP x 1.30), so 1a may be slightly understated.

## Script output (abridged)

```
Check vs the earlier test: raw eligible 68,620 (earlier test 68,620)
TY2024 rules on the same 2024-dollar units: eligible 83,063 ($172.1M); IRS TY2024 claims 81,800, DOTAX 78,399
TY2024 rules, construction fixes chained: eligible 95,342 (unfixed 83,063); implied at 83.3% from TY2024 claims: 98,199
CHAIN construction+vintage by kids: {0: 25822, 1: 29744, 2: 20568, 3: 12651} vs claims/0.833: {0: 32293, 1: 33397, 2: 22161, 3: 14154}
```
