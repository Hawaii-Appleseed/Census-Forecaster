# H.R.1 in Hawaiʻi: SNAP and Medicaid losses — methodology

**Status (November 22, 2026): first estimate, not published.** Script: `analyses/forecast_hr1_snap_medicaid.py`.
Logic: `tax_modeler.programs.hr1_safety_net`. Outputs: `reports/hr1_snap_medicaid/`. Nothing here is on the
published chain or the site.

The question: in a full year under the 2025 reconciliation law (H.R.1, Public Law 119-21), how many people in
Hawaiʻi lose SNAP or Medicaid because of the new work rules, how many federal dollars go with them, and what does
the State pay, either because the law bills it or because it chooses to replace what was lost.

## Results (generated November 22, 2026)

| | LOW | MID | HIGH |
|---|---|---|---|
| SNAP: adults losing eligibility | 4,950 | 7,250 | 9,630 |
| SNAP: benefits lost a year, FY2027 allotments | $22.9M | $33.3M | $44.0M |
| Medicaid: expansion adults losing coverage | 24,500 | 39,300 | 51,900 |
| Medicaid: federal dollars lost a year | $107M | $172M | $227M |

- SNAP, 90% sampling interval on the MID case: 5,600 to 9,000 adults; $25.5M to $41.1M.
- SNAP costs moved to the State from federal fiscal year 2028: $91.1M a year for benefits at the 15% rate
  Hawaiʻi's FY2025 error rate implies ($0, $30.4M or $60.7M at the lower tiers), plus $14.8M for administration.
- Medicaid: of 129,577 expansion adults, 42% already meet the test, 33% are exempt on what the survey shows, and
  24% (31,400) are neither.
- Replacing the lost Medicaid coverage with state funds would cost $191M a year in the MID case; the State's own
  share of that coverage today is $19M.

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

## SNAP

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

DHS counted 16,570 members aged 55 to 64 in April 2025.

Each is `exempt` (a disability, or a work-registration exemption that carries to the time limit: unfit for work,
caring for a child under 6 or an incapacitated person, student, TANF work compliance, unemployment compensation,
treatment), `meets` (20 or more hours a week, the 30-hour registration exemption, or a qualifying work program) or
`at_risk`. Of the 20,916: 7,845 exempt, 3,163 meet the test, 9,908 at risk.

**Not counted.** Adults 18 to 54 with no child were already under the time limit when Hawaiʻi's waiver ended on
June 30, 2024, before H.R.1. The file shows 6,900 of them neither exempt nor working 20 hours. Maui and Kauaʻi
counties kept a waiver until June 30, 2025, four days before the law was signed, and the law's new test
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

## Medicaid

**Data.** ACS PUMS persons aged 19 to 64 who report Medicaid (`HINS4`). The 2024 one-year file is the main run
(1,118 non-disabled adults in sample); the 2020–24 five-year file is a second run and agrees within 1%.

**Scaling** (`split_expansion`). The survey finds 107,600 non-disabled Medicaid adults; Med-QUEST enrolled
179,583 in September 2026 (129,577 expansion, 50,006 parents and caretakers). Every weight is scaled by one
factor (1.67) to the pool, then parents are assigned to the parent and caretaker group from the lowest family
income up until its count is met. The rest are expansion adults.

**Status** (`classify_medicaid_adults`). `exempt`: a child 13 or under of their own, an ACS disability, a birth
in the last year, American Indian or Alaska Native. `meets`: employed at the interview, enrolled in school, or
worked in the last year at $580 a month or more, with the probability that a six-month window catches a spell of
that length. `at_risk`: neither. Of the at-risk group, 88% are out of the labor force and 31% are 55 to 64.

**Dollars.** $4,853 per expansion adult per year (KFF from T-MSIS, 2024), 90% federal. Not indexed to 2027.

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
do not meet the requirement (2.9 million) and people dropped by verification (2.8 million); MID here is 25,100
and 9,400. For six-month renewals CBO has 10% of expansion adults losing coverage at the first one, 70% of them
for procedural reasons, and a net 0.7 million more uninsured; the 3% to 8% used here is the range DHS drew from
CBO for Hawaiʻi (4,000 to 10,000 of 119,000).

For comparison: DHS estimates 19,000 to 38,000 expansion adults (16% to 32%) lose coverage from the work
requirement and six-month renewals together; CBO's national estimate scaled to Hawaiʻi is 33,000 to 34,000 for
the work requirement. MID here is 39,300 (30%): 34,500 from the work requirement and 4,800 from renewals.

## Known weaknesses

1. **The loss rates carry the result.** Halving the MID at-risk rate cuts the SNAP estimate by nearly half.
2. **Medical frailty is undercounted.** The ACS asks six disability questions. The Medicaid exemption also covers
   substance use disorder, disabling mental illness and serious chronic conditions, so part of the at-risk 24% is
   exempt in law. They still have to prove it.
3. **Adults 60 to 64 on SNAP were never screened.** They were exempt from work registration by age, so the file
   records no fitness finding for them. Some of the at-risk 60-to-64-year-olds will be found unfit for work.
4. **The Medicaid undercount may not be random.** The scaling assumes the 40% of enrollees the survey misses look
   like the 60% it finds.
5. **Small samples.** 145 newly subject SNAP adults; the child-14-to-17 and repealed-exception groups (24 and 7)
   should not be quoted alone. FY2023 uses an older time-limit coding and is not pooled.
6. **Composition is 2024.** The SNAP file predates the law; the caseload has shrunk since.
7. **Medicaid dollars use the average expansion adult.** People who lose coverage over paperwork tend to use less
   care than average, so federal dollars lost are likely overstated for a given headcount.
8. **No overlap credit.** A Medicaid adult in a SNAP household that meets SNAP's work rules is exempt; the two
   files cannot be linked.

## Not modeled

SNAP: the utility-allowance limit for households without an elderly or disabled member, the internet-cost
exclusion, the frozen Thrifty Food Plan, the end of SNAP-Ed, and the removal of refugees and asylees (the file has
too few to count). Medicaid: provider-tax and state-directed-payment limits, cost sharing from October 2028, the
shorter retroactive coverage, and the non-citizen limits (DHS: 1,200 to 2,400 people). The premium tax credit is
out of scope by request. SPM poverty is not estimated: `snap_income_depth.csv` shows instead how far below the
poverty line the affected SNAP units already are (MID: 52% have countable income under half the line, 32% have
none).

## Sources

- Statute: Public Law 119-21, sections 10102, 10105, 10106, 71107, 71119.
- SNAP QC FY2024 public-use file and technical documentation, Mathematica for USDA FNS, snapqcdata.net.
- USDA FNS cost-of-living memos FY2025–FY2027 (allotments); payment error rates FY2025 (June 2026).
- Hawaiʻi DHS: SNAP participation summaries SFY2023–SFY2027; FAQ on the work-requirement changes (October 9,
  2025); 2026 legislative budget briefing (January 2026); Med-QUEST enrollment report (November 6, 2026);
  informational testimony to HHS-CPN (December 19, 2025).
- KFF: Medicaid spending per full-benefit enrollee, 2024; Medicaid expansion spending, FY2024.
- CBO: estimated effects of Public Law 119-21 on SNAP (August 11, 2025) and on Medicaid (October 28, 2025).
- Gray, Leive, Prager, Pukelis and Zaki, "Employed in a SNAP? The Impact of Work Requirements on Program
  Participation and Labor Supply," *AEJ: Economic Policy* 15(1), 2023. Ku, Brantley and Pillai, *AJPH* 109(10),
  2019. Harris, *Economic Inquiry*, 2021 (Upjohn WP 19-297).
- Sommers, Goldman, Blendon, Orav and Epstein, "Medicaid Work Requirements: Results from the First Year in
  Arkansas," *NEJM* 381, 2019; follow-up in *Health Affairs* 39(9), 2020. KFF on Arkansas disenrollment, March 2019.
- USDA FNS time-limit waiver status reports, FY2024 and FY2025 (Hawaiʻi statewide to June 30, 2024; Maui and
  Kauaʻi to June 30, 2025). 7 CFR 273.11(c)(2) and 273.24.
