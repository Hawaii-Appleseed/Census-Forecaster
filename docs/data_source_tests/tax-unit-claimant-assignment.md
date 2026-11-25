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

**What would settle it:** (1) DOTAX's TY2025 Earned Income Tax Credit Report (the TY2024 one is dated December 2025): a second year of claims and dollars on one basis; (2) the IRS Statistics of Income Hawaii EITC returns by children for TY2023 and TY2024, which would replace the stale TY2022 targets lever 3a and the truncation use (the count is what moves the answer), after which the model's implied claims trend could be compared to the actual one; (3) rerunning this script with the reweight targets set to the IRS count for each year: if the projections then agree across constructions at about $80-81M (the rake-only figures above) the gap is the lever's stale targets, not the fix.

### Measured and inferred

Measured (by the replay, exact against the chain): every figure in the tables above, the stage waterfall, the by-bucket waterfall, the pool, lift, kept and claimant counts, the schedule-region shares, the identity new / legacy = dollar ratio x factor ratio, the anchor agreement ($76.98M in all three runs). Inferred: that the 3+-child swing is ageing-driven record composition (not decomposed within the bucket); that the food-credit decline comes from the smaller lift in low-income family weight; that the DOTAX/IRS trend rows are comparable across reports; the reading that the legacy extra growth is the reweight's compensation for a short pool rather than real growth. The order of the three waterfall steps does not matter beyond $0.03M. Not done: stage-level decomposition of the food and care credits; the TY2022 and TY2023 model years (the TY2022 credit is re-pegged to its nominal DOTAX dollars by the take-up step, so it is not a growth observation).

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
