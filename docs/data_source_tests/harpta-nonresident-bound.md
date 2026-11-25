# HARPTA withholding as a bound on nonresident capital gains

Branch `data/harpta-nonresident-bound`. Exploratory; changes no published number or default.

## Verdict: reject as a data source (no totals are published); the fallback bound is loose

DOTAX does not publish HARPTA (HRS 235-68) withholding or refund totals in anything I could reach. The conveyance-based fallback bound is consistent with the model's TY2023 level but cannot tell the low, central and high levels apart. A records request to DOTAX is the only way to get a tight bound.

## 1. What is published (checked 2026-11-24)

- DOTAX Annual Reports page, https://tax.hawaii.gov/stats/a5_1annual/a5_1tax_annuals/, lists FY1997-FY2025. The FY2024 file (`archive/24annrpt.pdf`) returned 404 from that URL pattern. I pulled and text-searched FY2012, FY2018-FY2023 (`https://files.hawaii.gov/tax/stats/stats/annual/archive/<yy>annrpt.pdf`) and FY2025 (`https://files.hawaii.gov/tax/stats/stats/annual/25annrpt.pdf`) for "HARPTA", "N-288", "real property tax act" and "nonresident".
- None gives withholding or refund totals. FY2025's individual income tax table (Table 1.2) has "Withholding Tax on Wages" ($2,739,876K) and refunds ($890,263K) with no HARPTA line. HARPTA appears only in audit narrative:
  - FY2018: the rate change from 5% to 7.25% (effective July 5, 2018); Maui Office Audit examined 901 HARPTA exemption claims with selling prices of $283.7M (82 disapproved, $50.1M).
  - FY2019: Maui, 792 exemption claims, $250.4M of selling prices (58 disapproved, $1.7M).
  - Audit adjustments (reductions of refunds claimed, not withholding): FY2020 $2.3M; FY2021 about $4.88M (the aggregate line); FY2022 $1.27M (Oahu N-288C adjustments); FY2023 $3.9M; FY2025 $4.2M, HARPTA and refundable credits combined.
- Monthly Tax Collections page https://tax.hawaii.gov/stats/a5_3txcolrpt/ (`.../monthly/YYYYMMcollec.pdf`, `2025cy-collec.pdf`): files.hawaii.gov answered HTTP 429 to repeated requests, so I stopped on that route and did not read a collections statement. Whether HARPTA receipts have their own line there is NOT verified; the annual report's tax breakdown has none.
- Not searched: Council on Revenues materials, legislative testimony and fiscal notes (no browser or Perplexity in this test). A targeted search there is the remaining cheap check.

Verification 2026-11-24 (resumed run): script re-run and reproduces the table; FY2025 and FY2018 report URLs return HTTP 200; FY2025 text re-searched for HARPTA. Other-year audit figures above were taken from the previous run's reading and not re-fetched.

## 2. The test run: an independent proceeds bound

Script: `scripts/harpta_nonresident_bound.py` (read-only, prints the table below). Inputs already in the repo:
- DOTAX Table 21 nonresident gain returns and gains, TY2018-2023 (`dotax_indinc_<year>.json`).
- `county_home_sales_tg_2015_2025.csv`: all-county SF and condo sales, count and dollars, by year, with BUYER origin (local, mainland, foreign). Excludes land and commercial.
- `nonresident_seller_origin_oahu.csv`: 21,564 Oahu resales, Dec 2023 to Sep 2026; sellers outside Hawaii (unknown excluded) are 14.6% of resale value, 15.1% with unknown.

Proceeds range: low = 14.6% (Oahu seller share) x all-county home-sale dollars; high = mainland plus foreign buyer share of home-sale dollars (TY2023: 34% statewide; Honolulu 23%, neighbor islands 49-56%), a stand-in for neighbor-island second-home sellers. That is an assumption, not data.

| TY | Nonres gain returns | Nonres gains $M | Implied nonres proceeds $B (low-high) | Gains / proceeds | Implied N-288 withholding at 7.25% ($M) |
|---|---:|---:|---|---|---|
| 2018 | 3,208 | 589 | 4.3-9.2 | 6-14% | 311-666 |
| 2019 | 3,299 | 556 | 4.0-7.3 | 8-14% | 287-531 |
| 2020 | 2,832 | 508 | 3.9-7.0 | 7-13% | 282-506 |
| 2021 | 5,543 | 1,279 | 6.5-15.1 | 8-20% | 468-1,094 |
| 2022 | 4,467 | 1,217 | 5.4-12.2 | 10-22% | 393-887 |
| 2023 | 3,107 | 967 | 4.0-9.4 | 10-24% | 290-680 |

The withholding column is a model output, not an observed total (and the rate was 5% for part of 2018).

Checks:
- Count: TY2023 Oahu nonresident seller share by count (13.7%) times 31,276 TG sales is about 4,270 nonresident sales; Table 21 shows 3,107 gain returns, about 73%. Coherent (losses, exemptions, co-owners).
- The model's TY2023 levels (`nonresident_cg`: low 0.78x, central 1.0x, high 1.45x of $967M) imply gains of 8-19%, 10-24% and 15-35% of proceeds. All three sit inside plausible gain-to-price ratios for long-held and deferred property. The bound does not discriminate among them; it only excludes a level several times the high case.
- Direction matches `nonresident_volume_check.py`: gains per dollar of proceeds rise through 2021-23 with appreciation.

## 3. Limits
- Proceeds are not observed. The roughly 2.3x spread from low to high comes from unknown seller origin outside Oahu; turning proceeds into gains needs a gain-to-price ratio, a second assumption.
- Real HARPTA data would be a lower bound on proceeds: N-288B waivers and exemptions (principal residence, 1031 exchanges, losses, clearance certificates) reduce withholding, and withholding is on amount realized, not gain. N-288C refund claims net out over-withholding and are the better signal of true tax on gain.
- Pass-through-entity (composite) sales are outside this comparison.

## 4. What adopting would change
Nothing now. With data, the nonresident level (TY2023 held; low/high from TY2018-20 and TY2021-22 means via FHFA repricing) could be anchored to net HARPTA (N-288A withheld minus N-288C refunded) by year in place of the repricing and the low/high weights.

## 5. A records request to DOTAX (HRS ch. 92F) should ask for
By calendar or fiscal year, 2018 to latest, by county of property if possible:
1. N-288/N-288A: number of forms, total amounts realized, total withholding remitted.
2. N-288B certificates: number and amounts realized by basis (principal residence, 1031, loss, other).
3. N-288C refund claims: number, amount claimed, amount paid, and the matching return's tax and gain.
4. Withholding per remittance by size band (to see the $1M+ tail) and seller residence (state or foreign), no names.
5. Which account and monthly-statement line carry HARPTA receipts, if any.
Matching N-288C to Table 21 gain returns would reconcile the TY2023 $967M directly.

Raw downloads (about 28 MB) stayed in /tmp and are not committed.
