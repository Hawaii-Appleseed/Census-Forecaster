# Cached external data sources

## `irs_soi_zip_hawaii_2022.csv`

Hawaiʻi rows extracted from the IRS Statistics of Income (SOI) ZIP-code data,
tax year 2022 release (published Feb 2025).

- **Source:** <https://www.irs.gov/statistics/soi-tax-stats-individual-income-tax-statistics-zip-code-data-soi>
- **Direct file:** <https://www.irs.gov/pub/irs-soi/22zpallnoagi.csv>
- **Subset:** STATE='HI' rows only (60 rows, 58 active ZIPs + state-total + 99999 placeholder)
- **Columns kept:** `STATEFIPS, STATE, ZIPCODE, agi_stub, N1, A00100, N59660, A59660, N11070, A11070, N2, N03220, A03220`
- **Used by:** `tax_modeler.analysis.district_raking.rake_weights_to_irs_zip`
- **State-total cross-check:** N59660 (EITC count) = 84,010 ✓ matches `hawaii_caseload.csv` IRS SOI anchor.

## CBPP table 367 (unavailable)

The original Tier 2 plan called for caching CBPP's EITC/CTC State Legislative
District spreadsheet as `cbpp_table367_ty2022.csv`. **CBPP no longer publishes
EITC/CTC data at the SLD level on its Program Participation Data Dashboard
(<https://apps.cbpp.org/program_participation/>);** only SNAP has SLD-level
exports, and those are gated behind a Cloudflare/captcha wall that blocks both
`curl` and direct browser downloads from this session.

The IRS SOI ZIP cache above is the authoritative replacement for the
EITC/CTC-by-geography use case (richer columns, ZIP rather than SLD granularity,
direct IRS source). The Tier 2 task is marked closed on these grounds.

If a CBPP-style SLD comparison is needed in the future, paths to explore:
1. Email CBPP directly for the historical SLD spreadsheet
2. Sign in via the Hawaiʻi Appleseed CBPP partner account, if any
3. Use a residential browser proxy to bypass Cloudflare

## `../external/dbedt_databook_2023_table_1_24.xls` and `../external/hi_population_by_age.json`

DBEDT resident population projections by five-year age group and sex, State
of Hawaiʻi, 2020 / 2030 / 2040 / 2050 — the **DBEDT 2050 Series** (*Population
and Economic Projections for the State of Hawaii to 2050*, April 2024,
Appendix Tables A-7 to A-9), as reprinted in the *Hawaiʻi State Data Book
2023*, Table 1.24.

- **Source:** <https://files.hawaii.gov/dbedt/economic/databook/2023-individual/01/012423.xls>
  (retrieved 2026-10-10; the DBEDT projections page itself,
  `dbedt.hawaii.gov/economic/economic-forecast/long-range-projections/`, rate-limits
  scripted access).
- **Derived file:** `hi_population_by_age.json`, written by
  `scripts/parse_dbedt_age_projection.py` — population by filer age band
  (16–24 [15–24 in the source], 25–44, 45–64, 65+) per published year, with
  the vintage and the folding rule in the file.
- **Used by:** `tax_modeler.calibration.age_targets` — filer counts by age band
  for the Phase 1 forward rake (`year_recalibrator.project_and_recalibrate(use_age_margins=True)`).
  Only each band's growth ratio between the base year and the target year
  enters; years between the published points are interpolated linearly.
- **Cross-check:** Census PEP Vintage 2024 (`sc-est2024-agesex-civ.csv`, civilian
  population) puts Hawaiʻi's 65+ share at 22.1% in 2024; the DBEDT series
  interpolates to 21.5% (19.6% in 2020, 24.4% in 2030; the DBEDT base includes
  military, which is younger). Not bundled.
- **Superseded:** the DBEDT 2045 Series (2018) is not used; 2050 is the current release.

## `../calibration/forward_growth_assumptions.json`

The growth constants of `calibration/forward_targets.py` (low-bracket
growth, top differential, rate drift, population growth, COR extrapolation)
with a vintage date, the source of each value and its low/high sweep values
for `forecast_sb3125_sensitivity.py --growth-sweep`. See the file.
