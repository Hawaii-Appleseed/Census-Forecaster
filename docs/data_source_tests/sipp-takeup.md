# Data source test: SIPP public-use file for take-up propensity and H.R.1 loss rates

Run 2026-11-24 on branch `data/sipp-takeup`. Exploratory. No published number, default, site page or run was
changed. Scripts: `analyses/sipp_extract.py` (streams the zip, keeps 42 of 5,200 columns),
`analyses/sipp_takeup_test.py` (every table below). Full printed output: `docs/data_source_tests/sipp-takeup_output.txt`.

## Verdict

**Reject for the H.R.1 loss rates. Adopt with caveats as a national source of the shape of SNAP take-up. Do not use for
Hawaii-specific estimates, EITC take-up, or Medicaid churn.**

* The loss-rate gap (2): SIPP measures voluntary and administrative exit in a year when the work rule was suspended.
  It cannot say what share of at-risk adults lose SNAP under the rule. Its Hawaii sample has zero rows in the target
  group. The note's rates stay literature-based.
* The take-up gap (1): a national SNAP participation logit on covariates the module accepts fits and holds out well
  (below), and `fit_from_survey` runs on it unchanged. But the module is applied to credits claimed by tax units, and
  the SNAP logit describes SNAP, not the EITC or the working-family credits the sensitivity script scores. Using it for
  those credits is an assumption, not an estimate. SIPP has no usable question on state credits at all.

## Source

* Census Survey of Income and Program Participation, 2023 public-use release: file `pu2023_csv.zip` (104 MB zipped,
  3.73 GB unzipped), `pu2023_schema.json`, from the Census data directory
  `www2.census.gov/programs-surveys/sipp/data/datasets/2023/` (file dated 2024-07-10). Directory listings for 2023,
  2024 and 2025 were read to see what exists; only this one file was downloaded. The server answered 200 to curl,
  with no 403 or 429.
* The file holds four overlapping panels (2020, 2021, 2022, 2023) in the same reference year, calendar 2022: 40,263
  persons, 476,744 person-months (12 per person), 17,314 sample households. `WPFINWGT` adds to 327.7 million in month 1
  across panels, so the weights are already pooled (checked; no rescaling).
* The 2024 and 2025 directories list only longitudinal and replicate-weight files (`lgtrw`, `lgtwgt`), not a new
  person-month file. The 2023 file is therefore the latest cross-section found by listing. Not verified further.
* Downloaded raw zip deleted after extraction. Extract kept at `data/artifacts/sipp_raw/sipp.parquet` (5 MB,
  git-ignored, rebuildable from the script).
* Not obtained: the SIPP replicate weights (separate file, `rw2023`), the code-value dictionary (the schema JSON has
  labels but no value labels; codes below follow Census convention and were checked against counts, not a dictionary),
  and any other panel year.

## Does it identify Hawaii

State is `TEHC_ST` (monthly state) and `TST_INTV` as FIPS codes; 53 state codes appear (the 50 states, the District and one more, 61), and Hawaii (15) is
identified. The sample is tiny:

| Hawaii | count |
|---|---|
| persons | 181 (85 households), from 4 panels |
| person-months | 2,058 (1,782 adult) |
| weighted persons, month 6 | 1.38 million (Hawaii has about 1.4 million, so the weights are sane) |
| adults ever on SNAP in 2022 | 8 (81 person-months) |
| adults ever on Medicaid | 13 (156 person-months) |
| income-eligible adults, month 6 (SNAP test) | 18, of whom 4 on SNAP |
| working adults answering the EITC question | 75, of whom 5 report an EITC |
| SNAP exit observations, no-child adults 18-64 | 0 |

No Hawaii-specific coefficient or rate can be estimated.

## What SIPP supplies

Variables used (all in the 2023 schema): SNAP monthly receipt `RSNAP_MNYN` and amount `TSNAP_AMT`, SNAP unit size
`ESNAP_CNT`, reason SNAP ended `ESNAP_ERSN` (codes not labeled in the schema); Medicaid monthly `RPUBTYPE2` and `EMDMTH`
(identical but for 5,189 rows); monthly earnings `TPEARN`, household income `THTOTINC`, household income-to-poverty
`THINCPOV`; hours worked per week averaged over the month `TMWKHRS`; employment status `RMESR`; age, sex, education,
race, origin, marital status, citizenship, disability, tenure, household size, children under 18 and adults 65+ in the
household (`RHNUMU18`, `RHNUM65OVER`), parent and spouse pointers.

**EITC and tax credits.** Two annual items, asked of each person for the tax year before the reference period:
`EFILING` (filed a federal return) and `EEITC` (received an EITC on it), plus `EFSTATUS` and `EDEPCLM`. Nothing on any
state credit, the CTC, or the amount. 22% of adults 18-64 have `EEITC` missing. Tabulated, working adults 18-64
(hours > 0), share who filed and who report an EITC:

| | income-to-poverty | filed | report EITC |
|---|---|---|---|
| child | under 100% | 0.78 | 0.39 |
| child | 100-199% | 0.86 | 0.40 |
| child | 200-299% | 0.87 | 0.23 |
| no child | under 100% | 0.71 | 0.13 |
| no child | 100-199% | 0.81 | 0.14 |

Weighted persons reporting an EITC: 18.9 million (persons, not returns; joint filers count twice). SIPP records
claims, not eligibility: there is no tax unit and no way to tell a claimant from an eligible non-claimant without
building the credit, which is the step the ACS-based model already does. It supports a "who claims" shape only if the
eligibility is rebuilt on SIPP, which this test did not do.

## What the propensity module accepts

Read from `packages/tax_modeler/src/tax_modeler/calibration/takeup_propensity.py`:

* `Term(name, column, kind, lo, hi, values, center, scale)` where `column` must be a column of the frame, kinds
  `band`, `isin`, `linear`.
* `fit_from_survey(survey, outcome=<0/1 column>, terms, weight, ridge)` returns a `PropensityModel` (intercept,
  coefficients, sandwich standard errors that ignore the weights' own fitting). `pseudo_weights` rakes a survey to the
  tax units and `known_answer_test` compares a survey's predicted SNAP, tenure or coverage with the PUMS record.
* The tax-unit cache carries `income`, `num_dependents`, `filing_status`, `primary_agep`, `secondary_agep`, wage and
  other income components, `PUMA`, weights; the sensitivity script's terms use `federal_agi`. Tenure, disability,
  citizenship and education are not unit columns: using them means merging person fields from PUMS by `SERIALNO`.
  No code was changed for this test; I fitted both specs with the module as it is.

## Test 1: SNAP participation among income-eligible adults

Frame: adults 18+ in month 6 whose household income is at or below 130% of poverty (the gross-income test only; no
asset, immigration or student tests, which SIPP's `THINCPOV` cannot apply). n = 4,759 (35.6 million weighted).
Weighted participation 33.1%. Because the frame includes people SNAP excludes, the level is not a take-up rate;
`claim_probabilities` resets the level to an administrative total anyway, so only the shape matters.

Spec A, full set (`fit_from_survey`, ridge 1.0; log-odds, sandwich SE):

| term | coef | SE |
|---|---|---|
| income under 50% of poverty (ref 100-130%) | 0.39 | 0.11 |
| income 50-99% | 0.60 | 0.10 |
| child under 18 in household | 0.74 | 0.11 |
| age 18-24 | -0.48 | 0.16 |
| age 50-64 | 0.13 | 0.12 |
| age 65+ | 0.08 | 0.12 |
| renter | 1.07 | 0.09 |
| disabled | 1.17 | 0.09 |
| noncitizen | -1.41 | 0.19 |
| less than high school | 0.37 | 0.10 |
| college degree | -0.97 | 0.13 |
| works (hours > 0) | -0.45 | 0.11 |
| Hispanic | 0.10 | 0.11 |

Spec B, only what tax units carry (income band, any dependent, age bands): income under 50% 0.24 (0.10), 50-99% 0.63
(0.09), any dependent 0.37 (0.10), age 18-24 -0.38 (0.15), 50-64 0.36 (0.11), 65+ 0.33 (0.11).

Checks:

* Holdout: fit on panels 2020-21 (n = 1,532), predict 2022-23 (n = 3,227): predicted 32.5% against actual 32.9%.
  By fifth of predicted probability, predicted against actual: 9.1/8.7, 17.6/17.0, 29.3/29.8, 42.6/45.0, 65.9/66.0.
  The panels are the same calendar year and share the design, so this is a weak test of stability, not of
  transport over time or space.
* Hawaii: national coefficients predict 25.2% (spec A) and 27.3% (spec B) against 28.2% observed in 18 Hawaii
  adults (4 on SNAP). A sample of 18 cannot confirm or reject either.
* Known-answer test: not run. It needs the PUMS side of the comparison for a column both frames hold; the SIPP
  columns line up for SNAP receipt, tenure and health coverage, but the SNAP outcome is what the logit fits, so a
  known-answer run would use tenure or coverage and was not attempted.

Caveats: coefficients are national, 2022, and fitted on a person-month's household income, not annual tax-unit
income; SIPP under-reports SNAP receipt (the module's own known-answer rationale; the amount of
under-reporting here was not measured); the sandwich SEs ignore the SIPP's design effects (no replicate weights
pulled); several terms (noncitizen, disabled, renter) are not tax-unit columns and would need a merge. The module's
tax-unit shape also has to be mapped from person-level SIPP covariates, which spec B does only roughly.

## Test 2: SNAP exits for adults without children, by hours

Group: age 18-64, no child under 18 in the household (the law exempts only a child under 14, so this is wider
than the rule's exempt set), not disabled (`EDISABL` != 1), on SNAP in month t. Hours: monthly average per week at
all jobs, with 18.5 as the weekly equivalent of 80 hours a month. Interval: household-cluster bootstrap, 300 draws.
Weighted share no longer on SNAP:

| hours at t | rows | persons | 1 month later | 3 months later | 6 months later |
|---|---|---|---|---|---|
| all | 4,132 | 491 | 2.2% (1.6-2.9) | 6.2% (4.6-8.1) | 11.5% (8.1-15.2) |
| no work | 2,209 | 282 | 1.3% | 3.9% | 8.5% |
| 0 to 18.5 h/wk | 257 | 65 | 0.6% (0.0-2.2) | 3.4% (0.0-8.6) | 10.9% (0.3-27.2) |
| 18.5 h/wk or more | 1,666 | 240 | 3.5% | 9.0% | 15.0% |

Off SNAP at both t+1 and t+3: 1.4% (no work), 0.9% (low), 2.9% (working). On SNAP in month 1 and off in month 12:
14.2% (no work, n = 198), 10.1% (low hours, n = 15), 23.8% (working, n = 144); 18.2% overall (n = 357).

Comparison with `HR1_SNAP_MEDICAID_METHODOLOGY.md` loss rates (at risk 50% / 70% / 90%; meets 0 / 5 / 10%):

* The people who could be at risk (no or low work) left SNAP at 3.9% after three months and 14% after eleven, in 2022.
  The rule's assumed loss is 50 to 90%. The two are not the same thing: 2022 is the case with no work rule enforced
  (the national work-requirement waiver for the public health emergency ran through mid-2023; recalled, not checked
  in this run), so the figures are the baseline exit rate without a rule. They say the pre-rule churn that the rule
  would be added to is small, a few percent. They do not test whether the rule removes 55% or 70%.
* Working adults leave faster (9% at 3 months, 24% at 11) than non-working ones, as people whose earnings rise
  leave. The note's 5% (MID) for people who "meet" the test is not contradicted by 9-24% voluntary exit; but those
  leavers are people who got off for income reasons, not people dropped despite meeting it.
* Low-hours cell has 15-65 persons and an interval from 0 to 27%: no use even as a baseline.
* Seam and recall: this file has one annual interview for the 12 months, so month-to-month changes come from recall.
  SIPP's own history is that recall smooths transitions and that receipt of SNAP is under-reported. Within the year
  only 2.6% of persons change SNAP status at all. These exit rates are likely lower bounds. Taken from SIPP
  literature, not measured here.

## Test 3: Medicaid

The monthly Medicaid variable barely moves within the year: 1.0% of persons change status across the 12 months
(22.3% ever covered), month-to-month off-rate 0.0-0.1% and six-month 0.3% for adults 19-64 under 138% of poverty
(9,127 rows, 1,797 persons; no child in household and working: 0.0%, 638 rows). Real Medicaid churn is not this
low, so this series is unfit for the note's 5% to 8% six-month renewal rates, the 9% MID rate for those who meet the
requirement, or anything about expansion-adult retention. Could not be used.

## Do the gaps get filled

| gap | what SIPP gives | verdict |
|---|---|---|
| Who claims (module `fit_from_survey`) | national SNAP participation shape, passes a same-year holdout, SEs small; ready to run on module as written | national coefficients, applies to SNAP, not to the credits the module scores; no Hawaii-specific; unit columns limit it to income, dependents, age unless PUMS fields are merged |
| EITC "who claims" | claim share by income and children; no unit, no state credits | cannot feed the module without rebuilding eligibility; item nonresponse 22% |
| SNAP loss rates (note's LOW/MID/HIGH) | baseline exit, 3-14% for non-working, no work rule in force | reject: not a loss rate |
| Medicaid loss rates | none usable | reject |
| Hawaii | 181 persons, 8 SNAP adults, 0 relevant exit observations | none |

## Limits to repeat

* National, one reference year (2022), annual recall design.
* Hawaii sample too small for any Hawaii-only figure.
* Under-reporting of SNAP, and the standard errors here ignore the SIPP's replicate weights.
* A voluntary or administrative exit rate in a year without a rule is not a loss rate under a rule.
* Income eligibility used only the gross-income line from monthly household income.

## Reproduce

```
.venv/bin/python -I analyses/sipp_extract.py <pu2023_csv.zip> data/artifacts/sipp_raw/sipp.parquet
.venv/bin/python -I analyses/sipp_takeup_test.py data/artifacts/sipp_raw/sipp.parquet
```
