# Conveyance Tax (SB 3028, 2026) — Scope

**Status (2026-09-25): built.** Published as the "Top of the Market" page
(`site/conveyance-tax/`), from `forecast_conveyance_sb3028.py`. Revised
four times on 2026-09-25 (HST):

1. The first version (2026-09-24) scaled Maui's results by conveyance
   collections and gave $69M–$118M after the sales response.
2. The first revision scaled by tax-roll value above $2M and gave $67M.
3. The second built Oʻahu and Hawaiʻi Island from their housing stock at
   Maui's sale rates, with one Maui calibration factor (k = 1.35), and gave
   $70M.
4. The third (2026-09-25, published) calibrated each county to its own sales by
   price and gave $79.1M with a single response of sales.

The current version, described in **As built** below, models that response
channel by channel with a Monte Carlo simulation, and scores the draft on every
past market since FY2016. It uses only public or in-hand data. What changed
across the third and fourth versions:

- Oʻahu, Hawaiʻi Island and Kauaʻi are each calibrated to their own sales by
  price band.
- Kauaʻi is built from its own parcel values.
- Maui's extract is corrected:
  - Land Court deeds covering several parcels now count once.
  - Owner-occupancy no longer caps the exemption at $300K.
  - Near-miss tax matches, undated rows and repeated prices are handled.
- The sales response comes from the UK and Los Angeles evidence, and is now
  structural: volume, developer lag, price, buyer mix, company sales and
  timing, each with a prior, and a 1,000-draw simulation for the range.
- The draft is scored on each fiscal year's market, FY2016–26.
- The current-law baseline is DOTAX grown by the model rather than rescaled
  by Maui's recorded tax.

Title Guaranty's reports are used with its permission (see below).

The rest of this document is the original scope, kept for its reasoning.
Goal: a "Proposed policy" estimate on the estimates site for restructuring
Hawaiʻi's conveyance tax the way the 2026 Legislature last had it, with
attention to the $3–4 million homes where the change bites hardest.

## As built

| Piece | Where |
|---|---|
| Schedules (current §247-2 cliff; HD2 marginal with caps and CPI indexing; HB 2049 HD3 and HB 1410 HD2 as benchmarks, `BILLS`); tier interpolation (`value_above`) | `packages/tax_modeler/src/tax_modeler/conveyance.py`; tests `tests/tax_modeler/test_conveyance.py` |
| Maui sales, stock, sales-by-value, value × price | `data/raw/conveyance/maui_*.csv`, `maui_sources.json`, from `scripts/conveyance/maui_sales_extract.py`; tests `tests/test_maui_sales_extract.py` |
| Oʻahu, Hawaiʻi County and Kauaʻi housing stock | `parcel_stock_by_value.csv` (+ `_hawaii_capped`, `hawaii_homeowner_cap_factors.csv`), from `scripts/conveyance/fetch_parcel_stock.py` (public ArcGIS REST, completeness-checked); tests `tests/test_fetch_parcel_stock.py` |
| MLS house and condo sales by price band and county, FY2023–26 | `mls_sales_by_band_fy2023_2026.csv`, `luxury_report_counts.csv`, from `scripts/conveyance/mls_sales_by_band.py` (reads a local PDF cache; see Title Guaranty's terms); tests `tests/test_mls_sales_by_band.py` |
| Oʻahu ownership turnover check | `oahu_owner_turnover_cells_2023_2026.csv`, `maui_sale_composition_fy2023_2026.csv`, `oahu_turnover_review_tallies.csv`, from `scripts/conveyance/oahu_owner_turnover.py`; tests `tests/test_oahu_owner_turnover.py` |
| Company-owned share of top non-owner sales | `honolulu_entity_share_by_band.csv`, from `scripts/conveyance/honolulu_entity_share.py`; tests `tests/test_honolulu_entity_share.py` |
| Other inputs | `county_home_sales_tg_2015_2025.csv`, `rpt_residential_tiers_fy2027.csv`, `pums_owner_value_tail_2020_2024.csv` (`pums_owner_tail.py`). The old Oʻahu MLS tables (Data Book 21.33–21.34) were removed, superseded by the MLS band file. |
| Scoring, county build-up, checks, benchmarks, disposition | `forecast_conveyance_sb3028.py` → `runs/conveyance_sb3028/`; tests `tests/test_conveyance_forecast.py` |
| Page | `scripts/build_site.py` `build_conveyance()`; data in `site/data/conveyance-tax/` |

**Maui: every sale** (`maui_sales_extract.py`, pure Python).

- Only Maui publishes a file of every recorded sale with its price *and* the
  conveyance tax paid (the "RPT Sales Data File"). The file is fixed-width
  and is parsed by the header's positions.
- **Multi-parcel deeds.** Rows are keyed by instrument number or Land Court
  document number (the two are joined when a row has both), so a deed
  covering several parcels counts once.
  - The browser extractor counted each Land Court deed once per parcel at
    the full price: in FY2023–26, 58 deeds were counted 204 times.
  - That overstated the statewide estimate by about 4%.
- **Schedule.** The schedule is the nearer of (1) and (2) when within 2% of
  the tax. 98% of FY2023–26 documents match; the rest are nonresidential.
- **Price and date.**
  - Rows that disagree on price take the value the tax fits.
  - Rows without a record date are dated by sale date.
- **Owner-occupied** = homeowner tax class 9 with an exemption, not fully
  exempt. The old $300K cap misclassified 1,783 homes with add-on
  exemptions. With it removed, 66% of Maui's $2M+ homes are not
  owner-occupied, not 69%.
- **Vacant land.** Schedule-(1) purchases of residential land with no
  building are nonresidential: HD2's §247-2(a)(3) keeps property with no
  dwelling unit on schedule (1).
- **CPR master records** are excluded from the stock.
- **Apartment buildings** (5+ units) are scored under HD2's per-unit rule and
  appear in exactly one file.
- **How the raw files were obtained.** The county site answers Python
  requests with HTTP 404. The research round fetched the two zips anyway
  with a browser User-Agent before this was flagged. The cached files are
  copies of that 2026-09-25 download, byte-identical to the county's
  published files. `maui_sources.json` records this. The committed script
  never poses as a browser: a refresh needs the zips saved from a browser.

**Oʻahu, Hawaiʻi Island, Kauaʻi: homes × Maui's sale rates, calibrated to each county's sales by price** (`stock_method`).

- **Homes** (improved residential parcels and condo units, by assessed
  value and homeowner exemption):
  - *Oʻahu*: RPAD's ASMTPITT. CPR master records are excluded (533 homes,
    $1.07B, which duplicated their units' value).
  - *Hawaiʻi County*: the State GIS parcel layer.
    - Condo projects are split into units. The owner units in each homeowner
      project are set from its exemptions (share about 0.26).
    - The county caps homeowner values (3% a year). Within a plat, owner
      homes sit at 0.78× their non-owner neighbors, against 0.99 on Maui and
      1.00 on Honolulu. Owner homes are therefore divided by zone × class
      factors, f (`hawaii_homeowner_cap_factors.csv`).
    - Condo units, which carry their project's average value, are divided by
      s·f + 1 − s, where s is the project's owner share.
    - Agricultural parcels with a building of $100K+ count as homes, here and
      on Kauaʻi.
  - *Kauaʻi*: the County's TY26_PropertyTaxData layer, at market value
    (MODTOT). The improved flag comes from BUILDINGS_public, whose service
    text reserves it for internal and partner use although the County lists
    it publicly; the page discloses this. The layer matches the valuation
    report: non-owner taxable above $2M is $2.83B against $2.80B.
- **Maui's rates.** Per value bin × occupancy: arm's-length sales per home,
  buyer mix, and price over assessed value. Developer first sales are
  included.
- **Price spread.** Each cell's sales are spread over 40 prices on a
  mean-preserving lognormal with σ = 0.25, which best matches Maui's sales by
  price band (fitted σ = 0.24). Results barely move with σ: $78.6–79.6M for
  σ from 0 to 0.4.
- **Calibration by price band** (`band_weights`):
  - From $1M, each county's synthetic sales in each band ($1–3M, $3–6M,
    $6–10M, $10M+) are scaled to MLS houses + condos × (Maui recorded home
    sales ÷ Maui MLS) × the county's unlisted-share adjustment.
  - The adjustment is 1 except on Oʻahu (1.00 / 1.06 / 0.95 / 0.96), for
    tower closings off the MLS and near-absent vacant-lot sales.
  - Below $1M, every county takes Maui's recorded ÷ synthetic scale (1.50).
  - This brings in, in Maui's proportions, what the rates leave out: vacant
    lots, and transfers under 0.5× or over 2.5× assessed value. The residual
    (Maui exact ÷ Maui calibrated synthetic) is 1.03, against the old k of
    1.35.
- **What the calibration found.** Weights relative to Maui's:

  | County | $1–10M | $10M+ |
  |---|---|---|
  | Hawaiʻi Island | 1.3–1.9× Maui's rates | 0.84× |
  | Oʻahu | 0.81× at $1–3M; 1.03–1.17× at $3–10M | 0.85× |
  | Kauaʻi | 0.9–1.6× | 0.63× |

  Hawaiʻi Island's $10M+ band was an old worry. Its MLS count is 17 a year,
  and the model's recorded-equivalent figure is 18.
- **Market inputs** (`mls_sales_by_band.py`):
  - Title Guaranty's monthly Residential Sales Reports, parsed from the drawn
    bars and summed by July–June fiscal year, less land using the year-end
    house/condo shares. Kauaʻi uses the year-end shares.
  - Hawaiʻi Life luxury reports and List Sotheby's ($10M+ and the $6M split).

**Title Guaranty's terms and permission.** Hawaiʻi Appleseed has Title
Guaranty's permission to use and publish data from these reports (confirmed
2026-09-25), so the calibrated estimate is published as the central figure.
Without that permission, the FNF Terms of Use (Aug 15, 2021) would bar three
things:

- "any robot, spider, or other automatic devise" [sic];
- any process "(manual or automatic) to gather, extract, monitor, or copy
  any information";
- publishing links to the site.

The research round downloaded 170 report PDFs automatically, six at a time
under a browser User-Agent, before the terms were read. The local cache
(`runs/conveyance_sb3028/mls_cache/`, gitignored) is a copy of that
download. The committed script downloads nothing and prints no links, and
the page cites the reports without a link. Without the calibration (the
first sensitivity row), the estimate would be $72.9M with the sales
response, against $77.7M.

**Behavior** (`Behavior`, `score`, `simulate`). Research notes and every source
read are in the session scratch (behav2/lit-structure, hawaii-experiments);
the evidence, channel by channel:

| Channel | Central | Prior (Monte Carlo) | Evidence |
|---|---|---|---|
| Lasting volume response | ε = 6% per point of price added in tax | lognormal, median 6, sd log 0.38 (3.2–11.2) | OBR Oct 2017 (£1M+: 6.0 in year 1, year 2 and steady state, from the Dec 2014 slab-to-slice reform); Measure ULA single-family ≈6.5 (Green et al. 2025), 4.5–7.5 (RAND 2026); Germany 5.5–6.5 long run; Toronto >$400K ≈6 (ns). Lower: France, DC ≈0. Higher: Australia, mobility studies |
| Non-owner buyers vs others | ×1.0 | lognormal, sd log 0.15 | No study compares buyer types at the same price and tax |
| First year | ×1.1 | uniform 1.0–1.25 | OBR lower bands' year 1 ≈15% above steady state; £1M+ equal |
| Curvature | d × (d/3)^0.125 | uniform 0–0.25 | Small changes show no lasting effect (DC, France); OBR ≈ ULA per point |
| Developer first sales | none in years 1–2, then 0.5, 0.75, 1 | fixed | Maui SA code 8: 24% of $10M+ home-sale value, 13% at $6–10M; presold inventory closes; no grandfather clause |
| Recorded price | −0.5% per point; tax on the lower price | triangular −0.4, 0.5, 1.5 | Seller pays (HRS §247-4(a)); OBR buyer-paid −1.5 ⇒ −0.5 gross; capitalization 0.6–0.8 |
| Owner-schedule shift | 2% of non-owner purchases per point of gap (cap 25%) | triangular 0, 2, 6 | Only 41% of FY2014–23 $4M+ owner-schedule buyers still holding had a homeowner exemption in 2026 (Maui); Vancouver foreign-buyer tax; HD2's TVR clause limits it |
| Company sales | 20% of company-seller $4M+ non-owner sales, growing 7.5% of itself a year | triangular 0, 0.2, 0.5; growth uniform 0–0.15 | No controlling-interest rule; TFH "nothing is required to be reported to anyone"; UK enveloping underestimated (OBR WP8) |
| Timing (sensitivity) | 0.4 months of sales per point pulled ahead, cap 2 | — | OBR WP10 ≈1/pt; King County 0.4–0.85; NYC 2019 0.5–1.5 with equal payback; Maui 2005 and 2009 ≈1 month dip |
| Market data | central MLS counts | triangular low–high by county and band | mls_sales_by_band |

- The simulation's drivers (rank correlation with FY2029): ε −0.76,
  company sales −0.40, non-owner multiplier −0.25, price −0.22, owner
  shift −0.12.
- **Hawaiʻi's own evidence.** Maui's file cannot pin down ε:
  - The 2009 (Act 59) and 2005 rate changes were too small, on too few
    sales. The placebo spread is 125% per point.
  - Sales do sort below today's $1M and $2M cliffs, but they are repriced
    rather than lost; HD2 removes the cliffs.
  - Forced sales (estates, divorce, foreclosure) are only 1–2% of $2M+
    sales, so they need no separate channel.

**Results ($M):**

| FY2028 | Static | Central (structural) |
|---|---:|---:|
| Maui County (every sale) | 34.3 | 22.5 |
| Oʻahu | 34.4 | 23.0 |
| Hawaiʻi Island | 34.0 | 21.7 |
| Kauaʻi | 15.9 | 10.6 |
| **State** | **118.7** | **77.7** |

- **Path.** FY2029 $81.2M, FY2030 $80.9M, FY2031 $81.7M.
  - The first-year overshoot lowers FY2028 by about $2M.
  - The developer lag raises FY2028 and FY2029 by about $4.5M each; the lasting response alone gives $75.8M in FY2028.
  - The path flattens after FY2029 as developer sales begin to respond and company sales grow.
  - Static grows about 3.9% a year, near home prices.
- **Simulation (1,000 draws), 5th–95th percentile.** FY2028 $56.1–91.8M (median $76.5M); FY2029 $59.9–96.2M; FY2031 $56.0–99.6M.
- **Current law and disposition.** Current law is $119.5M in FY2028. The general fund gets $27.1M less.
- **Past markets** (`market_cycle.csv`; Maui every sale, the state scaled by the base years' ratio by component). DOTAX's FY2021–22 collections are re-timed by sale: about $36M of FY2021 tax was deposited in FY2022, giving $99.0M and $152.2M against $62.7M and $188.4M reported.
  - FY2022 at its own prices: +$173M static and +$109M with the response. Total collections are about $325M static and $261M with the response.
  - At FY2028 prices: FY2022's market gives +$162M. The FY2016–26 average is +$81M and the FY2023–26 base +$76M (all with the response, at its lasting level).
- **The "$300 million".**
  - No DOTAX estimate near $300M exists. Checked: every 2025–26 conveyance measure's testimony and fiscal notes, and the Tax Review Commission.
  - It is the "up to $300 million a year" for HB 2049 in Appleseed's July 14, 2026 blog. That is a *total*: the testimony table's FY2031 total ($302.6M).
  - This model, static, puts HB 2049 HD3's FY2031 total at $297.6M and SB 3028 HD2's at $274.0M.
  - The Feb 2024 brief's $302.8M is also a total, for SB 678 on 2022 sales. The Tax Fairness Coalition's "extra $300–400M" is an increase claimed for SB 678's steeper whole-price rates.

**Sensitivity** (FY2028, static / central, $M):

| Variant | Static | Central |
|---|---:|---:|
| Not calibrated to county sales (Maui rates and scale) | 112.0 | 72.9 |
| MLS counts low / high | 100.1 / 147.4 | 65.5 / 96.4 |
| Oʻahu turnover from owner rolls (1.63× at $4M+) | 132.9 | 86.8 |
| No price spread | 118.7 | 78.2 |
| Hawaiʻi homeowner values capped | 119.4 | 78.2 |
| Kauaʻi from tax-roll tiers | 117.7 | 76.8 |
| ε 4 / ε 10 | | 85.3 / 64.4 |
| Non-owner buyers respond 30% more | | 71.9 |
| Developer sales respond at once | | 73.4 |
| Recorded prices do not fall / fall 1.5% per point | | 80.8 / 71.6 |
| Owner-schedule shift 0 / 6% per point | | 79.4 / 74.4 |
| Company sales 0% / 50% | | 85.5 / 66.1 |
| First year after a pre-effective-date rush | | 64.5 |
| Single response (third revision: ε 6, 25% company sales) | | 79.1 |
| Second revision's assumptions (ε 10, no company sales) | | 72.7 |

**Checks** (`summary.json` → `checks`, `benchmarks`):

- **Oʻahu's own turnover.** The frozen tax-year-2024 owner roll (Dec 2023)
  is diffed against the live roll (Sept 25, 2026). Sale-like changes are
  counted, excluding developer first sales, bulk buyers and lessee changes.
  A hand review found 91–93% of them were real sales.
  - Like for like against Maui's rates: <$1M 1.05, $1–2M 0.95, $2–4M 1.02,
    $4M+ 1.63 (90% range about 1.38–1.88).
  - Calibration to MLS by price implies only 1.05 at $4M+. Oʻahu's top
    homes turn over more often but appear to sell lower relative to their
    assessments. The owner-roll case is the upper sensitivity.
- **DOTAX residual.** DOTAX collections less Maui's recorded tax averaged
  $75.4M in FY2023–25.
  - The model's current-law tax on home sales in the other three counties
    is $48.3M, which leaves 36% to nonresidential sales, against Maui's 24%.
  - At Maui's share, the modeled home sales would be about 19% short.
- **House.** Scored the same way, before any change in sales:
  - HB 2049 HD3 raises $20.6M more than SB 3028 HD2 in FY2028 ($16.4M at
    base prices), against Chair Todd's "about $20M less".
  - Same-or-less shares: 90% for HD2 against Todd's 91%; 78% for HD3
    against Rep. Evslin's 75%.
  - The House's levels are 1.26× this estimate (FY2028) and 1.60× at base
    prices ($93.5M).
- **DOTAX, HB 1410 HD2 (2025).** DOTAX's estimate of +$57.9M in FY2026 is
  1.44× the model's $24.3M on homes plus about $15.8M nonresidential
  (Maui's own change, scaled). This is a weak check.
- **DOTAX, SB 362 SD1 (2023).** Its +$8.2M is less than Maui alone yields.
  Excluded.
- **Direction.** These checks all suggest the estimate is more likely low
  than high.

**Where the money comes from.**

- Non-owner buyers supply 88% of the static increase.
- Maui's $3–4M band:
  - 50.5 non-owner and 18.5 owner sales a year.
  - Average tax rises from $20.5K to $83.6K (non-owner) and from $16.9K to
    $38.5K (owner).
  - The band supplies 10% of Maui's gain.
- The draft raises the tax on sales just under $2M: from $1.80M for
  owner-occupants and $1.68M for other buyers, by up to $300 or $800.

**Other estimates, provenance:**

- "$270M+" (Appleseed infographic, April 2026) is a *total*: FY2027
  collections under HB 2049 HD3 rates in Appleseed's 2026 testimony table
  ($270.8M).
- "$300M" traces to SB 678 (2023), a steeper cliff bill on all property:
  the Feb 2024 Appleseed brief ($302.8M in total from 2022 sales) and
  hitaxfairness ("extra $300–400M", unsourced). Neither measures SB 3028.
- "$68.5M" for HB 2049 in advocacy testimony has no stated source.

**Still left out:**

- Leases.
- HD2's vacation-rental clause (former TVRs taxed on schedule (2) whoever
  buys), about +$0.5M static on Maui.
- Maui homes on agricultural or other nonresidential land, which HD2 would
  move to the residential schedules: about +$0.2M static on Maui, with an
  upper bound of +$16.5M if every Maui nonresidential sale under $20M were a
  non-owner home.
- CPI indexing of schedule (3): about −$0.04M.

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
