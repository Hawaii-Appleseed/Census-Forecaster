# State tax revenue, every line — Scope

**Status (2026-11-27): phase 1 built** (the ledger and its data, below). Phases 2–5 are scope.

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
- **The general excise tax is most of the remaining work.** A rate change scores directly. An
  exemption needs the part of the base the resident incidence model leaves out: visitors,
  business inputs and insurer-paid medical care (`GET_INCIDENCE_METHODOLOGY.md`, "What is left
  out").

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
- **Direction of imports.** `state_revenue` may import `tax_modeler` and `census_forecaster`
  (from phase 2); neither imports it. The GET incidence model (`tax_modeler.excise`) and the
  conveyance model stay where they are, because they need the household population and the
  band data. The ledger calls them.
- **Name.** Not `revenue`: `tax_modeler.revenue` is already the income-tax estimator.
- **Shape.** One catalogue of lines (`lines.py`) whose collections must add to DOTAX's total.
  From phase 2 each line exposes `baseline(fy)` and, for the first two tiers, `score(change)`.

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

## Phases

1. **Ledger and data.** Done (above).
2. **Baseline for every line, backtested (1–2 weeks).** Fiscal-year forecast per line: the
   structural lines from their models, base-times-rate lines from their bases and drivers, trend
   lines from history and the economy. Rolling-origin backtest FY2010–FY2025 against the Council
   on Revenues' forecasts made at the time (COR publishes general fund forecasts and the
   individual income line; the archive needs collecting). **Gate:** if the general excise and
   transient accommodations levels cannot get near COR's error, publish changes only, not levels.
3. **General excise, all of it (2–3 weeks).** Visitors' share from DBEDT visitor spending by
   category; business inputs through DBEDT's input-output table; base by activity from DOTAX's
   General Excise and Use Tax Collections and Base report (already in
   `tax_modeler/data/excise/dotax_get_by_activity.json` for FY2025–26, needs history). Scores
   rate changes by activity and exemptions with their visitor and business parts.
4. **Transient accommodations and excise scorers (about 1 week).** TAT on DBEDT/HTA visitor
   nights and room rates; fuel on DOTAX gallons; tobacco and liquor on quantities with published
   price elasticities; vehicle taxes on registrations.
5. **Fiscal notes beyond income tax.** `fiscal_notes` translates a bill into an
   `IncomeTaxSpec`; add specs for general excise and transient accommodations changes and score
   them through the ledger.

## Risks

- **Cash versus liability.** The income-tax model is tax-year liability; the ledger is
  fiscal-year cash. The repository's FY(n+1) = TY(n) convention is crude at this level
  (estimated payments and refunds cross fiscal years). Phase 2 needs a timing model for the
  income line or it will miss COR by the timing alone.
- **Interactions between taxes.** A transient accommodations change moves the general excise tax
  on hotel rentals; a general excise rate change moves the county surcharge base. Each scorer
  must say which other lines it moves.
- **Breaks in the series.** The income tax compiling change from FY2017, COVID (FY2020–FY2022),
  the estate swings. Backtests must not fit through them.

## Decisions taken

- State taxes only (no county real property tax).
- Annual (fiscal-year) first; monthly only if a nowcast needs it.
- Its own package, imported by nothing in the chain.
