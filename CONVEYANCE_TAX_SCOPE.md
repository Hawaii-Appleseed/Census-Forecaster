# Conveyance Tax (SB 3028, 2026) — Scope

**Status (2026-09-25): built and published** as the "Top of the Market" page
(`site/conveyance-tax/`), from `forecast_conveyance_sb3028.py`. Revised twice
on 2026-09-25 to build the statewide estimate county by county. The first
version (2026-09-24) scaled Maui's results by conveyance collections and gave
$69M–$118M after the sales response. The first revision scaled by tax-roll
value above $2M and gave $67M. The current version, described in **As built**
below, builds Oʻahu and Hawaiʻi Island from their housing stock, home by home.
The rest of this document is the original scope, kept for its reasoning.
Goal: a "Proposed policy" estimate on the estimates site for restructuring
Hawaiʻi's conveyance tax the way the 2026 Legislature last had it, with
attention to the $3–4 million homes where the change bites hardest.

## As built

| Piece | Where |
|---|---|
| Schedules (current §247-2 cliff; HD2 marginal with caps and CPI indexing); tier interpolation (`value_above`); grouped Pareto fit | `packages/tax_modeler/src/tax_modeler/conveyance.py`, tests in `tests/tax_modeler/test_conveyance.py` |
| Maui sales, stock and sales-by-value inputs | `data/raw/conveyance/maui_*.csv`, produced by `scripts/conveyance/maui_sales_extract.js` (run in a browser on the county site) |
| Oʻahu and Hawaiʻi County housing stock | `data/raw/conveyance/parcel_stock_by_value.csv`, from `scripts/conveyance/fetch_parcel_stock.py` (public ArcGIS REST) |
| Kauaʻi and cross-check inputs | `county_home_sales_tg_2015_2025.csv`, `rpt_residential_tiers_fy2027.csv`, `pums_owner_value_tail_2020_2024.csv` (`scripts/conveyance/pums_owner_tail.py`), `oahu_mls_by_price.csv`, `oahu_mls_summary.csv` |
| Scoring, county build-up, checks, disposition | `forecast_conveyance_sb3028.py` → `runs/conveyance_sb3028/`; tests in `tests/test_conveyance_forecast.py` |
| Page | `scripts/build_site.py` `build_conveyance()`; data in `site/data/conveyance-tax/` |

**Maui: every sale.**
- Only Maui publishes every recorded sale with price *and* conveyance tax paid
  (the county's "RPT Sales Data File"). Honolulu publishes no bulk sales file.
  The qPublic sales pages for all three other counties sit behind terms that
  prohibit "robots, screen scraping or other data mining tools".
- The tax paid identifies the schedule, so owner-occupancy is observed
  (open question 5).
- In FY2023–FY2026, 97% of priced, taxed documents match (1) or (2) within
  0.5%. The rest are folded into nonresidential, which HD2 leaves unchanged.
- Land-use codes split schedule-(1) sales into owner homes and nonresidential
  property.
- Bins align with every break point of both laws. Current law reproduces
  Maui's recorded tax on home sales to within a few hundred dollars a year.
- **Apartment buildings** (5+ dwelling units per the State GIS dwelling
  layer; 17 sales in four years) are listed separately and scored under HD2's
  per-unit rule: rate set by price ÷ units, applied to the whole price, as a
  non-owner purchase. Today they pay schedule (1) on the whole price. The rule
  cuts their tax (open question 4, answered).

**Oʻahu and Hawaiʻi Island: housing stock × Maui's sale rates** (`stock_method`).
- **Maui's rates.** Maui's full assessment listing (tax year 2026) is joined
  to every FY2023–26 sale. Per assessed-value bin and current owner-occupancy
  (homeowner exemption), this gives:
  - arm's-length sales per home per year (price 0.5–2.5× assessed value);
  - the buyer mix (schedule 1 or 2);
  - price over assessed value.
- **Oʻahu's stock.** The City's RPAD ASMTPITT table on its open data hub
  (CadastralTables/FeatureServer/10, which the Revenue-Modeling-TOD project
  also uses), one row per parcel or condo unit:
  - residential = tax rate code 1;
  - owner-occupied = an exemption of $300K or less, not Residential A or TVR;
  - fully exempt records are dropped.
- **Hawaiʻi County's stock.** The county parcel layer on the State GIS portal
  (ParcelsZoning/MapServer/5, April 27, 2026; homeowner flag):
  - condo projects (pittcode 200) are split into units using the dwelling
    layer's unit counts, or the median value per unit where counts are
    missing;
  - homes on agricultural land with a building are included (sensitivity
    without).
- **Common rules for both counties' stock:**
  - improved homes only (the bill treats vacant land as nonresidential);
  - 5+ unit buildings and non-condo records of $20M+ (other than Honolulu
    Residential A) are left out, and Maui's rates are built the same way.
- **Calibration.** On Maui's own stock the method recovers 74% of Maui's
  sale-by-sale change (k = 1.35). The gap is nominal-price, new-construction
  and vacant-lot sales, and the $20M+ records left out. Oʻahu and Hawaiʻi are
  scaled by k.
- **Key assumption:** homes of a given value and occupancy sell as often, and
  to the same kinds of buyer, as on Maui.

**Kauaʻi: tax-roll tiers.** Kauaʻi publishes no values by parcel. Its
increase is Maui's scaled by Kauaʻi's non-owner residential value above $2M
from the 2026–27 statewide valuation report tiers (`value_above`), and by
owner-occupied value above $2M from ACS PUMS (`county_bases`). This was the
first revision's method for all three counties and is now a sensitivity.

**Checks:**
- **Oʻahu's high end.** The model has 456 sales a year of $2M or more,
  against 287 single-family MLS sales alone (condos and unlisted sales come on
  top). At $3M+ it has 124 against 111, and at $5M+ 41 against 39.
- **Oʻahu rebuilt from MLS bands** (HBR via Data Book 2024, scaled to Title
  Guaranty counts, Pareto tails) gives $25M–$43M static. The stock method
  gives $34M.
- **All home sales.** The model finds fewer than Title Guaranty/BOC records:
  7,783 against 10,781 a year on Oʻahu and 2,050 against 3,853 on Hawaiʻi
  Island. The gap is mostly new and lower-priced homes, where HD2 changes
  little.
- **Hawaiʻi Island's top band.** $10M+ sales (24 a year at FY2028 prices) may
  be high. A broker count found 14 in North Kona and South Kohala in 2025.

**Aging:**
- Each base year (FY2023–26 for Maui) is aged separately to the target year
  and the results are averaged.
- Prices grow 4% a year.
- HD2 brackets are indexed by 3% a year starting with 2027, upward only.

**Behavioral response:**
- Sales volume is multiplied by exp(−ε·Δpp), where Δpp is the change in tax
  as points of price. It rises where HD2 cuts the tax.
- ε = 10, with a range of 5–15.
- Verified anchors:
  - Toronto's 1.1% land transfer tax cut sales by about 15%, roughly 14 per
    point (Dachis, Duranton & Turner, 2012).
  - A 1-point cut in UK stamp duty raised short-run activity by about 20%
    (Best & Kleven, 2018). Part of that is timing.

**Results (FY2028, $M), as published:**

| | Static | With sales response (ε = 10) |
|---|---:|---:|
| Maui County (every sale) | 36.2 | 22.2 |
| Oʻahu (housing stock) | 34.4 | 21.5 |
| Hawaiʻi Island (housing stock) | 29.1 | 16.3 |
| Kauaʻi (tax-roll tiers) | 15.7 | 9.5 |
| **State** | **115.4** | **69.5** (ε 15–5: 52.5–90.2) |

- **Current law.** Current law statewide is $115.7M.
- **Sensitivity** (FY2028, static / with response):

  | Variant | Static | With response |
  |---|---:|---:|
  | Without the Maui calibration | 98.9 | 59.9 |
  | Hawaiʻi Island without agricultural-land homes | 114.2 | 68.7 |
  | Oʻahu rebuilt from MLS sales | 106.2–124.4 | 63.9–75.4 |
  | All three counties from tax-roll tiers | 108.9 | 66.4 |

- **Sales a year of $3–4M homes, FY2028 prices:** Oʻahu 119, Maui 72,
  Hawaiʻi Island 15.
- **Where the money comes from.** Non-owner buyers supply 89% of the statewide
  increase. Of homes worth $2M+, 69% on Maui are not owner-occupied, against
  38% on Oʻahu. Those homes sell about twice as often on Maui (~4%/yr against
  ~2%).
- **Cross-check.** House Finance Chair Todd put the draft at about $20M below
  the original House proposal's ~$170M, so about $150M (Civil Beat,
  2026-04-10). This model, static at 2023–26 prices, gives $91M. The House's
  method is not public.
  - A figure near $150M follows only if the other counties hold about 4×
    Maui's high end, in proportion to collections.
  - Parcel records give Oʻahu and Hawaiʻi Island together 1.8× Maui's count
    of $2M+ homes that are not owner-occupied.
- **Breakevens.** $2.247M (owner) and $2.073M (other), matching Todd's
  "about $2.3M / $2.1M".
- **Who pays, on Maui:**
  - 43% of sales pay less, 42% the same and 15% more. Todd said 91% of sales
    statewide would pay the same or less; Maui's high end is heavier.
  - Sales of $4M+ supply 88% of the gain.
  - Non-owner buyers supply 90%.
- **The $3–4M band on Maui:**
  - About 52 non-owner and 20 owner sales a year.
  - Average tax rises from $20.6K to $84.1K (non-owner) and from $16.9K to
    $39.0K (owner).
  - Only 10% of the gain.
- **Market caveat.** The base period (2023–26) was a slow market. DOTAX
  collections averaged about $95M, against $188M in FY2022.

**Disposition (open question 2, answered).**
- HD2 §4 sends, in order:
  - 5% or $10M to land conservation;
  - 20% or $40M to rental housing;
  - 30% or $60M to the Hawaiian home lands infrastructure and housing special
    fund;
  - 20% or $40M to the TOD infrastructure subaccount of the dwelling unit
    revolving fund.
- That is 75% of *all* collections up to $150M of caps, so the general fund
  gains only if HD2 raises more than about $107M a year.
- **FY2028 general fund:**
  - $46.3M under the central estimate, against $72.6M under current law
    (−$26M).
  - $55.9M even at ε = 5 (−$17M).

**Open questions, answered:**
1. No CD1 was printed (`SB3028_CD1_.HTM` returns 404). Conferees met April 28
   to May 1 and the bill died.
3. Scoring starts FY2028, the first full fiscal year after a 2027 enactment.

**Still left out:**
- Leases.
- Pre-effective-date timing.
- Price capitalization.
- Maui vacant residential lots. They pay schedule (1) today, so the model
  counts them as owner sales, but HD2 treats them as nonresidential. They are
  a small part of the owner-sales gain, which is itself 10% of Maui's gain.

**Unverified or unsourced, not used:**
- The Tax Foundation of Hawaiʻi figures below (~$100M/yr, ~1,000 sales over
  $3M).
- The "$68.5 million a year" for HB 2049 in 2026 advocacy testimony, which
  has no stated source.

## The policy

### Current law — HRS §247-2 (last amended L 2009, c 59)

A **cliff** schedule: once a sale crosses a threshold, one rate applies to the
*whole* price. Two schedules:

| Value | (1) All other conveyances, incl. owner-occupied homes | (2) Condo / single-family, purchaser **ineligible** for county homeowner exemption |
|---|---:|---:|
| Under $600K | 0.10% | 0.15% |
| $600K–$1M | 0.20% | 0.25% |
| $1M–$2M | 0.30% | 0.40% |
| $2M–$4M | 0.50% | 0.60% |
| $4M–$6M | 0.70% | 0.85% |
| $6M–$10M | 0.90% | 1.10% |
| $10M+ | 1.00% | 1.25% |

Leases of 5+ years are taxed on capitalized rentals; minimum $1 per transaction.

**Disposition (§247-7):** general fund, except 10% or $5.1M (whichever is less)
to the land conservation fund and 50% or $38M to the rental housing revolving
fund. So nearly every dollar above ~$86M of collections lands in the general fund.

### The proposal — SB 3028 SD2 HD2 (April 8, 2026)

The last printed draft. It went to conference, where talks ran until May 1,
2026, and failed; companion HB 2049. The effective date is the defective
"July 1, 3000", the House's usual device for forcing conference. So this is
the most complete statement of where the Legislature was at the end of
session, not an agreed text. *(Verified against the HD2 bill text on
data.capitol.hawaii.gov; the failure date and HB 2049 from bill-tracker
reporting — confirm on the status page.)*

**Residential sales move to marginal rates** (each rate applies only to value
inside its band, like income tax brackets):

| Value band | Owner-occupant (homeowner-exemption eligible) | Non-owner-occupant |
|---|---:|---:|
| Under $600K | 0.10% | 0.15% |
| $600K–$1M | 0.30% | 0.35% |
| $1M–$2M | 0.45% | 0.65% |
| $2M–$3M | **2.0%** | **5.0%** |
| $3M–$6M | **4.0%** | **7.0%** |
| $6M–$10M | 5.0% | 8.0% |
| $10M+ | 6.0% | 9.0% |
| Cap on total tax | 4% of price | 6% of price |

Other provisions: **nonresidential** property keeps today's cliff schedule (1);
**multifamily** value is divided by the number of units, rates applied per
unit, cap per unit; **brackets CPI-indexed** (Urban Hawaiʻi CPI, July–July)
from 2027, upward only. Reporting on earlier drafts mentions a new allocation
to the Department of Hawaiian Home Lands — **disposition in HD2 still to be
read** (open question 2).

### What it does at the prices that matter

Tax per sale (computed from the two schedules above):

| Price | Current, owner-occ. | HD2, owner-occ. | HD2 effective rate | Current, other | HD2, other | HD2 effective rate |
|---:|---:|---:|---:|---:|---:|---:|
| $800,000 | $1,600 | $1,200 | 0.15% | $2,000 | $1,600 | 0.20% |
| $1,500,000 | $4,500 | $4,050 | 0.27% | $6,000 | $5,550 | 0.37% |
| $2,000,000 | $10,000 | $6,300 | 0.32% | $12,000 | $8,800 | 0.44% |
| $2,500,000 | $12,500 | $16,300 | 0.65% | $15,000 | $33,800 | 1.35% |
| **$3,000,000** | **$15,000** | **$26,300** | 0.88% | **$18,000** | **$58,800** | 1.96% |
| **$3,500,000** | **$17,500** | **$46,300** | 1.32% | **$21,000** | **$93,800** | 2.68% |
| **$3,999,000** | **$19,995** | **$66,260** | 1.66% | **$23,994** | **$128,730** | 3.22% |
| $4,000,000 | $28,000 | $66,300 | 1.66% | $34,000 | $128,800 | 3.22% |
| $5,000,000 | $35,000 | $106,300 | 2.13% | $42,500 | $198,800 | 3.98% |
| $10,000,000 | $100,000 | $346,300 | 3.46% | $125,000 | $588,800 | 5.89% |

The $3.5M owner-occupant row matches the figure reported for the latest draft
($17,500 → $46,300), a useful check that the schedule is transcribed right.

Three things follow, and the page should say them plainly:

1. **It is a tax cut for most sales.** HD2 owes less than current law on every
   owner-occupied sale below **$2.25M** and every other residential sale below
   **$2.07M** (where 0.5% × V = $6,300 + 2% × (V − $2M), and 0.6% × V =
   $8,800 + 5% × (V − $2M)). The median Hawaiʻi home sells well below that.
2. **The $3–4M band is where it turns steep.** The marginal rate jumps to 4%
   (owner) / 7% (other) at $3M, so a $3.5M home's tax rises 2.6× (owner) or
   4.5× (other), and at $3,999,000 more than 3× / 5×.
3. **It removes current law's cliffs.** Today a $4,000,000 sale owes $8,005
   more than a $3,999,000 one; bunching just below $2M, $4M, $6M and $10M
   should show up in current sales data (a check on the data, below).

## Estimate to produce

Revenue by fiscal year, FY2027–FY2031 (conveyance tax is paid at recording,
so fiscal years are natural): current law vs HD2, static and with a
transaction response, by price band and owner-occupancy, and the net of
winners (sub-$2.2M sales) and losers. Plus the $3–4M spotlight: sales per
year, average tax change, share of the new revenue.

## Data needed

### 1. Sales microdata with price and owner-occupancy (the hard part)

The estimate is a sum over sales, so it needs the price distribution — above
all the count and prices of sales above $2M — and whether each buyer gets the
homeowner exemption. Candidates, best first:

| Source | Gives | Caveats |
|---|---|---|
| County real property sales records (Honolulu RPAD open data; Maui, Hawaiʻi, Kauaʻi assessment sales files) | Every recorded sale: price, date, TMK, property class; homeowner exemption status on the parcel afterwards | Four formats; exemption status lags the sale a year; non-arm's-length transfers to filter |
| Hawaiʻi Information Service / Hawaiʻi Realtors monthly MLS stats | Sales counts by price band, single-family vs condo, statewide and by county | Aggregates only; MLS misses off-market luxury sales |
| DOTAX monthly collections reports (the source of `data/dotax_monthly/collections.json`) | Conveyance tax collected per month | Totals only; the refresh script does not yet parse the conveyance line — add it |
| DOTAX annual report | Conveyance tax by fiscal year; check whether it breaks out conveyances by consideration class | If it does, that is the calibration target |

Plan: build sale-level records from the county files for the latest two
fiscal years, then **require that current law applied to them reproduces
DOTAX's conveyance collections** within a few percent. That is the model's
core validation, the analogue of the TY2022 food/excise check on the
working-family credits page.

### 2. Owner-occupancy

HD2's two schedules hinge on homeowner-exemption eligibility. From the county
files: exemption granted on the parcel in the following tax year, else a
mailing-address test (in-state vs out-of-state owner). Report a band (e.g. ±10
points on the owner share above $2M), because non-owner sales carry 5–7%
marginal rates and dominate the revenue.

### 3. Price and volume paths

Age prices with the Hawaiʻi HPI already in `data/markets/prices_panel.json`
(`fred_hi_hpi`); hold volume at the recent average, with a sensitivity. HD2's
brackets are CPI-indexed from 2027 while prices follow the HPI, so any gap
between the two drifts revenue.

## Method

1. **`tax_modeler.conveyance`** (new): both schedules as data (a cliff table
   and a marginal table with per-category caps), `conveyance_tax(value,
   category, schedule, year)`, per-unit multifamily handling, CPI indexing.
   Unit-test against the table above ($3.5M → $17,500 / $46,300).
2. **Static score:** Σ over sales of (HD2 − current), by FY, band, category.
3. **Behavioral response**, with its literature, as bands, not points:
   - *Transaction volume.* Transfer taxes cut sales sharply. Toronto's land
     transfer tax, about 1.1% of price, reduced sales roughly 15% (Dachis,
     Duranton & Turner 2012); a 1-point cut in UK stamp duty raised
     transactions about 20% in the short run and less over time (Best &
     Kleven 2018). *(Magnitudes from memory of the papers — verify before
     use.)* HD2 adds 0.3 to 2.4 points of price in the $2.5–5M range, so the
     response is first-order there, and the static score will overstate.
   - *Bunching and price response.* The marginal design removes the notches
     that drive bunching today (Kopczuk & Munroe 2015, New York mansion tax),
     but the 4%/6% caps and steep band edges could create some.
   - *Incidence.* Part is capitalized into lower prices (the seller bears it);
     affects distribution framing, not the revenue arithmetic.
   - *Timing.* A known effective date pulls sales forward; score the first
     year separately.
4. **Disposition:** apply §247-7's caps (and HD2's changes, once read) to
   split the change between the special funds and the general fund. Since the
   rental housing fund's $38M cap already binds, most new money lands in the
   general fund unless HD2 re-points it.
5. **Validation:**
   - current law on the microdata vs DOTAX collections (required);
   - sales above $3M vs the roughly **1,000 a year** reported by the Tax
     Foundation of Hawaiʻi (unverified);
   - the static HD2 gain vs the TFH figure of about **$100M a year**
     (unverified; also check which draft it scored).

## Site page (after the model)

A "Proposed policy" card and page modeled on the capital-gains page:
headline revenue, the cut-below-$2.2M / increase-above point with the
worked-example table, a $3–4M spotlight, revenue by year, who pays (by price
band and owner/non-owner, including the out-of-state buyer share), and a
check against DOTAX collections. Data through `scripts/build_site.py
--import-runs`, as the other pages do.

## Effort estimate

| Piece | Days |
|---|---:|
| County sales files → clean sale-level panel (4 counties) | 3–5 |
| DOTAX conveyance line in the collections refresh + annual-report pull | 0.5 |
| `tax_modeler.conveyance` + tests | 1 |
| Scoring, aging, behavioral bands, disposition | 2 |
| Validation and write-up (this doc → a methodology doc) | 1–2 |
| Site page | 1 |
| **Total** | **~9–12** |

The county data is the critical path. With MLS price-band aggregates only
(fallback), the estimate is feasible in ~3 days, but the owner-occupancy split
becomes an assumption rather than data.

## Risks

- **Top-end thin data.** A few dozen $10M+ sales carry a large share of the
  revenue and swing year to year; report a multi-year average and range.
- **Owner-occupancy misclassification** moves revenue a lot (5% vs 2% on
  $2–3M; 7% vs 4% above $3M).
- **Draft risk.** HD2 is the last printed version, not an agreement. The page
  should say so, and the schedule should be a parameter so a 2027 bill can be
  scored in hours.

## Open questions

1. Is there a conference draft (CD1) text or conference worksheet with rates
   beyond HD2? Reporting says the final days' drafts left rates blank.
2. HD2's disposition: does it re-point conveyance revenue (e.g. to DHHL or
   housing) and change the §247-7 caps?
3. Scoring start: first full year FY2027 (as if enacted in 2027), or a later
   effective date?
4. Should multifamily per-unit valuation be modeled? It matters for apartment
   buildings (per-unit values usually sit in the low brackets, so HD2 likely
   cuts their tax) but needs unit counts from the county files.
5. Owner-occupancy: exemption-after-sale vs mailing address — which does DOTAX
   itself use to apply schedule (2) today?

## Sequence

1. Answer open questions 1–2 (bill texts, committee reports, DOTAX testimony).
2. Build the conveyance module and tests (independent of data).
3. Pull DOTAX collections; build the county sales panel; validate current law.
4. Score HD2 (static, then behavioral); write the methodology doc.
5. Site page.
