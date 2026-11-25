# Per-person denominators for CBO's national income lines

Branch `data/cbo-per-person-denominators`. Nothing here changes a default; the live vintage is untouched.

## What was obtained

| Denominator | Source | Status |
|---|---|---|
| Number of returns (millions), 2022-2036 | CBO Feb 2026 `51138-2026-02-Revenue.xlsx`, sheet 3 addendum (same table as the income lines) | read from local workbook |
| Total nonfarm employment, civilian household employment, civilian noninstitutional population 16+ | CBO Feb 2026 `51135-2026-02-Economic-Projections.xlsx`, "2. Calendar Year". 2022 level backed out of CBO's own 2023 percent change (the sheet starts 2023) | read from local workbook |
| Population 65+ and 18-64 (2022-2030) | Census Bureau 2023 National Population Projections, main series: `https://www2.census.gov/programs-surveys/popproj/datasets/2023/2023-popproj/np2023_d1_mid.csv` (2.9 MB, HTTP 200, not committed) | downloaded, summed |
| SSA Trustees OASDI beneficiaries / average benefit / COLA | ssa.gov/oact/tr/2025 and /2026 | **NOT OBTAINED: 403 to scripts (tested /tr/2026/, /tr/2025/, two table pages). Stopped on that route.** |
| CBO Demographic Outlook population by age | cbo.gov | **NOT OBTAINED: 403 to scripts.** Census 2023 projections stand in for age 65+ (Census, not CBO, and its 2023 vintage predates CBO's lower net-immigration path; the 16+ series is CBO's) |
| BLS employment projections | not pulled; CBO's own nonfarm employment (same Outlook) is the matching series for the wage line |

Committed small outputs: `data/tax_modeler/cbo/denominators_2026-02.csv`, `cbo_components_2026-02-per-person.csv` (new vintage name, not a default), `per_person_comparison_2026-02.csv`. Reproduce with `scripts/build_cbo_per_person.py <np2023_d1_mid.csv>`; aggregate check is `scripts/cbo_per_person_aggregate_check.py`.

## Denominator indices (2022 = 1.00)

| Year | Returns | Nonfarm jobs | Pop 16+ | Pop 65+ | Pop 18-64 | Raw taxable pensions+IRA+SS | Raw taxable SS |
|---|---|---|---|---|---|---|---|
| 2025 | 1.042 | 1.045 | 1.038 | 1.096 | 1.004 | 1.369 | 1.432 |
| 2027 | 1.059 | 1.057 | 1.050 | 1.156 | 1.006 | 1.556 | 1.658 |
| 2030 | 1.081 | 1.066 | 1.068 | 1.232 | 1.009 | 1.674 | 1.976 |

## Three paths per component (growth factor from 2022)

"Live" = 2025-01-cg2026-02. "Raw" = cbo_components_2026-02.csv as built (note: it already has retirement on CPI and proprietors on NIPA; only wages, interest, dividends, other are still raw national totals). "Per-person" = candidate file.

| Component (per-person denominator) | Year | Live | Raw 2026-02 | Per-person |
|---|---|---|---|---|
| wages (nonfarm jobs) | 2025 / 2027 / 2030 | 1.149 / 1.252 / 1.425 | 1.167 / 1.268 / 1.428 | 1.117 / 1.200 / 1.339 |
| interest + ordinary div (returns) | same | 1.176 / 1.246 / 1.357 | 1.392 / 1.640 / 1.988 | 1.336 / 1.549 / 1.839 |
| qualified dividends (returns) | same | 1.149 / 1.255 / 1.432 | 1.163 / 1.239 / 1.294 | 1.117 / 1.170 / 1.197 |
| capital gains (kept national) | same | 1.275 / 1.279 / 1.199 | same | same |
| proprietors/business (returns) | same | 1.169 / 1.284 / 1.479 | 1.195 / 1.314 / 1.505 | 1.147 / 1.241 / 1.393 |
| retirement (pop 65+) | same | 1.144 / 1.237 / 1.392 | 1.158 / 1.225 / 1.315 (CPI) | 1.249 / 1.346 / 1.359 |
| other = total income (returns) | same | 1.125 / 1.217 / 1.369 | 1.203 / 1.316 / 1.454 | 1.155 / 1.243 / 1.345 |

Retirement, raw national totals for reference: 1.369 (2025), 1.556 (2027), 1.674 (2030).

## Aggregate effect on the cached tax units (verified by running)

`data/artifacts/tax_units_cache.parquet` (39,996 units, weighted income $61.7B at base), `age_filers_with_components`, one component swapped at a time from the live vintage, % change in aggregate aged income:

| Swap from live to | TY2025 | TY2027 |
|---|---|---|
| all components, raw 2026-02 | +2.73% | +3.19% |
| all components, per-person | +0.70% | +0.50% |
| wages raw / per-person | +0.95 / -1.70 | +0.79 / -2.51 |
| interest raw / per-person | +1.35 / +1.01 | +2.30 / +1.76 |
| proprietors raw / per-person | +0.12 / -0.10 | +0.13 / -0.18 |
| retirement CPI (as in 2026-02) / per-65+ | +0.19 / +1.44 | -0.16 / +1.39 |
| other raw / per-person | +0.12 / +0.05 | +0.14 / +0.04 |

Qualified dividends swap moves 0.00%: the cache carries no separate dividends income the aging picks up (likely folded into `other`/interest shares), so the dividends row is a no-op in this test. Capital gains are identical across the swaps.

## Limits

- Model weights are held fixed (ACS weights), so the per-unit growth is the right concept: national totals grow with headcount that the units do not. I did not check whether the poverty path reweights units by age, which would change which retirement denominator is right.
- Number of returns is a filer count, not a recipient count. Interest, dividends, business income are held by a minority; per-return understates per-recipient growth if recipient counts grow slower than returns, and says nothing about distribution (CBO's growth is dollar-weighted and concentrated at the top; applying it uniformly to every unit is the larger error, not the denominator).
- Retirement per 65+ is an upper bound for the existing retiree: it includes cohort turnover (new retirees have higher lifetime earnings), the unindexed taxation thresholds (bracket creep, which a fixed-unit model does capture only if it applies it separately), and the SECURE-era RMD/IRA surge. CPI is the lower bound (pure COLA). Raw taxable pensions+IRA jumps 1,522 to 1,819 (+19%) in 2025 alone, which is a CBO model/legislative event, not recipient growth.
- Wages per nonfarm job (+11.7% by 2025) reflects average wage growth; ECI private wages would be the cleaner price index but is not a per-worker earnings measure. Hawaii wage divergence is handled separately by the Hawaii factors.
- Census 2023 age projections are not CBO's; SSA beneficiary counts could not be fetched, so no beneficiary-based denominator.
- 2022 denominator levels (CBO) are backed out of rounded percent changes: error under 0.01 pt.

## Recommendation per component

| Component | Use | Why |
|---|---|---|
| wages | per-person (per nonfarm job) | raw total includes ~4.5% job growth that the fixed-weight units do not have; the swap is the largest mover (-1.7% to -2.5% of aggregate income vs live, since raw 2026-02 wages are close to live). Hawaii factors still apply on top. |
| interest | per-return, lower confidence | raw 2026-02 is +39% by 2025 vs live +18%, mostly rates and asset growth; dividing by returns trims only ~4 pts. Biggest unresolved item: the level is CBO's national interest surge, and the share of Hawaii units that hold it is unknown. Consider the live line until a rates-based check. |
| dividends | per-return | trivial (no-op in the cache) |
| capital gains | keep national (already adopted) | realizations, not a per-person flow; headcount is second order |
| proprietors | per-return, small | ~0.1-0.2% of aggregate either way |
| retirement | keep CPI for COLA-only; if the review wants thresholds and cohort turnover, take per-65+ capped, not national totals | per-65+ adds +1.4% of aggregate income (largest upward mover) and is an upper bound; CPI is the lower bound; the truth is in between, and needs SSA average-benefit data to pin down (not obtainable here) |
| other | per-return | small |

Adopting the per-person file in full would put aggregate income +0.7% (TY2025) / +0.5% (TY2027) above live, versus +2.7% / +3.2% for raw 2026-02 totals. The per-person file is a candidate only: do not move a default without the poverty-rate review the README calls for.

## Verdict

Adopt with caveats, as a per-component choice rather than one switch: wages (per job) and dividends/other/proprietors (per return) are low-risk and strictly better than raw national totals. Interest and retirement are unresolved: they need denominators this test could not fetch (SSA average benefit and beneficiary counts; recipient counts for interest) and a decision on the retirement range.
