# Test: DHS county/island caseload for SNAP and Medicaid

Run 2026-11-24 on branch `data/dhs-county-caseload`. Exploratory: nothing here changes a published number, default, `runs/` or `site/`. The H.R.1 estimate was not rerun; model figures are read from `runs/hr1_snap_medicaid/` (`by_geography.csv`, `snap_adults.csv`).

## Verdict

**Adopt with caveats.** The SNAP side is a real, official, monthly series by island (county-equivalent) and is good enough to replace ACS-derived county shares and to size the Maui/Kauaʻi waiver pool. The Medicaid side exists only as annual June recipient counts by island, for all recipients (not the expansion-adult group); the Med-QUEST enrollment reports themselves returned 403 to scripts, so the eligibility-group-by-island series could not be tested. Neither source touches the assumed loss rates.

## What exists

### SNAP: DHS BESSD participation reports (pulled and parsed)
- Index: https://humanservices.hawaii.gov/bessd/snap/ (one workbook/PDF per state fiscal year, July to June).
- Files pulled: `.../wp-content/uploads/2026/06/Partic-SFY-2026-WCAG.xlsx`, `.../bessd/files/2026/10/SFY-2027-SUMMARY-1.xls`, `.../wp-content/uploads/2025/07/SFY-2025-SUMMARY.xls` (SFY2024 and earlier PDFs/xls are listed on the page and were not parsed; the SFY2024 PDF was downloaded only).
- Content: for each month, participants, households and benefits issued by Oahu, Hawaii, Kauai, Maui Island, Molokai, Lanai and Maui County total, split by program category (TANF, TAONF, GA, SSI, ABD, SNAP-only). Branches equal counties (Honolulu = Oahu).
- Cadence and lag: monthly; the SFY2027 workbook (posted October 2026) already has July to September 2026, about a 2-month lag, one file per fiscal year. The SFY2026 workbook carries July 2025 to May 2026; its June 2026 sheet is all zeros (never filled), so June 2026 is a gap.
- History: the page links summaries back to SFY2009 (xls to SFY2015 and SFY2009-2014, PDFs SFY2016-2024). Pulled and tabulated here: July 2024 to September 2026 (26 months; 1,071 rows).
- Not available: no age, no ABAWD or work-status split, no adults-without-children count. Persons are all participants, children included.
- Checks: the four branch counts sum to the state total in every month (difference 0); SFY2025 average households 83,041 equals `hawaii_caseload.csv` (83,041).

### Medicaid: DHS Databook (pulled), Med-QUEST reports (blocked)
- DHS Databook FY2025, https://humanservices.hawaii.gov/wp-content/uploads/2026/03/DHS-Databook-2025.pdf (posted March 2026), p. 24: "Number of recipients", MQD enrollment as of June each SFY, by Oahu, Hawaii, Maui, Kauai, Molokai, Lanai, SFY2015 to SFY2025. Annual, about 9 months lag, 11 years. Island sums equal the statewide figure to within 1 (rounding in SFY2016-2019). Databooks for FY2011 to FY2024 are linked at https://humanservices.hawaii.gov/reports/annual-reports/.
- The databook points to https://medquest.hawaii.gov/en/resources/reports.html for "enrollment by program, plan and island". Both that page and two other medquest.hawaii.gov pages returned HTTP 403 ("Your request was blocked") to curl. Route stopped, per the rules. Whether those reports break out the expansion adults (and whether monthly) is therefore **unverified**; I did not guess.
- USDA FNS county/project-area "SNAP Data Tables" file: not pulled. DHS's own island file is finer (monthly, current), so FNS adds nothing for this test.

Extracts committed: `dhs-county-caseload/snap_island_monthly.csv` (month, area, persons/households/issuance_usd), `dhs-county-caseload/medquest_recipients_by_island.csv`, and `dhs-county-caseload/extract_snap_island_monthly.py` (run with the raw files in `raw/`).

## Test (a): county shares vs the model

Model figures: MID scenario, `runs/hr1_snap_medicaid/by_geography.csv` (SNAP adults losing 7,251; Medicaid expansion adults 129,577; Medicaid losing coverage 39,139). ACS: PUMS 2024 1-year, `packages/data/raw/pums_2024_1yr`, household SNAP (`FS`) and Medicaid (`HINS4`), run by PUMA. ACS cannot separate Maui from Kauaʻi (PUMA 100 covers both), so the model's split of them is an apportionment.

### SNAP participants
| | Honolulu | Hawaiʻi | Maui County | Kauaʻi | Maui+Kauaʻi |
|---|---|---|---|---|---|
| DHS persons, SFY2025 avg | 58.6% | 25.5% | 10.8% | 5.1% | 15.9% |
| DHS persons, May 2026 | 59.7% | 24.5% | 10.8% | 5.1% | 15.9% |
| DHS households, SFY2025 avg | 58.3% | 25.9% | 10.7% | 5.1% | 15.9% |
| ACS 2024 SNAP households | 60.9% | 22.5% | | | 16.6% |
| ACS 2024 SNAP persons | 65.0% | 20.6% | | | 14.5% |
| ACS 2020-24 SNAP persons | 63.3% | 21.3% | | | 15.5% |
| Model SNAP adults losing (MID) | 62.2% | 21.7% | 11.1% | 5.0% | 16.1% |

The model puts about 2.5 points too much on Honolulu and 2.8 too little on Hawaiʻi County relative to administrative counts; Maui and Kauaʻi are close (the model's Kauaʻi share of the Maui+Kauaʻi pair, 30.8%, against DHS 32.1%). The model's loser shares follow ACS SNAP-household geography, and ACS shows the same Honolulu tilt. ACS also reports 205,940 SNAP persons against about 158,000 in DHS records, so its level overstates too. Caveat: the model's loser shares are shares of a subgroup (newly subject adults), DHS shares are of all participants; the comparison assumes the subgroup is spread like the caseload.

### Medicaid
| | Honolulu | Hawaiʻi | Maui County | Kauaʻi | Maui+Kauaʻi |
|---|---|---|---|---|---|
| Med-QUEST recipients, June 2025 | 60.3% | 21.1% | 13.0% | 5.7% | 18.6% |
| Med-QUEST recipients, June 2024 | 60.0% | 20.6% | 13.7% | 5.8% | 19.4% |
| ACS 2024 HINS4, all ages | 64.3% | 20.1% | | | 15.6% |
| ACS 2024 HINS4, ages 19-64 | 65.2% | 19.3% | | | 15.5% |
| Model expansion adults (MID) | 64.7% | 18.7% | 11.5% | 5.1% | 16.6% |

Administrative Medicaid is about 4 points less Honolulu and 2 to 3 points more Maui+Kauaʻi than the ACS-based model. The DHS series is all recipients, not expansion adults, so this is a proxy: the gap between ACS and administrative counts is evidence the ACS geography is skewed, not a measured expansion-adult share.

### Effect on the county outputs (arithmetic on the existing MID outputs; totals unchanged)
| MID | Honolulu | Hawaiʻi | Maui | Kauaʻi |
|---|---|---|---|---|
| SNAP adults losing: model | 4,511 | 1,573 | 807 | 359 |
| at DHS May 2026 shares | 4,327 | 1,773 | 780 | 371 |
| Medicaid losing: model | 24,455 | 7,757 | 4,795 | 2,132 |
| at Med-QUEST June 2025 shares | 23,601 | 8,247 | 5,072 | 2,219 |

So county counts would move by about 200 (SNAP) and 500 to 850 (Medicaid) people per county, a few percent of each, and the statewide totals do not change. Poverty-rate effects are computed on SPM units and would move proportionally less. Not run: this is a rescaling of the outputs, not a re-estimate.

### Out-of-sample signal on the SNAP loss rates (not a calibration)
DHS persons fell from October 2025 to May 2026 by 6.9% statewide (169,604 to 157,954), against a 3.4% rise over the same months a year earlier. By island: Honolulu -6.4%, Hawaiʻi -7.3%, Kauaʻi -5.6%, Maui County -9.0%. The 11,650-person statewide drop covers children and adults, and October 2025 is an elevated starting point, so it does not isolate the time-limit effect and cannot be compared with the model's 7,251 adult losers. It shows that a monthly county series could be used to check the loss-rate assumption once the time-limit effect is separable (adult counts by county would be needed; DHS does not publish them).

## Test (b): the open Maui/Kauaʻi waiver question

The methodology note (`HR1_SNAP_MEDICAID_METHODOLOGY.md`, "Not counted") leaves out adults 18 to 54 with no child who are neither exempt nor working 20 hours, because the QC file has no county and Maui and Kauaʻi kept a waiver to June 30, 2025. `snap_adults.csv` has 6,909 such persons statewide (group `already_subject`, `at_risk`).

County shares size the waiver pool. Maui County plus Kauaʻi hold 15.9% of SNAP participants (DHS; 14.5% to 16.6% by ACS), so about **1,100** of the 6,909 (range about 1,000 to 1,150) were in the waiver area, and about 5,800 were not. Assumes the ABAWD at-risk group is spread like the caseload; DHS has no adult or ABAWD split by island to test that.

What that settles:
- The waiver pool is about 16% of the group. Only that slice, not the whole 6,909, could be "low by whatever a renewed waiver would have protected".
- Because the waiver ended June 30, 2025, before H.R.1 was signed, these ~1,100 became subject to the time limit under the expiry, not under the new law. The expiry-versus-H.R.1 attribution matches the other ~5,800, which have been subject since June 2024. The note's phrase "the estimate is low" holds only if a waiver is renewed; it can now be stated as "of about 6,900 already-subject at-risk adults, about 1,100 live in the former Maui/Kauaʻi waiver area".
- Adding the pool to the 7,251 MID count would raise it by about 15% (to about 8,350), but it would double-count the baseline rather than measure H.R.1, so it is a sensitivity, not a correction.

What it does not settle: whether Maui or Kauaʻi would qualify under the law's 1.5x-national-unemployment test (needs BLS county unemployment, not tested here), and the assumed SNAP/Medicaid loss rates (no source here measures them).

## Limits
- SNAP series is persons and households, all ages; no ABAWD, no adult-only counts; island, not district.
- Medicaid island series is annual, all recipients, 9-month lag; the expansion-adult group by island is unobtained (Med-QUEST 403).
- The comparison with model shares assumes the subgroups follow the whole caseload.
- Taken from documents rather than a run: the waiver dates and the 6,909 / 7,251 / 39,139 figures (read from `runs/` and the methodology note); the Med-QUEST databook table (parsed from the PDF text); everything else was computed in this session (parsing, share tables, ACS PUMA tabulations, reallocation arithmetic).
