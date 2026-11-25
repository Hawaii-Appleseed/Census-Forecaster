# Interest-and-dividends growth: is the level right for Hawaii units?

2026-11-25. A check, not a change: no default, calibration, model input, published number, `site/` or `runs/` file was touched. Follow-up to the last section of `cbo-per-person-denominators.md` (branch `data/cbo-per-person-denominators`), which flagged the level of the `interest` path as the one choice worth revisiting.

Scripts (all ruff-clean) and result CSVs, all under `data/tax_modeler/cbo/`:

| Check | Script | Output |
|---|---|---|
| 1, 4 | `scripts/diag_interest_growth_effect.py` | `interest_growth_effect.csv` |
| 2 | `scripts/diag_interest_soi_history.py` | `interest_soi_history_hi_us.csv`, `interest_soi_growth_hi_vs_us.csv` |
| 3 | `scripts/diag_interest_rates_check.py` | `interest_rates_check_2022_2030.csv` |

Sources: IRS SOI Historic Table 2, Hawaii (`{YY}in12hi.xlsx`) and United States (`{YY}in54us.xlsx`), TY2018-TY2023 (TY2023 is the latest published; `24in12hi.xlsx` returns 404), fetched with curl from irs.gov. CBO February 2026 Revenue and Economic Projections workbooks from `runs/cbo_downloads/`. Cached tax units (`data/artifacts/tax_units_cache.parquet`, 39,996 units, PUMS HI 5yr 2020-24), read only.

## Bottom line

**Keep the CBO line and the 1.00 Hawaii factor.** The "near-doubling" is real in the data, not an artifact of CBO: US interest plus non-qualified dividends already rose 106% from 2022 to 2023 in SOI, and Hawaii rose at least as fast. Two framing corrections matter more than the factor:

1. The model does not apply +39% (2025) and +99% (2030). `year_recalibrator` ages from the PUMS dollar-year (2024), so the growth a unit actually receives is **+4.6% (2025), +23.2% (2027), +49.3% (2030)**, against +3.2% / +9.4% / +19.1% on the pre-#110 line.
2. The 2024 base is not a 2024-rate base. ACS 2020-24 INTP is an average of income years that straddle the 2020-21 near-zero-rate period, then is inflated by CPI only. So the base is more likely too low than too high, which cuts against a discount.

What it moves: relative to the pre-#110 interest path the current path adds **$59M (0.10%) in 2025, $608M (0.99%) in 2027, $1,325M (2.15%) in 2030** to aggregate income, 67% of it in the top income decile; for units within 50-150% of the SPM line it adds **$1.5M / $15M / $33M (0.03% / 0.30% / 0.65% of their income)**. The low-income-retiree concern is real in direction and tiny in size.

## 1. What the path is applied to

`_component_amounts_for_filer` sets `interest = primary_intp + secondary_intp` and `dividends = primary_div + secondary_div`. PUMS INTP is interest, dividends **and net rental, royalty, estate and trust income** combined. In the cache `primary_div`/`secondary_div` are zero in every unit, so every dividend dollar the model has sits in INTP, and so does all net rent. **Rental income is grown at the interest rate**. Nothing in the code splits it out; the `other` component (factor 0.90) is only the residual of `income` minus the named components, and rent is not in it.

Measured on the cached units (weighted, 725,372 units, income $61.66B, 2024 dollars):

| | INTP $M | share of unit income | units with INTP > 0 |
|---|---|---|---|
| All units | 4,387 | 7.1% | 19.2% |
| Income decile 1 | 5.9 | 3.3% | 4.8% |
| Deciles 2-5 | 21-96 each | 2.1-2.7% | 8-16% |
| Deciles 6-9 | 166-476 each | 3.5-4.4% | 18-29% |
| Decile 10 | 2,923 | 13.0% | 45.4% |
| SPM ratio < 0.5 | 14.3 | 2.6% | 5.7% |
| SPM ratio 0.5-1.5 (172,102 units) | 110.7 | 2.2% | 10.5% |
| Head 65+ | 2,568 | 16.2% | 35.3% |
| Head 65+ and SPM ratio 0.5-1.5 | 55.1 | 3.9% | 19.2% |

Decile 10 holds 67% of INTP dollars. SPM ratio is unit income over the Honolulu renter threshold for the unit's filing composition (the cache has no tenure or county, so `threshold_for_units` takes its tax-unit fallback); it is a rough stand-in for the line, measured on 2024 base incomes.

How much of INTP is rent is **not measurable from PUMS**. A reasoned bound from SOI Hawaii TY2023 (net rental and royalty $453M against taxable interest $989M and ordinary dividends $1,587M) puts rent near 15% of the three combined; ACS INTP ($4.39B) is larger than the SOI total ($3.03B) and includes trusts and estates, so 15-25% is a plausible range, unverified.

## 2. Hawaii against the US, and observed against projected

SOI all-returns amounts, $ thousands (`interest_soi_history_hi_us.csv`). "Int + non-qual div" is taxable interest plus ordinary minus qualified dividends, CBO's definition (it reproduces CBO's 2022 actual: SOI US $232.0B, CBO $232.7B).

| Year | HI taxable interest | US taxable interest | HI int + non-qual div | US int + non-qual div |
|---|---|---|---|---|
| 2018 | 367,779 | 127,360,352 | 637,990 | 204,720,467 |
| 2019 | 462,651 | 152,285,865 | 759,212 | 235,483,412 |
| 2020 | 389,955 | 126,333,222 | 623,499 | 193,063,940 |
| 2021 | 373,408 | 103,674,558 | 698,125 | 194,947,205 |
| 2022 | 369,546 | 133,121,649 | 698,593 | 232,028,516 |
| 2023 | 988,610 | 311,468,789 | 1,545,977 | 478,759,261 |

Growth since 2018 (`interest_soi_growth_hi_vs_us.csv`) and the ratio of Hawaii to US growth, the empirical counterpart of the Hawaii factor (the README fits the other factors as Hawaii CAGR over national CBO CAGR):

| Series | HI 2018-23 | US 2018-23 | Ratio | HI 2022-23 | US 2022-23 | Ratio |
|---|---|---|---|---|---|---|
| Taxable interest | +168.8% | +144.6% | **1.17** | +167.5% | +134.0% | **1.25** |
| Int + non-qual div | +142.3% | +133.9% | **1.06** | +121.3% | +106.3% | **1.14** |
| Ordinary dividends | +56.9% | +56.4% | 1.01 | -40.6% | +21.9% | n/m |

Year by year the ratio is unstable (2019: 1.32 interest; 2020: 0.92; 2021 and 2022: Hawaii flat while the US swung, so the ratio is meaningless when the US change is near zero). Hawaii ordinary and qualified dividends are very noisy (HI ordinary dividends: $1.0B, $1.1B, $2.1B, $1.3B, $2.7B, $1.6B), almost certainly a few very large filers, so the Hawaii non-qualified series (a difference of two noisy numbers) is the least reliable row. The usable statement: **over the only window that contains the rate surge, Hawaii grew 6% to 25% faster than the US, never slower. Nothing here supports a factor below 1.00.** Only one surge year is observed, so the ratio is a single data point, not a fit.

**Observed or projected?** Observed. CBO's 2023 value is a projection off a 2022 sample ($286.8B, +23%); SOI's actual TY2023 is **$478.8B (+106%), 67% above CBO's 2023 figure and already above CBO's 2030 value ($462.5B)**. CBO's own line ($232.7B in 2022 to $462.5B in 2030, +99%) therefore reaches in 2030 what the nation had already reached in 2023. The 2022-based "+99%" headline is mostly in the data already; it is the 2022-to-2024 portion (+33% in the CBO file) that is too small, and the model drops that portion anyway by re-basing to 2024.

## 3. Rates check

`interest_rates_check_2022_2030.csv`. CBO's Economic Projections (calendar year) put the 3-month bill at 4.97% (2024) falling to 3.18% (2030) and the 10-year at 4.21% rising to 4.34%. NIPA personal interest income rises 59% from 2024 to 2030 ($1,927B to $3,069B), and CBO's tax-return line rises 49% over the same years. Nominal GDP rises 29%.

| 2024 to | NIPA personal interest income | CBO tax-return line | avg(3m, 10y) rate | 10y rate | Nominal GDP | Implied asset base, avg rate | Implied asset base, 10y rate |
|---|---|---|---|---|---|---|---|
| 2025 | x1.056 | x1.046 | x0.91 | x1.019 | x1.048 | x1.16 | x1.04 |
| 2027 | x1.266 | x1.232 | x0.82 | x1.011 | x1.149 | x1.55 | x1.25 |
| 2030 | x1.593 | x1.493 | x0.82 | x1.032 | x1.288 | x1.94 | x1.54 |

Income growth is **not** explained by rates: the short rate falls 36% and the long rate is flat, yet income grows 49-59%. Dividing out rates leaves an implied interest-bearing asset base growing 54% (10-year as the yield) to 94% (average of the two) over 2024-2030, against GDP +29%. That is national balance-sheet growth (Treasury and deposit balances, portfolio repricing from the low-yield vintage to current yields), and a fixed-weight Hawaii unit does not gain that. This is a **reasoned** decomposition: the proxy yield is crude (a portfolio's yield is a lagged blend of past rates, so a rising effective yield with falling bill rates is plausible), and I have no data on Hawaii household assets. If a unit's assets merely grew with nominal GDP (+29%) at the CBO rate path, the 2024-2030 gain would be about +6% (average rate) to +33% (10-year rate) rather than +49%. So the 2030 end of the path is where the national composition could overstate; 2025 and 2027 are close to rates-and-GDP and to what SOI shows.

## 4. Size of the effect

`interest_growth_effect.csv`. In-memory `age_filers_with_components(base_year=2024)` on the cached units, interest path swapped, everything else as default. Zero-growth drift is $0.0M (decomposition sums to income). "Old" is the pre-#110 interest line (vintage `2025-01-cg2026-02`, Hawaii factor 1.00).

| Aggregate income change, current path vs | 2025 | 2027 | 2030 |
|---|---|---|---|
| pre-#110 path | +$58.6M (0.10%) | +$607.8M (0.99%) | +$1,324.7M (2.15%) |
| factor 0.5 on current line | +$100.6M | +$509.9M | +$1,082.2M |
| interest frozen at 2024 | +$201.1M | +$1,019.9M | +$2,164.4M |

Units within 50-150% of the SPM line (172,102 units, $5,111M of income, 2.2% of it INTP):

| | 2025 | 2027 | 2030 |
|---|---|---|---|
| vs pre-#110 path | +$1.5M (0.03%) | +$15.3M (0.30%) | +$33.4M (0.65%) |
| vs factor 0.5 | +$2.5M | +$12.9M | +$27.3M |
| vs frozen at 2024 | +$5.1M | +$25.7M | +$54.6M |

Heads 65+ in the band get $0.7M / $7.6M / $16.6M (0.05% / 0.54% / 1.17% of their income). Among the 18,021 near-line units that actually hold INTP the 2030 effect is +6.0% of their income. Income decile 10 takes $883M of the $1,325M in 2030. So "applying a national, dollar-weighted growth rate uniformly to every holder" mostly adds income where holders are rich; the low-income-retiree overstatement is bounded at the figures above.

Rent split (assumed shares, not measured): moving a share s of INTP to the `other` path (CBO total income, factor 0.90) changes aggregate income by +$11M / -$53M / -$152M at s = 15% (2025/2027/2030), -$254M at 25% and -$406M at 40% in 2030; near-line income changes by under $11M in every case. The sign flips because `other` outgrows interest through 2025 and under-grows it after.

## 5. Recommendation

**Keep** `interest` on the CBO February 2026 line with Hawaii factor 1.00. Reasons, with what each rests on:

- Measured: the Hawaii/US growth ratio is 1.06-1.25 over the surge window, never below 1; a factor below 1.00 has no empirical support.
- Measured: the surge is already in SOI national data for 2023, above CBO's own 2030 value; CBO is the low side of what has been observed.
- Measured: the effect on near-poverty income is $33M by 2030 (0.65%) and $1.3B (2.15%) in aggregate, mostly top-decile; poverty outputs are insensitive to this choice at that size.
- Reasoned: the base INTP is an average over low-rate years, so it sits below 2024 levels; this argues the model's level is conservative at 2025.
- Reasoned: by 2030 CBO's path embeds national asset-base growth that fixed-weight units would not have. If you want a hedge, the least invasive is a factor of 0.5 on 2028-2030 only; applied to the whole path it would remove $510M (2027) and $1,082M (2030) of aggregate income and $13M / $27M near the line. I do not recommend adopting it on this evidence: it is a judgment about household balance sheets that nothing fetched here measures.
- Not recommended: capping (no unit-level evidence of overstatement; decile 10 carries the dollars and is where SOI shows the growth); a different CBO line (the only alternative, NIPA personal interest income, grows faster: +59% against +49% from 2024).
- Worth doing separately, low priority: split rent out of INTP. It is a real mislabel (rent grows at the interest rate) but second order: -$254M in 2030 at a 25% rent share, under $11M near the line. It needs a rent estimate PUMS does not give.

Open items: SOI TY2024 (due 2026-27) will show whether 2024 interest held the 2023 level; refit then. The `DEFAULT_HAWAII_FACTORS` comment in `cbo_aging.py` says dividends/interest factor 1.00 is "federal-rate-driven"; the data here support the number but not that reason (rates fall while the line rises).
