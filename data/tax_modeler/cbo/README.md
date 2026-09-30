# CBO Component Growth Rates

Per-component nominal annual growth factors used by `tax_modeler.calibration.cbo_aging`
for ITEP-style year-by-year aging of filer income components.

## Source

CBO publishes the **Budget and Economic Outlook** twice yearly (Jan + summer
update) at https://www.cbo.gov/topics/budget/economic-projections.

The relevant figures live in:
- **Table 2-3** (or current naming): Components of Income, Calendar Years
- 10-year nominal projections per income component
- Re-published every Outlook with revisions

## Versioned CSVs

Each CSV is named `cbo_components_YYYY-MM.csv` for the publication month
of the source Outlook. To refresh:

1. Download the most recent Outlook XLSX from CBO's website
2. Locate Table 2-3 (Components of Income, Calendar Years)
3. For each component (wages, proprietors, dividends, interest,
   capital gains realizations, pensions, etc.), extract nominal level
   per year for 2022-2031
4. Compute `growth_factor_from_2022 = level_y / level_2022`
5. Save as `cbo_components_<vintage>.csv` matching the schema below

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

The values in `cbo_components_2025-01.csv` are derived from the CBO Jan
2025 Outlook published projections. For final fiscal-note publication,
verify against the source XLSX — CBO occasionally revises historical
nominal levels in subsequent Outlooks.
