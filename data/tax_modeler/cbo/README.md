# CBO Component Growth Rates

Per-component nominal annual growth factors used by `tax_modeler.calibration.cbo_aging`
for ITEP-style year-by-year aging of filer income components.

## Source

CBO publishes the **Budget and Economic Outlook** twice yearly (Jan + summer
update) at https://www.cbo.gov/topics/budget/economic-projections.

The figures come from the Outlook's revenue workbook
(`51138-<YYYY-MM>-Revenue.xlsx`), sheet 3, "Detailed Individual Income Tax
Projections": income reported on tax returns by calendar year, component by
component, the same definitions the model ages. (Table 2-3's national-accounts
income is a different concept and starts a year later.)

## Versioned CSVs

Each CSV is named `cbo_components_YYYY-MM.csv` for the publication month
of the source Outlook. `cbo_aging.DEFAULT_VINTAGE` names the one every caller
ages on.

| Vintage | How it was made | Source file (SHA-256) |
|---|---|---|
| **2025-01-cg2026-02** (default from 2026-11-24) | `build_cbo_components.py --capital-gains-only 2025-01 2026-02`: the 2025-01 file with its capital gains replaced by the 2026-02 file's (CBO's realizations). The other components stay on 2025-01 until a review of per-person growth: moving them to CBO's own lines moved published poverty by up to 0.4 points (working-family credits TY2028 10.52% to 10.93%; H.R.1 people into poverty 852 to 643). | as below |
| 2026-02 (CBO's own lines; under review) | `scripts/build_cbo_components.py`, from CBO's "Revenue Projections, by Category", sheet 3 (income reported on tax returns, 2022 actual, 2023-2036 projected); retirement grows per person on the prior year's CPI-U from the Economic Projections (CBO's pension and Social Security lines are national totals that also grow with the number of retirees) | `51138-2026-02-Revenue.xlsx` (8f5bf6db9b40b4d997ea3932a340515f92b08e035f69e5e603e15791f3ce258c), `51135-2026-02-Economic-Projections.xlsx` (ae8f4920702fabf8fb3136bc94a42c53466cff5e890dec22396d8dc49dc2f776) |
| 2025-01 (default until 2026-11-24; its non-capital-gains rows are still in use) | Typed by hand (commit 1069f96). Its capital gains are **not CBO's**: it has 2023 at +8.5% from 2022, where CBO's January 2025 Table 6 has realizations down 26% ($1,283B to $943B), and a flat 5.5% a year after 2026. The other components are near CBO's but not its figures. | none recorded |

To refresh: save the Outlook's revenue workbook from
https://www.cbo.gov/data/budget-economic-data with a browser (cbo.gov refuses
scripted downloads), run
`python scripts/build_cbo_components.py 51138-<YYYY-MM>-Revenue.xlsx 51135-<YYYY-MM>-Economic-Projections.xlsx <YYYY-MM>`,
add a row above, and move `DEFAULT_VINTAGE`.

## CSV schema

```
component,year,growth_factor_from_2022,annual_cagr_pct,source_note
```

Where:
- `component` ∈ {wages, proprietors, business, capital_gains, dividends,
  interest, retirement, other}
- `business` is an alias mapped to proprietors+partnership+S-corp
  pass-through; CBO publishes them combined as "proprietors income"
- `other` covers rental, transfers, and miscellaneous components

## Component → income field mapping

| CBO component | tax_modeler PUMS / SOI field |
|---|---|
| wages | `primary_wagp + secondary_wagp` |
| proprietors / business | `primary_semp` (Sch C / partnership / S-corp) |
| capital_gains | `synthetic_cg_share × income` (synthesized + imputed) |
| dividends | `primary_div + secondary_div` |
| interest | `primary_intp + secondary_intp` |
| retirement | `primary_retp + secondary_retp + primary_ssp + secondary_ssp` (the 85% of Social Security that `income` counts; else 0.85 × `*_ssp_full`) |
| other | residual: OIP, rental, royalty, misc |

The components sum to the unit's `income`, so aging to the base year with no
growth leaves it unchanged. SSI, public assistance and the untaxed 15% of
Social Security are in total cash income but not in `income`, so they are not
components (see `transfers_outside_income`). Until 2026-09-30 retirement held
full Social Security, SSI and public assistance, which inflated income about
1.5% at zero growth.

## Hawaii calibration

CBO is national. Hawaii has documented divergence from national rates,
particularly in wages (HI tourism economy lags national). Apply
component-specific Hawaii adjustment factors at use time — see
`data/tax_modeler/cbo/hawaii_calibration_factors.json` (built by
`backtests/cbo_aging_hawaii_factors.py`).

## Caveats

CBO revises past years in later Outlooks (2023's capital gains realizations
were $943B in January 2025 and $976B in February 2026), so a new vintage moves
past years too, not only the projection. `other` ages on total income: CBO's
"all other sources" line is a small residual that swings by half a year.
