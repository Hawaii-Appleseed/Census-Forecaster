# State tax revenue, every line — Scope

**Status (2026-11-27): phases 1–4 built** (the ledger; the baseline and its backtest against the
Council on Revenues; the general excise tax in full; the transient accommodations and excise
scorers, below). Phase 5 is scope.

Goal: model all of Hawaiʻi's state tax collections, not only the individual income tax, so
that a baseline forecast covers every dollar and a change to any tax the repository can model
is scored against the same baseline. State taxes only: county real property tax is not
collected by DOTAX and would be its own parcel-based build.

## Bottom line

- **A baseline forecast of every line is feasible.** `census_forecaster` already refreshes the
  drivers (visitors, permits, labor, prices, DOTAX monthly), and the ledger below gives each
  line's history back to FY2004.
- **Scoring a change to the law is feasible for 87% of collections** (FY2025): the two lines
  with a model of who pays (29.5%) and the base-times-rate lines (57.6%).
- **The rest (12.8%) is forecast only:** estate, corporate income, unemployment contributions,
  insurance premiums, public service companies, banks. No usable base, or a few payers decide
  the year (estate collections were $406M in FY2025 and $56M the year before).
- **Phase 2's answer: the Council's line items are the baseline for the general fund.** A
  forecast from each line's history alone matches the Council on the general fund total (mean
  error 2.9% a year out, 4.7% two out, against the Council's 2.9% and 4.9%, outside COVID) but
  loses badly on the transient accommodations tax and on income tax two years out, where enacted
  law decides the number. So the published levels come from the Council where it itemises a line;
  the model covers what it does not (fuel, vehicle taxes, unemployment contributions, the
  non-general-fund parts) and is the check on the rest.
- **Phase 3's answer: who bears the general excise tax.** Counting the tax businesses pay on
  their inputs and pass on, residents bear 51% of the state tax, visitors 27%, investment 12%,
  government 6%, exports 4% (DBEDT's 2022 input-output table). A grocery exemption would cost
  the state about $305M in FY2025, 18% of it tax visitors pay now.
- **Phase 4: every base-times-rate line scores.** The transient accommodations tax, fuel and the
  barrel tax, cigarettes and the rental vehicle surcharge on their own bases and statutory rates;
  liquor and the rest proportionally. A point on the transient accommodations tax is about $88M
  in FY2027, 69% of it paid by visitors.

## The ledger (FY2025, DOTAX Annual Report Table 1.8)

| Line | $M | Share | Tier |
|---|---|---|---|
| General excise and use | 4,630 | 40.4% | base_rate |
| Individual income | 3,288 | 28.7% | structural |
| Transient accommodations tax | 816 | 7.1% | base_rate |
| County surcharge on GET | 565 | 4.9% | base_rate |
| Inheritance and estate | 406 | 3.5% | trend |
| Corporate income | 400 | 3.5% | trend |
| Employment security contributions | 281 | 2.5% | trend |
| Insurance premiums | 219 | 1.9% | trend |
| Fuel | 168 | 1.5% | base_rate |
| Public service companies | 154 | 1.3% | trend |
| Motor vehicle tax and fees | 145 | 1.3% | base_rate |
| Rental vehicle tax and fees | 118 | 1.0% | base_rate |
| Conveyance | 96 | 0.8% | structural |
| Tobacco and licenses | 80 | 0.7% | base_rate |
| Liquor and permits | 50 | 0.4% | base_rate |
| Environmental response | 27 | 0.2% | base_rate |
| Banks and financial corporations | 10 | 0.1% | trend |
| All others, transient accommodations fees | 1 | 0.0% | trend |
| **Total** | **11,456** | | |

Tiers (`state_revenue.lines`):

- **structural**: a model of who pays exists and scores a change line by line. Individual
  income (`tax_modeler`), conveyance (`tax_modeler.conveyance`).
- **base_rate**: a published base times a statutory rate. A rate change scores directly; a base
  change needs a driver (visitor nights, gallons, packs, registrations).
- **trend**: forecast from history and the economy; a change to the law is not scored.

How hard each is to forecast, by the spread of year-over-year growth FY2005–FY2025: general
excise 8%, fuel 8%, tobacco 7%, insurance premiums 10%, public service companies 12%, individual
income 14%, transient accommodations 54% (COVID), conveyance 58%, estate 162%, corporate income
451%.

## Architecture

A workspace package, `packages/state_revenue` (`census-state-revenue`).

- **Why its own package.** The ledger is aggregate, fiscal-year and cash-basis; `tax_modeler`
  is tax-unit, tax-year and liability-basis. Kept apart, nothing in the chain imports it, so no
  step's cache key (`common.fingerprint`, narrow by default) moves when it changes.
- **Direction of imports.** `state_revenue` may import `tax_modeler` and `census_forecaster`;
  neither imports it. Through phase 2 it imports neither. The GET incidence model (`tax_modeler.excise`) and the
  conveyance model stay where they are, because they need the household population and the
  band data. The ledger calls them.
- **Name.** Not `revenue`: `tax_modeler.revenue` is already the income-tax estimator.
- **Shape.** One catalogue of lines (`lines.py`) whose collections must add to DOTAX's total;
  `baseline.build_baseline()` gives every line by fiscal year with its source. Scorers
  (phases 3–5) will take a change and return each line's difference from that baseline.

## As built (phase 1, November 27, 2026)

| Piece | Where |
|---|---|
| Line catalogue: key, tier, statute, the code that models it, report labels | `packages/state_revenue/src/state_revenue/lines.py` |
| Annual collections by line, FY2004–FY2025, from 21 annual reports | `.../state_revenue/data/annual_collections.json` |
| Table parser (`pdftotext -layout` text) | `.../state_revenue/annual_report.py` |
| Fetch and merge | `.../state_revenue/scripts/refresh_annual_collections.py` |
| Ledger: series, tier coverage, folded lines | `.../state_revenue/ledger.py` |
| Monthly report: every tax line, not 13 of them | `census_forecaster/scripts/refresh_dotax_collections.py` (`TAX_LINE_KEYS`) |
| Tests | `tests/state_revenue/`, `tests/census_forecaster/test_refresh_dotax_collections.py` |

Checks: each year's lines add to the report's printed total within $9 thousand (rounding). Each
year comes from the newest report whose column adds up, so it carries DOTAX's revisions. The one
column that does not add up is the FY2008 report's FY2007 tobacco figure ($98.4M against $94.4M in
the FY2007 report, $4.0M off its own total), so FY2007 comes from the FY2007 report. In the monthly
report the 25 tax rows add to the TOTAL row exactly (December 2025 and 2024).

Rows that change over time (`lines.HOST`, `FiscalYear.folded`):

- Through FY2018 the fuel row includes the environmental response tax. The FY2014 report gives
  fuel $167.5M and the barrel tax $26.0M; together they are the $193.6M row.
- Through FY2018 motor vehicle includes rental vehicles.
- The county surcharge starts in FY2007 (Honolulu only until Kauaʻi in FY2019, Hawaiʻi County in
  FY2020, Maui in FY2024).
- DOTAX's FY2018 report says individual and corporate income tax for FY2017 on are not
  comparable to earlier years (a new compiling method).

Monthly history stays thin. Only the current fiscal year's monthly reports stay up as XLSX
(older months answer 404), so the bundle holds the full line set for September–December 2024 and
2025 so far and accumulates from here. DOTAX's archive page keeps PDF monthly reports back to
1997 (`tax.hawaii.gov/stats/a5_3txcolrptarchive/`) if monthly models need them.

Refreshing (each January, after the annual report is posted):

    .venv/bin/python -m state_revenue.scripts.refresh_annual_collections --cache /tmp/dotax_annual

It needs `pdftotext` (poppler). pdfplumber splits digits inside numbers on the newer reports.

## As built (phase 2, November 27, 2026)

| Piece | Where |
|---|---|
| General fund table (Table 1.9) beside collections, FY2004–FY2025 | `annual_collections.json` (`general_fund`), `load_ledger("general_fund")` |
| The Council's line items, 52 meetings, January 2014 to September 2026 | `.../state_revenue/data/cor_line_forecasts.json`, `cor.py`, `scripts/refresh_cor_forecasts.py` |
| Line forecaster (damped trend or level) | `.../state_revenue/forecast.py` |
| Backtest against the Council, and of the model alone | `.../state_revenue/backtest.py` (`python -m state_revenue.backtest`) |
| Baseline by line and source | `.../state_revenue/baseline.py` (`python -m state_revenue.baseline`) |

### The Council's line items

COR forecasts only the general fund's total. With each forecast DOTAX's Tax Research and Planning
Office posts Attachment 1, line items consistent with that total: two BASE fiscal years and seven
estimated, twelve tax rows. Every attachment since January 2014 is in the archive; each one's rows
add to its total within $2 thousand. Its BASE figures equal the annual report's general fund table
for FY2012–FY2024 (to $1 thousand of rounding), so the annual report is a fair truth for both
forecasts. FY2025 differs slightly (banks by $2.0M) because the September 2026 attachment carries
later revisions.

### The forecaster

Each line is forecast from its own history (`forecast.py`):

- **damped**: log growth starts at the recency-weighted geometric mean of the last 8 years'
  growth (weight 0.85 per year back) and decays toward the line's long-run growth (median of 15
  years) at φ = 0.85 a year, the repository's annual damping. Growth beyond ±40% in a year is a
  break and is left out.
- **level**: the median of the last 5 years grown at the long-run rate, for lines whose growth
  swings by more than 25% a year (estate, corporate income, banks, conveyance, unemployment).
- A line with fewer than 8 years of growth (rental vehicles, the barrel tax as its own row)
  takes the total's long-run growth.
- The county surcharge is forecast as a share of the general excise forecast (its ratio in the
  last actual year), since it is levied on the same base: 7.2% error a year out instead of the
  level method's 11.8%.

The window and recency weight were chosen on this backtest, so its figures flatter the model
somewhat. The grid tried, general fund total, error a year / two years out: the first version
(5 years, weight 0.5) 3.2% / 6.2%; 8 years and 0.85, 3.1% / 4.8% before the short-history
fallback; a median of recent growth in place of the weighted mean did about as well (2.5% /
5.7%) but the repository's rule is the weighted geometric mean, and a compliant setting matched
it.

### Backtest against the Council

One origin a year: the August or September meeting, when the fiscal year just ended is known.
Horizon 1 is the fiscal year in progress (the Council has two months of its collections), 2 the
next. Origins 2014–2024; the model sees the general fund history through the origin year and
nothing else. Mean absolute percentage error, FY2020–FY2022 targets left out (the pandemic):

| Line | h | n | Model MAPE | COR MAPE | Model / COR | Model closer |
|---|---|---|---|---|---|---|
| general_excise | 1 | 8 | 2.7% | 3.1% | 0.89 | 5/8 |
| general_excise | 2 | 7 | 6.9% | 5.9% | 1.18 | 4/7 |
| individual_income | 1 | 8 | 8.4% | 8.8% | 0.96 | 4/8 |
| individual_income | 2 | 7 | 14.6% | 10.5% | 1.39 | 3/7 |
| transient_accommodations | 1 | 8 | 24.0% | 5.8% | 4.14 | 0/8 |
| transient_accommodations | 2 | 7 | 30.6% | 13.9% | 2.21 | 0/7 |
| corporate_income | 1 | 8 | 35.5% | 37.0% | 0.96 | 5/8 |
| corporate_income | 2 | 7 | 36.4% | 53.0% | 0.69 | 7/7 |
| insurance_premiums | 1 | 8 | 2.3% | 5.2% | 0.44 | 7/8 |
| insurance_premiums | 2 | 7 | 2.8% | 5.6% | 0.50 | 5/7 |
| public_service_companies | 1 | 8 | 11.1% | 9.4% | 1.18 | 3/8 |
| public_service_companies | 2 | 7 | 22.0% | 15.8% | 1.39 | 1/7 |
| tobacco | 1 | 8 | 4.2% | 5.8% | 0.72 | 5/8 |
| tobacco | 2 | 7 | 7.9% | 10.5% | 0.75 | 5/7 |
| liquor | 1 | 8 | 2.1% | 3.3% | 0.63 | 5/8 |
| liquor | 2 | 7 | 6.6% | 6.3% | 1.04 | 2/7 |
| banks | 1 | 8 | 80.4% | 93.9% | 0.86 | 5/8 |
| banks | 2 | 7 | 100.3% | 158.2% | 0.63 | 6/7 |
| estate | 1 | 8 | 36.8% | 56.7% | 0.65 | 5/8 |
| estate | 2 | 7 | 40.9% | 47.4% | 0.86 | 4/7 |
| conveyance | 1 | 8 | 36.1% | 42.1% | 0.86 | 3/8 |
| conveyance | 2 | 7 | 34.1% | 56.1% | 0.61 | 4/7 |
| total | 1 | 8 | 2.9% | 2.9% | 1.02 | 3/8 |
| total | 2 | 7 | 4.7% | 4.9% | 0.96 | 4/7 |

What it says:

- **The total is a draw.** History alone does as well as the Council on the general fund's total.
  Neither is good at turning points: both missed FY2020 by 11–12% from September 2019.
- **Law decides the transient accommodations line.** The general fund's share of it was changed
  repeatedly (FY2004 $5.6M, FY2019 $340M, FY2021 $150M, FY2023 $757M). The Council knows the
  law; history cannot. Model 22% against 6% a year out.
- **Income tax two years out** goes to the Council for the same reason: enacted rate steps
  (Act 46, from TY2024). The Council is also closer on public service companies, whose tax
  follows utility revenue and so the price of oil.
- **The Council's small lines are mechanical.** On corporate income, banks, estate, conveyance,
  insurance premiums and tobacco the model is closer, often by half. Those lines are noise around
  a trend and the Council's line items carry its total's growth onto them.
- With every target in, the model's general fund total error is 4.9% / 7.6% against the
  Council's 6.3% / 8.2%: the Council missed the FY2021–FY2022 rebound by more.

**Gate (phase 2's own rule):** general excise passes a year out (2.7% against 3.1%) and is near two
out (6.9% against 5.9%); transient accommodations fails. So the model's own levels are not the
published baseline for the general fund lines; the Council's are.

### The model alone, lines the Council does not itemise

All funds, origins FY2010–FY2024, FY2020–FY2022 targets left out:

| Line | h | n | Model MAPE | Method |
|---|---|---|---|---|
| county_surcharge | 1 | 12 | 7.2% | rides_on general_excise |
| county_surcharge | 2 | 11 | 10.2% | rides_on general_excise |
| fuel | 1 | 12 | 4.9% | damped |
| fuel | 2 | 11 | 6.8% | damped |
| motor_vehicle | 1 | 12 | 12.0% | damped |
| motor_vehicle | 2 | 11 | 18.8% | damped |
| rental_vehicle | 1 | 3 | 24.4% | level |
| rental_vehicle | 2 | 2 | 27.8% | level |
| environmental_response | 1 | 3 | 3.2% | damped |
| environmental_response | 2 | 2 | 4.9% | damped |
| unemployment | 1 | 12 | 50.7% | level |
| unemployment | 2 | 11 | 65.8% | level |
| transient_accommodations | 1 | 12 | 7.3% | damped/level |
| transient_accommodations | 2 | 11 | 8.4% | damped/level |
| conveyance | 1 | 12 | 14.2% | level |
| conveyance | 2 | 11 | 17.4% | level |

Fuel, the barrel tax and the transient accommodations total (all funds) forecast well from
history. Motor vehicle taxes carry rate increases and the FY2019 split of rental vehicles out of
the row; unemployment contributions follow the trust fund's rate schedule, which moves with the fund's
balance (contributions more than tripled from FY2022 to FY2024), and history cannot see it: their baseline is wide and says so.

### The baseline

`build_baseline(fund)` (`fund="all"` or `"general_fund"`), through the Council's last year
(FY2033 from the September 2026 meeting). Each value carries its source:

- `cor`: the general fund's part of a line the Council itemises (its latest meeting; the
  preliminary FY2026 BASE column counts as its figure for that year).
- `cor_scaled`: all funds, a line the Council itemises: its figure times the line's
  all-funds-to-general-fund ratio in FY2025 (current law's split between funds held). The
  transient accommodations tax is 1.14 times its general fund part; the general excise tax 1.01.
- `rides_on`: the county surcharge, a share of the general excise baseline.
- `model`: everything else.

`build_baseline(source="model")` forecasts every line from history alone: the check, and the
fallback past the Council's horizon.

Totals, $ billions (September 2026 meeting):

| | FY2026 | FY2027 | FY2028 | FY2029 | FY2030 | FY2031 | FY2032 | FY2033 |
|---|---|---|---|---|---|---|---|---|
| General fund (the Council's) | 9.79 | 10.04 | 10.34 | 10.65 | 10.83 | 11.23 | 11.60 | 12.03 |
| All funds | 11.22 | 11.51 | 11.86 | 12.22 | 12.45 | 12.90 | 13.32 | 13.80 |
| All funds, history alone | 11.64 | 12.19 | 12.78 | 13.41 | 14.07 | 14.78 | 15.52 | 16.32 |

FY2026 sits below FY2025's $11.46B because the estate tax returns to about $50M from $406M and
income tax falls with the Act 46 steps. The history-only line runs $2.5B above by FY2033, most
of it income tax ($4.8B against the Council's $3.4B: the rate cuts) and the transient
accommodations tax.

Refreshing the Council's line items after each general fund meeting:

    .venv/bin/python -m state_revenue.scripts.refresh_cor_forecasts --cache /tmp/cor

## As built (phase 3, November 27, 2026)

| Piece | Where |
|---|---|
| General excise base and tax by activity (Table 1.1), FY2004–FY2025 | `annual_collections.json` (`general_excise_by_activity`), `annual_report.parse_get_activity` |
| DBEDT's 2022 input-output table, 62 sectors | `.../state_revenue/data/io_2022.json` |
| Visitor spending by category, 2023–2025 (HTA) | `.../state_revenue/data/visitor_spending.json` |
| Fetch for both | `.../state_revenue/scripts/refresh_get_inputs.py` |
| Burden by final purchaser; rate-change, category and sector scorers | `.../state_revenue/general_excise.py` (`python -m state_revenue.general_excise`) |

### The data

- **Table 1.1** gives each activity's base, tax and rate in all 22 years. Every year's activity tax
  plus "unallocated" (penalties, assessments, refunds) equals the collections table's general
  excise line to the dollar. Two columns don't add up (the FY2007 base in the FY2007 report, a
  missing use row in the FY2010 report); the newest report that does wins, as in phase 1.
- **The input-output table** (DBEDT, 2022) records the state general excise tax each sector pays:
  $4,117M in all, against DOTAX's $4,226M averaged over FY2022 and FY2023. DBEDT placed DOTAX's
  activities on sectors: retail trade's $1,732.3M is DOTAX's FY2022 retailing exactly, and
  construction's $413.1M is contracting. Each sector's sales add to its output within $0.05M.
- **Visitor spending** is HTA's "Total Visitor Expenditures by Category" (air, cruise and
  supplemental business visitors): $21.9B in 2025, groceries and snacks $1,077M, Hawaiʻi food
  products $352M, restaurants $3,150M.

### Who bears it

The tax per dollar of each sector's output (`t`), carried through the Leontief inverse, puts every
dollar on a final purchaser: `t L f_k`. Direct is the tax on the final sale, embedded the tax on
inputs carried into its price (full forward shifting assumed).

| Purchaser | Direct | Embedded | Total | Share |
|---|---|---|---|---|
| residents | 1,553 | 551 | 2,104 | 51.1% |
| visitors | 882 | 210 | 1,092 | 26.5% |
| investment | 364 | 141 | 505 | 12.3% |
| government | 155 | 81 | 236 | 5.7% |
| exports | 101 | 78 | 179 | 4.4% |
| total | 3,056 | 1,061 | 4,117 | |

- **Residents' direct share checks out.** Households' own purchases carry 37.7% of the tax here
  and 35% in the resident incidence model (which levels spending to BEA). The 51% adds what
  businesses pass on in prices.
- **Investment** is mostly construction (the contracting tax is 52% investment, 23% government):
  new housing and commercial buildings. It lands on whoever buys or rents them, residents mostly;
  the table cannot say how much.
- **Visitors** bear 69% of the tax on accommodation, 31% of the retailing tax, 16% of services.

### Scorers

All static: the base does not respond, and prices rise or fall by the tax alone.

- `score_rate_change({activity: rate}, fy)`: the change in rate times the activity's last base
  (FY2025), grown with the phase 2 baseline. Who bears it: the burden of the sectors the activity
  maps to (`ACTIVITY_SECTORS`; exact for retailing and contracting, approximate for services and
  rentals).
- `score_category_exemption(category, fy)`: residents' spending is BEA's lines for the category
  (the incidence model's mapping), groceries less SNAP benefits (DHS, $695M in state FY2025);
  visitors' is HTA's (`VISITOR_CATEGORIES`; groceries are groceries and snacks plus Hawaiʻi food
  products). State 4% and county 0.5% of both. The wholesale levy on the goods stays.
- `score_sector_exemption(sectors, fy, purchasers=...)`: removes the tax on input-output sectors'
  sales, to final purchasers and, with `"businesses"`, to other sectors. The relief from the
  latter follows the price chain to final purchasers.

FY2027, state revenue ($ millions):

| Change | State | County | Residents | Visitors | Investment | Government | Exports |
|---|---|---|---|---|---|---|---|
| Every 4% activity to 4.5% | 608.5 | 0.0 | 50% | 26% | 14% | 6% | 4% |
| Retailing to 4.5% | 267.6 | 0.0 | 54% | 31% | 10% | 3% | 2% |
| Exempt groceries | -332.0 | -41.5 | 82% | 18% | 0% | 0% | 0% |
| Exempt restaurant meals | -390.4 | -48.8 | 63% | 37% | 0% | 0% | 0% |
| Exempt hospitals and outpatient care | -279.7 | -35.0 | 97% | 3% | 0% | 0% | 0% |

- **Groceries** check against the resident model: residents' $6,236M of taxable grocery spending
  (2024) taxed 4% is $249M; the incidence model has $251M. Visitors add $55M. The $332M is
  FY2027's, grown with the baseline.
- **Hospitals and outpatient care** is the tax those sectors pay now (much of their revenue is
  already exempt). Its cost and the out-of-pocket category's in the incidence model answer
  different questions.

### Limits

- **One year's structure.** The input-output table is 2022; shares are held to its structure.
  DBEDT benchmarks every five years.
- **Activity versus industry.** Restaurants file as retailers with DOTAX but are their own sector
  (eating and drinking, $164M) in the table, so the restaurant line of the table and the retailing
  activity overlap in ways the table does not show.
- **Visitor categories are coarse.** "Groceries and snacks" includes snacks a food exemption might
  not reach; Hawaiʻi food products include gifts.
- **No behaviour.** A rate change's cost is static, and full forward shifting decides who bears
  it.

Refreshing (after DBEDT's next benchmark table or HTA's next annual report):

    .venv/bin/python -m state_revenue.scripts.refresh_get_inputs --cache /tmp/get_inputs

## As built (phase 4, November 27, 2026)

| Piece | Where |
|---|---|
| Fuel gallons and tax by type, the barrel tax, tobacco by product (calendar 2024) | `.../state_revenue/data/excise_bases.json`, `scripts/refresh_excise_bases.py` |
| Rate schedules and scorers | `.../state_revenue/excise.py` (`python -m state_revenue.excise`) |

### Bases and rates

- **Fuel.** DOTAX's calendar-year "Liquid Fuel Tax Base & Tax Collections": gallons and state and
  county tax by fuel type, statewide. Each type's rate is its state tax over its gallons:
  gasoline 16.0 cents, highway diesel 16.3, off-highway diesel and aviation 1.0. The environmental
  response tax is $1.05 a barrel and $0.19 per MMBtu. State fuel tax adds to the report's
  $79,236,953.86 to the cent. The ledger's fuel line is state plus county; a scorer changes the
  state rate only.
- **Tobacco.** The calendar-year "Cigarette & Tobacco Tax Collections": $64.0M from cigarettes
  in 2024, so 400 million cigarettes at 16 cents. Products add to the report's $82,249,304.63.
- **Rates in law** are the DOTAX annual report's (FY2025, sections 1.5, 1.7, 1.8), with dates, in
  `SCHEDULES`: the transient accommodations tax 10.25% (2018–2030) plus Act 96's 0.75 points
  (2026–2030), 9.25% after; the rental vehicle surcharge $6.00, $6.50 (2024), $7.00 (2025) a day;
  cigarettes 16 cents (since July 2011). `rate_in(tax, fy)` weights a fiscal year's rates by days
  (FY2026's transient accommodations rate is 10.62%).
- **Bases without a published quantity** are the baseline's revenue over the rate in force. The
  transient accommodations base this implies for FY2025 is $7.96B, above the general excise
  table's transient accommodations base ($7.46B); the timeshare occupancy tax, levied on fair
  market rental value, may account for the difference.

### Scorers

| Function | Changes | Base |
|---|---|---|
| `score_tat(rate, fy, elasticity=0)` | the transient accommodations rate | baseline revenue / rate in force |
| `score_fuel({type: rate}, fy)` | state rates per gallon, per barrel, per MMBtu | 2024 quantities grown with the fuel (or environmental response) baseline |
| `score_cigarettes(per_cigarette, fy, elasticity=0, pack_price=None)` | the tax per cigarette | 2024 cigarettes grown with the tobacco baseline |
| `score_rental_vehicle(per_day, fy)` | the surcharge per day | baseline revenue / rate in force |
| `score_proportional(line, multiplier, fy)` | every rate of a line by the same factor | the line's baseline |

FY2027:

| Change | State | Borne by visitors |
|---|---|---|
| Transient accommodations +1 point (11.00% to 12.00%) | 87.9 | 69% |
|   same, elasticity -0.5 | 83.2 | 69% |
| Gasoline and highway diesel +5 cents a gallon | 22.8 |  |
| Environmental response tax +$1 a barrel | 25.2 |  |
| Cigarettes +5 cents each ($1 a pack) | 20.0 |  |
| Rental vehicle surcharge +$1 a day | 17.5 |  |
| Liquor rates +10% | 5.1 |  |

- Visitors' share of the transient accommodations tax is the input-output table's (its own row of
  the tax by sector, through the same Leontief inverse as phase 3).
- `elasticity` is the taxed quantity's response to the price with tax. It is zero by default: no
  estimate here is sourced for Hawaiʻi.
- The other lines (liquor, motor vehicle, insurance premiums, public service companies) score
  proportionally: no published quantity for them is read yet.

Refreshing (each year, after DOTAX posts the calendar-year summaries):

    .venv/bin/python -m state_revenue.scripts.refresh_excise_bases --cache /tmp/excise

## Phases

1. **Ledger and data.** Done (above).
2. **Baseline for every line, backtested.** Done (above): the Council's line items for the
   general fund, the model elsewhere. Open: unemployment contributions on the trust fund's rate
   schedule (DLIR publishes it) and motor vehicle taxes on the rate history, the two model-only
   lines that miss by more than 10%.
3. **General excise, all of it.** Done (above). Open: the base by activity by industry from DOTAX's
   detailed GET reports, to tighten `ACTIVITY_SECTORS` for services and rentals; behaviour (a
   rate's effect on the base); the simulator's GET line.
4. **Transient accommodations and excise scorers.** Done (above). Open: liquor gallons by beverage
   (DOTAX's calendar-year liquor summary gives dollars only), motor vehicle weight and
   registration counts, and sourced elasticities.
5. **Fiscal notes beyond income tax.** `fiscal_notes` translates a bill into an
   `IncomeTaxSpec`; add specs for general excise and transient accommodations changes and score
   them through the ledger.

## Risks

- **Cash versus liability.** The income-tax model is tax-year liability; the ledger is
  fiscal-year cash. The baseline sidesteps it (income tax levels are the Council's), but a scorer
  that moves income tax must still turn a tax-year change into fiscal-year cash; the repository's
  FY(n+1) = TY(n) convention is crude for that.
- **Interactions between taxes.** A transient accommodations change moves the general excise tax
  on hotel rentals; a general excise rate change moves the county surcharge base. Each scorer
  must say which other lines it moves.
- **Breaks in the series.** The income tax compiling change from FY2017, COVID (FY2020–FY2022),
  the estate swings. Backtests must not fit through them.

## Decisions taken

- State taxes only (no county real property tax).
- Annual (fiscal-year) first; monthly only if a nowcast needs it.
- Its own package, imported by nothing in the chain.
- The baseline's general fund levels are the Council's line items; the model's levels are a check
  and cover only what the Council does not itemise (phase 2's gate).
