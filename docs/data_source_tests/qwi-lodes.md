# Data-source test: Census LEHD QWI and LODES (Hawaii)

Run 2026-11-24, exploratory. No published number, default or crosswalk was changed. Scripts: `scripts/data_source_tests/qwi_vs_qcew_anchor.py` (Part A) and `scripts/data_source_tests/lodes_rac_districts.py` (Part B). Raw downloads sit in gitignored `data/qwi-lodes/` (about 70 MB; the PL/BEF cache there is the same one `scripts/build_puma_district_crosswalk.py` uses).

## What was pulled

| Item | URL (all curl, no auth, HTTP 200) | Vintage |
|---|---|---|
| QWI Hawaii, sex x age file, county geography, all-industry rows | `https://lehd.ces.census.gov/data/qwi/latest_release/hi/qwi_hi_sa_f_gc_ns_op_u.csv.gz` (17.9 MB) | directory stamped 2026-11-23; data 1995Q4 to 2026Q1 |
| LODES8 Hawaii RAC, all jobs (JT00), segment S000, 2022 | `https://lehd.ces.census.gov/data/lodes/LODES8/hi/rac/hi_rac_S000_JT00_2022.csv.gz` (0.29 MB) | LODES8, year 2022 (2023 also exists; not used, to match the 2022 district plan) |
| Block population, tract-to-PUMA, 2022 state legislative block assignment files | URLs already in `scripts/build_puma_district_crosswalk.py` | 2020 PL 94-171; 2020 tracts/PUMAs; 2022 plan |

The QWI file is owner code A05 (private employment only; no state/local/federal government), unadjusted, quarterly. Only the all-industry, all-sex, all-age rows are used (`industry == "00"`, `sex == "0"`, `agegrp == "A00"`). The existing script as found did not filter `industry`, so industry rows overwrote one another in its dictionary; fixed before any number below was produced. I did not use the API (the bulk CSV carries the same data).

## Part A: QWI as an anchor for ACS county growth

**Method.** QWI annual earnings = 12 x mean of the four quarterly `EarnS` (monthly earnings of stable employees), county and state. ACS targets: B19013 (median household income) and B20002 (median worker earnings) for Hawaii, Honolulu, Maui, Kauai counties, 1-year, from `acs_panel.json`. Log growth, year t over t-1. Walk-forward: for each test year t from 2014, fit `acs_growth = a + b * anchor_growth` on pooled county-years before t, predict t; compare RMSE to the baseline "mean of prior ACS growth". Anchors compared: QWI county, QWI statewide, QCEW statewide (`qcew_hawaii_wages.json`, the existing anchor). Also a no-fit pass-through (predict ACS growth = anchor growth) and the pooled correlation.

**Observations.** 4 counties, 13 growth years with both endpoints (2011 to 2019, 2022 to 2025; there is no 2020 1-year ACS, so 2020 and 2021 growth are lost) = 52 usable county-years, 40 of them walk-forward test points (2014 onward). Kalawao is excluded. County ACS 1-year medians carry sampling error of several percent, comparable to the RMSEs below; at n=40 with 4 counties sharing year shocks, none of these differences is statistically distinguishable. No significance test was run.

| Target | Anchor | WF RMSE (log growth) | Baseline RMSE | Pass-through RMSE | Corr |
|---|---|---|---|---|---|
| B19013 | QWI county | 0.0692 | 0.0765 | 0.0707 | 0.45 |
| B19013 | QWI state | 0.0746 | 0.0765 | 0.0745 | 0.23 |
| B19013 | QCEW state | 0.0759 | 0.0765 | 0.0743 | 0.21 |
| B20002 | QWI county | 0.0937 | 0.0871 | 0.0820 | 0.16 |
| B20002 | QWI state | 0.0917 | 0.0871 | 0.0834 | 0.13 |
| B20002 | QCEW state | 0.0945 | 0.0871 | 0.0835 | 0.14 |

Reading: for median household income, county QWI is the best of the three (RMSE 9.5% below baseline, correlation 0.45 against 0.21 for QCEW); statewide QWI and QCEW are about the same as each other and barely beat the baseline. For worker earnings (B20002) no anchor beats the baseline in the fitted walk-forward; pass-through does a little better than the fits for all three, with QWI county marginally ahead of QCEW (0.0820 against 0.0835). So there is a hint that county detail helps for household income, and nothing more. Caveats that point both ways: QWI here is private-sector only; QCEW in the repo is all ownership; the household-income gain comes from county variation, which the existing statewide QCEW anchor cannot supply by construction (a county-level QCEW series exists and was not tested here).

**Publication lag (from the files pulled, 2026-11-24).** The QWI release dated 2026-11-23 has `EarnS` through 2025Q4 and employment (`Emp`) through 2026Q1; earnings for 2026Q1 are blank. So the last full-year earnings figure (2025) arrives about 11 months after year-end, and the latest quarterly earnings figure lags its quarter end by about 11 months (employment about 8 months). QCEW in the repo already holds calendar 2025 (`last_refresh` 2026-11), and BLS QCEW normally posts a quarter about 5 to 6 months after it ends (this is from general knowledge, not checked in this run). ACS 2025 1-year is already in the panel (fetched 2026-11-22). So QWI is slower than QCEW and about the same timing as, or later than, the ACS it would be anchoring; for forecasting ACS year t+1 it supplies the t-1... t data only, never a leading signal. Revisions were not measured (only one vintage pulled).

**Verdict A: reject for now (adopt only if a county-level anchor is wanted).** It is a null on worker earnings and a weak, unproven gain on household income from 40 noisy test points, with a longer lag than QCEW. If county-level anchors are ever wanted, QWI county is a candidate alongside county QCEW, and should be tested against it with more years.

## Part B: LODES residence workers by state legislative district

**Method.** The 2020 PL block population, tract-to-PUMA and 2022 BEF logic of `build_puma_district_crosswalk.py` assigns each of Hawaii's blocks to a PUMA and to a House and Senate district. RAC 2022 `CE01` (resident workers earning up to $1,250 a month, the lowest band) is summed by district ("actual"). "Population-proportional" is the PUMA's CE01 total times the district's share of the PUMA's 2020 population, which is what the repo's apportionment does. Every one of the RAC blocks matched the PL block list (0 lost). Totals: 1,455,271 people, 579,598 resident workers, 98,762 in `CE01`. The district tables are in `data/qwi-lodes/testB.csv` (gitignored; rerunnable).

| Measure | Chamber | n | Median abs diff | p90 | Max | Share of districts >20% off | Misallocated share of workers |
|---|---|---|---|---|---|---|---|
| CE01 vs population split | House | 51 | 17.9% | 78.8% | 252% | 49% | 25.1% |
| CE01 vs population split | Senate | 25 | 17.3% | 68.1% | 146% | 36% | 19.1% |
| All workers (C000) vs population split | House | 51 | 22.6% | 83.4% | 273% | 55% | 24.8% |
| All workers (C000) vs population split | Senate | 25 | 17.1% | 70.9% | 148% | 44% | 19.3% |
| CE01 vs worker-proportional split | House | 51 | 5.3% | 15.6% | 37% | 8% | 5.4% |
| CE01 vs worker-proportional split | Senate | 25 | 4.7% | 6.7% | 15% | 0% | 3.5% |

Reading: the within-PUMA population split misses LODES low-earner counts by a median of about 18% (House) and 17% (Senate), consistent with the "roughly plus or minus 20%" in METHODOLOGY.md for a typical district, but with a long tail: the worst House districts are 46 (LODES 4,285 against 1,216 implied, +252%), 2 (+164%), and 36, 37, 33 (about -80%). The third block of the table shows that most of the gap is not an earnings-mix effect: all-worker counts diverge from population by the same amount, and once the split follows resident workers, low-earner counts agree within about 5% (median). So the district misallocation is mainly that residents-who-work-in-covered-jobs are unevenly distributed relative to total population.

**Why, and the caution.** District 46 has a 2020 PL population of 51,085 against about 28,500 average; the 2022 plan is drawn on Hawaii's permanent-resident base, which excludes non-resident military and students, whereas the repo's crosswalk uses raw PL totals. LODES covers civilian jobs by residence and does not cover active-duty military. That is the likely cause of the largest gaps (Schofield, Pearl Harbor and similar blocks), but this run did not test that explanation; treat it as a hypothesis. Other limits: LODES is workers (not households or tax units) and adds noise infusion at block level; RAC `CE01` is a monthly-earnings band of a job count, not an annual income measure; one year (2022) only; the 2020-block to 2022-plan join is the repo's own and was taken as given.

**Verdict B: adopt with caveats, as a diagnostic or an optional weighting, not as a drop-in replacement.** The data are clean, small, free and join without losses, and they show the ±20% band understates error in a handful of districts (roughly 8 to 10 House districts exceed 50%). Two follow-ups before any use: (1) test the military hypothesis by computing the split on a population base that excludes group quarters/military, which may remove most of the gap with no LODES at all; (2) if LODES is used, apportion on resident workers (or blend), since a CE01-specific split adds little beyond that. Not done here: validating against any known district-level truth (none was available), so the claim that LODES is closer to the truth than the population split is untested; the table only measures how different they are.

## Not obtained / not done

- QWI vintage history (revisions) and county QCEW comparison.
- Statistical significance tests for Part A.
- Any non-LODES validation of district low-earner counts.
- Which published pipeline numbers would change: none touched.
