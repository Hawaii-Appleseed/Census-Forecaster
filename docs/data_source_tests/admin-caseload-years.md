# Admin caseload years test

Branch `data/admin-caseload-years`. Question: does year-matching the non-SNAP admin caseload targets
(`data/admin_caseload/hawaii_caseload.csv`) matter for the SPM poverty runs (TY2022-2028)?

## (a) Where targets are read, and what a missing year does

* `tax_modeler/calibration/admin_caseload.py`: `AdminCaseload.target(program, year)` raises `ConfigError` on a
  missing (program, year); `latest(program, year)` falls back to the latest earlier year, else the earliest.
* `scripts/poverty_impact_report.py`: SNAP uses `latest("snap", tax_year)` (so SNAP is year-matched).
  Housing, childcare_subsidy, WIC and LIHEAP call `target(prog, tax_year)`, catch the error, log a warning and
  use the **2022** row. The file has only a 2022 row for each, so every tax year 2022-2028 uses the 2022 anchor.
* `calibration/takeup_imputation.py` (~line 424) calls `caseload.target(program, year)` for eitc/ctc/actc/hi_eitc
  with no fallback.
* SSI and school lunch: no `target()` call found in the poverty report; those rows are documentation only.

## (b) Extended CSV

`packages/tax_modeler/src/tax_modeler/data/admin_caseload/hawaii_caseload_extended.csv` (same schema; the bundled
file is untouched). It is the 19 bundled rows (bundled WIC 2022 replaced) plus 11 new rows, produced by
`docs/data_source_tests/admin_caseload_years_build.py` from official USDA-FNS workbooks (www.fns.usda.gov,
`/sites/default/files/resource-files/`).

Verified in this session (re-downloaded the four workbooks, rebuilt the CSV with the script; output identical to the
committed CSV; Hawaii rows also read directly):

* WIC participation `26wifypart-11.xlsx` and food cost `24wicfood-costs-11.xlsx` (data as of 2026-11-13),
  FY2021-FY2025.
* NSLP average daily participation `01slfypart-11.xlsx` and federal cash `06slcash-11.xlsx`, FY2021-FY2025.
  Program name is `school_lunch_total` (all lunches), not `school_lunch_free`: the free-only split was not pulled,
  so the existing 55,000 free-meal row is not comparable and is left alone.

Taken from the earlier (interrupted) run, not re-fetched: WIC FY2026 from the agency monthly file
`wicagencies2026ytd.xlsx` (Oct 2025 to Jul 2026, annualised x12/10).

WIC dollars are gross food cost before infant-formula rebates (net would be lower).

## (c) Movement against the single bundled year

| program | bundled (2022) | year by year (verified) | move |
|---|---|---|---|
| WIC | 31,000 persons, $22.3M | 25.9k, 25.9k, 26.1k, 25.1k, 25.1k, 25.1k (FY21-26); $12.9M, 17.9, 21.3, 21.9, 22.5, 23.0 | count 16-19% below bundled in every year; dollars -20% (FY22) to +3% (FY26) |
| NSLP | 55,000 free, $42M | ADP 47.8k, 93.1k, 90.3k, 92.0k, 88.9k; federal cash $39.3M, 77.0, 53.3, 59.3, 57.9 (FY21-25) | COVID free-for-all inflates FY22; FY23-25 steady; different definition from bundled |

The bundled WIC row ($22.3M tagged FY2022) does not match FNS FY2022 ($17.9M, 25,854); its dollars match
FY2025-26. For TY2024+ the bundled WIC dollars are about right, while the count is about 20% too high (a wider
take-up draw, not more dollars).

Not obtained:
* SSA SSI by state (`ssa.gov/policy/docs/statcomps/ssi_sc/<year>/hi.html`): HTTP 403 to scripts, every year
  2019-2025. Stopped.
* HUD Picture of Subsidized Households (huduser.gov) and ACF pages for CCDF and LIHEAP (acf.gov): HTTP 202 with an
  empty body (bot challenge) to curl; `liheappm.acf.hhs.gov/data` 404. No script route found; stopped, no browser.

So housing, childcare_subsidy, liheap and ssi have no new years. Their bundled rows were not independently
confirmed (housing, childcare and LIHEAP cite approximations such as "~9,400 HCV + ~6,200 public-housing households"
with no table; SSI 2023-24 could not be checked). They are carried over unchanged.

## (d) Could year-matching move the SPM poverty rate?

Scale reference: the SNAP correction ($355M to $607M, +$252M, METHODOLOGY.md near line 1551). Hawaii SPM poverty is
about 10-12% of about 1.4M people (roughly 150k persons, perhaps 50-60k units). Rough conversion taken from the SNAP
case (the census-gap note put SNAP under-reporting at about 3 points): about 1 percentage point of SPM rate per
$80-100M of benefits reaching units near the line. A rule of thumb, not a model run; I did not run the model.

| program | plausible dollar shift from year-matching | rate effect |
|---|---|---|
| WIC | at most about $5M | under 0.05 pt |
| School lunch | tens of $M only across COVID-waiver years; FY23-25 flat | under 0.1-0.2 pt, only for COVID-affected years |
| Housing ($140M bundled) | unknown; 10-20% would be $14-28M | about 0.1-0.3 pt, the largest of the group |
| Childcare ($50M) | unknown; 20% is $10M | under 0.1 pt |
| SSI ($225-232M) | about +3% a year, about $7M | under 0.1 pt |
| LIHEAP ($2M) | negligible | none |

## (e) Verdict: adopt with caveats (WIC and NSLP only)

* Value is small: no non-SNAP program plausibly moves the SPM rate by more than about 0.3 pt, and the one
  candidate of that size (housing) could not be sourced.
* Cheap and safe: WIC (the bundled count is about 20% too high, a verified correction) and NSLP FY23-25.
  Using them needs a code change (`latest()` instead of `target()` plus the 2022 fallback) and decisions on gross
  versus net WIC food cost and on `school_lunch_total` versus free-only. No numbers or defaults were changed here.
* Next, in order: HUD housing and SSI (both need a manual download or browser). Childcare and LIHEAP are not worth it.
* Limits: the effect column is a hand estimate; WIC FY2026 is ten months annualised; NSLP FY2025 is preliminary;
  no program has rows for TY2027-28 (`latest()` would carry the last year).
