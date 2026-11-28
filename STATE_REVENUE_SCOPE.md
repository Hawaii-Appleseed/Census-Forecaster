# State tax revenue, every line — Scope

**Status (2026-11-28): phases 1–5 built.** The ledger; the baseline and its backtest against the
Council on Revenues; the general excise tax in full; the transient accommodations and excise
scorers; fiscal notes for those taxes. Each has a dated section below.

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
  liquor and the rest proportionally. A point on the transient accommodations tax is about $87M
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
- **Bases without a published quantity** (the rental vehicle surcharge) are the baseline's revenue
  over the rate in force. The transient accommodations base is DOTAX's own since "Limitations
  addressed" below.

### Scorers

| Function | Changes | Base |
|---|---|---|
| `score_tat(rate, fy, elasticity=0)` | the transient accommodations rate | DOTAX's reported base (rooms and timeshare), grown with the baseline |
| `score_fuel({type: rate}, fy)` | state rates per gallon, per barrel, per MMBtu | 2024 quantities grown with the fuel (or environmental response) baseline |
| `score_cigarettes(per_cigarette, fy, elasticity=0, pack_price=None)` | the tax per cigarette | 2024 cigarettes grown with the tobacco baseline |
| `score_rental_vehicle(per_day, fy)` | the surcharge per day | baseline revenue / rate in force |
| `score_proportional(line, multiplier, fy)` | every rate of a line by the same factor | the line's baseline |

FY2027:

| Change | State | Borne by visitors |
|---|---|---|
| Transient accommodations +1 point (11.00% to 12.00%) | 86.7 | 69% |
|   same, elasticity -0.5 | 82.0 | 69% |
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

## As built (phase 5, November 28, 2026)

Fiscal notes for bills that change the general excise tax, the transient accommodations tax or the
excises: `fiscal_notes/state_taxes.py`, `prompt_state_taxes.md`, CLI `state-run`, `state-score`,
`state-eval` (`fiscal_notes/README.md`, "State taxes"). The income tax path is untouched.

- **Spec.** One JSON object: an effective date and any of general excise exemptions by consumer
  category, general excise rates by DOTAX activity, repeals of exemptions in law, the transient
  accommodations rate, cents per cigarette, fuel cents per gallon and dollars per barrel, liquor
  dollars per gallon, the rental vehicle surcharge. Each maps to one `state_revenue` scorer.
- **Checks.** The same four as the income tax translator. Figures are matched as the statutes
  write them, in digits or words ("increased by one percentage point").
- **Score.** Five fiscal years from the effective date, the first prorated by days; state revenue
  by component, the county surcharge beside it, who bears each component.
- **Fixtures.** Six real 2026 bills, answers recorded from the model (`sonnet`):

| Bill | What it does | Verdict | Spec |
|---|---|---|---|
| HB 1585 | Exempts SNAP- and WIC-eligible groceries | partial (approximate) | `general_excise.exempt: [groceries]` |
| HB 604 | Transient accommodations tax up one point | partial (approximate) | `rate_change_points: 1` |
| HB 1085 | Cigarettes from 16 to 21 cents | partial (little cigars, fund shares) | `cents_per_cigarette: 21` |
| HB 959 | Grocery exemption; general excise increase left blank; income tax | partial | groceries only |
| HB 1991 | Liquor tax by alcohol content | partial (approximate) | three beverage rates |
| HB 2156 | Filing thresholds | out of scope | none |

All six carry dates already past (they were 2026 bills), so each note assumes July 1, 2026 and
says so. HB 604's note: +$478.5M over FY2027–FY2031, 69% borne by visitors.

Limits:

- **No routing.** A person picks the path; the hub client sends requests down the income tax path
  only. A bill that changes both taxes (HB 959) needs both runs and the notes are not combined.
- **Consumer categories are four.** Nonprescription drugs, reduced rates on one kind of good, and
  exemptions limited to some buyers are listed as not scored.
- **After 2030** the transient accommodations rate in law falls back to 9.25% while the Council's
  line keeps growing, so the base implied for FY2031 on is about 9% high. HB 604's FY2031 figure
  ($109.9M against $97.7M the year before) shows it.
- **Static.** No behavioural response until elasticities are sourced.

## Limitations addressed (November 27, 2026)

Six limits of phases 1–4, each closed with data DOTAX already publishes.

| Limit | What changed | Where |
|---|---|---|
| The transient accommodations base was collections over the rate ($7.96B), not the GET table's $7.46B | DOTAX reports the base: June's "Tax Liabilities Incurred" report cumulates the fiscal year. FY2025: $7,458.0M of rooms plus $418.8M of timeshare occupancy (fair market rental value), $7,876.8M in all. The scorer uses it | `excise.tat_base`, `excise_bases.json` (`transient_accommodations`, FY2022–FY2025) |
| The fuel line mixed state and county tax | County fuel taxes from the annual reports' allocation table, every year | `ledger.fuel_split(fy)`: FY2025 state $80.2M, county $87.8M |
| Exemption scores rested on BEA and HTA alone | DOTAX's Act 94 report: exempt income claimed on returns, by exemption and rate, tax year 2024 | `general_excise.load_exemptions`, `score_exemption_repeal`, `get_exemptions.json` |
| All-funds scaling held one ratio per line | The transient accommodations tax's fixed-dollar funds ($15.5M: Turtle Bay, convention center, land and development) are added, not scaled | `baseline.TAT_FIXED_FUNDS`, `ledger.tat_distribution(fy)` (FY2010 on) |
| Refreshes were by hand | The four refresh scripts run in the monthly `refresh-data` workflow with `--if-new`: each reads its index page and downloads only when a report it has not bundled is listed | `.github/workflows/refresh-data.yml` |
| The forecaster's settings were chosen on the backtest that scores them | Origins FY2008–FY2013, which the settings were not chosen on | `backtest.run_holdout` |

What the new data says:

- **Transient accommodations.** Liability over base is 10.25% in each of FY2022–FY2025, the
  statutory rate. Collections run 1–3% above liability (penalties and assessments), which a rate
  change does not move, so a point on the rate is $78.8M on FY2025's base ($79.6M before).
  The 2018 and 2020–2022 June reports cumulate by calendar year and are skipped.
- **Exemptions in law.** Repealing every exemption DOTAX does not suppress scores $1,367.6M
  (FY2025) here; DOTAX's own total of foregone taxes is $1,371.1M with the five suppressed rows
  in. Drugs and prosthetic devices: $119.3M both ways. The largest: nonprofit organizations
  ($8.3B of exempt income, $331M), the subcontract deduction ($5.5B, $221M), out-of-state sales
  ($3.8B), drugs ($3.0B). A repeal's score is an upper bound: claims can move to another
  exemption.
- **Groceries and SNAP.** DOTAX's "food stamps and WIC" exemption claimed $842M in 2024; the
  grocery exemption's cost subtracts DHS's SNAP issuance, $695M. The difference (WIC, and
  timing) is $6M of tax on a $305M estimate. The SNAP figure stays, to match the incidence
  model.
- **Out of sample, the forecaster is worse.** On origins FY2008–FY2013 the general fund total
  misses by 7.8% a year out and 8.9% two out, against 4.9% and 7.6% on the origins the settings
  were chosen on (every target in). Those years hold the 2009–2010 recession and start from
  five years of history, so it is not a clean comparison, but it confirms the phase 2 decision:
  the Council's line items, not the model's levels, are the baseline.

Unemployment contributions and motor vehicle taxes followed the same day (next section). Still
open: splitting investment's share of the general excise tax
between residents and others; income tax cash timing; sourced elasticities; liquor gallons (DOTAX
publishes rates per gallon by beverage, no gallons).

## Unemployment contributions and motor vehicle taxes (November 27, 2026)

The two lines the model forecast worst (about 50% and 12–19% error). Both errors were in the
series, not the method.

| Piece | Where |
|---|---|
| DOL's net UI contributions by month (ETA 2112), taxable payroll (ETA 204), DLIR's schedules | `.../state_revenue/data/ui_hawaii.json`, `scripts/refresh_ui.py` |
| Schedule-times-payroll model, its backtest, the carry to DOTAX's line | `.../state_revenue/unemployment.py` (`python -m state_revenue.unemployment`) |
| First year a row means what it means now | `lines.SERIES_START`, `Ledger.clean_series` |

### Unemployment contributions

**DOTAX's line changed coverage.** Against the Department of Labor's count of net contributions
deposited, DOTAX's "employment security contributions" was 50–70% through FY2023, 87% in FY2024
and 107% in FY2025. Month by month the two agree within $1–2M since September 2024 (October
2025: DOTAX $44.0M, DOL $42.8M). So the line's rise from $114M in FY2022 to $377M in FY2024 is
partly a change in what DOTAX counted; no report read here says what changed. The DOL series is
the one modelled.

| FY | DOL | DOTAX | DOTAX / DOL |
|---|---|---|---|
| 2016 | 182.4 | 128.6 | 0.71 |
| 2017 | 152.8 | 104.3 | 0.68 |
| 2018 | 167.1 | 110.9 | 0.66 |
| 2019 | 177.6 | 110.3 | 0.62 |
| 2020 | 188.1 | 104.1 | 0.55 |
| 2021 | 180.3 | 110.6 | 0.61 |
| 2022 | 238.8 | 114.4 | 0.48 |
| 2023 | 333.7 | 167.9 | 0.50 |
| 2024 | 434.1 | 377.5 | 0.87 |
| 2025 | 262.7 | 281.2 | 1.07 |

**The rate is a rule.** DLIR puts one of eight contribution rate schedules in force each calendar
year by the trust fund's reserve (HRS §383-68). Receipts for a rate year (February through the
next January, when the fourth quarter's report is paid) over that year's taxable payroll:

| Rate year | Schedule | Receipts | Taxable payroll ($B) | Effective rate |
|---|---|---|---|---|
| 2016 |  | 158.8 | 14.32 | 1.11% |
| 2017 |  | 161.9 | 15.27 | 1.06% |
| 2018 |  | 172.5 | 16.05 | 1.08% |
| 2019 |  | 192.0 | 16.41 | 1.17% |
| 2020 | C | 178.5 | 14.52 | 1.23% |
| 2021 | D | 235.2 | 15.99 | 1.47% |
| 2022 | D | 240.6 | 18.00 | 1.34% |
| 2023 | F | 505.0 | 20.01 | 2.52% |
| 2024 | D | 289.1 | 20.96 | 1.38% |
| 2025 | C | 219.2 | 21.89 | 1.00% |

Schedule C runs 1.0–1.2%, D 1.3–1.5%, F 2.5%. Schedules before 2020 are not on DLIR's page.

**The model.** Rate-year receipts = the latest effective rate under the schedule in force, times
taxable payroll grown by its own damped trend. A schedule with no complete year yet takes the
last year's rate moved by the ratio of the two schedules' new-employer rates. A year DLIR has
not announced keeps the last schedule (C for 2026). A fiscal year is the July–January part of one
rate year (66%) and the February–June part of the next; months DOL has reported are used as they
are. The ledger's line is the DOL-basis figure times DOTAX's ratio to it in FY2025 (1.07).

Each rate year forecast from the years before it, its schedule known (DLIR announces it before
the year starts):

| Rate year | Schedule | Actual | Forecast | Error | Schedule seen before |
|---|---|---|---|---|---|
| 2020 | C | 178.5 | 201.3 | +12.8% | no |
| 2021 | D | 235.2 | 226.4 | -3.7% | no |
| 2022 | D | 240.6 | 241.9 | +0.5% | yes |
| 2023 | F | 505.0 | 335.3 | -33.6% | no |
| 2024 | D | 289.1 | 282.7 | -2.2% | yes |
| 2025 | C | 219.2 | 271.7 | +24.0% | yes |

Mean absolute error 12.8% over six rate years, against about 50% for the line's own history. The
misses are the first year under a schedule (F in 2023) and drift within one (C was 1.23% in 2020,
1.00% in 2025). Six years is a small sample.

| FY | DOL basis | Ledger line |
|---|---|---|
| 2026 | 230.5 | 246.8 |
| 2027 | 234.8 | 251.3 |
| 2028 | 246.9 | 264.3 |
| 2029 | 259.4 | 277.7 |

FY2026 is DOL's reported months. The baseline carries the line as `ui_model` in both the "best"
and the history-only versions.

Limits: the schedule for 2027 on is assumed, not predicted. Predicting it needs the adequate
reserve fund computation (the trust fund balance is bundled, the benefit cost rate behind the
adequate reserve is not). The 1.07 carry to DOTAX's line rests on one year.

### Motor vehicle taxes and fuel

Through FY2018 the motor vehicle row held rental vehicles and the fuel row the environmental
response tax (`lines.HOST`), so forecasts read a break in FY2019 as a trend. `SERIES_START` now
keeps the earlier years out, and a steady line with at least four years of growth keeps its own
long-run rate instead of the total's.

| Line | Before (origins FY2010–24) | After (origins FY2022–24) |
|---|---|---|
| Motor vehicle, 1 year out | 12.0% (n=12) | 1.3% (n=3) |
| Motor vehicle, 2 years out | 18.8% (n=11) | 2.5% (n=2) |
| Fuel, 1 year out | 4.9% (n=12) | 0.7% (n=3) |
| Fuel, 2 years out | 6.8% (n=11) | 1.7% (n=2) |

The "after" sample is three origins, and those years were quiet. The annual reports name one
rate change in the line's history (Act 86, SLH 2011: the weight tax from 0.75 to 1.75 cents a
pound), before the clean series starts; a rate schedule like the transient accommodations tax's
waits for the next change.

### What moved elsewhere

- The all-funds baseline: FY2026 $11.29B and FY2033 $13.93B (were $11.22B and $13.79B), nearly
  all of it unemployment contributions ($247M in FY2026 where history alone gave $179M).
- The steadier long-run rule moved the general fund backtest slightly: the model's total error a
  year out is 3.0% (was 2.9%), and on the out-of-sample origins 7.9% and 9.2% (were 7.8% and
  8.9%). The tables in the dated sections above are as they were when written.

## Open items closed (November 27, 2026)

Four items were left open. Three are done; one is blocked.

| Item | What changed | Where |
|---|---|---|
| Monthly history was 24 months, full detail in eight | Every month from July 2013 to December 2025 with all tax lines, from DOTAX's PDF archive | `census_forecaster/scripts/backfill_dotax_collections.py`, `dotax_monthly/collections.json` |
| Income tax: tax-year liability to fiscal-year cash | Shares by channel from those months | `.../state_revenue/income_timing.py` (`python -m state_revenue.income_timing`) |
| Investment's 12% of the general excise tax | Passed on to the purchasers who use the capital | `general_excise.allocate_investment` |
| Liquor had rates but no gallons | NIAAA's gallons by beverage; a per-gallon scorer | `excise.score_liquor`, `excise_bases.json` (`liquor_gallons`) |
| Elasticities | **Blocked.** The research browser (Perplexity) now asks for a sign-in before answering. Scorers still take an `elasticity`; none is sourced | |

### Monthly collections back to July 2013

DOTAX archives each month's "State Tax Collections and Distribution" as a PDF back to 2010. The
backfill reads 132 of them (July 2014 to June 2025; each carries the same month a year before).
Every report's tax rows add to its printed total within $5. Twelve months equal the annual
report's fiscal year to the thousand: individual income in FY2015, FY2019, FY2024 and FY2025,
general excise in FY2019, FY2024 and FY2025.

- Through FY2021 the report leads with four tax-district columns; the statewide month is the
  fifth. Read as the newer layout, the first district's rows still add to the first district's
  total, so the total check alone would not have caught it; the fiscal-year check did.
- A month appears in its own report and, a year later, as the prior-year column. The later one
  wins: it carries revisions and the newer rows (rental vehicles left the motor vehicle row
  during FY2020, restated for the year before). A month with no `rental_vehicle` or
  `environmental_response` value still has them inside `motor_vehicle` or `fuel`.
- FY2017's months sum $96.9M above the annual report's individual income figure: the report
  restated FY2017 under "a new compiling method" (FY2018 report, Table 1.8 note).
- The backfill never overwrites a value from the XLSX refresh. July and August 2025 keep only
  the 13 series read before the parser was widened; their PDFs are not in the archive yet.

### Income tax cash timing

| Tax year | Withholding Jan-Jun | Jul-Dec | Estimates Feb-Jun | Jul-Jan | With returns | Refunds | Net |
|---|---|---|---|---|---|---|---|
| 2014 | 831 | 853 | 263 | 220 | 189 | -447 | 1,908 |
| 2015 | 876 | 881 | 279 | 257 | 203 | -469 | 2,027 |
| 2016 | 929 | 926 | 301 | 311 | 193 | -507 | 2,153 |
| 2017 | 991 | 957 | 241 | 390 | 222 | -480 | 2,321 |
| 2018 | 1,075 | 1,030 | 310 | 293 | 280 | -525 | 2,463 |
| 2019 | 1,072 | 1,081 | 432 | 400 | 299 | -596 | 2,687 |
| 2020 * | 1,051 | 1,046 | 184 | 600 | 425 | -541 | 2,765 |
| 2021 * | 1,115 | 1,164 | 659 | 605 | 536 | -954 | 3,125 |
| 2022 | 1,226 | 1,256 | 859 | 529 | 413 | -767 | 3,518 |
| 2023 | 1,307 | 1,357 | 627 | 394 | 398 | -845 | 3,239 |
| 2024 | 1,417 | 1,445 | 556 | 484 | 446 | -872 | 3,475 |

\* 2020 and 2021 (moved deadlines) are left out of the averages.

Withholding is 67% of gross payments and half of it arrives in January to June; estimated
payments are 24%, 53% of them by June; payments with returns 9%.

- **A change that reaches withholding** (DOTAX changes the tables with the law): 46% in the
  fiscal year that ends in the tax year's June, 54% in the next.
- **A change settled on returns** (a credit, or tables left alone): all in the next fiscal year.
  This is the repository's FY(n+1) = TY(n) convention, which therefore puts a withholding
  change about half a year late.

`to_fiscal_years({tax_year: change}, channel)` spreads a scored change. Extension payments and
late refunds, which reach a third fiscal year, are counted with the second.

### Investment passed on

The input-output table ends at investment: a new building is a final purchase. Its general
excise tax is borne later by whoever uses it. `allocate_investment` sends the tax in
single-family construction to residents ($63M of investment's $505M) and spreads the rest
(other buildings $206M, equipment bought at retail $122M, civil works $68M) over the other
purchasers in proportion to the tax they already bear, as if capital were one more input.

| Purchaser | Total | Share |
|---|---|---|
| residents | 2,424 | 58.9% |
| visitors | 1,226 | 29.8% |
| government | 265 | 6.4% |
| exports | 201 | 4.9% |

Residents 59%, visitors 30%, against 51% and 27% with investment left as its own purchaser. The
rule is an assumption: the table has no capital-flow matrix saying which sectors buy which
capital, and a hotel's building bears on visitors more than this split says.

### Liquor

NIAAA's "apparent per capita alcohol consumption" file gives gallons of spirits, wine and beer
sold in each state by year, compiled from state sales and tax records. Hawaiʻi, 2024: 2.46M
gallons of spirits, 5.13M of wine, 31.25M of beer. At DOTAX's rates per gallon ($5.98, $1.38
still wine, $0.93 beer other than draft) that is $50.9M; DOTAX collected $50.1M in FY2025 and
$50.5M in calendar 2024. The same check for 2022 gives $53.8M against $54.5M. Sparkling wine
($2.12), coolers ($0.85) and draft beer ($0.54) are not separated in the gallons; the fit says
they roughly offset.

`score_liquor({"beer": 1.03}, fy)`: a dime a gallon on beer is $3.2M in FY2027. Raising all
three rates 10% is $5.2M, where the proportional scorer gave $5.1M.

### Still open

- **Elasticities**, once the research browser is signed in: hotel room nights, cigarettes,
  gasoline, with a low, middle and high value each and its source.
- **The unemployment schedule after 2026** (the adequate reserve computation).
- **Phase 5:** fiscal notes for bills that change the general excise or transient
  accommodations tax.

## Phases

1. **Ledger and data.** Done (above).
2. **Baseline for every line, backtested.** Done (above): the Council's line items for the
   general fund, the model elsewhere.
3. **General excise, all of it.** Done (above). Open: the base by activity by industry from DOTAX's
   detailed GET reports, to tighten `ACTIVITY_SECTORS` for services and rentals; behaviour (a
   rate's effect on the base); the simulator's GET line.
4. **Transient accommodations and excise scorers.** Done (above). Open: liquor gallons by beverage
   (DOTAX's calendar-year liquor summary gives dollars only), motor vehicle weight and
   registration counts, and sourced elasticities.
5. **Fiscal notes beyond income tax.** Done (above). Open: routing in the hub client; a combined
   note for a bill that changes income and excise taxes.

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
