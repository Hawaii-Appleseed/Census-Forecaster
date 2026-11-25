# H.R.1 in Hawaiʻi: SNAP and Medicaid losses — methodology

**Status (November 22, 2026): published at `site/hr1-snap-medicaid/` ("Work Rules, Lost Benefits").** Script:
`analyses/forecast_hr1_snap_medicaid.py`. Logic: `tax_modeler.programs.hr1_safety_net`. Run outputs:
`runs/hr1_snap_medicaid/` (gitignored), published to `site/data/hr1-snap-medicaid/`. Not on the published chain:
`scripts/publish.py` does not rerun it ([Refreshing](#refreshing) says how).

The question: in a full year under the 2025 reconciliation law (H.R.1, Public Law 119-21), how many people in
Hawaiʻi lose SNAP or Medicaid because of the new work rules, how many federal dollars go with them, what that does
to poverty, where in the state it falls, and what the State pays, either because the law bills it or because it
chooses to replace what was lost.

## Changelog

- **November 25, 2026 (sixth version).** Housing assistance on the poverty side is now one value per renter
  household, Census's SPM valuation (the lesser of the Fair Market Rent and the housing part of the household's
  renter line, less 30% of its income), with take-up a single probability that meets HUD's 15,600 households and
  is drawn per household (`benefits.housing`). Before, it was valued per tax unit at $30,000 less 30% of income
  and given to the lowest incomes first, so the cutoff fell among tax units with no income and which households
  got $30,000 was an arbitrary tie-break: different tie-breaks alone moved MID between 692 and 969, and small
  income changes moved it by 160. The poverty draws now hash each household's own random numbers. Modeled housing
  aid is $147.5M against HUD's $140M (about $470M before). The baseline SPM rate rises to 12.22%, MID to 820
  people (90% interval 620 to 1,020); a change in interest income's growth that moved MID by 162 now moves it by
  28. SNAP and Medicaid figures are unchanged.
- **November 25, 2026 (fifth version).** Rerun after two changes on the poverty side, neither in this analysis's
  own code: the SPM thresholds and medical-spending draws (PRs #103, #106, #107), which had already moved the
  published page to an 11.71% baseline and 852 people pushed into poverty (MID) without this note being updated,
  and income aged on CBO's February 2026 projections for every component (`cbo_aging.DEFAULT_VINTAGE = "2026-02"`;
  retirement per person on prior-year CPI-U, business on proprietors' income). The baseline SPM rate is now
  11.81% and MID 760 people (90% interval 550 to 970). The count of people pushed into poverty moves 10-20% with
  any small change in the incomes it draws on (the draws of who loses SNAP reshuffle), so changes of that size
  are within its sampling error. Statewide SNAP and Medicaid counts and dollars are unchanged; the county split
  of SNAP losers moves by about a dozen people (Hawaiʻi County 1,573 to 1,585, Honolulu 4,511 to 4,499).
- **November 23, 2026 (fourth version).** Federal Medicaid funding now follows KFF's method, which Appleseed's
  SNAP and Medicaid timeline also uses: CBO's national estimate for each Medicaid section of the law, allocated to
  Hawaiʻi by KFF's keys (`programs/kff_medicaid_allocation.py`). The result is $3.9 billion over 2025-2034
  ($2.9 billion to $4.9 billion), about $390 million a year, rising from $196 million in 2027 to $694 million in
  2034. The timeline's $330M-$550M a year is KFF's July 1, 2025 figure for the Senate bill ($4.37 billion over ten
  years); KFF replaced it on July 23, 2025 with the enacted-law figure used here. The dollars attached to this
  analysis's own coverage estimate now use MACPAC's spending per full-year-equivalent expansion adult ($5,731,
  FY2023) instead of KFF's $4,853 per full-benefit enrollee, which counts anyone enrolled for a month: MID federal
  dollars move from $171.0 million to $201.9 million a year.
- **November 22, 2026 (third version).** The SNAP placement used here moved into the shared
  `benefits.snap.calibrate_snap_receipt`, which the poverty pipeline now uses too, and gained Hawaiʻi's minimum
  benefit for households of one or two. SPM MID moves from 610 to 550 people pushed into poverty; the SNAP and
  Medicaid counts are unchanged. `assign_geography` now draws from the population-weighted crosswalk, so the two
  issues this note listed under "Known issues elsewhere" are fixed.
- **November 22, 2026 (second version, PR after #97).** Added SPM poverty, counties and all 51 House and 25 Senate
  districts, and the site page. Medicaid's main run moved from the 2024 one-year ACS to the 2020–24 five-year file
  (5,788 non-disabled Medicaid adults in sample instead of 1,118); MID moves from 39,269 to 39,140, and the
  one-year file stays as the check. Outputs moved from `reports/` to `runs/` with a manifest. SNAP figures
  unchanged.
- **November 22, 2026 (PR #97).** First estimate: SNAP from the QC file, Medicaid from the ACS.

## Results (run of November 25, 2026, commit `08cfdfd`)

| | LOW | MID | HIGH |
|---|---|---|---|
| SNAP: adults losing eligibility | 4,950 | 7,250 | 9,630 |
| SNAP: benefits lost a year, FY2027 allotments | $22.9M | $33.3M | $44.0M |
| SPM: people pushed into poverty | 570 | 820 | 1,060 |
| SPM: children among them | 59 | 87 | 113 |
| SPM: added shortfall below the line | $14.6M | $20.8M | $25.9M |
| Medicaid: expansion adults losing coverage | 24,400 | 39,140 | 51,800 |
| Medicaid: federal dollars for those adults, a full year | $125.9M | $201.9M | $267.2M |
| Medicaid: all federal funding lost, 2025-2034, KFF's method | $2,927M | $3,902M | $4,878M |

- SNAP, 90% sampling interval on MID: 5,620 to 8,990 adults; $25.5M to $41.1M.
- SPM, 90% sampling interval on MID: 620 to 1,020 people (ACS replicate weights). Baseline SPM rate 12.22% (TY2027).
- 52% of the adults who lose SNAP (MID) are in SPM units already in poverty; 23% under half the line.
- SNAP costs moved to the State from federal fiscal year 2028: $91.1M a year for benefits at the 15% tier
  Hawaiʻi's FY2025 error rate implies ($0, $30.4M or $60.7M at the lower tiers), plus $14.8M for administration.
- Medicaid: of 129,577 expansion adults, 48% already meet the test, 28% are exempt on what the survey shows, and
  24% (30,780) are neither. Sampling interval on MID: 37,920 to 40,360. The 2024 one-year file gives 39,270.
- Replacing the lost Medicaid coverage with state funds would cost $224M a year in the MID case; the State's own
  share of that coverage today is $22M.
- Federal Medicaid funding by KFF's method (LOW and HIGH in the table above are KFF's plus or minus 25 percent, not
  this analysis's loss rates): $196M in FY2027, $266M in 2028, $419M in 2029, $495M in 2030, $694M in 2034. By change,
  2025-2034: work requirement $1,805M, six-month redeterminations $347M, state-directed payments $859M, eligibility
  and enrollment rules $449M, provider taxes $329M, other expansion-group changes $198M, other changes $205M, less
  interactions $290M.
- By county (MID): Honolulu 24,460 lose Medicaid, 4,500 lose SNAP, 500 pushed into poverty; Hawaiʻi 7,760,
  1,590, 190; Maui 4,800, 810, 90; Kauaʻi 2,130, 360, 40. Hawaiʻi County loses the most Medicaid coverage for its
  size (4.1% of residents).

LOW, MID and HIGH differ only in the share of people who fail to keep a benefit. That share is an assumption
(see "Loss rates"). The counts of who is subject, exempt and working are measured.

## What the law changes (only the parts modeled)

**SNAP, section 10102.** The three-month time limit for adults who do not work 80 hours a month used to cover
ages 18 to 54 in units with no child under 18. It now covers ages 18 to 64, and only a child under 14 exempts
the adults in a unit. The exceptions for veterans, people who are homeless and former foster youth are repealed.
Hawaiʻi DHS applied the new groups from November 1, 2025.

**SNAP, sections 10105 and 10106.** From federal fiscal year 2028 a state pays 0%, 5%, 10% or 15% of benefit
costs, by its payment error rate (under 6%, 6 to 8, 8 to 10, 10 or more). For FY2028 the State may use its FY2025
or FY2026 rate. Hawaiʻi's FY2025 rate is 10.92%; FY2026 is not published. From FY2027 the State pays 75% of
administrative costs, up from 50%.

**Medicaid, section 71119.** Expansion adults aged 19 to 64 must show 80 hours a month of work, service or
school, or $580 of monthly income. Parents of a child 13 or under, the medically frail and several smaller groups
are exempt. Hawaiʻi asks for one qualifying month in the six before each renewal, goes live January 1, 2027, and
holds people harmless through June 30, 2027.

**Medicaid, section 71107.** Expansion adults are renewed every six months, not every twelve, from January 2027.

## SNAP: who and how much

**Data.** Hawaiʻi's 571 units in the FY2024 SNAP Quality Control public-use file (October 2023 to September
2024), tracked in `data/snap_qc/` and rebuilt by `scripts/fetch_snap_qc_hawaii.py`. They weight to 83,109 units,
152,269 members and $695.1M a year; DHS issued $694.9M in state fiscal year 2025. The file records each member's
age, disability, work registration, time-limit status, hours and countable income, and the unit's deductions and
benefit. The ACS has none of the program's own facts and finds a third fewer SNAP households.

**Who is newly subject** (`classify_snap_adults`):

| Group | Adults | Sample |
|---|---|---|
| 55 to 64, no child under 14 | 16,418 | 114 |
| 18 to 54, youngest child 14 to 17 | 3,426 | 24 |
| 18 to 54, repealed exception (veteran, homeless, foster) | 1,072 | 7 |
| **Newly subject** | **20,916** | **145** |

DHS estimated about 16,000 members aged 55 to 64 newly covered (FAQ, October 2025); Hawaiʻi Appleseed's timeline
gives 16,570 from DHS's April 2025 data.

Each is `exempt` (a disability, or a work-registration exemption that carries to the time limit: unfit for work,
caring for a child under 6 or an incapacitated person, student, TANF work compliance, unemployment compensation,
treatment), `meets` (20 or more hours a week, the 30-hour registration exemption, or a qualifying work program) or
`at_risk`. Of the 20,916: 7,845 exempt, 3,163 meet the test, 9,908 at risk.

**Not counted.** Adults 18 to 54 with no child were already under the time limit when Hawaiʻi's statewide waiver
ended on June 30, 2024, before H.R.1. The file shows 6,900 of them neither exempt nor working 20 hours. Maui and
Kauaʻi counties kept a waiver until June 30, 2025, four days before the law was signed, and the law's new test
(unemployment of 1.5 times the national rate) rules out another. The public file has no county, so those adults
are left out with the rest; the estimate is low by whatever a renewed Maui and Kauaʻi waiver would have protected.

**Dollars** (`snap_loss_if_removed`). A one-person unit loses its whole benefit. A larger unit is recomputed one
person smaller with all but a pro rata share of the removed member's income still counted, as 7 CFR 273.11(c)(2)
prescribes. The benefit formula reproduces the recorded benefit for 570 of 571 units. FY2024 dollars are restated
at FY2027 maximum allotments by unit size (Hawaiʻi's maximums fell 6% between the two).

**State cost.** The benefit share is applied to $607.4M, DHS issuance for July to September 2026 annualized. That
base already reflects whatever the time limit has removed since November 2025.

**Check against the caseload.** DHS's count fell from 169,604 persons in October 2025 to 157,954 in May 2026.
The MID case here is 7,250 adults. Other things moved the caseload in those months, so this is a plausibility
check only.

## SNAP: poverty, on the ACS population

The QC file has no county and no household around the SNAP unit, and the SPM counts the whole family's
resources. So SNAP is put on the ACS population the rest of the site uses (2020–24 five-year file, aged to
TY2027 with CBO component growth; the credits and other benefits as the working-family credits step builds them,
on their latest tables, TY2025), and the QC results are carried over to it.

**SNAP baseline** (`acs_snap_baseline`, a wrapper over `benefits.snap.calibrate_snap_receipt`, which the
poverty pipeline's `_apply_snap` also uses):

1. Every household that reports SNAP (`FS` = 1) is a recipient: 56,579 weighted households. Its benefit is the
   module's formula under FY2027 Hawaiʻi rules (`hawaii_snap_parameters(2027)`: the October 2026 allotments,
   standard deductions, a 200%-of-poverty gross limit and no net test, as categorical eligibility gives, and the
   $40 minimum for households of one or two). 22,450 of them fail the gross test on annual survey income (income
   of members outside the SNAP unit, or months off SNAP); they get the QC file's average benefit for their size
   instead of being dropped.
2. Eligible households that do not report SNAP are drawn at random (a hash of the household id, the same on every
   run), each with probability 0.26, to reach DHS's 77,985 households a month: 78,538 in all.
3. Every benefit is scaled by one factor (0.76) to DHS's annualized issuance, $607.4M.

An earlier version of step 2 added the lowest-income non-reporters first. That put SNAP on the very poorest
households and pulled the baseline SPM rate down to 8.3%; the random draw gives 9.34%. Before this placement was
shared, the poverty pipeline's own SNAP gave 9.22% on the same population, from $355M placed on the
lowest-income eligible tax units.

Persons in SNAP households come to 266,000 against DHS's 157,850: ACS households are larger than SNAP units,
which is also why many adults who lose SNAP live in SPM units above the line.

**Who loses it on the ACS** (`classify_acs_snap_adults`, `calibrate_to_qc`). Adults 18 to 64 in recipient
households get the QC file's groups: 55 to 64 with no child under 14 in the household; 18 to 54 whose youngest
child in the household is 14 to 17; 18 to 54 veterans with no child (the ACS cannot see homelessness or foster
history, so the repealed-exception group is veterans only). Status: `exempt` (ACS disability, SSI or in school),
`meets` (employed and usually working 20 or more hours), `at_risk`. Each adult's probability of losing SNAP is then
set cell by cell, (group, status), so the ACS's expected losers equal the QC file's under the same loss rates. A cell
the ACS lacks, or one held to probability 1, passes its shortfall to the group's other cells. In MID the 55-to-64
at-risk cell needs a probability of 0.97 (the ACS finds 5,770 such adults, the QC file 7,990)
(`snap_calibration.csv`).

**Dollars on the ACS** (`acs_snap_loss`). Each adult's loss is the household's benefit recomputed one person smaller
with 1/n of the adult's income still counted, or 1/n of the household's benefit where it is on the size average.
The total is then scaled, per case, to the QC file's dollars (MID: the ACS gives $20.7M, the QC file $33.3M; scale
1.60).

**Poverty.** Who loses SNAP is drawn 2,000 times (`tax_modeler.poverty.draws`, one block per case), and each SPM
unit's probability of being poor is the mean over the draws. The poverty engine reads the expected loss as the
`hr1_snap_<case>_loss` column (`_LOSS_COLUMN_SCENARIOS` in `poverty/impact.py`). Standard errors are the ACS
successive-difference replicates; children into poverty are computed from the SPM units' child counts as in the
working-family credits step.

## Medicaid

**Data.** ACS PUMS persons aged 19 to 64 who report Medicaid (`HINS4`). The 2020–24 five-year file is the main run
(5,788 non-disabled adults in sample); the 2024 one-year file is the check and agrees within 0.5%.

**Scaling** (`split_expansion`). The survey finds 117,300 non-disabled Medicaid adults; Med-QUEST enrolled
179,583 in September 2026 (129,577 expansion, 50,006 parents and caretakers). Every weight is scaled by one
factor (1.53) to the pool, then parents are assigned to the parent and caretaker group from the lowest family
income up until its count is met. The rest are expansion adults. Each of the 80 replicates is scaled to the same
Med-QUEST count, so the sampling error is about who, not how many.

**Status** (`classify_medicaid_adults`). `exempt`: a child 13 or under of their own, an ACS disability, a birth
in the last year, American Indian or Alaska Native. `meets`: employed at the interview, enrolled in school, or
worked in the last year at $580 a month or more, with the probability that a six-month window catches a spell of
that length. `at_risk`: neither. Of the at-risk group, 82% are out of the labor force and 30% are 55 to 64.

**Dollars.** $5,731 per expansion adult covered for a full year (MACPAC MACStats Exhibit 22, FY2023, all
enrollees in the new adult group), 90% federal. Not indexed to 2027. KFF's $4,853 per full-benefit enrollee, used
before November 23, 2026, divides spending by everyone enrolled for at least a month and so understates the cost of
a year of coverage.

## Medicaid: federal funding, KFF's method

`programs/kff_medicaid_allocation.py` rebuilds KFF's "Allocating CBO's Estimates of Federal Medicaid Spending
Reductions Across the States: Enacted Reconciliation Package" (July 23, 2025) for Hawaiʻi. CBO's estimate of each
Medicaid section (July 21, 2025, Title VII Table 7: $990 billion over 2025-2034 before interactions) is divided among
the states by KFF's key for that section:

| Sections | Key | Hawaiʻi's share |
|---|---|---|
| 71119 work requirement, 71107 redeterminations, 71110 emergency match, 71120 cost sharing | FY2024 federal spending on the expansion group (Wisconsin's ACA-equivalent added) | 0.563% |
| 71116 state-directed payments, House-passed part | FY2023 federal managed care spending, states with directed payments | 0.568% |
| 71116, the rest | the same, states KFF found likely or possibly affected (Hawaiʻi likely) | 0.599% |
| 71115 provider taxes, House-passed part | FY2024 federal spending, all states | 0.375% |
| 71115, the rest | expansion states with hospital or MCO taxes over 3.5% | 0 (Hawaiʻi not listed) |
| 71117 uniform-tax waiver; 71114 expansion-incentive sunset | states with MCO taxes; non-expansion states | 0 |
| 71108 home equity limit | long-term care spending, states with limits over $1 million | 0.9% |
| everything else, and CBO's interactions (82.4% of Subtitle B's) | FY2024 federal spending, all states | 0.375% |

The rebuild comes to $3,965 million, 1.6% above KFF's published $3,902 million; not every state list or baseline
KFF used is published. Every section and year is scaled by one factor (0.984) to KFF's total, so the totals quoted
are KFF's and the split by provision and year is the rebuild's. The range is KFF's, plus or minus 25 percent. The
inputs, each with its source, are in `data/medicaid/kff_allocation_inputs.json` and its README.

How it lines up with the coverage estimate above: KFF's method gives the work requirement and six-month
redeterminations $100M in FY2027, $144M in 2028, $262M in 2029 and $300M in 2030 as CBO phases them in and costs grow.
This analysis's MID, $202M for a full year at 2027 enrollment and FY2023 cost, sits between 2028 and 2029. CBO's
national work-requirement estimate, scaled by Hawaiʻi's share of expansion spending, implies roughly as many
Hawaiʻi adults losing coverage at full effect as the MID case here.

Two Hawaiʻi facts the method does not use: DHS says the hospital provider tax already sits at the law's 3.5% limit
(the method still assigns Hawaiʻi its share of the House-passed provider-tax cut, $329M over ten years); and the
directed-payment cut ($859M) rests on KFF counting Hawaiʻi among the likely-affected states.

Medicaid is not in the SPM (coverage is not a resource), and the model's medical out-of-pocket costs do not depend
on coverage, so losing Medicaid moves no one across the poverty line here.

## Counties and districts

The ACS identifies nothing below the PUMA (ten in Hawaiʻi, about 100,000 to 240,000 people each). Every figure is
computed by PUMA and spread over the areas each PUMA touches in proportion to their 2020 census population
(`puma_crosswalk.apportion`), so an area gets its PUMAs' averages. Totals match across counties, House and Senate
districts (each sums to the statewide figure). Replicate totals are apportioned the same way for the standard
errors, which therefore carry PUMA-level sampling error only: a district inside one PUMA cannot differ from it, and
the districts of PUMA 200 (Hawaiʻi County) all show the same rates.

The crosswalk (`data/crosswalks/hawaii_puma_district_population_2022.csv`, 112 pieces) is built from census blocks
by `scripts/build_puma_district_crosswalk.py`: 2020 P.L. 94-171 block populations, the Census Bureau's 2022 state
legislative block equivalency files (Hawaiʻi is not in the 2024 release, which covers only states that redrew),
and the 2020 tract-to-PUMA file. It has all 51 House and 25 Senate districts and 1,455,271 people.

`assign_geography` draws each household's district from the same crosswalk, in proportion to population (until
November 2026 it drew from a list of 35 House and 18 Senate districts, each equally likely within a PUMA).
Apportioning, as here, gives district totals that do not ride on one draw per household.

The site shows poverty only by county: by district the MID figures are a few dozen people each, within sampling
error of zero. `by_geography.csv` has every area, all three cases, with standard errors.

## Loss rates

| | Status | LOW | MID | HIGH |
|---|---|---|---|---|
| SNAP | at risk | 50% | 70% | 90% |
| SNAP | meets | 0% | 5% | 10% |
| SNAP | exempt | 0% | 2% | 5% |
| Medicaid | at risk | 60% | 80% | 95% |
| Medicaid | meets or exempt | 2% | 9% | 15% |
| Medicaid | six-month renewals, of those still enrolled | 3% | 5% | 8% |

**SNAP.** Studies of the time limit coming back after a waiver measure the share of non-exempt adults who leave.
Gray, Leive, Prager, Pukelis and Zaki (*AEJ: Economic Policy* 2023) find participation 53% lower 18 months after
reinstatement in Virginia, among adults 18 to 49, with no effect on employment. Ku, Brantley and Pillai (*AJPH*
2019) put it at more than one third; Harris (2021) finds 20% in QC data. Of the 13,071 newly subject adults here
who are not exempt, LOW removes 38%, MID 55% and HIGH 74%, so LOW sits at Ku and MID at Gray. The adults here are
older than the ones those studies observed, and fewer of them work.

**Medicaid.** Arkansas in 2018 is the only case of coverage actually ended: 18,164 people lost it, and coverage
in the target age group fell 13.2 points although more than 95% already met the requirement or should have been
exempt (Sommers et al., *NEJM* 2019). That is the basis for the 9% MID rate on people who meet the test or are
exempt; LOW (2%) assumes the State verifies nearly everyone from data it holds, HIGH (15%) that it does worse
than Arkansas. For people who neither work nor qualify for an exemption, CBO assumes about 5% keep coverage (HIGH
here). MID is 80% because Hawaiʻi asks for one qualifying month in six and because part of this group is
medically frail in ways the survey does not record. CBO splits its national loss about evenly between people who
do not meet the requirement (2.9 million) and people dropped by verification (2.8 million); MID here is 24,600
and 9,800. For six-month renewals CBO has 10% of expansion adults losing coverage at the first one, 70% of them
for procedural reasons, and a net 0.7 million more uninsured; the 3% to 8% used here is the range DHS drew from
CBO for Hawaiʻi (4,000 to 10,000 of 119,000).

For comparison: DHS estimates 19,000 to 38,000 expansion adults (16% to 32%) lose coverage from the work
requirement and six-month renewals together; CBO's national estimate scaled to Hawaiʻi is 33,000 to 34,000 for
the work requirement. MID here is 39,140 (30%): 34,380 from the work requirement and 4,760 from renewals.

## Known weaknesses

1. **The loss rates carry the result.** Halving the MID at-risk rate cuts the SNAP estimate by nearly half.
2. **Medical frailty is undercounted.** The ACS asks six disability questions. The Medicaid exemption also covers
   substance use disorder, disabling mental illness and serious chronic conditions, so part of the at-risk 24% is
   exempt in law. They still have to prove it.
3. **Adults 60 to 64 on SNAP were never screened.** They were exempt from work registration by age, so the file
   records no fitness finding for them. Some of the at-risk 60-to-64-year-olds will be found unfit for work.
4. **The Medicaid undercount may not be random.** The scaling assumes the 35% of enrollees the survey misses look
   like the 65% it finds. Record-linkage studies find the misreporters have higher incomes and more often a
   working household head (Boudreaux et al., *HSR* 2015), which would put more of them in `meets` and lower the
   estimate.
5. **Small samples.** 145 newly subject SNAP adults; the child-14-to-17 and repealed-exception groups (24 and 7)
   should not be quoted alone. FY2023 uses an older time-limit coding and is not pooled.
6. **Composition is 2024.** The SNAP file predates the law; the caseload has shrunk since.
7. **Medicaid dollars use the average expansion adult.** People who lose coverage over paperwork tend to use less
   care than average, so federal dollars lost are likely overstated for a given headcount.
8. **No overlap credit.** A Medicaid adult in a SNAP household that meets SNAP's work rules is exempt; the two
   files cannot be linked.
9. **The ACS SNAP baseline is built, not observed.** Households, not SNAP units; the formula has no shelter or
   dependent-care deduction (the dollar scale absorbs the level, not the shape); non-reporters are drawn at
   random among the eligible. The SPM results depend on it more than the headline counts do.
10. **Districts are PUMA averages.** See "Counties and districts".

## Not modeled

SNAP: the utility-allowance limit for households without an elderly or disabled member, the internet-cost
exclusion, the frozen Thrifty Food Plan, the end of SNAP-Ed, and the removal of refugees and asylees (the file has
too few to count). Medicaid: provider-tax and state-directed-payment limits, cost sharing from October 2028, the
shorter retroactive coverage, and the non-citizen limits (DHS: 1,200 to 2,400 people). The premium tax credit is
out of scope by request. The SPM baseline leaves out the food/excise and care credits (the working-family credits
step adds them; the poverty report path does not), which matters little for a change confined to SNAP.

## Refreshing

The run takes about two minutes. Nothing on the chain refreshes it; when an input changes:

1. Edit the figure in `Hr1Parameters` (`programs/hr1_safety_net.py`) or the table it reads, and the matching
   sentence of this note. What changes, and when:
   - DHS SNAP participation and issuance (`snap_annual_benefits_2027`, `snap_households_2027`,
     `snap_persons_2027`, and the `snap` rows of `admin_caseload/hawaii_caseload.csv`, which the poverty pipeline
     reads): monthly, humanservices.hawaii.gov/bessd/snap (the SFY summary files).
   - Med-QUEST enrollment (`medicaid_expansion_adults`, `medicaid_parent_caretakers`): the enrollment report,
     medquest.hawaii.gov, every few months.
   - The FY2026 payment error rate (`snap_error_rate_fy2025` and the tier the State elects for FY2028): USDA FNS,
     about June 2027. Below 10% it lowers the State's benefit share.
   - SNAP allotments, deductions and poverty tables (`benefits/snap.py`'s `_HI_TABLES`, a new fiscal year each
     August from the USDA cost-of-living memo; `hr1_safety_net.HI_MAX_ALLOTMENT` for the QC restatement).
   - The QC file: snapqcdata.net publishes a fiscal year about two years after it ends. FY2025 (the first with the
     new rules in its last months) should arrive around mid-2027; add it to `scripts/fetch_snap_qc_hawaii.py`
     and check the time-limit codes (`ABWDST`) against its technical documentation before pooling.
   - Medicaid cost per enrollee (`medicaid_cost_per_expansion_adult`): MACPAC MACStats Exhibit 22, yearly.
   - Federal funding by KFF's method (`data/medicaid/kff_allocation_inputs.json`): when CBO re-estimates the law or
     KFF revises its allocation (its Hawaiʻi total sits in `kff_result`).
   - Once months of data under the new rules exist (DHS caseload by age group; Med-QUEST disenrollments for
     noncompliance from July 2027), replace the LOW/MID/HIGH loss rates with observed ones.
2. Commit, then run on the clean tree (the site refuses runs made from uncommitted or older model code):

       .venv/bin/python analyses/forecast_hr1_snap_medicaid.py
       .venv/bin/python scripts/build_site.py --import-runs hr1-snap-medicaid

3. Run the site tests (`pytest tests/site`), commit `site/`, and update this note's results and changelog.

The district crosswalk only needs rebuilding after a redistricting (`scripts/build_puma_district_crosswalk.py`).

## Issues found elsewhere, since fixed

Both were found building this estimate and fixed in the PR after #98:

- `benefits/snap.py` carried tables labeled FY2024 that were not Hawaiʻi's (one person $359 against $527), and
  `poverty_impact_report._apply_snap` ranked tax units by income against a household count, giving $355M a year
  against DHS's $607M. Both now use the placement above, with the year's tables and caseload. The working-family
  credits page's poverty figures moved with it (TY2028, Act 163 sunset: 2,720 more people in poverty became
  4,520, 770 children became 2,050; the poverty gap stayed at $16 million).
- `assign_geography` drew districts from a 35-district crosswalk, evenly; it now uses `population_crosswalk()`.

## Sources

- Statute: Public Law 119-21, sections 10102, 10105, 10106, 71107, 71119.
- SNAP QC FY2024 public-use file and technical documentation, Mathematica for USDA FNS, snapqcdata.net.
- USDA FNS cost-of-living memos FY2025–FY2027 (allotments); payment error rates FY2025 (June 2026).
- Hawaiʻi DHS: SNAP participation summaries SFY2023–SFY2027; FAQ on the work-requirement changes (October 9,
  2025); 2026 legislative budget briefing (January 2026); Med-QUEST enrollment report (November 6, 2026) and work
  requirement one-pager (July 2026); informational testimony to HHS-CPN (December 19, 2025).
- KFF: "Allocating CBO's Estimates of Federal Medicaid Spending Reductions Across the States: Enacted
  Reconciliation Package," July 23, 2025 (first published July 1, 2025 for the Senate bill); Medicaid expansion
  spending, federal and state shares, managed care spending; the state-directed payment and provider-tax analyses.
- CBO: Estimated Budgetary Effects of Public Law 119-21, July 21, 2025 (Title VII Table 7); H.R. 1 as passed by the
  House, June 4, 2025.
- MACPAC: MACStats Exhibit 22, Medicaid benefit spending per full-year equivalent enrollee, FY2023.
- CBO: estimated effects of Public Law 119-21 on SNAP (August 11, 2025) and on Medicaid (October 28, 2025).
- Gray, Leive, Prager, Pukelis and Zaki, "Employed in a SNAP? The Impact of Work Requirements on Program
  Participation and Labor Supply," *AEJ: Economic Policy* 15(1), 2023. Ku, Brantley and Pillai, *AJPH* 109(10),
  2019. Harris, *Economic Inquiry*, 2021 (Upjohn WP 19-297).
- Sommers, Goldman, Blendon, Orav and Epstein, "Medicaid Work Requirements: Results from the First Year in
  Arkansas," *NEJM* 381, 2019; follow-up in *Health Affairs* 39(9), 2020. KFF on Arkansas disenrollment, March 2019.
- Boudreaux et al., "Measurement Error in Public Health Insurance Reporting in the American Community Survey,"
  *Health Services Research* 50(6), 2015.
- USDA FNS time-limit waiver status reports, FY2024 and FY2025 (Hawaiʻi statewide to June 30, 2024; Maui and
  Kauaʻi to June 30, 2025). 7 CFR 273.11(c)(2) and 273.24.
- U.S. Census Bureau: 2020 P.L. 94-171 redistricting file (Hawaiʻi); 2022 state legislative district block
  equivalency files; 2020 census tract to 2020 PUMA relationship file.
