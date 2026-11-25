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

## Comparison with PR #110

Added after the fact. Data: `data/tax_modeler/cbo/per_person_vs_pr110_2026-02.csv` (2022-2030, every component; CBO-level and Hawaii-factor-adjusted growth, plus the pre-#110 line).

### What the test's "Live" and "Raw 2026-02" columns were

This branch's parent is 02fc884 (#110), so the branch already contains #110. `cbo_components_2026-02.csv` is byte-identical to origin/main's, and `cbo_aging.DEFAULT_VINTAGE` is `"2026-02"` on origin/main. So the test's **"Raw 2026-02" column is #110's adopted file**, and its **"Live" column is the pre-#110 default** (2025-01-cg2026-02, live 2026-11-24 to 2026-11-25). The test's own note that "Raw" already has retirement on CPI and proprietors on NIPA is #110. The test never compared against #110 as live; its swap percentages ("all components raw: +2.73% / +3.19%") are the size of #110 versus the old default, and its per-person percentages are per-person versus the old default, not versus #110. Per-person versus #110 is the table below.

### Growth, 2022 = 1.00 (CBO national line before the Hawaii factor)

| Component | #110 rule (file) | Test rule | 2025 #110 / test | 2027 #110 / test | 2030 #110 / test | Test vs #110 (2025 / 2027 / 2030) |
|---|---|---|---|---|---|---|
| wages | CBO salaries and wages, national total (`build_cbo_components.py` COMPONENT_LINES), Hawaii factor 0.85 | per nonfarm job | 1.167 / 1.117 | 1.268 / 1.200 | 1.428 / 1.339 | -4.3% / -5.4% / -6.2% |
| interest + ordinary dividends | CBO line, national total, factor 1.00 | per return | 1.392 / 1.336 | 1.640 / 1.549 | 1.988 / 1.839 | -4.0% / -5.6% / -7.5% |
| qualified dividends | CBO line, national total, factor 1.00 | per return | 1.163 / 1.117 | 1.239 / 1.170 | 1.294 / 1.197 | -4.0% / -5.6% / -7.5% |
| proprietors | nonfarm proprietors' income (Economic Projections) after 2023, factor 0.90 | per return | 1.195 / 1.147 | 1.314 / 1.241 | 1.505 / 1.393 | -4.0% / -5.6% / -7.5% |
| retirement | prior-year CPI-U, per person, factor 1.00 | per 65+ (Census 2023 projections) | 1.158 / 1.249 | 1.225 / 1.346 | 1.315 / 1.359 | +7.9% / +9.9% / +3.4% |
| capital gains | CBO realizations (national), factor 0.85 | unchanged | 1.275 | 1.279 | 1.199 | 0 |
| other | CBO total income, factor 0.90 | per return | 1.203 / 1.155 | 1.316 / 1.243 | 1.454 / 1.345 | -4.0% / -5.6% / -7.5% |

With the Hawaii factor applied (`net = 1 + factor * (cbo - 1)`, the form the model uses), wages are 1.142 (#110) vs 1.099 (test) in 2025, 1.228 vs 1.170 in 2027, 1.364 vs 1.288 in 2030, i.e. -3.8% / -4.7% / -5.5%. The pre-#110 default was 1.127 / 1.214 / 1.361 on that basis.

### Component verdicts

- **Wages.** The test's per-job division is not a reason to revisit #110. The README's caveats section (written in #110) already says Hawaii factors were fitted to Hawaii's total income growth and Hawaii's headcount barely grows, so national total x Hawaii factor is about per person, and dividing by the national job count as well removes population growth twice. The test's -1.7% / -2.5% aggregate effect was measured with the 0.85 factor still applied (`age_filers_with_components` defaults), so it stacks both corrections. #110's wages already sit close to the old line (net 1.142 vs 1.127 in 2025; 1.364 vs 1.361 in 2030). If wages are revisited it should be by refitting the factor against a per-job national series, not by adding a denominator.
- **Interest + ordinary dividends.** The test gives grounds to revisit, but not through its denominator. Per-return trims 4 to 7.5% of a 39% (2025) / 99% (2030) national rise, while the pre-#110 line was 17.6% / 35.7%. Factor 1.00 ("federal-rate-driven") means the full CBO total lands on every unit; per-return is the same double count as wages for the headcount part. The test itself rated this low confidence and said to consider the old line until a rates-based check. Interest is the largest single swap (+1.35% / +2.30% of aggregate income for raw, +1.01% / +1.76% per-person vs pre-#110). The unresolved question is the level, which the test could not test (no recipient counts, no Hawaii interest-bearing-asset data).
- **Qualified dividends.** Differences are the same -4 to -7.5%, but the test found the swap moves aggregate income 0.00% (cache carries no separate dividend income that the aging picks up), so nothing to act on. Note #110 moved this line from 1.149 / 1.255 / 1.432 to 1.163 / 1.239 / 1.294 (2025 / 2027 / 2030).
- **Proprietors.** -3.7% to -7% effective, 0.1 to 0.2% of aggregate income either way. No reason to revisit; #110's NIPA-based rule already removes the bonus-depreciation dip the test's per-return version would inherit.
- **Retirement.** The test leaves CPI vs per-65+ unresolved, and this comparison does not resolve it. CPI (#110) is the lower bound, per-65+ the upper: +7.9% (2025) to +9.9% (2027) higher, and +1.4% of aggregate income. The 65+ denominator is Census 2023 projections, national, and not Hawaii's; per-65+ also folds cohort turnover and bracket creep into every existing retiree, which #110 documents as having cut baseline poverty 0.4 points when raw totals were used. The test's own range (CPI to per-65+ is 1.158 to 1.249 in 2025) brackets #110 at the bottom. No new evidence favors moving off CPI; the missing evidence is SSA average benefit and whether the poverty path reweights by age, both outstanding.
- **Capital gains.** Identical (both 2026-02 CBO realizations, Hawaii factor 0.85). Nothing to compare.
- **Other.** -3.7% to -7%, small aggregate effect (+0.05% per-person vs +0.12% raw). No reason to revisit.

### Bottom line

The test's per-person candidate is an alternative to the pre-#110 line and to raw totals; against #110 it gives a reason to revisit only interest (level, not denominator). Its per-return and per-job divisions on wages, proprietors and other repeat what the Hawaii factors already do; its retirement range is unchanged by #110. Nothing here changes a default.
