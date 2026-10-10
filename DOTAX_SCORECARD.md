# DOTAX scorecard — the TY2022-anchored model against TY2023

**Date:** 2026-10-09
**Model scored:** commit `48761b8` (the last commit before the TY2023 rebase, parent of `e043510`), every DOTAX anchor on the TY2022 edition.
**Actuals:** DOTAX *Hawaiʻi Individual Income Tax Statistics, Tax Year 2023* (`packages/tax_modeler/src/tax_modeler/data/calibration/dotax_indinc_2023.json`, parsed from `2023indinc.pdf`).
**Outputs:** `reports/dotax_scorecard/TY2023/` (markdown and one CSV per table and scenario), from `scripts/dotax_scorecard.py` run in a worktree of `48761b8`.

This is the first forecast-vs-actual check the Act 24 model has had. Every earlier validation compared the model with the edition it was calibrated to.

## Method

1. In a worktree of `48761b8`, `scripts/build_calibrated_base.py` (the cache and calibration steps of `forecast_sb3125.py` / `forecast_sb3125_enhanced.py`, nothing else): PUMS 2020–24 tax units, enrich, base tax at TY2023 law, IPF rake to the TY2022 Table A-8 filer counts and tax, Table 4 filing status and the SOI AGI targets, re-score. 643,964 raked filers, $2,433M `hi_tax_liability` before the synthetic tail.
2. `scripts/dotax_scorecard.py --base-edition-year 2022 --scenarios MID,LOW,HIGH`: the Act 24 population exactly as `forecast_sb3125_enhanced.py` builds it (`act24_population.build_units`: Pareto tail at the scenario's α, `tail_k` scaled to the TY2022 $1M+ tax of $663M, aged to the PUMS 2024 dollar year with the scenario's premium; `project_units` to TY2023; MID's DOTAX capital-gains anchor factors), scored under TY2023 law (the 2018 schedule, `TaxSystemRegistry.get_2017_system`) with the statutory §235-51(f) alternative tax, through the same `TaxCalculator.unit_liabilities` kernel the pipeline's Act 46 baseline uses.
3. Classes are assigned on the scoring `income` column at the printed A-8 boundaries (Loss = income < 0). Table A-1's taxable returns are the units with positive tax before credits. Filing status is the unit's. The $1M+ tax error is split into a count part (count error × the actual average tax) and a per-filer part (the model's count × the average-tax error); the two sum to the error.

Two things about "projecting to TY2023" from this model. The PUMS incomes are in 2024 dollars, so the county step is Honolulu B19013(2023)/B19013(2024) = 0.980, a small step back, and the top-income premium (base year 2024) does not apply — but the tail aging has already put two years of the premium (2022→2024) into the tail, and the step back does not remove them. So the model's TY2023 $1M+ tail carries 2022→2023 B19013 growth (×1.068) and two years of premium (×1.020 at MID): ×1.089 of income. That is what the pipeline does; the scorecard reports it rather than correcting it.

## Results (MID; LOW and HIGH differ only in the $1M+ class, below)

### Table A-8 — resident returns and tax before credits by Hawaiʻi AGI class

| Class | Returns model | Returns actual | Err % | Tax model $M | Tax actual $M | Err $M | Err % |
|:---|---:|---:|---:|---:|---:|---:|---:|
| Loss | 1,108 | 13,506 | −91.8 | 0.0 | 0.0 | 0.0 | |
| $0–10K | 107,174 | 110,447 | −3.0 | 1.4 | 3.0 | −1.6 | −53.5 |
| $10–20K | 59,646 | 63,776 | −6.5 | 18.4 | 21.0 | −2.6 | −12.2 |
| $20–30K | 53,906 | 56,358 | −4.4 | 50.0 | 50.0 | 0.0 | 0.0 |
| $30–40K | 65,583 | 58,625 | +11.9 | 97.8 | 90.0 | +7.8 | +8.7 |
| $40–50K | 52,250 | 54,823 | −4.7 | 113.6 | 120.0 | −6.4 | −5.3 |
| $50–75K | 100,905 | 96,433 | +4.6 | 323.3 | 311.0 | +12.3 | +4.0 |
| $75–100K | 63,065 | 57,836 | +9.0 | 287.1 | 277.0 | +10.1 | +3.6 |
| $100–150K | 71,475 | 65,585 | +9.0 | 480.9 | 467.0 | +13.9 | +3.0 |
| $150–200K | 33,283 | 30,326 | +9.8 | 322.1 | 319.0 | +3.1 | +1.0 |
| $200–300K | 21,268 | 21,375 | −0.5 | 306.2 | 349.0 | −42.8 | −12.3 |
| $300–400K | 6,998 | 6,607 | +5.9 | 148.8 | 166.0 | −17.2 | −10.4 |
| $400–500K | 2,893 | 2,952 | −2.0 | 98.1 | 103.0 | −4.9 | −4.8 |
| $500–750K | 2,549 | 3,120 | −18.3 | 102.8 | 157.0 | −54.2 | −34.5 |
| $750K–1M | 1,145 | 1,158 | −1.1 | 79.4 | 87.0 | −7.6 | −8.7 |
| **$1M+** | **1,824** | **1,704** | **+7.0** | **763.2** | **441.0** | **+322.2** | **+73.1** |
| Total | 645,072 | 644,631 | +0.1 | 3,193.1 | 2,960.0 | +233.1 | +7.9 |
| Total below $1M | 643,248 | 642,927 | +0.0 | 2,429.9 | 2,519.0 | −89.1 | −3.5 |

### Table A-1 — AGI of taxable returns by class

| Class | Returns model | Returns actual | Err % | AGI model $M | AGI actual $M | Err % |
|:---|---:|---:|---:|---:|---:|---:|
| $0–10K | 33,833 | 35,364 | −4.3 | 257 | 231 | +11.3 |
| $10–20K | 59,333 | 55,687 | +6.5 | 871 | 831 | +4.8 |
| $20–30K | 53,906 | 53,308 | +1.1 | 1,354 | 1,334 | +1.5 |
| $30–40K | 65,583 | 57,213 | +14.6 | 2,279 | 2,004 | +13.7 |
| $40–50K | 52,250 | 54,078 | −3.4 | 2,339 | 2,426 | −3.6 |
| $50–75K | 100,905 | 95,675 | +5.5 | 6,205 | 5,868 | +5.7 |
| $75–100K | 63,065 | 57,507 | +9.7 | 5,471 | 4,983 | +9.8 |
| $100–150K | 71,475 | 65,370 | +9.3 | 8,729 | 7,963 | +9.6 |
| $150–200K | 33,283 | 30,251 | +10.0 | 5,722 | 5,202 | +10.0 |
| $200–300K | 21,268 | 21,328 | −0.3 | 5,085 | 5,090 | −0.1 |
| $300–400K | 6,998 | 6,596 | +6.1 | 2,390 | 2,258 | +5.8 |
| $400K+ | 8,411 | 8,917 | −5.7 | 12,424 | 9,319 | +33.3 |
| Total | 570,309 | 541,294 | +5.4 | 53,124 | 47,510 | +11.8 |

### Table 4 — resident returns by filing status

| Status | Model | Actual | Model share | Actual share | Err % |
|:---|---:|---:|---:|---:|---:|
| Single | 330,081 | 341,973 | 51.2% | 53.0% | −3.5 |
| Married filing jointly | 232,176 | 216,407 | 36.0% | 33.6% | +7.3 |
| Head of household | 67,189 | 69,345 | 10.4% | 10.8% | −3.1 |
| Married filing separately | 15,626 | 16,718 | 2.4% | 2.6% | −6.5 |
| Qualifying widow(er) | 0 | 188 | 0.0% | 0.0% | −100.0 |
| Total | 645,072 | 644,631 | | | +0.1 |

### The $1M+ class, by scenario

| | LOW (α 1.7, +0.3%/yr) | **MID (α 1.5, +1.0%/yr)** | HIGH (α 1.4, +2.3%/yr) | Actual |
|:---|---:|---:|---:|---:|
| Returns | 1,824 | **1,824** | 1,824 | 1,704 |
| Tax before credits | $760.5M | **$763.2M** | $797.4M | $441.0M |
| Error | +72.5% | **+73.1%** | +80.8% | |
| Average tax | $416,952 | **$418,412** | $437,163 | $258,803 |
| Count part of the error | +$31.1M | **+$31.1M** | +$31.1M | |
| Per-filer part | +$288.5M | **+$291.1M** | +$325.3M | |
| In logs: total = count + per filer | 0.545 = 0.068 + 0.477 | **0.548 = 0.068 + 0.480** | 0.592 = 0.068 + 0.524 | |
| tail_k | 1.7743 | 1.4210 | 1.2343 | |

**Decomposition.** Of MID's +$322M, the filer count explains $31M (10%) and the tax per filer $291M (90%). The count is the TY2022 target carried forward unchanged (1,824; TY2023 printed 1,704, −6.6%). The per-filer tax is 62% too high: the model's TY2023 average is $418K against $259K printed, and against TY2022's own $363K ($663M / 1,824). Realized per-filer tax fell 29% in one year; the model's rose 15%.

## What the dials implied for one year, against what happened

The $1M+ class went $663M → $441M (−33.5%) on 1,824 → 1,704 returns. The model, with every top-growth dial stacked, went $663M → $763M (+15.1%). Each dial's one-year contribution, TY2022 → TY2023:

| Dial | Setting at `48761b8` | Implied TY2022→23 | Realized | Verdict |
|:---|:---|---:|---:|:---|
| Honolulu B19013 (county growth; observed, drives the tail aging and the projection) | ACS 1-year, $96,580 → $103,131 | ×1.068 income | $1M+ average AGI fell (A-1 $400K+: $9,319M on 8,917 returns; model $12,424M on 8,411) | Median growth is the wrong carrier for the top class in a year the top falls. Not a tunable dial, but the aging rule that applies it to the tail is. |
| Top-income premium above $500K, +1.0%/yr MID (IRS SOI 1.8pp − 0.8pp migration haircut) | 2022→2024 baked into the tail (×1.020), not removed by the 2024→2023 step | ×1.020 (MID), ×1.006 (LOW), ×1.047 (HIGH) | per-filer −29% | Wrong sign for this year and immaterial to the miss: premium 0 would leave the class +70%. The 0.8pp migration haircut is a one-point adjustment to a dial that is itself one point; neither matters at this horizon. |
| CBO capital-gains factor × Hawaiʻi 0.85 (`cg_anchor.cg_growth`, Table 21 resident NLTCG grown from TY2022) | CBO Jan 2025 1.085 → net 1.072 | ×1.072 gains | Table 21 resident NLTCG $2,995M → $2,646M (−11.7%); $400K+ class $2,210M → $2,041M (−7.6%) | Wrong sign; the 0.85 haircut on the increment cannot turn CBO's +8.5% into Hawaiʻi's −12%. One year is one year, but the anchor's growth should come from a Hawaiʻi series when one exists (Table 21 now has TY2018–2023). |
| Pareto α (1.7 / 1.5 / 1.4) | shape of the tail above $1M | $760M / $763M / $797M | | α moves the class's tax by 5% across the whole range; `tail_k` scales whichever shape to the one-year $663M anchor. α is not where the miss lives. |
| The $1M+ tax anchor itself: one edition, TY2022 $663M | `calibrate_synthetic_tail_to_tax_target` | ×1.00 (the level) | $441M | This is the miss. The class's tax has no persistence year to year ($413M, $415M, $799M, $663M, $441M, TY2019–23); anchoring on one year puts the whole series' spread into the forecast. The rebase (`e043510`, `493ef76`) anchors on the TY2019–2023 window: 1,633 returns, $562.3M, which would still have been +27% against TY2023 printed but −15% against TY2022 — a smaller miss in either direction than any single year. |

Honest accounting of the +15.1%: the tail's income is ×1.089 (B19013 ×1.068, premium ×1.020), which on a near-flat 11% top rate is about +9% of tax; the remaining ~5–6 points are the gap between the kernel `tail_k` is fitted on (`hawaii_calculator`, the stacked gains shortcut, `hi_tax_liability`) and the kernel the pipeline scores with (`TaxCalculator.unit_liabilities`, statute alternative tax, on the DOTAX-anchored gains). That gap is in every published Act 46 baseline too; the scorecard shows it rather than hides it.

## Below $1M: what else the edition says

- **Everything below $1M is −3.5% in tax on the right count** (643,248 vs 642,927 returns; $2,430M vs $2,519M). The model is a good forecast of the resident base outside the top class; the top class is the forecast problem.
- **$500–750K: returns −18%, tax −35%.** The raked count (2,549) is the hand-typed TY2022 target that `dotax_base.py` has since found wrong (the TY2022 edition printed 2,991); TY2023 printed 3,120. $54M of the $89M shortfall below $1M is this one class, and most of it is a typo in the old target, not a projection error.
- **$200–300K and $300–400K: counts right, tax −12% / −10%.** Per-filer tax is low at $200–400K: either deductions are too generous or too much income is on the gains cap there. The $150–200K class is +1%, so the break is at $200K.
- **$30–40K: returns +12%, AGI +14%.** The TY2022 count for this class was hand-typed as 58,135 against 59,827 printed, so this is not the typo; the PUMS mass at $30–40K is high relative to DOTAX in both years.
- **Loss returns: 1,108 vs 13,506.** PUMS income rarely goes negative; the model has no business-loss returns to speak of. Their tax is zero, so this is a count error only, but it is 2% of returns.
- **Filing status: MFJ +7.3%, Single −3.5%.** The raked status mix is TY2022's (MFJ 34.1% hand-typed); TY2023's joint share is 33.6%. The model's 36.0% is above both, so the rake did not hold the status margin against the AGI margins.
- **Table A-1 total AGI +11.8%, of which $400K+ is +33%.** Below $400K AGI is +6.5%; the model's taxable-return count is +5.4%, so per-return AGI is about right below $400K and a third too high above it.

## Standing step

`scripts/dotax_scorecard.py --base-edition-year N` scores the base raked to edition N against `dotax_indinc_<N+1>.json` with these tables, into `reports/dotax_scorecard/TY<N+1>/`. Run it when `scripts/parse_dotax_indinc.py` adds an edition, before rebasing on it, and keep the output with the rebase commit. For the current base (TY2023, `dotax_base.BASE_YEAR`) the next run is against TY2024, when DOTAX publishes it; the target year then equals the PUMS dollar year, so the county step is ×1.000 and the scorecard reads as a test of the calibration and the window anchor alone.
