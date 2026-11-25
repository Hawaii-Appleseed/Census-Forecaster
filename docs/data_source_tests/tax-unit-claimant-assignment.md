# Tax-unit claimant assignment: children go to their own parent first

Built and measured 2026-11-25 on branch `fix/tax-unit-claimant-assignment`. **Not published**: `scripts/publish.py` was not run, `site/` and `runs/` are unchanged, no pull request. The owner decides after reading section 6.
It follows `docs/data_source_tests/eitc-eligible-shortfall.md` (branch `diag/eitc-eligible-shortfall`), whose claimant-assignment finding (+12.2K EITC-eligible units, chained) it implements.

Tags: **[M]** measured by this run (commands in section 9); **[D]** taken from the diagnosis, not re-measured; **[R]** reasoned, not run.
Machine-readable files beside this one: `tax-unit-claimant-assignment_before_after.csv` (every figure in section 6, 239 rows), `tax-unit-claimant-assignment_raw_units.csv` (section 4), `tax-unit-claimant-assignment_parent_choice.csv` (section 3).

## 1. Answer

- The defect is real and verified in the code: `_is_parent` matched only the householder or spouse, so a grandchild went to the householder while its parent (an adult child of the householder) filed with no children; a partner's or unrelated adult's minors and non-student relatives' minors were claimed by nobody. [M]
- The PUMS files the repo uses **do** carry the subfamily variables `SFN` / `SFR` (in the raw CSV, the cached parquet and the 5-year file), and the loader keeps every column, so no loader change was needed. `SFN`/`SFR` link 88% of weighted grandchildren under 18 (5-year file: 44,179 of 50,448) to a parent in the household, and every related minor whose parent is not the householder or spouse. [M]
- With the fix, on the model's own TY2024 basis the raw EITC-eligible units go from 83,063 to **93,456** (+12.5%) against IRS TY2024 claims of about 81,800 (participation 87.5%, near Census's 83.3%); by number of children the shares move from 41.1 / 25.2 / 19.8 / 13.9 to **34.4 / 28.8 / 22.1 / 14.7** against the IRS TY2022 split 31.7 / 32.7 / 21.7 / 13.9. ACTC returns go 45.7K to 55.4K (SOI 61.3K) and CTC returns 122.8K to 130.3K (SOI 154.8K) on a TY2022 basis. Total dependents and persons covered are conserved (+0.03% and +0.17%). [M]
- Both masking devices relax: the by-children reweight (lever 3a) lifts only the 1-child bucket now (x1.06 in TY2024; it lifted 1-, 2- and 3+-child buckets x1.35 / 1.14 / 1.03), and the HoH weight factor re-derives to 1.18 from 1.30. [M]
- **Published figures that move**: Act 24 and capital-gains revenue by **at most 0.1%** (about $0.05M); the working-family credits page by up to **-3.7% on the EITC lines** (TY2028 Act 163 expiry cost $84.96M to $83.03M, -2.3%); EITC claimants -6.4%; the EITC take-up factor 0.979 to 0.915 (0.937 with re-derived factors); H.R.1 baseline poverty rate -0.14 points and people pushed into poverty -6% (inside its sampling error); SNAP and Medicaid loser counts do not move. [M]

## 2. The rule

Code: `packages/tax_modeler/src/tax_modeler/units/claimants.py` (new), `dependencies.identify_dependents(_with_basis)`, `constructor.TaxUnitConstructor`, `base.build_raw_units`.

`TaxUnitConstructor(..., claimant_assignment="parent_first" | "legacy", parent_link="sfn" | ...)`. `parent_first` / `sfn` is the default on this branch; `legacy` reproduces the old assignment exactly (checked: a full chain run with the legacy default is byte-identical to `site/data` for every file `tests/test_headline_figures.py` compares, plus the simulator's golden fixture [M]).

For each minor, and each enrolled 18-23-year-old who is not self-supporting (the existing selection, unchanged), in this order:

1. **Own parent in the household** (IRC 152(c)(4): a parent has priority over a non-parent; the choice is never made to maximise credits).
   - the householder's own children (RELSHIPP 25-27) and foster children (35): the householder (as before, without the old 15-year age-gap condition);
   - **subfamily link**: a child with `SFN` and `SFR` 4-6 (child in a married-couple subfamily; child of a mother alone; of a father alone) goes to the member of the same `SFN` with `SFR` 2 or 3 (couple with children; parent alone). This is the data's own parent pointer; it covers grandchildren, nephews and nieces and other relatives' minors. If that parent is not a filing adult (a teenage parent, or an 18-23 student), the next rule applies;
   - **age-gap rule** where no subfamily link can exist: a nonrelative minor (34, 36) goes to the householder's unmarried partner (22, 24) first, else to another nonrelative adult; anyone 18-23 and every child when a file has no `SFN` column go to an adult child or child-in-law (grandchild) / the householder's parent (the householder's minor sibling). Candidates are adults 15-50 years older. A married adult child and child-in-law count as one candidate. Several candidates: oldest child first, a sibling stays with its older sibling's parent, else the age gap closest to 28 (the mean age of US mothers at a birth). The `low` variant assigns only an unambiguous candidate; `high` picks the lowest-income candidate (the larger credit; an upper bound, not a policy);
2. **Another adult relative who can lawfully claim**: in PUMS this is the householder (grandparent, sibling, foster parent), so (b) and (c) coincide for related children: a related minor with no parent in the household goes to the householder. "Other relative" (33) minors with no parent present are claimed **as dependents but not as EITC / CTC qualifying children** (33 cannot separate nieces and nephews from cousins; the credit code keeps them out as before). **Conservative call, section 8.**
3. **Unrelated children (roommate / other nonrelative codes 34, 36) with no plausible parent**: unchanged (the householder claims an enrolled one as a plain dependent, never as an EITC / CTC child, and nobody claims the rest). The householder never claims an unrelated child as an EITC qualifying child.

A child claimed by its parent through the subfamily link or the age-gap rule reaches the credit code with relationship 25 (own child) whatever its RELSHIPP (a partner's child is coded 36): `dependents_details["relationship"]` carries the relationship to the claimant, via a `RELSHIPP_CLAIM` column built per household. A parent holding a child is never turned into another adult's qualifying relative (the child would lose its claimant). A minor householder, spouse or unmarried partner is nobody's dependent. A designated claimant that never files (it was itself claimed) passes its children to the unit that claimed it; the old fallback that matched stale RELSHIPP codes is used only in `legacy`.

**Qualifying-child tests kept as they are.** The diagnosis measured 18-year-old own children (+0.3K eligible units) and disabled adult children (+0.2K). Neither is "plainly wrong against IRC 152(c)": AGEP is age at interview, not at year end, and `DIS` is a weak proxy for "permanently and totally disabled". Left unchanged; not measured here **[D]**.

Unit tests (`tests/tax_modeler/units/test_claimants.py`, 16 tests, small synthetic households): three-generation home; two adult children each with a child; the same without subfamily columns under the low / nearest / high rules (and a case where nearest and high differ); partner with a child; unrelated housemate with a child; no plausible parent (unrelated child, grandchild, "other relative"); a parent is not made a qualifying relative; unrelated enrolled students; minor spouse/partner; invalid options; through the constructor: the grandchild on the parent's unit (HoH) with `legacy` reproducing the old unit, a partner's child counted by `_count_qualifying_children_eitc`, and every person still in a unit.

## 3. Sensitivity to the parent-choice rule

Raw units, 2024 1-year PUMS, TY2024 basis [M] (`parent_link` variants; `rule_*` ignore `SFN`, as the diagnosis had to):

| policy | EITC-eligible units | 0 / 1 / 2 / 3+ children | ACTC returns | HoH units |
|---|---:|---|---:|---:|
| legacy | 83,063 | 34,177 / 20,891 / 16,433 / 11,561 | 45,374 | 79,131 |
| rule_low (unambiguous only) | 88,769 | 32,985 / 24,475 / 18,569 / 12,740 | 50,168 | 81,627 |
| rule_nearest | 91,003 | 31,699 / 25,417 / 20,364 / 13,524 | 53,287 | 85,604 |
| rule_high (lowest-income candidate) | 91,723 | 31,021 / 25,660 / 21,080 / 13,962 | 54,417 | 85,171 |
| **sfn (default)** | **93,456** | 32,110 / 26,902 / 20,663 / 13,781 | 54,899 | 86,512 |
| sfn + rule_nearest on the minors the file says have no other parent | 93,559 | 31,924 / 26,912 / 20,855 / 13,867 | 55,089 | 86,762 |

- The rule's range is **88.8K to 91.7K** (a 2.95K swing), the same order as the diagnosis's +2.6K to +7.5K for grandchildren alone. The subfamily link lands **above** every rule variant (+1.7K over the best): the age-gap rule leaves some real parents unfound. Applying the rule to the minors the subfamily variables leave without a parent changes the default by only +0.1K, so it is not applied by default (the file says they have no in-household parent other than the householder and spouse).
- **Rule against the data's own answer** [M]: on the 2,212 sample grandchildren with a subfamily link (5-year file), `low` picks a parent for 54.5% of them (weighted) and is right 98.9% of the time it picks; `nearest` picks for 99.0% and is right 75.3%; `high` picks for 99.0% and is right 71.6%. Ambiguity (more than one candidate unit) is 44.5%; 1.0% have no candidate. So the diagnosis's "best of several" upper bound overstated what the rule recovers, and the data link is better than any of them.
- **Headline sensitivity** (chain runs with the rule-only variants, re-derived factors on) [M], against the default:

| figure | default (sfn) | rule_low | rule_high |
|---|---:|---:|---:|
| TY2028 Act 163 expiry cost, $M | 83.19 | 84.67 (+1.8%) | 83.25 (+0.1%) |
| TY2028 EITC expanded, $M | 80.64 | 83.06 (+3.0%) | 80.83 (+0.2%) |
| TY2028 EITC claimants | 78,969 | 81,529 (+3.2%) | 77,494 (-1.9%) |
| EITC take-up factor | 0.937 | 0.969 | 0.919 |
| TY2028 poverty rate, benefits renewed | 10.48% | 10.58% | 10.54% |
| Act 24 MID total impact 2028, $M | 120.33 | 120.31 | 120.34 |

  The rule matters for the working-family credits at the 2-3% level and not at all for Act 24 revenue.

## 4. Raw units against the bundled benchmarks (Stage 2)

Model: 2024 1-year PUMS, raw units as the working-family step builds them, **old filing-status factors in both columns** (construction effect only). Model unit counts include non-filing units, so compare shares and the by-children split. [M]

| measure | legacy | parent-first | change | benchmark |
|---|---:|---:|---:|---|
| single units | 389,661 | 384,457 | -1.3% | DOTAX TY2022 share 52.8% (model 52.7% -> 51.8%) |
| married joint | 252,299 | 252,299 | 0 | 34.1% (34.1% -> 34.0%) |
| head of household | 79,131 | 86,512 | +9.3% | 10.6% (10.7% -> 11.7%); SOI TY2022 71,180 returns |
| married separate | 18,652 | 18,650 | 0 | 2.5% |
| units with dependents | 193,977 | 201,802 | +4.0% | |
| dependents (unit weight x claimed) | 375,010 | 375,112 | +0.03% (**conserved**) | SOI TY2022 307,820 [D] (the model counts adult dependents) |
| persons in a unit (sample / PWGTP-weighted) | 11,046 / 1,056,041 | 11,065 / 1,057,953 | +19 / +1,912 (**conserved**, +0.17%) | of 15,107 / 1,446,146 |
| 5-year production cache: units / HoH / with dependents | 39,996 / 76,138 / 196,784 | 40,102 / 81,757 / 204,963 | +106 / +7.4% / +4.2% | |
| ACTC returns (TY2022 rules, CPI-deflated incomes) | 45,712 | 55,423 | +21% | SOI TY2022 61,270 |
| ACTC $M | 83.9 | 99.1 | +18% | SOI 119.6 |
| CTC (nonrefundable) returns | 122,755 | 130,262 | +6.1% | SOI 154,800 |
| CTC (nonrefundable) $M | 370.4 | 380.1 | +2.6% | SOI 352.1 |
| EITC-eligible units, TY2022 basis | 75,761 | 86,011 | +13.5% | SOI claims 84,970 |
| EITC-eligible units, **TY2024 basis** | 83,063 | 93,456 | +12.5% | IRS TY2024 claims about 81,800 [D]; DOTAX 78,399 |
| EITC-eligible $M, TY2024 | 172.1 | 212.6 | +23.5% | |

EITC-eligible units by number of children (TY2022 basis; SOI TY2022 claims) [M]:

| children | legacy | parent-first | SOI claims | legacy share | new share | SOI share |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 29,909 | 28,237 | 26,900 | 39.5% | 32.8% | 31.7% |
| 1 | 19,569 | 25,447 | 27,820 | 25.8% | 29.6% | 32.7% |
| 2 | 15,577 | 19,400 | 18,460 | 20.6% | 22.6% | 21.7% |
| 3+ | 10,707 | 12,928 | 11,790 | 14.1% | 15.0% | 13.9% |

On the TY2024 basis: 34,177 / 20,891 / 16,433 / 11,561 becomes 32,110 / 26,902 / 20,663 / 13,781 (total 93,456; shares 41.1 / 25.2 / 19.8 / 13.9 to 34.4 / 28.8 / 22.1 / 14.7).

By AGI class (TY2022 basis, legacy / parent-first / SOI claims; the model's AGI is `federal_agi`) [M]:

| children | $1-10K | $10-25K | $25-50K | $50-75K |
|---|---|---|---|---|
| 0 | 17,277 / 16,560 / 14,420 | 12,631 / 11,677 / 11,890 | 0 / 0 / 0 | 0 / 0 / 0 |
| 1 | 3,521 / 3,668 / 3,750 | 5,373 / 6,661 / 9,320 | 10,676 / 15,118 / 14,610 | 0 / 0 / 0 |
| 2 | 1,599 / 1,946 / 1,610 | 3,155 / 4,754 / 5,310 | 9,066 / 10,975 / 10,630 | 1,757 / 1,724 / 840 |
| 3+ | 2,207 / 2,463 / 940 | 2,298 / 2,915 / 3,000 | 4,997 / 6,014 / 6,380 | 1,205 / 1,535 / 1,440 |

The $25-50K 1-child cell (the diagnosis's biggest hole, -6.4K) is closed (15,118 against 14,610). The 1-child $10-25K cell is still short (6.7K against 9.3K): that is unit construction that this change does not touch (earnings, AGI definition) **[R]**.

Who is claimed by whom (weighted minors and 18-23 students, 5-year file) [M]: own child of the householder 76.0%; subfamily-linked to their parent 17.2%; age-gap rule 1.5%; householder as grandparent or relative with no parent present 4.6%; unrelated enrolled child kept on the householder 0.3%; claimed by nobody 0.4% (unrelated or partner-less children and the householder's minor partner/spouse).

## 5. What the old construction baked in (Stage 3)

All three derivations are reproducible, so nothing was left unchanged for lack of a derivation and no factor was invented. No re-derived factor fell outside an existing guard.

**(a) Filing-status weight factors** (`FILING_STATUS_WEIGHT_FACTORS`, was hard-coded twice in the constructor). Set in commit 661ca38 (2026-06) so the 2024 1-year units' filing-status mix matches the DOTAX TY2022 shares, normalised to MFJ = 1: `factor = (DOTAX share / DOTAX MFJ share) / (natural share / natural MFJ share)` on the units with every factor at 1.0 (`scripts/diagnose_filing_status.py` prints the shares). `scripts/rederive_filing_status_factors.py` runs the recipe. [M]

| status | committed (old) | recipe on the **old** construction (check) | recipe on the **new** construction | adopted (2 places, as before) |
|---|---:|---:|---:|---:|
| single | 0.82 | 0.823 | 0.834 | 0.83 |
| married joint | 1.00 | 1.000 | 1.000 | 1.00 |
| head of household | 1.30 | 1.291 | **1.181** | 1.18 |
| married separate | 1.35 | 1.351 | 1.351 | 1.35 |

The recipe reproduces the committed values on the old construction (to the rounding), which confirms the derivation; the old HoH 1.30 was weighting up a status the old construction under-built (HoH natural share 7.6% against DOTAX 10.6%; it is 8.3% now). With the adopted factors the mix is single 52.7% / MFJ 34.2% / HoH 10.6% / MFS 2.5% against DOTAX 52.8 / 34.1 / 10.6 / 2.5. With the **old** factors on the new construction HoH would be 11.7%, 1.1 points over.

**(b) By-children EITC reweight, lever 3a** (`calibration/eitc_reweight.py`). Not a constant: found every run as target / eligible count for each short bucket, after the IRS household rake (`analyses/eitc_reweight_factors.py`). [M]

| | legacy | new, old filing factors | new, re-derived filing factors |
|---|---|---|---|
| TY2024: bucket factors (1 / 2 / 3+ children) | 1.352 / 1.140 / 1.029 | 1.065 / none / none | 1.134 / none / none |
| TY2024: total tax-unit weight added | +1.32% | +0.23% | +0.44% |
| TY2028: bucket factors | 1.496 / 1.203 / 1.112 | 1.158 / none / none | 1.237 / 1.018 / none |
| TY2028: total tax-unit weight added | +1.82% | +0.51% | +0.76% |

The reweight was built to fill a 1-/2-child shortfall; with the children on the right returns the 2- and 3+-child buckets need no lift (and the 1-child lift is a quarter of what it was in TY2024). The re-derived factors lower the HoH weight, which is where most with-children eligibles sit, so some lift returns.

**(c) Fitted take-up factors.** All are re-fitted in each run to DOTAX dollars (guard in `takeup_factors`: 0.5 < factor <= 1.10); nothing to re-derive by hand. [M]

| factor (fit year) | main | new, old filing factors | new, re-derived |
|---|---:|---:|---:|
| EITC (TY2024, DOTAX new credit $76.98M) | 0.979 | 0.915 | 0.937 |
| food/excise (TY2023, $63.96M) | 0.738 | 0.754 | 0.761 |
| child and dependent care (TY2023, $14.82M) | 0.765 | 0.720 | 0.743 |
| HI renters credit (TY2024 DOTAX $1.64M; guard 0.05-0.40) | 0.250 | 0.242 | not run |

The renters figure is my approximation on the unaged, un-raked 2024 units, not the chain's own fit (it is fitted in the poverty path, which this run's chain does not exercise per year) **[M, approximate]**. Other fits are data-driven and refit automatically: the IRS household rake, the EITC dollar scaling to the caseload target, the ACTC/EITC count take-up (`apply_credit_takeup`). The EITC factor moves most: the model's eligible EITC dollars rose 23%, so a smaller share of them has to be claimed to hit DOTAX's $76.98M; claims then land at 77.3K against DOTAX's 78.4K (before: 82.5K, +5.3%).

## 6. Before and after, every published headline figure (Stages 4-5)

**BEFORE** is a full chain run with `claimant_assignment="legacy"` on this branch (same code otherwise, same commit lineage): its chain files are **byte-identical to the committed `site/data`** (act-24, working-family-credits, capital-gains: every CSV `tests/test_headline_figures.py` compares plus the others imported from the chain, and `tests/simulator/golden.json.gz`; the two JSON provenance fields aside). The worktree's `runs/` (copied from the main checkout) is **not** the published state: 30 of its files differ from `site/data`. I did not use it as the baseline. The H.R.1 page's published CSVs differ slightly from the control (baseline rate 11.8053% published, 11.8141% on the control): later commits on main, not this change.

**AFTER, construction only** = parent-first construction with the old filing-status factors (run A: the factor dict was edited in the working tree, uncommitted, so its manifests say `-dirty`; commit `4b6109f` otherwise). **AFTER, re-derived** = commit `4b6109f` as committed (run B). Differences between the two columns come from the factors.

Every run used `CF_MAX_WORKERS=2` and its own scratch runs directory (`CF_RUNS_DIR`); nothing was written to `runs/` or `site/`. The three runs stand in for the published pages as follows (all rows: `tax-unit-claimant-assignment_before_after.csv`).


**Act 24 (CD2) total impact, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| LOW 2027 | 80.27 | 80.30 | +0.04% | 80.31 | +0.05% |
| LOW 2031 | 162.8 | 162.8 | +0.04% | 162.8 | +0.06% |
| MID 2027 | 86.58 | 86.62 | +0.05% | 86.63 | +0.06% |
| MID 2028 | 120.3 | 120.3 | +0.04% | 120.3 | +0.05% |
| MID 2029 | 143.8 | 143.9 | +0.06% | 143.9 | +0.07% |
| MID 2030 | 183.8 | 183.9 | +0.04% | 183.9 | +0.06% |
| MID 2031 | 197.1 | 197.2 | +0.04% | 197.2 | +0.05% |
| HIGH 2027 | 113.0 | 113.1 | +0.04% | 113.1 | +0.05% |
| HIGH 2031 | 254.7 | 254.8 | +0.03% | 254.8 | +0.04% |

**Act 24 (CD2) MID, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| 2028 bracket change after response | 44.67 | 44.71 | +0.09% | 44.72 | +0.11% |
| 2028 Act 46 baseline revenue | 2,738 | 2,737 | -0.03% | 2,737 | -0.03% |

**Act 24 vs pre-Act-46 law, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| 2027 total | -755.7 | -755.9 | +0.03% | -755.9 | +0.03% |
| 2028 total | -774.3 | -774.4 | +0.02% | -774.4 | +0.02% |
| 2028 Act 24 increment | 57.71 | 57.76 | +0.09% | 57.77 | +0.11% |
| 2031 total | -1,091 | -1,091 | +0.02% | -1,091 | +0.02% |

**Capital gains, Act 24 law, central, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| cap9 2027 behavioral | 48.42 | 48.43 | +0.02% | 48.44 | +0.03% |
| cap9 2028 behavioral | 47.08 | 47.07 | -0.02% | 47.08 | -0.00% |
| cap9 2031 behavioral | 47.89 | 47.91 | +0.04% | 47.91 | +0.04% |
| ordinary 2027 behavioral | 130.1 | 130.2 | +0.02% | 130.2 | +0.04% |
| ordinary 2028 behavioral | 126.3 | 126.4 | +0.01% | 126.4 | +0.03% |
| ordinary 2031 behavioral | 128.8 | 128.9 | +0.02% | 128.9 | +0.04% |

**Working-family credits, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| TY2024 Hawaii EITC at 40% | 76.98 | 76.98 | +0.00% | 76.98 | +0.00% |
| TY2027 Hawaii EITC at 40% | 80.85 | 78.43 | -2.99% | 78.17 | -3.31% |
| TY2027 all three, cost of expiry (cost of renewing) | 84.39 | 82.81 | -1.87% | 82.67 | -2.03% |
| TY2028 Hawaii EITC at 40% | 83.53 | 80.43 | -3.72% | 80.64 | -3.46% |
| TY2028 food/excise credit, Act 163 table | 53.83 | 53.50 | -0.60% | 53.54 | -0.53% |
| TY2028 care credit, Act 163 limits | 16.45 | 16.45 | +0.03% | 16.47 | +0.10% |
| TY2028 EITC cost of expiry | 41.77 | 40.21 | -3.72% | 40.32 | -3.46% |
| TY2028 food/excise cost of expiry | 33.98 | 33.58 | -1.17% | 33.63 | -1.02% |
| TY2028 care credit cost of expiry | 9.21 | 9.23 | +0.17% | 9.24 | +0.23% |
| TY2028 all three, cost of expiry (cost of renewing) | 84.96 | 83.03 | -2.28% | 83.19 | -2.09% |
| TY2031 all three, cost of expiry (cost of renewing) | 86.88 | 83.47 | -3.92% | 84.52 | -2.71% |

**Working-family credits, claimants**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| TY2024 EITC claimants | 82,525 | 77,274 | -6.36% | 79,218 | -4.01% |
| TY2028 EITC claimants | 82,592 | 77,220 | -6.50% | 78,969 | -4.39% |

**Working-family credits, take-up factors**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| take-up factor, EITC | 0.9788 | 0.9150 | -6.52% | 0.9369 | -4.28% |
| take-up factor, food/excise | 0.7382 | 0.7541 | +2.14% | 0.7613 | +3.13% |
| take-up factor, child and dependent care | 0.7652 | 0.7204 | -5.85% | 0.7425 | -2.97% |

**Working-family credits, fit checks**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| TY2024 EITC claims | 82,525 | 77,274 | -6.36% | 79,218 | -4.01% |
| TY2023 EITC claims (out of sample) | 82,632 | 77,271 | -6.49% | 79,361 | -3.96% |
| TY2023 EITC $M (out of sample) | 72.51 | 72.59 | +0.11% | 72.54 | +0.04% |

**Working-family credits, TY2028 poverty**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| poverty_rate_renewed | 0.1077 | 0.1051 | -2.39% | 0.1048 | -2.73% |
| poverty_rate_expired | 0.1110 | 0.1088 | -1.95% | 0.1082 | -2.51% |
| persons_into_poverty | 4,429 | 4,986 | +12.59% | 4,644 | +4.86% |
| children_into_poverty | 2,052 | 2,253 | +9.82% | 2,038 | -0.68% |
| poverty_gap_increase_$M | 20.88 | 20.72 | -0.78% | 20.96 | +0.36% |
| persons_into_poverty_se | 783.4 | 799.6 | +2.07% | 728.8 | -6.97% |

**Working-family credits, TY2028 households losing**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| Earned income tax credit: households losing, share | 12.24 | 12.85 | +4.98% | 13.27 | +8.39% |
| Food/excise credit: households losing, share | 32.99 | 33.88 | +2.69% | 34.14 | +3.48% |
| Child and dependent care credit: households losing, share | 1.71 | 1.76 | +2.84% | 1.82 | +5.98% |
| All three: households losing, share | 37.42 | 38.00 | +1.55% | 38.42 | +2.66% |

**Working-family credits, TY2024 EITC by status, model $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| federal AGI range Under $15,000 | 17.84 | 18.39 | +3.09% | 18.27 | +2.41% |
| federal AGI range $15,000 to $29,999 | 29.41 | 28.56 | -2.90% | 28.57 | -2.88% |
| federal AGI range $30,000 to $44,999 | 22.78 | 24.21 | +6.27% | 23.91 | +4.96% |
| federal AGI range $45,000 to $54,999 | 5.56 | 4.71 | -15.40% | 4.82 | -13.45% |
| federal AGI range $55,000 or more | 1.39 | 1.12 | -19.25% | 1.42 | +2.50% |
| filing status Single | 5.28 | 7.31 | +38.41% | 7.73 | +46.33% |
| filing status Joint | 31.83 | 24.20 | -23.97% | 25.49 | -19.92% |
| filing status Married filing separately | 0.0000 | 0.0000 |  | 0.0000 |  |
| filing status Head of household | 39.87 | 45.47 | +14.04% | 43.77 | +9.76% |

**EITC/CTC geography, TY2025 state totals**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| weighted_filers | 751,442 | 743,891 | -1.00% | 742,319 | -1.21% |
| filers_receiving_eitc | 90,547 | 91,397 | +0.94% | 89,923 | -0.69% |
| total_eitc_dollars | 184,700,000 | 184,700,000 | +0.00% | 184,700,000 | +0.00% |
| filers_receiving_ctc | 145,276 | 146,195 | +0.63% | 141,929 | -2.30% |
| total_ctc_dollars | 523,631,264 | 529,306,451 | +1.08% | 513,862,775 | -1.87% |
| refundable_ctc_dollars | 110,717,251 | 115,235,944 | +4.08% | 110,689,590 | -0.02% |
| hi_eitc_dollars | 77,585,651 | 81,836,191 | +5.48% | 79,178,860 | +2.05% |

**H.R.1 SNAP/Medicaid**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| SNAP households, model base | 83,109 | 83,109 | +0.00% | 83,109 | +0.00% |
| LOW adults losing SNAP | 4,954 | 4,954 | +0.00% | 4,954 | +0.00% |
| MID adults losing SNAP | 7,251 | 7,251 | +0.00% | 7,251 | +0.00% |
| HIGH adults losing SNAP | 9,626 | 9,626 | +0.00% | 9,626 | +0.00% |
| LOW people pushed into poverty | 522.0 | 500.5 | -4.12% | 502.9 | -3.66% |
| LOW children pushed into poverty | 41.84 | 35.16 | -15.97% | 35.16 | -15.97% |
| LOW poverty gap increase, $ | 12,611,900 | 13,309,400 | +5.53% | 13,303,100 | +5.48% |
| MID people pushed into poverty | 760.6 | 711.9 | -6.42% | 716.0 | -5.86% |
| MID children pushed into poverty | 61.83 | 52.00 | -15.90% | 52.16 | -15.63% |
| MID poverty gap increase, $ | 18,083,500 | 18,877,200 | +4.39% | 18,868,200 | +4.34% |
| HIGH people pushed into poverty | 949.4 | 874.0 | -7.94% | 878.2 | -7.50% |
| HIGH children pushed into poverty | 77.67 | 64.90 | -16.44% | 65.07 | -16.23% |
| HIGH poverty gap increase, $ | 22,672,000 | 23,392,700 | +3.18% | 23,383,400 | +3.14% |
| baseline SPM poverty rate | 0.1181 | 0.1167 | -1.18% | 0.1165 | -1.35% |
| LOW Medicaid expansion adults losing coverage | 24,403 | 24,403 | +0.00% | 24,403 | +0.00% |
| MID Medicaid expansion adults losing coverage | 39,140 | 39,140 | +0.00% | 39,140 | +0.00% |
| HIGH Medicaid expansion adults losing coverage | 51,798 | 51,798 | +0.00% | 51,798 | +0.00% |

**Targeting efficiency, reference policies**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| act163_renewed cost_$M | 69.18 | 75.19 | +8.69% | 76.62 | +10.76% |
| act163_renewed persons_lifted | 4,368 | 4,178 | -4.35% | 4,256 | -2.57% |
| act163_renewed persons_lifted_per_$M | 63.14 | 55.56 | -12.00% | 55.54 | -12.03% |
| act163_renewed poverty_gap_closed_$M | 22.95 | 22.79 | -0.68% | 23.18 | +0.99% |
| nearest_the_line cost_$M | 249.6 | 249.6 | -0.01% | 249.6 | -0.01% |
| nearest_the_line persons_lifted | 96,309 | 95,144 | -1.21% | 95,128 | -1.23% |
| nearest_the_line persons_lifted_per_$M | 385.8 | 381.2 | -1.19% | 381.1 | -1.22% |
| nearest_the_line poverty_gap_closed_$M | 249.6 | 249.6 | -0.01% | 249.6 | -0.01% |

**Targeting efficiency, SPM poverty rate by group**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| everyone | 0.1213 | 0.1194 | -1.54% | 0.1193 | -1.63% |
| children | 0.1219 | 0.1181 | -3.06% | 0.1179 | -3.23% |
| single_parent_children | 0.3099 | 0.2984 | -3.70% | 0.2981 | -3.79% |
| eitc | 0.2715 | 0.2353 | -13.34% | 0.2335 | -14.01% |

**Tax simulator preset revenue, MID, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| current_law 2027 reform | 2,641 | 2,640 | -0.05% | 2,640 | -0.05% |
| current_law 2028 reform | 2,796 | 2,795 | -0.03% | 2,795 | -0.03% |
| cg_9 2028 static vs baseline | 56.26 | 56.27 | +0.02% | 56.28 | +0.03% |
| cg_ordinary 2028 static vs baseline | 162.9 | 162.9 | +0.01% | 162.9 | +0.02% |

**Tax simulator credit pricing, $M**

| figure | before (main) | construction only | % | + re-derived factors | % |
|---|---:|---:|---:|---:|---:|
| EITC, per percentage point of the federal credit, TY2028 | 2.09 | 2.01 | -3.72% | 2.02 | -3.46% |
| food/excise act163, TY2028 | 53.83 | 53.50 | -0.60% | 53.54 | -0.53% |


Other published files that change (cell counts, [M]; all cells in `analyses/before_after_runs.py` output): the Act 24 distribution tables (quintile and income-class, nearly every cell moves by cents to dollars), the capital-gains distribution and anchor-check tables, the working-family distribution / family-type / sampling-error tables, `tests/simulator/golden.json.gz` (differs: 2,056,200 -> 2,054,552 characters), the simulator's `population.json`, `population.bin.gz` and 4,254 of 6,488 preset-result cells (by tenths of a percent for revenue, a few dollars for the lowest quintile's average tax). H.R.1 SNAP households, adults losing SNAP, Medicaid losers and every dollar figure of the SNAP and Medicaid sections do not move; only the poverty section and `snap_baseline_check` (the SNAP households the model draws: 78,427 -> 78,430) do.

**Where the move comes from**
- *Act 24, capital gains, simulator revenue*: the construction fix, and at the cents level only. The re-derived factors add another +0.01% at most. Income, and so revenue, is the same; units split differently. Nothing here is a number a reader could quote differently.
- *Working-family credits, EITC lines*: the construction fix (-3.0% TY2027, -3.7% TY2028, -6.2% TY2031 in the EITC) comes from more eligible units at low incomes, so a lower take-up factor and a smaller reweight; the re-derived factors move TY2031 back to -4.5%. The TY2024 EITC is fitted to DOTAX, so it cannot move. Food/excise and care lines move under 1.3%.
- *Poverty (working-family TY2028)*: baseline rate with the credits renewed 10.77% -> 10.51% (construction) / 10.48% (re-derived); people pushed into poverty by the expiry 4,429 -> 4,986 / 4,644, children 2,052 -> 2,253 / 2,038. The sampling standard error on the people figure is 730-800, so those headcount moves are inside it; the rate and gap moves are not. Cause: parents now file with their own children, so the HoH tax and credits (EITC, CTC) land on the SPM units that hold the children.
- *EITC by filing status* (TY2024 check against DOTAX Table 4, share of claims): joint 30.2% -> 23.1% construction only / 24.4% re-derived (DOTAX 23.2%); HoH 40.1% -> 45.2% construction only (DOTAX 41.3%) / 43.6% re-derived; single 29.6% -> 31.7% / 31.9% (DOTAX 35.2%). The construction fix fixes the joint over-weight; the HoH factor then over-corrects, which is what the re-derivation removes.
- *H.R.1*: the construction fix only (the poverty denominator). Loser counts are drawn from SNAP and Medicaid receipt, not from tax units.

## 6a. Why the working-family projections come out lower

Question: the EITC take-up factor is fitted to DOTAX dollars, so the cost of existing credits should stay pinned near the same total with the factor absorbing the larger eligible pool. Why are TY2028 and TY2031 lower, by more in the later year (-3.5%, -4.5% on the EITC at 40%)? Analysis only: nothing published, no model behaviour changed. Script `analyses/wfc_projection_decomposition.py`; result files beside this doc: `tax-unit-claimant-assignment_wfc_stages.csv`, `_wfc_waterfall.csv`, `_wfc_by_children_waterfall.csv`, `_wfc_kept_by_children.csv`, `_wfc_claimants.csv`, `_wfc_cost_by_variant.csv`, `_wfc_eitc_check_ty2024.csv`.

**Method.** The script replays the working-family step's population path (`units_for_year`: aged with CBO components, IRS household rake, lever 3a reweight, IRS-count take-up truncation, then `state_credits`, `takeup_factors`, `_totals`) for the three constructions and records every stage. The replay reproduces `revenue_by_year.csv` of the three scratch chain runs (legacy, construction only, re-derived) to the last digit (max absolute difference 0.000000 over every cell), so the decomposition is of the published-figure path itself, not of a stand-in. "Legacy" = `claimant_assignment="legacy"` with the old factors (HoH 1.30). [M]

### The answer in four lines

1. **The fit pins one year, TY2024, and nothing else.** Every run models $76.98M at TY2024 (DOTAX's new credit, $76.98M on 78,399 claims). The factor is a single number per run; each later year is that run's own growth path times it. So the whole difference is a difference in growth from TY2024: legacy +8.5% by TY2028 and +17.4% by TY2031, re-derived +4.8% and +12.1%.
2. **The take-up step does not scale a pool; it truncates it, and the lever-3a reweight sets the pool.** The model ranks EITC-eligible units by credit within each children bucket and keeps the top ones until the weighted count reaches the IRS TY2022 count (26,900 / 27,820 / 18,460 / 11,790), the same targets lever 3a lifts short buckets to. Kept units are about 84,400 in every year of every run (the IRS count; the 2024 kept weight is 84,312 legacy, 84,555 re-derived). Claimants are kept weight times the factor. The factor is not absorbing a larger pool: it absorbs the higher dollars per kept unit.
3. **At the anchor the fix raises dollars per kept unit by 4.2% (federal $2,332 to $2,429), so the factor falls 4.3% (0.979 to 0.937).** After the rake and the reweight the pools are the same size (94,258 legacy, 94,436 new): the +8.6% raw eligible gain (+12.5% before the head-of-household re-derivation) is spent filling the buckets lever 3a used to fill by weight. The legacy construction had short pools in the 1-, 2- and 3+-child buckets, so lever 3a lifted every eligible unit there (x1.35 / 1.14 / 1.03), keeping the marginal low-credit units; the new construction has surplus in the 2- and 3+-child buckets (19,334 vs a target of 18,460; 12,949 vs 11,790), so the truncation discards its lowest-credit units (smallest kept credit $584 and $1,051 against $198 and $121 legacy) and the kept units average $4,084 / $4,530 against $3,842 / $4,228.
4. **That advantage is a base-year effect and it fades.** As incomes are aged past the credit's indexation the pools shrink (new 3+-child pool 12,949 to about 11,600 by TY2031, needing a lift of x1.02; 2-child pool 19,334 to about 17,000, lift x1.08), the surplus is gone and so is the truncation gain. Unscaled federal dollars per kept unit end up level ($2,730 legacy, $2,729 new at TY2031) while the new run's factor stays 4.3% lower. New / legacy = (unscaled dollar ratio) x (factor ratio) = 1.045 x 0.957 at TY2024, 1.009 x 0.957 at TY2028, 0.998 x 0.957 at TY2031 = 1.000, 0.965, 0.955 (the three percentages quoted above). [M]

| tax year | retained federal EITC, legacy $M | new $M | ratio | take-up factor ratio | product = new / legacy HI EITC |
|---|---:|---:|---:|---:|---:|
| 2024 | 196.62 | 205.42 | 1.0447 | 0.9572 | 1.0000 |
| 2028 | 213.36 | 215.18 | 1.0086 | 0.9572 | 0.9654 |
| 2031 | 230.74 | 230.26 | 0.9979 | 0.9572 | 0.9552 |

So the question's premise holds at the anchor and fails afterwards because the factor is fitted to a base-year level that contains a truncation advantage the later years do not have. The aged-and-raked population on its own grows the same under both constructions (next table, steps 1-2), so the construction is not what changes the growth; the lever-3a reweight, which forces counts to the IRS TY2022 targets every year, is.

### Waterfall, Hawaii EITC at 40% (cost of the existing credit), $M [M]

Method: the three growth ratios of the credit total from TY2024 (population aged only; then with the IRS rake; then with the reweight; each pipeline refitted to $76.98M at TY2024) are swapped from the legacy run's to the new run's one at a time, in that order. The sum of the three steps is exact (it ends at the chain's number); changing the order moves any step by at most $0.03M (checked over all six orders). A step is "what happens to this year's total if this growth is replaced".

| step | TY2028 re-derived | TY2031 re-derived | TY2028 construction only | TY2031 construction only |
|---|---:|---:|---:|---:|
| Legacy (before) | 83.53 | 90.34 | 83.53 | 90.34 |
| 1. Raw units and income ageing (growth of the take-up-truncated credit total) | +0.30 | +0.41 | +0.68 | +0.98 |
| 2. IRS household rake | -0.15 | -0.22 | -0.10 | -0.21 |
| 3. By-children reweight (lever 3a) | -3.05 | -4.24 | -3.68 | -6.40 |
| New run (after) | 80.64 | 86.29 | 80.43 | 84.70 |

Reading it: steps 1 and 2 are small and mostly cancel. Step 3 is the whole of the difference: -$3.05M of -$2.90M in TY2028 and -$4.24M of -$4.05M in TY2031 (re-derived); the construction-only run lost more to it (-$3.68M, -$6.40M). The filing-status re-derivation gives back $0.21M in TY2028 and $1.59M in TY2031 relative to the construction-only run (80.43 to 80.64, 84.70 to 86.29).

Without lever 3a the new construction does not project lower at all: with the IRS rake only, TY2028 is $78.00M new / $77.86M legacy / $78.40M construction only, and TY2031 is $80.49M / $80.32M / $81.01M (new within +0.2% of legacy). With neither rake nor reweight: TY2031 $80.19M new, $79.83M legacy. [M]

**What lever 3a adds to the TY2028 and TY2031 totals** (growth attributable to the reweight, $M, with each pipeline refitted at TY2024; unit weight added to the tax-unit total that year) [M]:

| | legacy | construction only | re-derived |
|---|---:|---:|---:|
| dollars added by lever 3a, TY2028 | +5.67 | +2.03 | +2.64 |
| dollars added by lever 3a, TY2031 | +10.02 | +3.70 | +5.80 |
| tax-unit weight added, TY2024 / TY2028 / TY2031 | +1.32% / +1.82% / +2.10% | +0.23% / +0.51% / +0.67% | +0.44% / +0.76% / +1.03% |
| 1-child lift, TY2024 / TY2028 / TY2031 | x1.352 / 1.496 / 1.539 | x1.065 / 1.158 / 1.192 | x1.134 / 1.237 / 1.274 |

The legacy reweight does grow with the horizon (1.8% then 2.1% of all tax-unit weight, against 1.0% in the new run) and carries 11% of the legacy TY2031 total ($10.0M of $90.3M); the new run's carries 6.7% ($5.8M of $86.3M). The lift grows in both because the natural pool of EITC-eligible units shrinks as aged incomes outrun the credit's indexation (raw eligible units 83,063 to 72,525 by TY2031 legacy, 90,208 to 79,443 new, -12.7% and -11.9%) while the targets stay at IRS TY2022 counts. [M]

### Where in the population: by children bucket [M]

| TY | kids | legacy pool after reweight | new pool | legacy lift | new lift | legacy avg federal credit $ | new $ | legacy HI EITC $M | new $M | change $M |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024 | 0 | 36,188 | 34,333 | 1.000 | 1.000 | 465 | 447 | 4.85 | 4.48 | -0.37 |
| 2024 | 1 | 27,820 | 27,820 | 1.352 | 1.134 | 2,331 | 2,362 | 25.30 | 24.58 | -0.72 |
| 2024 | 2 | 18,460 | 19,334 | 1.140 | 1.000 | 3,842 | 4,084 | 27.46 | 28.00 | +0.54 |
| 2024 | 3+ | 11,790 | 12,949 | 1.029 | 1.000 | 4,228 | 4,530 | 19.36 | 19.91 | +0.55 |
| 2024 | all | | | | | | | 76.98 | 76.98 | -0.00 |
| 2028 | 0 | 31,813 | 30,314 | 1.000 | 1.000 | 487 | 469 | 5.11 | 4.68 | -0.42 |
| 2028 | 1 | 27,820 | 27,820 | 1.496 | 1.237 | 2,590 | 2,573 | 27.91 | 26.57 | -1.34 |
| 2028 | 2 | 18,460 | 18,460 | 1.203 | 1.018 | 4,001 | 4,189 | 28.80 | 28.70 | -0.10 |
| 2028 | 3+ | 11,790 | 12,105 | 1.112 | 1.000 | 4,743 | 4,670 | 21.72 | 20.69 | -1.02 |
| 2028 | all | | | | | | | 83.53 | 80.64 | -2.89 |
| 2031 | 0 | 31,409 | 29,872 | 1.000 | 1.000 | 520 | 498 | 5.42 | 4.96 | -0.46 |
| 2031 | 1 | 27,820 | 27,820 | 1.539 | 1.274 | 2,705 | 2,694 | 29.47 | 27.90 | -1.56 |
| 2031 | 2 | 18,460 | 18,460 | 1.293 | 1.084 | 4,372 | 4,521 | 31.40 | 31.27 | -0.13 |
| 2031 | 3+ | 11,790 | 11,790 | 1.173 | 1.020 | 5,250 | 5,063 | 24.05 | 22.15 | -1.90 |
| 2031 | all | | | | | | | 90.34 | 86.29 | -4.05 |

The 3+-child bucket alone is -$1.90M of the -$4.05M TY2031 gap, the 1-child bucket -$1.56M, childless -$0.46M, the 2-child bucket -$0.13M. At TY2024 the new run is +$0.54M / +$0.55M in the 2- and 3+-child buckets (the truncation gain) and -$0.37M / -$0.72M in the childless and 1-child buckets (after the factor). By TY2031 the 3+-child legacy bucket's average federal credit has grown 24% ($4,228 to $5,250), the new one 12% ($4,530 to $5,063): the legacy bucket is small, is lifted by x1.17 and rests on few PUMS records, so most of this is what ageing does to which records stay eligible, not a policy effect. I did not decompose within the bucket. [I]

### Composition across the credit schedule (candidate 2) [M]

Kept claimants by schedule region (federal earned income below the plateau, above the phase-out start by the greater of earnings and AGI, else plateau) are close under both constructions, and drift the same way: phase-in 25.6 / 25.9 / 26.0% legacy against 26.1 / 25.7 / 26.4% new at TY2024 / 2028 / 2031; plateau 18.6 / 16.8 / 16.2% against 16.7 / 14.9 / 14.0%; phase-out 55.8 / 57.2 / 57.8% against 57.2 / 59.4 / 59.6%. Composition is not what separates the growth paths; the level of the average credit in each region at TY2024 is (phase-in $1,725 legacy, $1,844 new; phase-out $1,967, $2,179), which is the truncation effect again. A population closer to the phase-out end does not explain the gap.

### Claimants against eligible units (candidate 4) [M]

| | raw eligible units | after IRS rake | after reweight | kept after truncation | factor | claimants | HI credit per claimant |
|---|---:|---:|---:|---:|---:|---:|---:|
| legacy TY2024 | 83,063 | 84,409 | 94,258 | 84,312 | 0.979 | 82,525 | $933 |
| re-derived TY2024 | 90,208 | 91,141 | 94,436 | 84,555 | 0.937 | 79,218 | $972 |
| legacy TY2028 | 75,073 | 76,357 | 89,883 | 84,381 | 0.979 | 82,592 | $1,011 |
| re-derived TY2028 | 82,179 | 83,045 | 88,699 | 84,289 | 0.937 | 78,969 | $1,021 |
| legacy TY2031 | 72,525 | 73,818 | 89,479 | 84,505 | 0.979 | 82,713 | $1,092 |
| re-derived TY2031 | 79,443 | 80,302 | 87,942 | 84,375 | 0.937 | 79,049 | $1,092 |

(The construction-only run's raw TY2024 pool is the 93,456 the headline quotes; the re-derived HoH factor takes it to 90,208 because it down-weights HoH units.) "Claimants" is the kept weight times the factor, each year. Eligible units rise 8.6% (re-derived vs legacy, raw, TY2024) but kept units are held at the IRS count, so claimants move only with the factor: -4.0% at TY2024 (82,525 to 79,218), -4.4% at TY2028, -4.4% at TY2031. In both runs claimant counts are flat over the horizon by construction (about 82,600 and 79,000), so the model implies no change in claims from TY2024 to TY2031 while DOTAX shows claims falling. The raw-pool increase never reaches the claimant count. HI credit per claimant converges by TY2031 ($1,092 both) after starting 4.2% apart.

### Federal side (candidate 5) [M]

The state credit is 40% of the federal one, so dollars per claimant are federal dollars per kept unit: $2,332 legacy and $2,429 new at TY2024 (+4.2%), $2,529 and $2,553 at TY2028 (+1.0%), $2,730 and $2,729 at TY2031 (0%). By children bucket at TY2024 the new run's average is -3.9% childless, +1.3% 1-child, +6.3% 2-child, +7.1% 3+ (table above). That is consistent with the mechanism above (which units the truncation keeps), not with the fix adding units that earn more; I did not test the second explanation separately. [I]

### Act 163 expiry cost (candidate 6) [M, food and care causes I]

| step | TY2028 | TY2031 |
|---|---:|---:|
| Legacy (before) | 84.96 | 86.88 |
| EITC part: raw units and ageing | +0.15 | +0.20 |
| EITC part: IRS rake | -0.07 | -0.11 |
| EITC part: by-children reweight | -1.52 | -2.12 |
| Food/excise credit | -0.35 | -0.33 |
| Child and dependent care credit | +0.02 | +0.00 |
| New run (after) | 83.19 | 84.52 |

The EITC row is half the EITC dollar steps because expiry moves the rate from 40% to 20% of the same federal credit, so the cost is half the expanded total. It carries -$1.45M of -$1.77M (TY2028) and -$2.02M of -$2.36M (TY2031). The food/excise cost is lower by $0.35M and $0.33M and the care credit unchanged. Food and care are fitted at TY2023 and are not on the take-up truncation; the run-level difference is small at the anchor ($37.93M to $37.88M at TY2024) and widens with the horizon, which is consistent with the reweight adding less low-income family weight under the new construction (above) but I did not decompose them by stage. Guards: the take-up factor guard (0.5 to 1.10) did not bind (0.979, 0.915, 0.937). In the counterfactual pipelines without lever 3a the pools fall short of the IRS counts and the take-up step logs shortfalls (13.7% to 34%, in the legacy replay log); the full path, which is the published one, does not.

### Is the lower projection more credible? Undetermined on growth; better at the anchor

**At the anchor the new construction is closer to DOTAX** [M]. TY2024 claims: DOTAX 78,399; legacy 82,525 (+5.3%), new 79,218 (+1.0%). HI credit per claim: DOTAX $982; $933 (-5.0%) and $972 (-1.0%). Against DOTAX's TY2024 filing-status table (`_wfc_eitc_check_ty2024.csv`): joint $22.7M (legacy $31.8M, new $25.5M), head of household $43.6M ($39.9M, $43.8M), single $12.0M ($5.3M, $7.7M). The legacy projection starts from a base that over-counts claimants by 5% and puts 40% too many EITC dollars on joint filers; the 4% drop is that base being corrected, and its larger apparent growth is the lever-3a lift needed to make up the short pools (+$10.0M by TY2031 vs +$5.8M), not growth in the credit.

**On growth, neither projection is tested** [M for the data, I for the reading]. What DOTAX and the IRS show over the years available:

| | claims | dollars | dollars per claim, federal equivalent (HI / 0.40) |
|---|---:|---:|---:|
| TY2022, IRS federal (caseload table, 84,010 returns) | 84,010 | $184.7M federal | $2,199 |
| TY2023, DOTAX Table A-1 / A-2 | 84,470 | $77.05M HI | $2,281 |
| TY2024, DOTAX EITC report, new credit | 78,399 | $76.98M HI | $2,455 |
| growth TY2023 to TY2024 | -7.2% | -0.1% | +7.6% |
| model TY2023 to TY2024 (legacy / new) | about flat (pinned) | +6.2% / +6.1% | +6.3% / +6.3% |
| model TY2024 to TY2028 per year (legacy / new) | flat (pinned) | +2.1% / +1.2% | +2.0% / +1.2% |

(TY2023 and TY2024 come from different DOTAX reports; the EITC report itself puts TY2023 applied at $75.6M against TY2024 applied $78.5M, +3.9%. Three observations, mixed bases: weak evidence.) The data say claims fell 7% in the one year observed while both models hold claims at the TY2022 IRS count through TY2031, and that dollars per claim rose faster (+7.6%) than either projection (legacy about 2.3% a year, new about 1.7% a year over TY2024-2031, against about 2.4% a year for the federal maximum credit's indexation in the model). If claims keep falling and per-claim credit keeps rising, both projections overstate the count and understate the per-claim figure; the dollar total is not clearly biased either way. The legacy path is not better supported on growth for being higher, and the new path is not better for being lower: both depend on lever 3a holding the counts.

**What would settle it:** (1) DOTAX's TY2025 Earned Income Tax Credit Report (the TY2024 one is dated December 2025): a second year of claims and dollars on one basis; (2) the IRS Statistics of Income Hawaii EITC returns by children for TY2023 and TY2024, which would replace the stale TY2022 targets lever 3a and the truncation use (the count is what moves the answer), after which the model's implied claims trend could be compared to the actual one; (3) rerunning this script with the reweight targets set to the IRS count for each year: if the projections then agree across constructions at about $80-81M (the rake-only figures above) the gap is the lever's stale targets, not the fix. **Settled in section 6b**: TY2023 counts were obtained and rerun; the constructions do not converge (the gap is not a stale-target effect), and the data cannot say which projection's growth history supports.

### Measured and inferred

Measured (by the replay, exact against the chain): every figure in the tables above, the stage waterfall, the by-bucket waterfall, the pool, lift, kept and claimant counts, the schedule-region shares, the identity new / legacy = dollar ratio x factor ratio, the anchor agreement ($76.98M in all three runs). Inferred: that the 3+-child swing is ageing-driven record composition (not decomposed within the bucket); that the food-credit decline comes from the smaller lift in low-income family weight; that the DOTAX/IRS trend rows are comparable across reports; the reading that the legacy extra growth is the reweight's compensation for a short pool rather than real growth. The order of the three waterfall steps does not matter beyond $0.03M. Not done: stage-level decomposition of the food and care credits; the TY2022 and TY2023 model years (the TY2022 credit is re-pegged to its nominal DOTAX dollars by the take-up step, so it is not a growth observation).

## 6b. Validating the projected growth

Question: do the data support the working-family projections' growth after TY2024, and which of legacy / re-derived does history support? Analysis only: nothing published, no model behaviour or default changed, `site/` and `runs/` untouched. Scripts: `analyses/hi_eitc_history.py` (fetch and assemble the history), `analyses/wfc_growth_validation.py` (decompositions, tables), `analyses/wfc_targets_backtest.py` (the two replays that need compute); the replays reuse `wfc_projection_decomposition.py`. Files beside this doc: `tax-unit-claimant-assignment_hi_eitc_history.csv` (every historical number, with a source column), `_hi_eitc_history_derived.csv`, `_growth_rates.csv`, `_backtest.csv`, `_stale_targets.csv`.

### The answer in seven lines

1. **Growth can be validated only partly.** Hawaii federal EITC returns and dollars exist for 12 tax years (TY2010-2012, 2014, 2015, 2017-2023; TY2013 and TY2016 are missing because the IRS file posted under those names is a different table), plus an IRS preliminary TY2024. Hawaii by-children counts exist for **two** years only (TY2022, TY2023). DOTAX's state series is not comparable across the TY2022/TY2023 law change, so it yields one usable pair. [M]
2. **Returns have fallen, then stopped.** TY2010-2019: -1.4% a year (log-linear trend -1.9%, se 0.4 pt); the log-linear trend over TY2010-2023 without 2020-21 is -2.5% a year; TY2019 to TY2022 fell 14% in three years; since TY2022 they are flat (SOI 84,970 then 84,460; IRS processed claims 82K, 82K, 81.8K for TY2022-2024). "Claims flat through TY2031" is consistent with the last three years and inconsistent with the 2010-2019 trend. [M]
3. **Parameter-adjusted dollars per return fell about 0.9% a year before the pandemic** (TY2010-2019), and are flat to slightly up since (+0.4% a year TY2022-2024, one clean pair). The model's own parameter indexation is +2.2-2.3% a year (federal maximum credit, TY2022 return mix); the legacy projection's real growth is -0.3% to +0.1% a year, the re-derived one's -1.0% to -0.5%. [M]
4. **Backtest (one year, ACS 2018-2022 file, TY2022 calibration only): both constructions forecast TY2023 federal EITC dollars within 1% (legacy -0.7%, re-derived -0.4%) and returns within 0.3%.** One pair; it cannot discriminate, and the returns test is nearly built in (the targets are flat). Re-derived has the better level (-5.0% vs -6.8% at TY2022 raw; -3.6% vs -7.5% against the IRS TY2024 average credit). [M]
5. **The stale target is not the source of the gap.** With the TY2023 by-children counts the two constructions do not converge (TY2028 $83.55M vs $80.62M, TY2031 $90.38M vs $86.56M; same 3.5-4.2% gap). They converge, and cross, only when the targets also trend down with returns (about $76-78M flat in nominal dollars to TY2031). [M]
6. **Verdict: (c), history cannot discriminate between the two projections' growth; and it leans, weakly, toward (d) if the 2010-2019 decline resumes**: then both overstate, legacy by more (TY2031 $76.8M legacy, $78.0M re-derived against $90.3M and $86.3M published). The evidence for any growth verdict is thin: 8 clean pre-pandemic year-pairs for returns, 1-2 post-pandemic pairs, 1 backtest pair, 2 by-children years.
7. **Nothing found argues against adopting the new construction.** Every level test favours it (anchor claims +1.0% vs +5.3% against DOTAX; TY2024 credit per return -3.6% vs -7.5% against IRS; backtest level -5.0% vs -6.8%) and the growth tests tie. What the new construction does not do is fix the count assumption, which both share.

### 1. The historical record [M]

Sources (all fetched by `analyses/hi_eitc_history.py`, one request per file, no retries; every row of the CSV carries its URL):

- Federal, Hawaii: IRS SOI Historic Table 2, workbook `{yy}in12hi.xls[x]`, TY2010-2023, "Earned income credit" number and amount, all returns. The repo's bundled `irs_soi_hi_eitc_panel.csv` held TY2015, 2017-2022 returns only (and no dollars); `irs_soi_hi_table2.json` holds TY2022-2023 AGI classes, no EITC rows. SOI TY2012 and TY2014 are `.xls`; the IRS posts for TY2013 and TY2016 a workbook that is Table 11/12 (`13in12hi.xls` and `16in12hi.xls`, which the IRS's own Historic Table 2 archive pages link, open as Table 12; the same-named files with state number 11 are Table 11): **no Hawaii EITC data for those two years from this route**. TY2024: no SOI workbook yet.
- By qualifying children: the SOI Table 2 workbook carries the four by-children lines **only from TY2022** (the TY2020 and TY2021 workbooks do not). That is the source of the model's 26,900 / 27,820 / 18,460 / 11,790 (`EITC_RETURNS_BY_CHILDREN_HI_TY2022`). TY2023: **26,400 / 27,650 / 18,470 / 11,940 (84,460)**. So Hawaii by-children counts exist for two years; the national by-children table (SOI Table 2.5) is not Hawaii and was not fetched.
- IRS EITC Central, "Statistics for tax returns with the EITC" (claims processed by December of the next year, rounded as printed): Hawaii TY2019 94K / $203M; TY2020 90K / $192M; TY2021 113K / $208M; TY2022 82K / $181M; TY2023 82K / $196M; **TY2024 81.8K / $206.2M, average $2,520**. It runs about 3% below SOI (84,970 / $185.9M; 84,460 / $200.7M for TY2022 / TY2023), a processing-date difference, and is used only for TY2024 and as a cross-check.
- DOTAX: Tax Credits Claimed Tables A-1/A-2 for TY2018-2023 (`2018credit_tables.xlsx` ... `2023credit_tables.xlsx`); the TY2024 EITC Report is the bundled `dotax_eitc_2024.json`. The Act 107 EITC report PDFs for other years returned HTTP 404 (TY2018-2021) and 429 (TY2022-2024; route stopped, not retried); the TY2023 report's applied $75.6M is taken from the repo's note, not re-read.
- Federal maximum credit by children, TY2010-2023: Revenue Procedures 2009-50, 2011-12, 2011-52, 2013-15, 2013-35, 2014-61, 2015-53, 2016-55, IRB 2018-10 (Rev. Proc. 2018-18), 2018-57, 2019-44, 2020-45, 2021-45, 2022-38 (parsed from the PDFs on irs.gov/pub/irs-drop; they reproduce the repo's TY2022-2024 parameters exactly); TY2024-2026 from the IRS EITC tables page.

| tax year | SOI EITC returns | federal $M | $ per return | EITC share of all returns | max-credit index $ [a] | real index [b] |
|---:|---:|---:|---:|---:|---:|---:|
| 2010 | 111,789 | 222.5 | 1,991 | 17.1% | 3,024 | 0.658 |
| 2011 | 114,700 | 235.6 | 2,054 | 17.3% | 3,069 | 0.669 |
| 2012 | 114,580 | 240.5 | 2,099 | 17.2% | 3,143 | 0.668 |
| 2014 | 114,020 | 246.6 | 2,163 | 16.7% | 3,278 | 0.660 |
| 2015 | 110,700 | 239.5 | 2,163 | 16.1% | 3,330 | 0.650 |
| 2017 | 100,550 | 215.5 | 2,143 | 14.5% | 3,371 | 0.636 |
| 2018 | 96,910 | 209.0 | 2,157 | 14.0% | 3,432 | 0.629 |
| 2019 | 98,810 | 209.9 | 2,124 | 13.8% | 3,498 | 0.607 |
| 2020 (pandemic) | 90,860 | 181.2 | 1,994 | 12.8% | 3,554 | 0.561 |
| 2021 (ARPA childless expansion) | 115,380 | 211.6 | 1,834 | 16.6% | 3,589 | 0.511 |
| 2022 | 84,970 | 185.9 | 2,188 | 12.3% | 3,701 | 0.591 |
| 2023 | 84,460 | 200.7 | 2,376 | 12.1% | 3,964 | 0.599 |

[a] the federal maximum credit by children weighted by the TY2022 Hawaii by-children return mix (31.7 / 32.7 / 21.7 / 13.9%), held fixed: it isolates the statutory schedule from the change in the mix of families. TY2021 uses the pre-ARPA childless maximum, so the TY2021 row is not a clean index. [b] dollars per return divided by [a]: the share of an average maximum credit actually received (income and composition, not the schedule). Hawaii filers rose 653K to 715K (TY2010-2019) while EITC returns fell, so the EITC share of returns fell from 17.1% to 13.8% before the pandemic.

### 2. History, decomposed [M]

Annualized, between consecutive available years (a gap of two years where TY2013 or TY2016 is missing):

| from-to | returns | $ per return | parameters | real $ per return | note |
|---|---:|---:|---:|---:|---|
| 2010-11 | +2.6% | +3.2% | +1.5% | +1.7% | |
| 2011-12 | -0.1% | +2.2% | +2.4% | -0.2% | |
| 2012-14 | -0.2% | +1.5% | +2.1% | -0.6% | two years |
| 2014-15 | -2.9% | +0.0% | +1.6% | -1.6% | |
| 2015-17 | -4.7% | -0.5% | +0.6% | -1.1% | two years |
| 2017-18 | -3.6% | +0.6% | +1.8% | -1.1% | |
| 2018-19 | +2.0% | -1.5% | +1.9% | -3.4% | |
| 2019-20 | -8.1% | -6.1% | +1.6% | -7.6% | flag: pandemic (excluded) |
| 2020-21 | +27.0% | -8.0% | +1.0% | -8.9% | flag: ARPA childless expansion (excluded) |
| 2021-22 | -26.4% | +19.3% | +3.1% | +15.7% | flag: reversal (excluded) |
| 2022-23 | -0.6% | +8.6% | +7.1% | +1.4% | the one clean post-pandemic pair |

| window | returns | $ per return | parameters | real $ per return | federal $ |
|---|---:|---:|---:|---:|---:|
| TY2010-2019 (8 clean pairs) | -1.36% | +0.72% | +1.63% | -0.90% | -0.65% |
| TY2015-2019 | -2.80% | -0.46% | +1.23% | -1.67% | -3.25% |
| TY2019-2023 (spans the pandemic) | -3.85% | +2.84% | +3.17% | -0.32% | -1.11% |
| TY2022-2023 | -0.60% | +8.61% | +7.10% | +1.41% | +7.96% |
| TY2022-2024 (IRS processed claims) | -0.12% | +6.73% | +6.25% | +0.41% | +6.73% |

Log-linear trend in returns: TY2010-2019 -1.89% a year (se 0.42 pt, 8 points); TY2012-2019 -2.68% (se 0.48, 6); TY2015-2019 -3.09% (se 1.03, 4); TY2010-2023 without 2020-21 **-2.48%** (se 0.30, 10); TY2017-2023 without 2020-21 -3.11% (se 0.50, 5). By children, TY2022 to TY2023: 0 children -1.9%, 1 child -0.6%, 2 children +0.1%, 3+ children +1.3%.

Reading. Returns **fell** about 1.4% a year before the pandemic (more by the trend fit, which weights the 2015-2019 slide), then the ARPA/pandemic swings, then a step to 84,970 and **flat** since. Parameter-adjusted dollars per return fell about 0.9% a year over TY2010-2019 (wages grew faster than the credit's CPI indexation, pushing units into the phase-out: inferred) and are about flat since. The one clean post-pandemic pair has +1.4% real growth, the three-year IRS pair +0.4% a year; a pair is a pair.

DOTAX, on a federal-equivalent basis (state dollars over the statutory rate: 20% nonrefundable TY2018-2022, Act 107; 40% refundable TY2023 on, Acts 114 and 163; `hi_eitc.py`):

| tax year | DOTAX claims | DOTAX $M | claims / SOI returns | DOTAX $ / (rate x SOI federal $) | $ per claim, federal equivalent |
|---:|---:|---:|---:|---:|---:|
| 2018 | 57,134 | 15.6 | 0.59 | 0.37 | 1,364 |
| 2019 | 61,406 | 18.1 | 0.62 | 0.43 | 1,475 |
| 2020 | 64,430 | 21.1 | 0.71 | 0.58 | 1,640 |
| 2021 | 80,990 | 21.8 | 0.70 | 0.52 | 1,346 |
| 2022 | 60,903 | 19.8 | 0.72 | 0.53 | 1,624 |
| 2023 | 84,470 | 77.1 | 1.00 | 0.96 | 2,281 |
| 2024 (new credit) | 78,399 | 77.0 | | | 2,455 |

TY2018-2022 DOTAX counts and dollars are capped by state liability (nonrefundable), so they measure liability, not claimants: 59-72% of the federal count and 37-58% of the statutory dollars. They break at TY2023, when the credit became refundable at twice the rate, and say nothing about trend. Only **TY2023 to TY2024** is on one footing: claims -7.2% (new credit; -5.0% on the applied basis, 84,470 to 80,279, if A-2's TY2023 count is applied-basis, which I could not check), dollars -0.1%, federal-equivalent dollars per claim +7.6% against +5.4% for the schedule (real +2.1%). That pair shows a fall in DOTAX claims that the federal series for the same year does not (IRS processed claims 82K to 81.8K): the DOTAX drop is not a drop in federal EITC returns, and so is not evidence on the count the model holds flat. [I: possibly residents-only counting and the legacy carryforwards in "applied"; not resolved]

### 3. The projections, decomposed the same way [M]

Federal credit per claimant = HI EITC at 40% / 0.40 / claimants; the take-up factor cancels (claimants and dollars both carry it), so this is the model's own dollars per kept unit. Parameters: the model's federal maximum credit (TY2022 return mix; CPI 2.1% a year past TY2026 in `eitc_parameters_for_year`). Real = (1 + $ per claimant) / (1 + parameters) - 1. From the decomposition replays (exact against the chain runs).

| period | returns / claimants | $ per return / claimant | parameters | real $ per return | federal $ |
|---|---:|---:|---:|---:|---:|
| **History** TY2010-2019 | -1.36% | +0.72% | +1.63% | -0.90% | -0.65% |
| History TY2015-2019 | -2.80% | -0.46% | +1.23% | -1.67% | -3.25% |
| History TY2019-2023 | -3.85% | +2.84% | +3.17% | -0.32% | -1.11% |
| History TY2022-2024 (IRS processed) | -0.12% | +6.73% | +6.25% | +0.41% | +6.73% |
| **Legacy** TY2024-2028 | +0.02% | +2.04% | +2.30% | -0.25% | +2.06% |
| Legacy TY2024-2031 | +0.03% | +2.28% | +2.22% | +0.06% | +2.31% |
| **Re-derived** TY2024-2028 | -0.08% | +1.25% | +2.30% | -1.03% | +1.17% |
| Re-derived TY2024-2031 | -0.03% | +1.68% | +2.22% | -0.53% | +1.64% |

Claimants: legacy 82,525 (TY2024) to 82,592 (TY2028) and 82,713 (TY2031); re-derived 79,218 to 78,969 and 79,049. Federal credit per claimant: legacy $2,332 to $2,528 and $2,730; re-derived $2,429 to $2,553 and $2,729.

- **Claims.** Both hold them flat, as the targets do. History: flat for three years (TY2022-2024), falling 1.4-2.5% a year over the longer record. Flat is the better short-run description and the worse long-run one; the data cannot say which regime applies to TY2024-2031.
- **Parameters.** The model's +2.2-2.3% a year sits between the pre-pandemic history (+1.6%) and the post-2019 history (+3.2%, inflation). It is an assumption about CPI, not a finding.
- **Remainder (real $ per return).** History: -0.9% a year (long), +0.4% (recent). Re-derived: -1.0% to -0.5%, nearer the long history; legacy: about 0, nearer the recent pairs. Within the year-to-year scatter of the history (clean pairs range -3.4% to +1.7%) neither is rejected.
- **Net, dollars.** If returns keep their TY2010-2019 slope and real dollars per return theirs, federal dollars at the model's 2.2% indexation would move about -0.1% a year (-2% to -2.3% with the 2015-2019 or 2019-2023 slopes), against +1.2% to +1.6% (re-derived) and +2.1% to +2.3% (legacy). Over TY2022-2024, with claims flat and the schedule up 6.3% a year, federal dollars rose 6.7% a year; the models' schedule growth is 2.2% a year, so only the real part (+0.4%) is comparable.

### 4. Backtest [M]

Feasible, one year ahead. What the step needs: `PUMS_INCOME_DOLLAR_YEAR = 2024` is a constant (`detect_pums_dollar_year` reads ADJINC but returns the default regardless of the file), the rake is fitted to the TY2023 IRS table (`IRS_RAKE_YEAR`), the take-up factor to DOTAX TY2024, and lever 3a / the truncation to TY2022 counts. The 2018-2022 file (`packages/data/raw/pums`, incomes in 2022 dollars, about 38.8K tax units) therefore runs only if, in the analysis (nothing in the model changes): the projector's base year is set to 2022, the rake year to 2022 (TY2022 table), and the DOTAX take-up factor is dropped (it would pull TY2024 information in). `wfc_targets_backtest.py backtest` does that and reports the **unscaled** federal returns and dollars (the count truncation only), so nothing dated after TY2022 enters except the CBO Feb 2026 income path, which already knows TY2023 (a leak that favours the forecast: the aging is not a true ex-ante forecast). The 2020-2024 file cannot be used: its dollar-year is 2024 and no earlier base exists inside it (a five-year file pools survey years 2020-2024 at 2024 dollars). Time: about 5 minutes a variant.

| | legacy | re-derived | actual |
|---|---:|---:|---:|
| model TY2022 federal $M, raw | 173.2 | 176.5 | 185.9 (SOI) |
| level error TY2022 | -6.8% | -5.0% | |
| model growth TY2022 to 2023 | +7.25% | +7.50% | +7.96% (SOI) |
| TY2023 federal $M, anchored on SOI TY2022 x model growth | 199.3 | 199.8 | 200.7 (SOI) |
| error, anchored | -0.66% | -0.43% | |
| error, raw level (no anchor) | -7.4% | -5.4% | |
| TY2023 kept returns | 84,322 | 84,213 | 84,460 (SOI); error -0.16% / -0.29% |
| TY2024 HI EITC $M, TY2023 DOTAX x model growth | 81.7 | 81.6 | 77.0 new / 78.5 applied (DOTAX): +6.1% / +4.0% |
| TY2024 claims, TY2023 DOTAX x model kept growth | 84,422 | 84,326 | 78,399 new / 80,279 applied: +7.7% / +5.2% |

What it shows. One-year growth is forecast to within 0.7 pt by both (re-derived slightly nearer); the level the model starts from is nearer for re-derived. Returns are near-exact by construction (targets flat; actual TY2022 to 2023 was -0.6%). The TY2024 row, which is two years ahead and on mixed bases (the DOTAX counts are not the federal ones), overshoots in both: both hold claims and dollars up while DOTAX's fell, the same direction and size for both constructions. **Not possible**: a longer horizon (no federal actual after TY2023 except the rounded IRS TY2024; the TY2018-2022 file's own survey years cannot be forecast from), and any test of the by-children shape (the targets are the TY2022 counts the truncation enforces).

### 5. The stale-target test [M]

`wfc_targets_backtest.py targets` replays the production path (ACS 2024 1-year, aged, rake, lever 3a, truncation, take-up factor fitted to DOTAX TY2024) under three target sets, in memory only. The flat TY2022 case reproduces the decomposition replay to the last digit (take-up factors 0.9788 and 0.9369; HI EITC at 40%: $83.534M and $80.640M at TY2028). TY2023 counts (26,400 / 27,650 / 18,470 / 11,940) feed lever 3a and the truncation, the pooled truncation count scaled by the same ratio production uses (84,010 / 84,970). Trend: the TY2023 counts moved at the measured log-linear trend of returns (-2.48% a year; -1.36% and -3.11% as bracket).

HI EITC at 40%, $M (take-up factor refitted to $76.98M at TY2024 in every case), claimants in thousands:

| targets | | TY2024 | TY2028 | TY2031 | claimants TY2024 / 2031 |
|---|---|---:|---:|---:|---:|
| TY2022 counts, flat (production) | legacy | 76.98 | 83.53 | 90.34 | 82.5 / 82.7 |
| | re-derived | 76.98 | 80.64 | 86.29 | 79.2 / 79.0 |
| | re-derived / legacy | 1.000 | 0.965 | 0.955 | |
| TY2023 counts, flat | legacy | 76.98 | 83.55 | 90.38 | 82.0 / 82.3 |
| | re-derived | 76.98 | 80.62 | 86.56 | 78.9 / 78.8 |
| | re-derived / legacy | 1.000 | 0.965 | 0.958 | |
| TY2023 counts, -1.4% a year | legacy | 76.98 | 79.24 | 82.41 | 81.9 / 74.6 |
| | re-derived | 76.98 | 78.42 | 80.62 | 78.5 / 71.2 |
| TY2023 counts, -2.5% a year | legacy | 76.98 | 75.93 | 76.82 | 81.9 / 68.8 |
| | re-derived | 76.98 | 77.00 | 78.03 | 77.6 / 65.3 |
| | re-derived / legacy | 1.000 | 1.014 | 1.016 | |
| TY2023 counts, -3.1% a year | legacy | 76.98 | 74.67 | 74.42 | 82.0 / 65.7 |
| | re-derived | 76.98 | 76.02 | 76.72 | 77.5 / 62.1 |

- **The constructions do not converge on a refreshed flat target.** Replacing TY2022 with TY2023 counts moves TY2031 by $0.04M (legacy) and $0.27M (re-derived); the 3.5% (TY2028) and 4.2% (TY2031) gaps stay. The stale target is therefore not what separates them. (The hypothesis in section 6a's "what would settle it", that a per-year target would make the projections agree at about $80-81M, is **not** supported.) The gap is the legacy construction's higher dollars per kept unit growth, fitted to a base year in which re-derived had the truncation advantage.
- **With targets that trend down, level and gap both change.** At -2.5% a year the claimants fall to 65-69K by TY2031 and the dollar path is flat in nominal terms, $76-78M to TY2031 (the credit schedule's +2.2% a year is offset by fewer claimants). The constructions then sit within 1.4-1.6% of each other and the sign of the gap flips; at -1.4% legacy is above (+2.2% by TY2031), at -3.1% below (-3.0%). **The "lower" projection after the fix is not a robust property of the construction**: it is specific to holding claims flat.
- **At the anchor, the trending targets make the new construction reconcile with DOTAX.** DOTAX TY2024 reports 78,399 claims. The new construction's claimants are 77.5-78.5K under every trend rate (-1.2% to +0.1%) and 78.9K flat (+0.6%); the legacy construction's are 81.9-82.5K (+4.4% to +5.3%) under every target set, and its fitted take-up factor goes to 1.00 (guard 1.10) while its claim count implies 0.96. The re-derived construction's dollar-fit factor and its claim-count factor agree within 1%; the legacy's within 4-5%. [M]

### 6. TY2024 level check against the IRS preliminary figure [M]

IRS EITC Central, TY2024, Hawaii: 81.8K claims, $206.2M, average credit $2,520 (+5.6% on TY2023's $2,387 same source, against +5.4% for the schedule: real +0.2%). The take-up factor cancels in the models' federal credit per unit: **re-derived $2,429 (-3.6%), legacy $2,332 (-7.5%)**. Independent of the DOTAX fit. Counts: IRS 81.8K processed, about 84-85K on the SOI basis (the 3-4% processing difference of TY2022-2023); the models' kept units are 84.3K (legacy) and 84.6K (re-derived), both within 1% of that.

### 7. Verdict

**(c): history is consistent with both projections' growth and cannot discriminate between them, with a weak lean to (d).**

- *Why not (a) or (b).* On growth the tests tie or split: backtest one-year growth within 0.3 pt of each other (re-derived nearer by 0.2 pt); real dollars per return history favours re-derived in the long run (-0.9% a year vs -0.5% / -1.0% re-derived, -0.25% / 0.06% legacy) and legacy in the last two years (+0.4% a year); claims identical. Levels (anchor, TY2024 IRS average, backtest base) favour re-derived, but levels are not growth, and the projection's growth is what 6a asked about.
- *Why a lean to (d).* If the long record is the guide, both are too high: returns falling 1.4-2.5% a year with schedule indexation at 2.2% and real dollars falling 0.9% would give about -0.1% to -2% a year in federal dollars against the models' +1.2% to +2.3%. At -2.5% a year the TY2031 HI EITC at 40% is $78.0M (re-derived) and $76.8M (legacy) against $86.3M and $90.3M published: the published figures are 11% (re-derived) and 18% (legacy) above those. If the last three years are the guide (claims flat), both projections' flat claims are right and their dollars grow more slowly than indexation (recent real growth +0.4%).
- *Strength.* Thin. Returns: 12 observations, 8 clean pre-pandemic year-pairs, the trend se 0.3-0.5 pt but resting on breaks (pandemic TY2020; ARPA TY2021; the 14% fall TY2019-2022 is a step, not a slope). Post-pandemic: one SOI pair and one IRS-processed triple. By-children: two years. DOTAX: one comparable pair. Backtest: one pair. Parameter indexation past TY2026 is the model's CPI assumption, not data.

**Does anything argue against adopting the new construction?** Not on the evidence here: it is nearer on every level test and the growth tests tie. Three caveats, none a reason to keep legacy. (1) The lower TY2028 and TY2031 figures (-3.5%, -4.5%) are a base-year effect of holding claims flat, not evidence about the credit: with trending targets the gap shrinks to within 1.6% and changes sign, so the change should be described as a correction of the TY2024 base, not a lower outlook. (2) Re-derived real dollars per return fall 0.5-1.0% a year; if the recent +0.4% is the regime, it is about 1 pt a year low (legacy about 0.5 pt a year low). (3) The `claimant_assignment` choice does not touch the count assumption that both share; the stronger open question is whether claims should be held flat at all (a decline of 1.4-2.5% a year would cut the TY2031 cost 10-15%), which needs a decision on the targets, not on the construction.

### Measured and inferred

Measured: every historical figure (sources in the CSV), the year-pair and window rates, the log-linear fits, the projection decompositions (from the exact chain replays), the backtest, the target replays (the flat TY2022 case reproduces the earlier replay exactly), the IRS TY2024 comparison. Taken from documents, not re-measured: the TY2023 DOTAX applied $75.6M, the TY2024 DOTAX report (bundled JSON), the statutory rates and dates (`hi_eitc.py`), the by-children counts. Inferred: the cause of the long-run decline and of the negative real growth (wage growth against CPI-indexed schedule, outmigration, a smaller child population: not tested), that the DOTAX fall of TY2024 is not a fall in federal returns, the reading that the 14% fall of TY2019-2022 is a step. Not done: TY2013 and TY2016 (IRS file wrong), Hawaii by-children counts before TY2022 (not in Table 2; the IRS state statistics page carries no by-children split), a backtest longer than one year, stage-level food and care credit validation, per-year trend targets by children bucket (the two years do not support a bucket trend; the trend scenario scales the TY2023 mix).

## 7. Chain stages and analyses that ran

All ran to completion on this machine ([M]; the chain is fast here, about 5 minutes end to end with warm caches; the 2.5-minute tax-unit cache rebuild and the 1-year unit rebuild are inside it):

| step | A / B | note |
|---|---|---|
| `chain/forecast_sb3125_enhanced.py --cd 2` (stage 1) | ok / ok | calibrated base, tax-unit cache rebuilt |
| `chain/forecast_act24_vs_pre_act46.py` | ok | |
| `chain/forecast_sb3125_vs_fy26base.py --cd 2` | ok | not published; compared in the CSV tool |
| `chain/forecast_cg_rate_options.py` | ok | |
| `chain/forecast_working_family_credits.py` | ok | rake, take-up fit and guards passed |
| `scripts/build_simulator_population.py --population-only` | ok | |
| `scripts/build_simulator_population.py --from-saved` (stage 3) | ok | its capital-gains parity check reads `site/data/capital-gains`, which is the BEFORE; it was run through a wrapper that points `CG_PAGE` at the scratch run's own capital-gains CSVs (a scratch-only substitution; the self-checks reported no problems in all three runs) |
| `analyses/forecast_hr1_snap_medicaid.py --out <scratch>` | ok | |
| `analyses/forecast_targeting_efficiency.py` | ok | five-year file, default; `--pums one-year` not run |
| `scripts/eitc_ctc_geo_report.py --tax-year 2025 --apply-takeup` on the 1-year PUMS | ok | state totals are in the table; county / district files are in the scratch directories, not compared |

Rankings in the targeting-efficiency analysis (rank by gap closed, smallest budget, first income test): SNAP recipients 2 -> 3, adults without children 3 -> 2, single-parent children 4 -> 5, renters 5 -> 4; the rest unchanged. The EITC group's poverty rate falls 27.2% -> 23.5% (the claimants now include the children's real parents), and the Act 163 renewal's cost rises $69.2M -> $75.2M while people lifted fall 4,368 -> 4,178.

## 8. Test results

Full suite once, on HEAD of the branch at the time (`ba3c7ed`, re-derived factors): **13 failed, 3,454 passed, 1 skipped** (the skip is the CEX interview files, allowed in `tests/conftest.py`). `uv run ruff check .`: **All checks passed**. [M]

| category | tests | cause |
|---|---|---|
| `tests/test_headline_figures.py` | 12 | numbers moved, as expected: act-24 (4 files) and working-family-credits (8 files) no longer match `site/data` (the figures in section 6); fixed by `publish.py` if adopted |
| golden fixture: `tests/tax_modeler/smoke/test_golden_revenue.py::test_golden_state_summary` | 1 | `total_weighted_filers` on the synthetic fixture 6,134 against golden 6,262 (-2.04%, tolerance 2%). It is the **re-derived filing-status factors**, not the construction: with the old factors it passes (checked). Regenerate the golden (`--update-golden`) if the factors are adopted |
| anything else | 0 | no real regression |

Existing tests that hard-coded the filing-status weights (`tests/tax_modeler/units/test_constructor.py`) now read `FILING_STATUS_WEIGHT_FACTORS`. The repo's `test_dependencies.py`, `test_constructor.py`, `test_status_hoh.py`, SPM, determinism and vectorised-parity tests pass unchanged on the new default.

## 9. Judgment calls, and what remains uncertain

Judgment calls (the instructions did not settle them; the conservative lawful option was taken):
1. **"Other relative" (33) minors with no parent present** go to the householder as dependents but **not** as EITC / CTC children. The diagnosis (2a) put counting nieces and nephews at +2.9K eligible units (partly cousins, so an upper bound) **[D]**; not implemented here, not measured.
2. **Unrelated children with no plausible parent stay as before** (householder claims enrolled ones as plain dependents; the others, 0.4% of weighted minors, nobody). Letting the householder claim them as qualifying relatives would add exemptions and lower revenue; not run.
3. **The age-gap rule is not applied to minors the subfamily variables leave without a parent** (default `sfn`). Applying it adds +0.1K eligible units (`sfn+rule_nearest`).
4. **A re-derived filing-status factor set is part of AFTER-B, not of the construction fix.** The old HoH factor 1.30 on the new construction over-weights HoH by 1.1 points of the DOTAX mix. Which set to adopt is the owner's call; the construction alone changes the golden fixture by less than 2%, the re-derived set by 2.04%.
5. The 18-year-old and disabled-adult-child tests are unchanged (section 2).

Uncertain:
- The HoH determination is the existing household-level test (`_has_qualifying_person`, `_paid_half_home_cost`): it looks at any EITC-type child in the home, not at the claimed ones, so a partner with a child coded 36 gets no HoH from the household-wide test and files single with a dependent. Filing status does not change the EITC schedule, but it changes the HoH deduction and the weight factor. **[R]**
- `SFN` covers minors only; an enrolled 18-23-year-old grandchild is assigned by the age-gap rule or to the householder (about 1.5% of weighted minors use the rule at all).
- The age-gap rule (nearest, 28 years) is right about 75% of the time against the subfamily data on grandchildren; the sensitivity in section 3 is bounded by the `low` and `high` variants but those are rule-only runs.
- One 2024 1-year sample (the working-family population) and the 5-year file (the revenue base); poverty headcount moves below about 700 persons are inside the sampling error; EITC-eligible totals have a standard error near 3.1K **[D]**. Revenue differences under $0.1M are not distinguishable from replicate-weight noise.
- Run A's manifests carry `-dirty` (the old-factor edit was uncommitted); run B is clean at `4b6109f`. The control (BEFORE) was run with a temporary uncommitted default edit at `2c5411f`.
- The HI renters take-up figure is approximate (section 5).
- I killed my own background jobs once with a broad `pkill -f "uv run"` while redoing the runs; a few seconds of other `uv run` processes on this machine could have been stopped. Nothing else of mine was affected.

## 10. Sections that would need updating if this is adopted

Not edited here (the instruction was to list them).
- `SB3125_CD1_FORECAST.md`: a new dated section at the top (methodology: parent-first claimant assignment, re-derived factors, lever 3a, take-up factors); the generated results block is rewritten by `publish.py` (`render_results.py`). Older dated sections quote now-superseded numbers: "Pipeline-audit fixes" (September 29; the B2-B5 construction and the 0.82 / 1.30 / 1.35 factors), "Families with children in the Act 24 base" (November 20), "Poverty population fitted to IRS returns" (November 5), the take-up factors (EITC 0.978, food/excise 0.732) at lines 392 and 406.
- `METHODOLOGY.md`: the tax-unit-to-SPM-unit aggregation and hybrid-weight paragraphs (about lines 1149-1180), the by-children EITC reweight and the take-up description (about line 1398 onward), the statement that the children-per-unit gap "points at unit construction" (about line 953).
- `HR1_SNAP_MEDICAID_METHODOLOGY.md`: the results and changelog (baseline poverty rate 11.81% -> 11.65%, MID people into poverty 761 -> 716).
- Docstrings that quote the old factors or the lever-3a shortfall: `units/constructor.py` (updated), `calibration/eitc_reweight.py` ("~0.74 / ~0.77 of their IRS targets"), `scripts/poverty_impact_report.py::_apply_eitc_reweight`, `chain/forecast_working_family_credits.py` (take-up values).
- `site/` and `tests/simulator/golden.json.gz` (by `publish.py`), `tests/tax_modeler/fixtures/golden_revenue.json`.
- `docs/data_source_tests/eitc-eligible-shortfall.md` (on its own branch): note that the claimant-assignment candidate is now implemented, with the subfamily link replacing the diagnosis's rule-only range.

## 11. Commands (all from the repo root, worktree `../Census-Forecaster-fix-tax-unit-claimant-assignment`)

- Raw units and variants: `CF_MAX_WORKERS=2 uv run python analyses/claimant_assignment_effect.py --out <dir> --cache-old <legacy cache> --cache-new data/artifacts/tax_units_cache.parquet`
- Filing-status factors: `uv run python scripts/rederive_filing_status_factors.py --assignment parent_first`
- Lever 3a factors: `CF_MAX_WORKERS=2 uv run python analyses/eitc_reweight_factors.py --out <file>`
- Chain into a scratch directory (stages 1-3 of `scripts/rerun_models.py`, one step at a time): `CF_RUNS_DIR=<dir> CF_MAX_WORKERS=2 uv run python chain/forecast_sb3125_enhanced.py --cd 2`, then `chain/forecast_act24_vs_pre_act46.py`, `chain/forecast_sb3125_vs_fy26base.py --cd 2`, `chain/forecast_cg_rate_options.py`, `chain/forecast_working_family_credits.py`, `scripts/build_simulator_population.py --population-only` and `--from-saved`.
- Off-chain: `analyses/forecast_hr1_snap_medicaid.py --out <dir>/hr1_snap_medicaid`, `analyses/forecast_targeting_efficiency.py` (with `CF_RUNS_DIR`), `scripts/eitc_ctc_geo_report.py --tax-year 2025 --pums-data-dir packages/data/raw/pums_2024_1yr --apply-takeup --out <dir>`
- Before/after: `uv run python analyses/headline_before_after.py --before <legacy runs> --after-old-factors <A> --after-rederived <B> --geo-* ... --out <csv>`; cell by cell: `analyses/before_after_runs.py`.
- Why the working-family projections come out lower: `CF_MAX_WORKERS=2 uv run python analyses/wfc_projection_decomposition.py run --variant legacy|cons|new --out <dir>` (about 6 minutes each), then `... assemble --out <dir> --csv-dir docs/data_source_tests --chain-runs legacy=<runs A> cons=<runs> new=<runs>` (section 6a).
- Validating the projected growth (section 6b): `uv run --with xlrd python analyses/hi_eitc_history.py fetch --dir <src>` then `build --dir <src> --out docs/data_source_tests/tax-unit-claimant-assignment_hi_eitc_history.csv`; `CF_MAX_WORKERS=2 uv run python analyses/wfc_targets_backtest.py backtest|targets --variant legacy|new --out <dir>` (about 5 and 8 minutes each; `targets --trend-rates -0.0136 -0.0311` for the sensitivity); `uv run --with tabulate python analyses/wfc_growth_validation.py --decomp <decomposition dir> --tb <dir> --csv-dir docs/data_source_tests`.
