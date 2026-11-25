# General excise tax incidence: methodology

**Status: first cut, November 25, 2026.** The model, its tests and two of its three inputs are in
the repository. The third input, the expenditure survey's decile means, is not yet fetched (the
BLS public API's daily limit; see [Refreshing](#refreshing)), so `runs/get_incidence/` does not
exist yet and no distribution figure is published. Nothing here is on the chain or the site.

## What it answers

Who pays Hawaiʻi's general excise tax (GET) among resident households, by income, and what an
exemption (groceries, medical services, residential rent) would cost and whom it would reach.

The income tax model reads returns. No return says who bore the GET: businesses file it on gross
receipts. So the model builds each household's taxable spending and applies the rates.

## Method

Code: `packages/tax_modeler/src/tax_modeler/excise/get_incidence.py`. Run:
`analyses/forecast_get_incidence.py`.

1. **Households.** The working-family credits step's 2024 population (2024 one-year ACS), one
   row per household: the tax units' cash income summed, SNAP as that step places it (DHS
   caseload and issuance), the food/excise credit at that step's fitted take-up, and the
   household's own contract rent from the ACS household file. Household weights are the ACS's
   (`WGTP`), with the 80 replicates for sampling error.
2. **Shape of spending over income.** The Consumer Expenditure Survey's published means by decile
   of income before taxes (national, 2024). Each category's spending is read at the household's
   own income, linear in log income between decile means, flat below the lowest decile's mean,
   and at the last interval's elasticity above the highest.
3. **Level.** Each category is scaled so the households' weighted total equals BEA's personal
   consumption expenditures for Hawaiʻi residents on it (table SAPCE4, 2024). The survey sets who
   spends relatively more; BEA sets how much is spent in all. `levels.csv` also reports the total
   with no scaling (the survey's national dollars), a floor.
4. **Rent.** Not imputed: each renter household's contract rent times twelve.
5. **Tax.** The GET is on gross receipts, which include the tax a business passes on, so the tax
   in a purchase is the rate times what the household paid: 4 percent state, 0.5 percent county
   surcharge (every county since January 1, 2024). Goods also carry the 0.5 percent wholesale
   levy from one stage back, taken as the Department of Taxation's wholesaling tax over its
   retailing base (about 0.26 cents per retail dollar in fiscal year 2025). Groceries bought with
   SNAP benefits are exempt (HRS §237-24.3): each household's SNAP benefit comes off its
   groceries before the retail tax.
6. **Exemptions.** A reform sets a category's retail tax to zero. Its wholesale levy stays.

### Categories

| Category | Survey items (shape) | BEA SAPCE4 lines (level) | Carries wholesale levy |
|---|---|---|---|
| Groceries | FOODHOME | 4 | all |
| Alcohol and tobacco | ALCBEVG, TOBACCO | 5, 127 | all |
| Restaurant meals | FOODAWAY | 102 | all |
| Clothing and footwear | APPAREL | 7 | lines 8, 17 |
| Vehicles and parts | VEHPURCH | 54, 58 | all |
| Gasoline and motor fuel | GASFUEL | 59 | all |
| Vehicle repair and services | CAREPAIR | 60, 61 | none |
| Furnishings and household supplies | HHFURNSH, HKPGSUPP | 30 | lines 31 to 35 |
| Recreation | ENTRTAIN, READING | 74, 78, 82, 90, 92, 93 | lines 75, 76, 79, 80, 90, 93 |
| Personal care and personal items | PERSCARE | 118, 119 | line 119 |
| Phone and internet | total expenditures | 67, 71, 72 | line 67 |
| Legal, accounting and funeral services | MISC, HHOPER | 122, 123, 126 | none |
| Medical services paid out of pocket | MEDSERVS | none: survey dollars | none |
| Rent | ACS contract rent | none | none |

Not taxed in the model, because the GET does not reach them or reaches them through another
tax: owners' housing costs, electricity and gas (public service company tax), water and sewer,
prescription drugs, health insurance and hospital care, tuition, air fares, financial services and
insurance (premium tax), gambling, religious and social services. Over-the-counter drugs and
residents' hotel stays are taxable and left out (no clean series for either).

## What is left out

`control_totals.csv` sets the modelled state tax beside the Department's allocated collections.
With BEA's 2024 levels the modelled part comes to about a third of fiscal year 2025's $4.65
billion (a trial with a stand-in profile; the total is nearly all BEA's levels times the rate, so
the survey shape will move it little). The rest is not an estimate of anything. It holds, together:

- **Business inputs past the wholesale levy.** Contracting ($591 million of tax), business
  services, commercial rent. These reach households through prices, including the price of new
  housing. The model has no input-output table yet, so none of it is attributed.
- **Visitors.** Hotel rentals alone are $299 million of tax, before visitors' retail and
  restaurant spending.
- **Medical care paid by insurers,** which is most of it. Only out-of-pocket payments are taxed
  here.
- **Government and nonprofit purchases,** and exports of services.
- **Survey and accounts differences.** BEA's state spending counts residents' purchases out of
  state, which Hawaiʻi does not tax.

So every figure is the tax in resident households' own purchases, not the tax residents bear.
An exemption's cost is likewise what resident households stop paying; visitors buy groceries too.

## Known weaknesses

- **Spending is read from income alone.** The published tables do not cross income with
  household size, age or tenure. A large family and a single person at the same income get the
  same basket. Breakdowns by family type would be wrong and are not produced. The survey's
  microdata fixes this (see below).
- **The lowest fifth's share of income is overstated.** The survey's lowest income decile spends
  about three times its income: students, retirees drawing on savings, people in a bad year.
  Measured against a year's income, their tax looks heavier than it is over a lifetime. Any
  consumption-tax table set against annual income has this; the direction is known, the size is
  not.
- **National shape at Hawaiʻi's nominal incomes.** A Hawaiʻi household at $80,000 is read at the
  national profile's $80,000, though prices here are higher. `impute_spending` takes an
  `income_deflator` for a regional price parity; it is 1.0.
- **The taxability table is unreviewed.** Which categories the GET reaches, and which fall under
  the public service company or insurance premium tax, is written from a reading of HRS chapters
  237 and 239, not checked line by line with the Department's guidance. Phone and internet
  service in particular is treated as fully under the GET.
- **Full pass-through.** Households bear all of the tax in what they buy, and renters all of the
  tax on rent (`--rent-pass-through` varies the second). Landlords who do not file are not
  accounted for.
- **Household weights are the ACS's,** not the income-tax fit's, and the population is
  households only (no group quarters), while BEA's totals cover everyone.
- **Standard errors are ACS sampling error only.**

## Next

1. Fetch the survey deciles, run the analysis, read the tables against ITEP's Hawaiʻi sales and
   excise shares and the Department's fiscal notes on grocery exemption bills.
2. Survey microdata (the interview files). bls.gov refuses scripted downloads, so someone has to
   download the zip in a browser. With it: spending by income, household size, age and tenure,
   and the Honolulu sample as a check on the national shape.
3. Business inputs through DBEDT's input-output table, which turns the unmodelled contracting
   and business-services tax into price effects by category.
4. Visitors' share from DBEDT visitor spending by category, so an exemption's full cost can be
   stated.
5. The simulator's GET line (`TAX_SIMULATOR_SCOPE.md`, "General excise tax").

## Refreshing

    .venv/bin/python scripts/fetch_get_incidence_inputs.py            # all three inputs
    .venv/bin/python scripts/fetch_get_incidence_inputs.py --only cex
    .venv/bin/python analyses/forecast_get_incidence.py

The fetch writes `packages/tax_modeler/src/tax_modeler/data/excise/`. The survey fetch is nine
API requests; an unregistered caller gets 25 a day, and the script keeps what has arrived and
resumes. `BLS_API_KEY` lifts the limit. BEA revises state spending each fall; the Department's
June report (`--dotax-fiscal-year`) carries a fiscal year and the one before.
