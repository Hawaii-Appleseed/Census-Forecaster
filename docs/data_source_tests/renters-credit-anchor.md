# Renters credit take-up anchor (DOTAX "Tax Credits Claimed")

Test of whether DOTAX's published claims for the Low-Income Household Renters credit (HRS 235-55.7) can replace the unanchored 0.30 `takeup_pct` in `packages/tax_modeler/src/tax_modeler/credits/hi_renters.py`. Exploratory; nothing published, no default changed.

## Source and vintages
- DOTAX, "Tax Credits Claimed by Hawaii Taxpayers", workbooks `https://files.hawaii.gov/tax/stats/stats/credits/archive/{2018..2022}credit_tables.xlsx` and `https://files.hawaii.gov/tax/stats/stats/credits/2023credit_tables.xlsx` (same URLs as `scripts/fetch_dotax_credit_claims.py`; fetched 2026-11-24, all HTTP 200). TY2024 is not published (404).
- Line: "Tax Credit for Low-Income Household Renters": Table A-1 (all taxpayers), A-5 (dollars, $1,000, individuals by income class), A-6 (claim counts by income class).
- Pulled with the new `scripts/fetch_dotax_renters_credit.py` into `packages/tax_modeler/src/tax_modeler/data/raw/dotax_renters_credit_claims.csv`. The bundled `dotax_credits_claimed.json` is untouched.

## What DOTAX reports
| TY | claims | dollars ($K) | $/claim |
|---|---|---|---|
| 2018 | 21,081 | 2,132 | 101 |
| 2019 | 20,530 | 2,024 | 99 |
| 2020 | 17,818 | 1,708 | 96 |
| 2021 | 17,387 | 1,697 | 98 |
| 2022 | 16,925 | 1,675 | 99 |
| 2023 | 17,205 | 1,644.6 | 96 |

By class (TY2023): under $10K 8,010 claims / $787K; $10K-$30K 9,195 / $857K; nothing above $30K. TY2018 and TY2020 suppress ("d") the under-$10K cell; the $10K-$30K cell and the total are published. Individual dollars equal all-taxpayer dollars (Table A-1).

## The test
`scripts/renters_credit_anchor_test.py` builds the TY2022 PUMS tax units as `poverty_impact_report._load_units` does (TY2023 via CBO aging), calls `compute_hi_renters_for_units(units, overrides={"takeup_pct": 1.0})`, and sums weighted eligible units and dollars. Run on this machine, about 2 min. Two eligibility definitions:
- income only: what the pipeline's `_apply_hi_renters` does today (the function never checks tenure);
- income and `tenure == "renter"`: what the statute requires.

| Model, full take-up | units | dollars | avg credit |
|---|---|---|---|
| TY2022, income only | 232,287 | $26.59M | $114 |
| TY2022, renters only | 97,004 | $11.10M | $114 |
| TY2023, income only | 243,348 | $27.82M | $114 |
| TY2023, renters only | 101,435 | $11.61M | $114 |

## Implied take-up (observed / eligible)
| Year | claims / renter units | dollars / renter $ | claims, income-only | dollars, income-only |
|---|---|---|---|---|
| 2022 | 17.4% | 15.1% | 7.3% | 6.3% |
| 2023 | 17.0% | 14.2% | 6.9% | 5.9% |

Across 2018-2023 on the fixed TY2022 renters-only base (no model run for earlier years): claim ratio 21.7%, 21.2%, 18.4%, 17.9%, 17.4%, 17.7%; dollar ratio 19.2%, 18.2%, 15.4%, 15.3%, 15.1%, 14.8%. The fall is DOTAX's (claims down 18% from 2018 to 2023), not the model's, since the base is fixed. 2020-2023 is stable within about 3 points.

## Effect of replacing 0.30 (TY2022)
- Today's pipeline (income-only eligibility, 0.30): $7.98M against $1.675M claimed, 4.8x too high. Anchoring that definition alone would need 0.063 to 0.073.
- Renters-only eligibility at 0.30: $3.33M, still 2.0x claimed. At 0.15 (dollars) to 0.17 (counts): $1.66M to $1.89M. Going from 0.30 to 0.15 halves renters-only credit dollars (-$1.66M).
- Setting 0.15 while leaving income-only eligibility would give $3.99M, 2.4x claimed. The tenure test and the take-up change go together.
- SPM poverty rate: not run (needs the full poverty pipeline, off limits on this machine). Scale only: the cut is roughly $4M to $6M statewide at about $114 per unit; any poverty-rate change should be small, but it is not measured.

## Limits
- Eligibility uses the PUMS tax-unit `income` proxy against the module's caps and $50/$100/$150 tiers; the tiers and caps were not re-verified against the statute. Observed $99 per claim against the model's $114 per unit suggests the schedule or the claim definition (returns vs exemptions; the report does not say) is not exact.
- Claims are tax-return filings. Renters who do not file cannot claim, so the ratio is take-up and filing combined, not take-up alone.
- Tenure comes from PUMS `TEN` via `attach_tenure` (rent-free maps to owner-no-mortgage). The low-income renter share is the main swing factor in the denominator and was not stress-tested.
- Earlier-year ratios reuse the TY2022 base. TY2023 uses CBO aging. No TY2024 claims exist.

## Verdict
Adopt with caveats. The DOTAX line is official and stable (16.9K-17.4K claims, 2021-2023) and implies a renters-only take-up of about 0.15 (dollars) to 0.17 (counts), well below 0.30. The bigger finding is that the pipeline applies no renter test, so today's 0.30 produces 4.8x the claimed dollars. Recommended follow-up PR: add the tenure filter, set `takeup_pct` to about 0.15 (dollar-anchored), and check that the tier amounts reproduce $99 per claim. Not done here; no default or published number changed.
