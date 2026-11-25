# Program caseloads as leading indicators — 2026-11-24

Exploratory data-source test, same gate as `backtests/results/market_signal_screen_*.md`
(METHODOLOGY.md "Market signals" through "Sector payrolls, electricity volume, disaster control").
Nothing published, no default changed, nothing under `site/`, `runs/` or `data/markets/` touched.

**Verdict: reject as a screened leading indicator. SNAP and Medicaid/CHIP caseloads add no
robust predictive content for any existing Hawaii monthly target, and no usable lift to the ACS
poverty or SNAP-share forecast. Keep the series bundled for descriptive use only (see Limits).**

## Sources and vintages

| series | source | vintage / coverage | rows |
|---|---|---|---|
| SNAP persons, households, benefits (Hawaii) | USDA FNS "SNAP Data Tables", national zip `snap-zip-fy69tocurrent-11.zip` on www.fns.usda.gov (`resource-files/`), parsed from the per-FY workbooks | retrieved 2026-11-24; 1988-10 to 2026-08 | 453 months each; one hole, 2019-01 and 2019-02 |
| Medicaid + CHIP total, Medicaid, CHIP enrollment (Hawaii) | CMS "State Medicaid and CHIP Applications, Eligibility Determinations, and Enrollment Data" on data.medicaid.gov (dataset 6165f45b-ca93-5bb5-9d06-db29c692a360), file `pi-dataset-october-2026-release.csv` from download.medicaid.gov | October 2026 release; 2017-06 to 2026-07 | 110 months (plus one stray 2013-09 point that the screen's 3-month gap rule ignores) |
| Hawaii statewide ACS 1-year poverty rate (S1701_C03_001E) and SNAP-household share (S2201) | Census Bureau table API behind data.census.gov (`ACSST1Y<year>`, state 15). api.census.gov itself answers "Missing Key" and no key is on this machine | 2010-2019 and 2021-2025 (no 2020 1-year) | 15 years |
| Honolulu County poverty rate | bundled `calibration_panel/acs_panel.json` (S1701_C03_001E, geoid 15003) | 2010-2019, 2021-2025 | 15 years |

Fetch scripts: `scripts/refresh_caseloads.py`, `scripts/refresh_caseloads_acs.py`. Outputs:
`data/caseloads/hi_caseloads_monthly.json` (macro_monthly row shape, new file) and
`data/caseloads/hi_state_acs_poverty_snap.json`.

Verified by a run today: the caseload JSON checkpointed from the interrupted session was
re-fetched from the two URLs and came out byte-identical. Honolulu 2025 poverty from the Census
table API (8.2) equals the bundled panel's value. The S2201 count cell moved from column C02
(2010-14) to C03 (2015+); each year was checked on receiving + not receiving = total households
(script asserts it). Medicaid rows come in two vintages per month (`N/P` and `Y/U`); the final
`Y/U` row is used.

Not verified by me: that the FNS and CMS figures equal the agencies' web tables (spot values
look right: Hawaii SNAP about 154k persons Dec 2019, 158k Aug 2026; Medicaid+CHIP 465k Mar 2023
peak). The ACS statewide SNAP share (11.0% in 2025, about 55k households) is far below FNS
administrative households (about 78k); the ACS survey under-reports SNAP, which matters for level
comparisons but not for the change-based tests here.

## Distortions visible in the data

- Emergency allotments: SNAP benefits fall from $84.5M (Feb 2023) to $80.2M (Mar 2023) to $61.9M
  (Aug 2023) with persons flat. The end date (early 2023) is from the brief; the break between the
  Feb and Mar 2023 months is the data's own confirmation.
- Medicaid unwinding: Medicaid+CHIP peaks 465k (Mar 2023) and falls to 394k (Jun 2025).
- Maui fire: SNAP persons jump 157.5k (Aug 2023) to 173.6k (Sep 2023), then revert by 2024 (155.3k
  Jun 2024); disaster SNAP issuance is the likely mechanism, which I did not confirm from a document.
- Pandemic: SNAP persons 153.0k (Mar 2020) to 180.3k (Jun 2020).

## Test 1: monthly screen (unchanged `run_screen`)

Script `scripts/caseload_screen.py` (full tables: `caseload-screen-tables.md`). Granger F-tests on
monthly log changes at lags 3/6/12, BH-FDR q=0.10 over all tests run, sign check on mean r over
leads 1..lag, same transform and sample rules as the other candidates. Predictors: SNAP persons
and Medicaid+CHIP total. Targets: every existing Hawaii monthly target that can be tested at monthly
cadence (HI_UNEMPLOYMENT, HONOLULU_ZHVI, HONOLULU_ZORI, HI_ELECTRICITY, HI_VISITORS,
HONOLULU_SF_MEDIAN; Honolulu CPI is bimonthly and US unemployment is not Hawaii, so both are
left out). 12 pairs. Registered before looking: only caseload -> HI_UNEMPLOYMENT carries a mechanism
(layoffs raise applications; sign +); the other ten pairs are declared two-sided and exempt from the
sign check. The sign table is set in-process in the script; `screen.py` is not edited.

Four runs: full sample; 2020 excluded; the repo's disaster control (`exclude_months` from
`hi_disasters.json`, 63 months dropped); and 2020 plus 2023-01..2024-12 excluded (emergency
allotment end, unwinding, Maui).

| run | Granger tests that ran | BH passes |
|---|---|---|
| Full sample | 27 | **0** |
| 2020 excluded | 23 | 2: SNAP -> ZHVI lag 3; Medicaid -> electricity lag 3 |
| Disaster control | 17 | 3: SNAP -> HI_UNEMPLOYMENT lag 3 (sign WRONG); SNAP -> SF median lag 6; Medicaid -> electricity lag 3 |
| 2020 + 2023-24 excluded | 21 | 3: SNAP -> HI_UNEMPLOYMENT lag 3; SNAP -> ZHVI lag 3; Medicaid -> electricity lag 3 |

Observations per test: SNAP rows rest on n = 200-249 (HI_UNEMPLOYMENT, full 249 / 2020-excluded
233 at lag 3), 295-313 (ZHVI), 133 (ZORI), 281-299 (electricity), 415-433 (visitors, SF median).
Medicaid+CHIP rows rest on n = 103-107 in the full sample at lag 3 and 104 at lag 6, falling to
59-91 once months are removed; every lag-12 test and most lag-6 tests report "insufficient aligned
months" (the screen's 8 observations per parameter floor). The Medicaid series is simply too short
for this screen. Because the screen's definition of a robust signal is BH in the full sample that
also survives the 2020-excluded rerun, **there are zero robust signals**.

The primary pair, caseload -> HI_UNEMPLOYMENT:

- SNAP: full-sample lag 3 p=0.51, lag 6 p=0.94, lag 12 p=0.025 (not BH). With 2020 excluded lag 3
  p=0.025, not BH. It clears BH only in the two runs that drop months for disasters or distortions
  (lag 3, p=0.0064 and p=0.0108) and not at lag 6 or 12. A signal that appears only after removing
  months chosen after the fact, and only at one lag, is not a finding. The sign check goes the wrong
  way where it has a verdict: mean r over leads 1-3 is -0.066 in the full sample and -0.110 in the
  disaster-control run (predicted +), and is below the materiality floor (+0.05) in the other two.
  The large lead-0 correlation (+0.37 full sample) is coincident co-movement, 2020-driven (falls to
  +0.18 without 2020), not a lead.
- Medicaid+CHIP: no BH pass; mean r is negative in all four runs (-0.11 to -0.42, flagged WRONG at
  lag 3), n = 59-103.

Secondary hits, none with a written mechanism and none robust: SNAP -> HONOLULU_ZHVI lag 3 (BH only
in the two exclusion runs; full-sample p=0.022 not BH; xcorr negative at leads of 3-6 months, which
is what a recession-then-recovery pattern would produce). Medicaid -> HI_ELECTRICITY lag 3 (BH in
three exclusion runs, p=0.0004-0.0051, n = 62-90; full-sample p=0.077, no BH). A Medicaid enrollment
series that moves with a continuous-enrollment policy and an unwinding is not a plausible predictor
of an imported-fuel price index; with 17-27 tests and n under 100 I treat it as a chance hit. Do not
promote either; neither would pass the two-gate definition.

## Test 2: annual walk-forward on the ACS baseline

Script `scripts/caseload_annual_walkforward.py` (tables: `caseload-annual-tables.md`). Baseline is
the forecaster's own `project_damped_trend` (phi 0.85), refit on observations before each target
year. Candidate adds b times the log December-to-December caseload change over the year before the
target (Dec T-2 to Dec T-1), with b a through-origin slope fit only on residuals already realised.
Targets: Hawaii poverty rate, Hawaii SNAP-household share, and Honolulu poverty rate from the bundled
panel. The bundled panel has no SNAP cell and no statewide row; the statewide series come from the
table fetched above.

Sample: 15 ACS years each (no 2020), first forecast year 2015, 10 forecast years. Needing four
realised residuals before b exists leaves **6 augmented folds for SNAP (2019, 2021-2025) and 2 for
Medicaid+CHIP (2024-2025)**. That is too few to test anything; the numbers are reported only so the
negative result is on the record.

| target | RMSE baseline | RMSE + SNAP change (6 folds) | MAE baseline | MAE + SNAP | SNAP closer | same, 2021 fold dropped (5 folds): RMSE base / +SNAP |
|---|---|---|---|---|---|---|
| HI poverty rate | 1.074 | 0.902 | 0.705 | 0.776 | 2/6 | 0.456 / 0.811 |
| HI SNAP-household share | 1.148 | 0.905 | 0.745 | 0.759 | 3/6 | 0.429 / 0.700 |
| Honolulu poverty rate | 1.148 | 1.104 | 0.876 | 0.911 | 3/6 | 0.565 / 0.863 |

The small RMSE gain with all folds is one fold. The 2021 forecast carries the pandemic SNAP spike as
its predictor (and is a 2-year-ahead forecast across the missing 2020 survey); remove it and the
caseload term makes RMSE and MAE worse on all three targets, and MAE is already worse with it in. The
residual-versus-x correlation tells the same story: r = +0.70 (p=0.02, n=10) for state poverty with
2021, +0.10 (p=0.80, n=9) without; the SNAP-share and Honolulu rows behave alike (+0.70 to +0.08 and
+0.64 to -0.26). Medicaid+CHIP: two folds, mixed, nothing to say. The same-year change (a nowcast,
not a lead) does worse than the baseline, not better.

## Limits

- Annual test: 6 folds (SNAP) and 2 (Medicaid), 15 ACS years per target with a hole at 2020; one
  pandemic observation decides the headline. It cannot detect a modest real effect; it can only show
  no effect large enough to see.
- Monthly Medicaid starts 2017-06, so the screen's sample floor blocks its lag-12 and most lag-6 tests.
- SNAP is not seasonally adjusted and has a 2019 hole (Jan-Feb); the screen drops the affected differences.
- All distortions flagged above overlap the Medicaid sample almost entirely. The exclusion runs
  remove most of what is left.
- The SNAP-share target is partly the same quantity as the predictor (ACS counts what the program
  administratively pays), so any relation there would be mechanical rather than predictive.
- Exploratory choices made by me: the 2023-24 distortion window, the December-to-December change,
  the K=4 residual requirement. They were set before the results, but they are my choices, not
  the repo's.
- Statewide ACS comes through the data.census.gov table API rather than api.census.gov (needs a key).
  It is an undocumented path.

## What this does not close

Caseload-driven poverty effects are better evaluated structurally (the H.R.1 SNAP/Medicaid page
models eligibility loss directly) than as a leading statistical signal. This test says nothing about
that use.

## Reproduce

```
uv run python scripts/refresh_caseloads.py
uv run python scripts/refresh_caseloads_acs.py
uv run python scripts/caseload_screen.py
uv run python scripts/caseload_annual_walkforward.py
```
