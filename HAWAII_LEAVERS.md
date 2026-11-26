# Who leaves Hawaiʻi

People who moved out of Hawaiʻi, compared with people who moved in and people who stayed. The comparison uses the ACS 2020–2024 5-year PUMS, and each group is scored under Hawaiʻi income tax law. It is aimed at the tax-flight argument against Act 24. Status: first results on `feature/hawaii-leavers`. It is not on the chain and has no site page yet.

- Code: `scripts/fetch_hawaii_leavers.py` pulls the data and `analyses/hawaii_leavers.py` runs the analysis.
- Output: `reports/hawaii_leavers/results.json`.

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

## IRS benchmark

From the IRS SOI state-to-state migration files (filers only, nominal dollars):

| Filing years | Returns out | AGI per return out | Returns in | AGI per return in |
|---|---|---|---|---|
| 2021→22 | 28,636 | $76,955 | 25,286 | $100,915 |
| 2022→23 | 27,623 | $70,863 | 25,026 | $78,083 |

The IRS shows the same pattern as the ACS. More households leave than arrive, 1.10 to 1.13 to 1 in the IRS against 1.17 to 1 in ACS tax units. The households that arrive have higher incomes than the ones that leave.

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

Possible headline: *"The people leaving Hawaiʻi aren't the rich. They're young, they earn less than the people who stay, and the people moving in more than replace their taxes."*

## Caveats

1. **Leaver income is mostly earned after the move.** The 12-month income window mixes Hawaiʻi and destination earnings. "Tax as if they stayed" describes the tax base that left; it is not a revenue loss.
2. **The ACS cannot settle the millionaire question.**
   - The ACS records only 17 stayer units at $1M or more, while DOTAX counts 1,824 resident filers. Top incomes are also top-coded.
   - Zero leaver records means millionaires are rare among leavers. Using the rule of three, the bound is under about 70 units a year, and it is soft given how badly the ACS undercounts the top.
   - A claim about millionaires should rest on DOTAX or IRS data by income class: SOI publishes migration by AGI class only for the top bracket ($200k+).
3. **Recently discharged service members** probably remain among the civilian leavers, since the veteran share is 15%. The no-veteran case bounds this.
4. **2020 is in the window.** Dropping that year changes nothing material.
5. **Mixed households.** A leaver's household mates may not have lived in Hawaiʻi. Units follow the primary filer.

## Next

- Get IRS SOI migration by AGI class (the `$200k+` rows) to address the top end directly.
- Build a site page in the pattern of `hr1-snap-medicaid`.
- Optionally, add an occupation and education cut for the "who leaves" story.
