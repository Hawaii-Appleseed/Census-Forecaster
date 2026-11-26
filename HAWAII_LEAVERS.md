# Who leaves Hawaiʻi

People who moved out of Hawaiʻi, compared with people who moved in and people who stayed. The comparison uses the ACS 2020–2024 5-year PUMS, and each group is scored under Hawaiʻi income tax law. It also uses the IRS migration files by AGI class, 2015–2024. It is aimed at the tax-flight argument against raising taxes on high earners.

- **Site page:** `site/hawaii-leavers/`, built by `build_leavers` in `scripts/build_site.py`.
- **Not on the chain:** `publish.py` does not rerun it.
- **Code:** `scripts/fetch_hawaii_leavers.py`, `scripts/fetch_irs_migration.py` and `analyses/hawaii_leavers.py`.
- **Outputs:** written to `runs/hawaii_leavers/` and published to `site/data/hawaii-leavers/`.

**Refreshing.** To pick up a new ACS 5-year vintage:

1. Download the national `csv_pus.zip` and `csv_hus.zip`.
2. Run `scripts/fetch_hawaii_leavers.py`.

To pick up a new IRS year:

1. Add the pair to `YEARS` in `scripts/fetch_irs_migration.py`.
2. Run it with `uv run --with xlrd`.

After either refresh:

1. Run `python analyses/hawaii_leavers.py` and commit.
2. Run `python scripts/build_site.py --import-runs hawaii-leavers`.

## Data and groups

Groups are set by where each person lived a year before the survey (`MIG`, `MIGSP`):

| Group | Lives now | Lived a year ago | Source |
|---|---|---|---|
| Stayers | Hawaiʻi | Hawaiʻi | Hawaiʻi state file |
| Movers in | Hawaiʻi | another state | Hawaiʻi state file |
| Leavers | another state | Hawaiʻi | national files (`csv_pus.zip` and `csv_hus.zip`, 3.2 GB). The fetch keeps every household with a member whose `MIGSP` is 15: 1,836 households, 4,545 persons, 2,880 leavers. |

The headline groups are civilians in housing units. Three kinds of people are handled differently:

- **Armed-forces households** (any member with `ESR` 4 or 5) are dropped. Their moves are orders, and they make up about a quarter of raw leavers.
- **Group quarters** are dropped. These are mostly students and barracks.
- **Veterans** (`MIL` 2) stay in. Recently discharged service members cannot be told apart from other veterans, so a sensitivity case drops every veteran household instead.

Units of analysis:

- Person statistics cover adults 18 and over.
- Tax units come from `build_raw_units`. Each unit is assigned to a group by its primary filer.

Weights and measures:

- **Weights** are the raw Census weights. The 5-year weights average one year, so sums are per year.
- **DOTAX filing-status factors** are set to 1.0. They are Hawaiʻi calibrations and would distort out-of-state units.
- **Standard errors** use the Census successive-difference replicate formula over 80 replicate weights. Ranges below are 90% margins.
- **Dollar values** are 2024 dollars (`ADJINC`).
- **Poverty** is the official measure (`POVPIP`), which uses the same federal line in every state.

## Results (adults 18+, civilian, per year)

| | Stayers | Movers in | Leavers |
|---|---|---|---|
| Adults | 1.0M | 27,400 ± 1,800 | 32,700 ± 2,100 |
| Median personal income | $41,800 | $41,800 ± 5,500 | $33,500 ± 2,800 |
| Mean income, age-standardized to stayers | $59,000 | $70,700 ± 5,200 | $61,500 ± 7,800 |
| Income $200k+ | 3.5% | 5.1% ± 1.2 | 3.7% ± 1.3 |
| Below poverty | 9.3% | 15.0% ± 2.7 | 14.3% ± 2.1 |
| Below poverty, age-standardized | 9.3% | 13.5% ± 3.0 | 11.3% ± 2.0 |
| Median age | 51 | 37 | 35 |
| Born in Hawaiʻi | 51% | 22% | 13% |
| Native Hawaiian (alone or in combination) | 21% | 8% | 6% |
| Veterans | 8% | 9% | 15% |

Tax units, each scored as if it lived in Hawaiʻi:

| | Stayers | Movers in | Leavers |
|---|---|---|---|
| Tax units | 0.7M | 20,300 ± 1,400 | 23,800 ± 1,700 |
| Median unit income | $52,900 | $51,000 ± 4,900 | $42,000 ± 3,000 |
| HI income tax, current law (TY2026) | $2.89B | $91.5M ± 11.8 | $89.7M ± 19.9 |
| HI income tax, Act 24 (TY2027) | $2.59B | $82.9M ± 11.2 | $80.6M ± 19.2 |
| Tax per unit, current law | $4,030 | $4,500 ± 530 | $3,770 ± 830 |
| Unit records at $500k+ | 465 | 13 | 6 |
| Unit records at $1M+ | 17 | 0 | 0 |

Under current law, leaver tax minus mover-in tax is **−$1.8M** a year. This is well inside the margins, so the two flows carry about the same tax base.

Leavers' top destinations are California (5,600 adults a year), Texas, Washington, Nevada, Florida and Arizona.

**Born in Hawaiʻi.** About 4,400 adults born in Hawaiʻi leave each year (177 records):

- They are young, with a median age of 30.
- Their median income is $30,600 ± 2,100.
- 15.8% are below poverty.
- Born-in-Hawaiʻi stayers, for comparison: median income $43,000 and 8.2% below poverty.

**Sensitivity cases.**

- *Without veteran households:* leavers' median income is $30,600. Leaver tax minus mover-in tax is −$12.8M ± ~22M.
- *Without survey year 2020:* leavers' median income is $34,600, and the difference is −$8.1M.

Neither case changes the direction of the results.

## IRS migration by AGI class

Source: the IRS SOI gross migration files by size of AGI (`<yy><yy>inmigall`), filing-year pairs 2015–16 through 2023–24. `scripts/fetch_irs_migration.py` writes their Hawaiʻi rows to `data/tax_modeler/irs_soi/migration_hawaii_by_agi.csv`, which is tracked. The pre-2020 files are `.xls`, so the fetch runs as `uv run --with xlrd`.

- **Income year.** Each workbook reports AGI for both years. Out-migrants' year-1 AGI is the income they reported while still filing from Hawaiʻi, which avoids the ACS post-move problem.
- **Income class.** The workbooks do not say which year's AGI sets the income class. For $200k+ out-migrants, AGI per return is about $300k in year 1 and about $430k in year 2. That pattern is consistent with classing on year-2 AGI.
- **Leaving rate.** Out-migrants divided by the Hawaiʻi year-1 matched base: non-migrants plus same-state movers plus out-migrants.

| Filing years | $200k+ out | $200k+ in | Net in | Leaving rate, $200k+ | Leaving rate, all filers |
|---|---|---|---|---|---|
| 2015–16 | 710 | 823 | +113 | 3.3% | 5.0% |
| 2016–17 | 1,022 | 1,202 | +180 | 4.5% | 6.9% |
| 2017–18 | 870 | 1,005 | +135 | 3.4% | 5.1% |
| 2018–19 | 922 | 1,021 | +99 | 3.3% | 4.9% |
| 2019–20 | 963 | 1,388 | +425 | 3.2% | 4.9% |
| 2020–21 | 1,217 | 1,707 | +490 | 4.1% | 5.1% |
| 2021–22 | 1,672 | 2,027 | +355 | 4.2% | 5.0% |
| 2022–23 | 1,561 | 1,714 | +153 | 3.7% | 4.8% |
| 2023–24 | 1,682 | 1,689 | +7 | 3.7% | 4.6% |

- **More $200k+ filers arrive than leave in every year.** That is 1,957 net over the nine years. The latest year, 2023–24, was close to even at +7, and the page says so.
- **They leave at a lower rate than filers overall.** The averages are about 3.7 percent against 5.1 percent.
- **Across all incomes, more filers leave than arrive,** about 3,500 a year net (2016–17 included). The net outflow is below $200k.
- **2016–17 is an outlier.** Out-migration that year spikes for every income class (39,448 returns against about 29,000 in other years). Treat it as an IRS matching artifact, not a real exodus. The pattern holds with or without it.

The ACS shows the same pattern, with more tax units leaving than arriving: 1.17 to 1 in the ACS against 1.10 to 1.13 to 1 in the IRS files.

## What it supports

- **Leavers are not disproportionately rich.**
  - Their median income is lower than stayers'.
  - The gap is mostly age. Standardized for age, leavers look like stayers.
  - None of the 1,012 leaver tax-unit records is at $1M or more.
- **Arrivals replace the tax base that leaves.**
  - Movers in owe as much Hawaiʻi income tax as leavers would have.
  - The IRS shows arrivals with higher AGI per return.
- **Most of the flow is churn.** Only 13% of leavers were born in Hawaiʻi. Most are people who came and then left.
  - The born-in-Hawaiʻi leavers are a small, young, lower-income group.
  - That is a cost-of-living story, not a tax story.

- **High earners arrive faster than they leave** in all nine IRS years, and they leave at a lower rate than other filers.

Possible headline: *"The people leaving Hawaiʻi aren't the rich. More $200,000 earners moved in than out every year from 2015 to 2024, and the people who do leave are young and earn less than those who stay."*

## Caveats

1. **Leaver income is mostly earned after the move.** The 12-month income window mixes Hawaiʻi and destination earnings. "Tax as if they stayed" describes the tax base that left; it is not a revenue loss.
2. **The ACS cannot settle the millionaire question.**
   - The ACS records only 17 stayer units at $1M or more, while DOTAX counts 1,824 resident filers. Top incomes are also top-coded.
   - Zero leaver records means millionaires are rare among leavers. Using the rule of three, the bound is under about 70 units a year, and it is soft given how badly the ACS undercounts the top.
   - A claim about millionaires should rest on DOTAX or IRS data by income class. SOI's top migration class is $200k+, and that class is covered above.
3. **Recently discharged service members** probably remain among the civilian leavers, since the veteran share is 15%. The no-veteran case bounds this.
4. **2020 is in the window.** Dropping that year changes nothing material.
5. **Mixed households.** A leaver's household mates may not have lived in Hawaiʻi. Units follow the primary filer.

## Next

- Optionally, add an occupation and education cut for the "who leaves" story.
- Once the IRS publishes 2027–28, it will show the first year under Act 24's brackets.
