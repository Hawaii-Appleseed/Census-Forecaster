"""Conveyance tax restructuring (SB 3028 SD2 HD2, 2026): revenue and who pays.

Usage:
  python forecast_conveyance_sb3028.py        # writes runs/conveyance_sb3028/

Question
--------
SB 3028 HD2 (April 8, 2026), the last printed draft before the bill failed in
conference, would replace Hawaii's cliff conveyance tax with marginal rates for
residential sales: 2% (owner-occupant) / 5% (other) on the $2-3M slice of a
price, 4% / 7% on $3-6M, up to 6% / 9% above $10M, capped at 4% / 6% of the
price; brackets CPI-indexed from 2027; nonresidential property unchanged.
What would it raise, and who would pay? Scope: CONVEYANCE_TAX_SCOPE.md.

Maui: every sale
----------------
Maui County's public sales file (every recorded conveyance, with price and
the conveyance tax paid) and full assessment listing, extracted to
data/raw/conveyance/ by scripts/conveyance/maui_sales_extract.py. The tax paid
identifies which schedule applied, so owner-occupancy is observed, not
assumed: nearly all priced, taxed conveyances in FY2023-FY2026 match schedule
(1) or (2) (the rest are treated as nonresidential, which HD2 leaves
unchanged; summary.json gives the share). Schedule-(1) sales of homes are owner-occupant; of other property,
including land with no dwelling (HD2 §247-2(a)(3)), nonresidential. A deed
covering several parcels counts once. Current law applied to the binned data
reproduces the tax Maui recorded on home sales to within a few hundred dollars
a year.

Statewide: each county's homes, calibrated to its sales by price
----------------------------------------------------------------
No other county publishes a file of its sales with prices (their property
search sites show sales a parcel at a time, under terms that bar collecting
them in bulk). Each is built from its own
parcel records with Maui's sales as the template, then calibrated to its own
home sales by price band:

  1. Homes (parcel_stock_by_value.csv, scripts/conveyance/fetch_parcel_stock.py):
     every residential parcel and condominium unit with a building, by
     assessed value and owner-occupancy. Honolulu: the Real Property
     Assessment Division's tables on the City's open data hub. Hawaii County:
     its parcel layer on the State GIS portal, condo projects split into units
     and homeowner values, which the county caps, restored to market. Kauai
     County: its tax-year 2026 property tax layer on its open data hub.
  2. Sales (stock_sales): Maui's full assessment listing joined to every
     FY2023-26 sale gives, per assessed-value bin and owner-occupancy,
     arm's-length sales per home per year, the buyer mix (owner or non-owner
     schedule) and sale price over assessed value (maui_rates). Applied to a
     county's homes, with prices spread around each cell's mean as they are
     on Maui (PRICE_SIGMA), these give synthetic sales.
  3. Calibration by price band (band_weights): from $1M up, each county's
     synthetic sales are scaled to its own recorded home sales in the band:
     its MLS house and condominium sales (Title Guaranty's monthly reports,
     Hawaii Life and List Sotheby's; mls_sales_by_band_fy2023_2026.csv) times
     Maui's ratio of recorded home sales to MLS sales in the band, adjusted
     where the county's unlisted share differs (Oahu's new condominium
     towers). Below $1M, and on Maui, the scale is Maui's recorded home sales
     over its synthetic sales. So the counties' sales include, band by band,
     the kinds of sale Maui's rates leave out (vacant-lot sales, transfers at
     under half or over 2.5 times assessed value, mostly nominal-price and
     related-party, and parcels the stock leaves out) in Maui's proportion.
     Developer first sales are already in the rates. The remaining residual, Maui's
     exact change over its own calibrated synthetic change, is applied to all
     three counties.

Checks (summary.json): Oahu's own ownership turnover from two snapshots of the
City's owner roll (oahu_owner_turnover_cells_2023_2026.csv), against Maui's
sale rates; each county's modeled home sales against Title Guaranty's; DOTAX
collections net of Maui's recorded tax, against modeled current-law tax on
homes elsewhere; and official and sponsor estimates for related bills
(benchmarks).

Behavior
--------
Sales volume falls VOLUME_SEMI_ELASTICITY percent per percentage point of price
added in tax (rises where HD2 cuts the tax): 6, the UK Office for Budget
Responsibility's figure for homes of GBP 1M+ after the UK moved from cliff to
slice rates in 2014, close to estimates for Los Angeles' Measure ULA on
single-family homes over $5M; range 4-10. Chapter 247 taxes documents that
convey real property, not transfers of interests in entities that own it, so
a share ENTITY_TAKEUP (25%, range 0-50%) of non-owner sales of $4M+ whose
seller is an LLC, corporation or partnership (entity_share(), from Honolulu's
owner roll: honolulu_entity_share_by_band.csv, scripts/conveyance/
honolulu_entity_share.py) is assumed to escape HD2 by selling the entity
instead.

Outputs (runs/conveyance_sb3028/):
  revenue_by_year.csv   FY2028-FY2031: current law, HD2 change, Maui and each
                        county, static and with the behavioral response
  by_county.csv         FY2028: each county's method, homes and change
  band_calibration.csv  each county's synthetic and target home sales a year by
                        price band, and the weights between them
  honolulu_entity_share_by_band.csv
                        company-owned shares of Oahu's top homes and sales (input)
  county_bands.csv      FY2028 prices: home sales a year by price band and buyer
                        type, by county
  sensitivity.csv       FY2028 statewide change under alternative choices
  maui_by_band.csv      Maui, average year: sales, tax now vs HD2, by price
                        band and purchaser category
  examples.csv          tax on example prices under both laws
  disposition.csv       where the money goes, FY2028 with the behavioral response
  summary.json          shares of sales paying less / same / more; $3-4M
                        spotlight; breakevens; checks; benchmarks
  manifest.json
"""
from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy.stats import norm
from tax_modeler.conveyance import (
    BILLS,
    CATEGORIES,
    breakeven_price,
    current_law_tax,
    value_above,
)

REPO = Path(__file__).parent
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT_DIR = REPO / "runs" / "conveyance_sb3028"

TARGET_YEARS = [2028, 2029, 2030, 2031]   # first full year if enacted in the 2027 session
MAUI_BASE_FY = [2023, 2024, 2025, 2026]
STATE_BASE_FY = [2023, 2024, 2025]        # DOTAX FY2026 not yet published
TG_YEARS = [2023, 2024, 2025]             # county home sales, calendar years
COUNTIES = ("Honolulu", "Hawaii", "Kauai", "Maui")
TAIL_AT = 2e6                             # HD2 raises the tax only above ~$2.1-2.2M

PRICE_GROWTH = 0.04          # nominal; FHFA Hawaii HPI +3.9% 2023-24, 6.5%/yr 2014-24
CPI_GROWTH = 0.03            # Urban Hawaii CPI 2.9%/yr 2015-25; drives HD2 bracket indexing

# DOTAX Annual Report FY2024-25, Table 1.11 (conveyance tax collections, $M).
DOTAX_COLLECTIONS_M = {2016: 66.083, 2017: 94.537, 2018: 100.603, 2019: 85.965, 2020: 61.110,
                       2021: 62.725, 2022: 188.418, 2023: 92.132, 2024: 97.411, 2025: 96.045}

# Disposition, HRS §247-7 today and as HD2 would amend it: (share, cap $M).
DISPOSITION_NOW = [("Land conservation fund", .10, 5.1), ("Rental housing revolving fund", .50, 38.0)]
DISPOSITION_HD2 = [("Land conservation fund", .05, 10.0), ("Rental housing revolving fund", .20, 40.0),
                   ("Hawaiian home lands infrastructure and housing fund", .30, 60.0),
                   ("Transit-oriented development infrastructure (dwelling unit revolving fund)", .20, 40.0)]

BILL = "SB3028 HD2"
PER_UNIT_BILLS = ("SB3028 HD2", "HB2049 HD3")   # 5+ units: rate set by the price per unit


# ─────────────────────────────────────────────────────────────────────────────
# Behavior

@dataclass(frozen=True)
class Behavior:
    """Responses to the new schedule.

    elasticity: percent fewer sales per percentage point of price added in tax
      (one number, or one per category).
    entity_takeup: share of entity-held non-owner sales of $4M+ that escape the
      tax by selling the entity instead of the home (they pay nothing).
    forestall: (months of sales pulled ahead of the effective date per point of
      added tax, cap in months), for a first year only.
    cert_shift: share of non-owner buyers at $4M+ who claim the owner-occupant
      schedule.
    """
    elasticity: float | dict = 0.0
    entity_takeup: float = 0.0
    forestall: tuple[float, float] | None = None
    cert_shift: float = 0.0


VOLUME_SEMI_ELASTICITY = 6       # OBR (UK, GBP 1M+ homes) 6; LA Measure ULA single-family ~4-7
ELASTICITY_RANGE = (4, 10)
ENTITY_TAKEUP = 0.25
ENTITY_TAKEUP_RANGE = (0.0, 0.5)


@lru_cache(maxsize=1)
def entity_share() -> tuple[tuple[float, float], ...]:
    """Share of non-owner purchases whose seller is an LLC, corporation or
    partnership, by price band: Honolulu's owner roll (OWNDAT) x its homes,
    weighted by who sells to non-owner buyers. ((lower bound, share), ...)
    from the top band down."""
    e = pd.read_csv(DATA / "honolulu_entity_share_by_band.csv").sort_values("band_lo", ascending=False)
    return tuple(zip(e["band_lo"].astype(float), e["seller_weighted_entity_share"].astype(float), strict=True))


STATIC = Behavior()
CENTRAL = Behavior(VOLUME_SEMI_ELASTICITY, ENTITY_TAKEUP)
LOW = Behavior(ELASTICITY_RANGE[1], ENTITY_TAKEUP_RANGE[1])     # least revenue
HIGH = Behavior(ELASTICITY_RANGE[0], ENTITY_TAKEUP_RANGE[0])    # most revenue
CASES = {"static": STATIC, "behavioral": CENTRAL, "behavioral_low": LOW, "behavioral_high": HIGH}


# ─────────────────────────────────────────────────────────────────────────────
# Maui

def _bin_lo(price: float) -> int:
    edges = [6e5, 8e5, 1e6, *np.arange(1.25e6, 6e6 + 1, 2.5e5), *np.arange(6.5e6, 1e7 + 1, 5e5)]
    return 0 if price < 6e5 else int(max(e for e in edges if price >= e))


def load_sales() -> pd.DataFrame:
    """One row per bin (or per $10M+ sale): fy, category, count, mean price,
    units. Sales of buildings with five or more dwelling units come out of
    their bins as category "mf", scored under HD2's per-unit rule (score)."""
    b = pd.read_csv(DATA / "maui_sales_bins_fy2023_2026.csv")
    mf = pd.read_csv(DATA / "maui_multifamily_sales_fy2023_2026.csv")
    for r in mf.itertuples():
        if r.price >= 10e6:                 # listed only here, not in the bins or the $10M+ list
            continue
        i = b.index[(b["fy"] == r.fy) & (b["category"] == r.category) & (b["bin_lo"] == _bin_lo(r.price))]
        if not len(i):
            raise ValueError(f"multifamily sale FY{r.fy} {r.category} ${r.price:,.0f} has no price bin")
        b.loc[i[0], ["count", "sum_price"]] -= (1, r.price)
    b = b[b["count"] > 0].copy()
    b["price"] = b["sum_price"] / b["count"]
    t = pd.read_csv(DATA / "maui_sales_over_10m_fy2023_2026.csv").assign(count=1, bin_lo=10_000_000)
    mf = mf.assign(category="mf", count=1, bin_lo=mf["price"].map(_bin_lo))
    cols = ["fy", "category", "bin_lo", "count", "price", "units"]
    return pd.concat([b.assign(units=1)[cols], t.assign(units=1)[cols], mf[cols]], ignore_index=True)


def score(sales: pd.DataFrame, target_fy: float, bh: Behavior = STATIC, bill: str = BILL) -> pd.DataFrame:
    """Current-law and new-law tax for each base-year row aged to *target_fy*.

    *fy* may be fractional (a calendar year y is fiscal y + 0.5)."""
    s = sales.copy()
    if "units" not in s:
        s["units"] = 1
    s["price_t"] = s["price"] * (1 + PRICE_GROWTH) ** (target_fy - s["fy"])
    tax_fn, first_index = BILLS[bill]
    # The bill indexes the brackets for each calendar (taxable) year from
    # first_index on, so the bill as written gives FY2028 1.5 adjustments on
    # average; enacted in the 2027 session it would miss the first recompute
    # (due December 15, 2026), giving 0.5. One is the midpoint.
    index = (1 + CPI_GROWTH) ** max(target_fy - first_index, 0)
    p, cat = s["price_t"].to_numpy(), s["category"].to_numpy()
    cur, new = np.zeros(len(s)), np.zeros(len(s))
    for c in CATEGORIES:
        m = cat == c
        cur[m] = current_law_tax(p[m], c)
        new[m] = tax_fn(p[m], c, index=index)
    # Five or more units: schedule (1) on the whole price today; under HD2 the
    # rate is set by the price per unit (a non-owner purchase), applied to the whole.
    m = cat == "mf"
    if m.any():
        u = s.loc[m, "units"].to_numpy()
        cur[m] = current_law_tax(p[m], "nonres")
        new[m] = u * tax_fn(p[m] / u, "nonowner", index=index) if bill in PER_UNIT_BILLS else tax_fn(p[m], "nonres", index=index)
    top = (cat == "nonowner") & (p >= 4e6)
    if bh.cert_shift:
        new[top] = (1 - bh.cert_shift) * new[top] + bh.cert_shift * tax_fn(p[top], "owner", index=index)
    s["tax_now"] = cur * s["count"]
    # Behavioral: sales volume responds to the change in tax as a share of price.
    d_pp = (new - cur) / p * 100
    e = (np.array([bh.elasticity.get(c, 0.0) for c in cat]) if isinstance(bh.elasticity, dict)
         else bh.elasticity)
    n = s["count"].to_numpy() * np.exp(-e / 100 * d_pp)
    if bh.forestall:
        per_pp, cap = bh.forestall
        n = n * (1 - np.minimum(per_pp * np.maximum(d_pp, 0), cap) / 12)
    # Entity sales: the home stays with the LLC, the buyer buys the LLC, no deed.
    share = np.select([p >= lo for lo, _ in entity_share()], [sh for _, sh in entity_share()], 0.0)
    keep = 1 - bh.entity_takeup * share * (cat == "nonowner")
    s["count_hd2"] = n
    s["tax_hd2"] = new * keep * n
    s["tax_hd2_static"] = new * s["count"]
    s["gains"] = new > cur
    return s


def components(sales: pd.DataFrame, target_fy: float, bh: Behavior = STATIC, bill: str = BILL) -> dict:
    """$M per year, averaged over base years: current law, new law, and the
    change split into increases by purchaser category and cuts (all categories)."""
    out = []
    for b in sorted(sales["fy"].unique()):
        sc = score(sales[sales["fy"] == b], target_fy, bh, bill)
        d = sc["tax_hd2"] - sc["tax_now"]
        out.append({"now": sc["tax_now"].sum(), "hd2": sc["tax_hd2"].sum(),
                    "gain_owner": d[sc["gains"] & (sc["category"] == "owner")].sum(),
                    "gain_nonowner": d[sc["gains"] & (sc["category"] == "nonowner")].sum(),
                    "cut": d[~sc["gains"]].sum()})
    return {k: v / 1e6 for k, v in pd.DataFrame(out).mean().items()}


def state_current_law(sales: pd.DataFrame, target_fy: float) -> float:
    """Current-law collections statewide, $M: each base year's DOTAX total
    grown by the modeled growth of Maui's current-law tax from that year to
    *target_fy*, averaged over STATE_BASE_FY."""
    return float(np.mean([DOTAX_COLLECTIONS_M[b] * components(sales[sales["fy"] == b], target_fy)["now"]
                          / components(sales[sales["fy"] == b], b)["now"] for b in STATE_BASE_FY]))


def maui_recorded_m(fy: int) -> float:
    t = pd.read_csv(DATA / "maui_sales_totals_fy2016_2026.csv")
    return float(t.loc[t["fy"] == fy, "sum_tax_recorded"].sum() / 1e6)


def lower_breakeven(category: str) -> float:
    """The price from which HD2 owes more than current law just below $2M
    (where today's rate is still 0.3% / 0.4% of the whole price)."""
    grid = np.arange(1e6, 2e6, 1e3)
    diff = BILLS[BILL][0](grid, category) - current_law_tax(grid, category)
    return float(grid[np.argmax(diff > 0)])


def disposition(total_m: float, plan) -> dict:
    out, left = {}, total_m
    for name, share, cap in plan:
        amt = min(share * total_m, cap, left)
        out[name] = amt
        left -= amt
    out["General fund"] = left
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Tax-roll tiers: the first revision's method, kept as a sensitivity for Kauai

def honolulu_alpha_mean_excess(tiers: pd.DataFrame) -> float:
    """Residential A is non-owner-occupied residential worth $1M or more, so
    its value above $1M per parcel is a Pareto tail's mean excess, 1M/(a-1)."""
    r = tiers[(tiers["county"] == "Honolulu") & (tiers["class"] == "Residential A")]
    above = r.loc[r["tier_lo"] == 1e6, "tier_value_k"].iloc[0] * 1e3
    return 1 + r["records"].iloc[0] * 1e6 / above


def county_bases(honolulu_alpha: float) -> pd.DataFrame:
    """Per county: non-owner and owner-occupied residential value above
    TAIL_AT ($B, tax-roll basis) and average annual home sales value ($B)."""
    tiers = pd.read_csv(DATA / "rpt_residential_tiers_fy2027.csv")
    nonowner = {c: 0.0 for c in COUNTIES}
    for (county, _cls), g in tiers[tiers["category"] == "nonowner"].groupby(["county", "class"]):
        pts = list(zip(g["tier_lo"], g["tier_value_k"] * 1e3, strict=True))
        if len(g) == 1:
            continue                       # untiered (Hawaii "Apartment"): no tail information
        nonowner[county] += value_above(pts, TAIL_AT, alpha=honolulu_alpha if county == "Honolulu" else None)

    # Owner-occupied: Maui's level from its tiers; the other counties by the
    # ratio of owner-reported value above $2M in ACS PUMS. PUMS pools Maui with
    # Kauai; split by the two counties' owner-occupied net taxable value.
    oo = tiers[tiers["category"] == "owner"]
    m = oo[oo["county"] == "Maui"]
    maui_owner = value_above(list(zip(m["tier_lo"], m["tier_value_k"] * 1e3, strict=True)), TAIL_AT)
    net = oo.groupby("county")["net_value_k"].first()
    kauai_net = oo[oo["county"] == "Kauai"].groupby("class")["net_value_k"].first().sum()
    maui_share = net["Maui"] / (net["Maui"] + kauai_net)
    pums = pd.read_csv(DATA / "pums_owner_value_tail_2020_2024.csv").set_index("area")["excess_over_2m"]
    pums_c = {"Honolulu": pums["Honolulu"], "Hawaii": pums["Hawaii"],
              "Maui": pums["Maui+Kauai"] * maui_share, "Kauai": pums["Maui+Kauai"] * (1 - maui_share)}
    owner = {c: maui_owner * pums_c[c] / pums_c["Maui"] for c in COUNTIES}

    tg = pd.read_csv(DATA / "county_home_sales_tg_2015_2025.csv")
    tg = tg[tg["year"].isin(TG_YEARS)].assign(value=lambda x: x["n_total"] * x["avg_total"])
    sales_value = tg.groupby("county")["value"].mean()
    return pd.DataFrame({"nonowner_above_2m_$B": pd.Series(nonowner) / 1e9,
                         "owner_above_2m_$B": pd.Series(owner) / 1e9,
                         "home_sales_$B": sales_value.reindex(COUNTIES) / 1e9}).reindex(COUNTIES)


def multipliers(bases: pd.DataFrame) -> pd.DataFrame:
    """Each county relative to Maui: non-owner and owner increases, and cuts."""
    mui = bases.loc["Maui"]
    return pd.DataFrame({"m_nonowner": bases["nonowner_above_2m_$B"] / mui["nonowner_above_2m_$B"],
                         "m_owner": bases["owner_above_2m_$B"] / mui["owner_above_2m_$B"],
                         "m_cut": bases["home_sales_$B"] / mui["home_sales_$B"]})


def county_change(comp: dict, mult: pd.DataFrame) -> pd.Series:
    """Change by county, $M, from Maui's components scaled by tax-roll value."""
    return (mult["m_nonowner"] * comp["gain_nonowner"] + mult["m_owner"] * comp["gain_owner"]
            + mult["m_cut"] * comp["cut"])


# ─────────────────────────────────────────────────────────────────────────────
# Statewide: each county's homes x Maui's sale rates, calibrated by price band

# Rate bins: the extract's value bins, merged above $2M so each holds enough
# Maui sales to estimate a rate.
RATE_EDGES = [0, 3e5, 6e5, 8e5, 1e6, 1.25e6, 1.5e6, 1.75e6, 2e6, 2.5e6, 3e6, 3.5e6, 4e6, 5e6, 6e6, 8e6,
              10e6, 15e6, 20e6]
SALES_BASE_FY = 2024.5    # Maui's FY2023-26 sale prices, relative to 2026 assessed values
STOCK_COUNTIES = ("Honolulu", "Hawaii", "Kauai")
STOCK_GROUPS = {"Honolulu": ("residential",), "Hawaii": ("residential", "condo_unit", "ag_dwelling"),
                "Kauai": ("residential", "ag_dwelling")}
# Spread of sale price over assessed value within a rate cell: lognormal with
# mean 1, sigma fitted so Maui's synthetic sales match its actual arm's-length
# sales by price band (fit_price_sigma). The tax is convex in price, so a
# single price per cell understates it.
PRICE_SIGMA = 0.25
N_QUANTILES = 40
# Calibration bands (sale price): below $1M no county target, Maui's scale.
CAL_EDGES = [0, 1e6, 3e6, 6e6, 10e6, np.inf]


def _rate_bin(v) -> np.ndarray:
    return np.array(RATE_EDGES)[np.searchsorted(RATE_EDGES, np.asarray(v, dtype=float), side="right") - 1]


def _cal_band(price) -> np.ndarray:
    return np.searchsorted(CAL_EDGES, np.asarray(price, dtype=float), side="right") - 1


def maui_rates() -> pd.DataFrame:
    """Maui, improved residential homes, per (value bin, owner-occupied now):
    arm's-length sales per year per home by buyer category, and sale price
    over assessed value. From Maui's full assessment listing joined to every
    FY2023-26 sale (maui_sales_extract.py)."""
    st = pd.read_csv(DATA / "maui_residential_stock_2026.csv").query("improved == 1")
    sv = pd.read_csv(DATA / "maui_sales_by_value_fy2023_2026.csv").query("improved == 1")
    n_years = sv["fy"].nunique()
    st = st.assign(rb=_rate_bin(st["value_lo"])).groupby(["rb", "owner_occupied"])["count"].sum()
    s = (sv.assign(rb=_rate_bin(sv["value_lo"])).rename(columns={"owner_occupied_now": "owner_occupied"})
           .groupby(["rb", "owner_occupied", "category"])
           .agg(n=("count", "sum"), p=("sum_price", "sum"), v=("sum_value", "sum")).reset_index())
    s["sales_per_home"] = s["n"] / n_years / s.set_index(["rb", "owner_occupied"]).index.map(st).to_numpy()
    s["price_ratio"] = s["p"] / s["v"]
    return s[["rb", "owner_occupied", "category", "sales_per_home", "price_ratio"]]


def scale_turnover(rates: pd.DataFrame, mult: float, from_value: float = 4e6) -> pd.DataFrame:
    """Rates with sales per home multiplied by *mult* for homes assessed at
    *from_value* or more."""
    r = rates.copy()
    r.loc[r["rb"] >= from_value, "sales_per_home"] *= mult
    return r


def stock_sales(stock: pd.DataFrame, rates: pd.DataFrame, sigma: float = PRICE_SIGMA) -> pd.DataFrame:
    """Synthetic sales (fy, category, count, price, rb) for a county: its homes
    by (value bin, owner-occupied) times Maui's rates for that cell, each
    cell's sales spread over N_QUANTILES prices, lognormal around its mean."""
    s = stock.assign(rb=_rate_bin(stock["value_lo"]), mean_value=stock["sum_value"] / stock["count"])
    m = s.merge(rates, on=["rb", "owner_occupied"])
    out = pd.DataFrame({"fy": SALES_BASE_FY, "category": m["category"], "rb": m["rb"],
                        "count": m["count"] * m["sales_per_home"], "price": m["mean_value"] * m["price_ratio"]})
    if sigma <= 0:
        return out
    q = np.exp(sigma * norm.ppf((np.arange(N_QUANTILES) + 0.5) / N_QUANTILES))
    q /= q.mean()
    out = out.loc[out.index.repeat(N_QUANTILES)].reset_index(drop=True)
    out["price"] *= np.tile(q, len(out) // N_QUANTILES)
    out["count"] /= N_QUANTILES
    return out


def county_stock(county: str, groups: tuple[str, ...] | None = None, *, hawaii_capped: bool = False) -> pd.DataFrame:
    if county == "Maui":
        return pd.read_csv(DATA / "maui_residential_stock_2026.csv").query("improved == 1")
    capped = county == "Hawaii" and hawaii_capped
    ps = pd.read_csv(DATA / ("parcel_stock_by_value_hawaii_capped.csv" if capped else "parcel_stock_by_value.csv"))
    groups = groups or STOCK_GROUPS[county]
    return ps[(ps["county"] == county) & ps["group"].isin(groups) & (ps["improved"] == 1)]


def fit_price_sigma(grid=np.arange(0.0, 0.51, 0.01)) -> float:
    """Sigma at which Maui's synthetic sales best match, by price band from $1M,
    the arm's-length sales of improved homes its rates are built from."""
    vp = pd.read_csv(DATA / "maui_sales_value_price_fy2023_2026.csv")
    edges = [1e6, 1.5e6, 2e6, 3e6, 4e6, 6e6, 10e6, np.inf]
    act = np.histogram(vp["price_lo"], edges, weights=vp["count"])[0] / vp["fy"].nunique()
    stock, rates = county_stock("Maui"), maui_rates()

    def loss(sig: float) -> float:
        syn = stock_sales(stock, rates, sig)
        n = np.histogram(syn["price"], edges, weights=syn["count"])[0]
        return float((((n - act) ** 2) / act).sum())
    return float(grid[np.argmin([loss(g) for g in grid])])


def by_band(rows: pd.DataFrame) -> np.ndarray:
    """Home sales a year per calibration band, at the rows' own prices."""
    return (np.bincount(_cal_band(rows["price"]), weights=rows["count"], minlength=len(CAL_EDGES) - 1)
            / rows["fy"].nunique())


def maui_homes(sales: pd.DataFrame) -> pd.DataFrame:
    """Maui's recorded home sales: owner and non-owner schedules, every sale."""
    return sales[sales["category"].isin(["owner", "nonowner"])]


def mls_targets(col: str = "mls_per_year") -> pd.DataFrame:
    """MLS house and condominium sales a year by county and calibration band
    from $1M (FY2023-26; *col* central, low or high for the other counties),
    times each county's unlisted-share adjustment relative to Maui."""
    m = pd.read_csv(DATA / "mls_sales_by_band_fy2023_2026.csv")
    m["band"] = _cal_band(m["band_lo"])
    # Maui's own count always central: its ratio of recorded to MLS sales is observed.
    n = m[col].where(m["county"] != "Maui", m["mls_per_year"]) * m["coverage_rel_maui"]
    return m.assign(n=n).pivot(index="county", columns="band", values="n").reindex(columns=range(1, len(CAL_EDGES) - 1))


def band_weights(syn: pd.DataFrame, county: str | None, maui_syn: pd.DataFrame, maui_rec: np.ndarray,
                 mls: pd.DataFrame | None = None) -> np.ndarray:
    """Per calibration band, the factor from a county's synthetic sales to its
    recorded home sales: from $1M, its MLS sales x Maui's recorded/MLS ratio
    (county None: Maui's recorded/synthetic ratio in every band)."""
    raw, raw_m = by_band(syn), by_band(maui_syn)
    w = np.divide(maui_rec, raw_m, out=np.ones_like(raw_m), where=raw_m > 0)
    if county is not None and county != "Maui":
        mls = mls_targets() if mls is None else mls
        for j in mls.columns:
            target = mls.loc[county, j] * maui_rec[j] / mls.loc["Maui", j]
            w[j] = target / raw[j] if raw[j] > 0 else 1.0
    return w


def weighted(syn: pd.DataFrame, w: np.ndarray) -> pd.DataFrame:
    return syn.assign(count=syn["count"] * w[_cal_band(syn["price"])])


def stock_method(target_fy: float, bh: Behavior = STATIC, *, bill: str = BILL, calibrate: bool = True,
                 mls_col: str = "mls_per_year", sigma: float = PRICE_SIGMA, oahu_turnover: float | None = None,
                 hawaii_capped: bool = False) -> dict:
    """Change ($M) for Honolulu, Hawaii and Kauai counties from their homes.

    Each county's synthetic sales are weighted, band by band, to its recorded
    home sales (band_weights); Maui's own synthetic sales weighted the same way
    give the residual factor, Maui's exact change over its weighted synthetic
    change, applied to every county. *oahu_turnover* multiplies Maui's rates
    for Honolulu homes assessed at $4M+ and replaces Honolulu's band targets
    with Maui's scale (a sensitivity from Oahu's own turnover)."""
    rates, sales = maui_rates(), load_sales()
    homes = maui_homes(sales)
    rec = by_band(homes)
    mls = mls_targets(mls_col)
    syn_m = stock_sales(county_stock("Maui"), rates, sigma)
    w_m = band_weights(syn_m, None, syn_m, rec)
    exact = components(homes, target_fy, bh, bill)
    raw = {"Maui": components(weighted(syn_m, w_m), target_fy, bh, bill)}
    k = (exact["hd2"] - exact["now"]) / (raw["Maui"]["hd2"] - raw["Maui"]["now"])
    out = {"residual": k, "exact": exact, "raw": raw, "weights": {"Maui": w_m}, "synthetic": {"Maui": syn_m},
           "calibrated": {}}
    for c in STOCK_COUNTIES:
        own = c == "Honolulu" and oahu_turnover is not None
        syn = stock_sales(county_stock(c, hawaii_capped=hawaii_capped), scale_turnover(rates, oahu_turnover)
                          if own else rates, sigma)
        w = band_weights(syn, c if calibrate and not own else None, syn_m, rec, mls)
        cal = weighted(syn, w)
        raw[c] = components(cal, target_fy, bh, bill)
        out[c] = k * (raw[c]["hd2"] - raw[c]["now"])
        out["weights"][c], out["synthetic"][c], out["calibrated"][c] = w, syn, cal
    return out


def by_county_change(comp: dict, sm: dict) -> pd.Series:
    """Maui sale by sale; the other counties from their homes."""
    return pd.Series({**{c: sm[c] for c in STOCK_COUNTIES}, "Maui": comp["hd2"] - comp["now"]})


def nonowner_share(comp: dict, sm: dict) -> float:
    """Share of the statewide increase (gains only) from non-owner buyers."""
    non = comp["gain_nonowner"] + sum(sm["residual"] * sm["raw"][c]["gain_nonowner"] for c in STOCK_COUNTIES)
    own = comp["gain_owner"] + sum(sm["residual"] * sm["raw"][c]["gain_owner"] for c in STOCK_COUNTIES)
    return float(non / (non + own))


# ─────────────────────────────────────────────────────────────────────────────
# Checks and benchmarks

def oahu_turnover_ratio() -> dict:
    """Oahu's own ownership turnover over Maui's sale rates, like for like, by
    assessed-value band (scripts/conveyance/oahu_owner_turnover.py): resales
    (sale-like owner changes, not developer first sales or bulk purchases) a
    year x repeat sales per parcel x the share judged real sales on review,
    over Oahu's homes x Maui's sales per home net of the developer first sales
    and related-party sales in Maui's rates, which the owner diff leaves out."""
    cells = pd.read_csv(DATA / "oahu_owner_turnover_cells_2023_2026.csv")
    comp = pd.read_csv(DATA / "maui_sale_composition_fy2023_2026.csv").set_index("band")
    tally = pd.read_csv(DATA / "oahu_turnover_review_tallies.csv").set_index("band")
    reviewed = {"<1M": ["<1M"], "1-2M": ["<1M", "2M+"], "2-4M": ["2M+"], "4M+": ["2M+"]}
    rates = maui_rates().groupby(["rb", "owner_occupied"])["sales_per_home"].sum()
    cells["expected"] = cells["homes"] * cells.set_index(["rb", "owner_occupied"]).index.map(rates).fillna(0).to_numpy()
    out = {}
    for name, lo, hi in (("<1M", 0, 1e6), ("1-2M", 1e6, 2e6), ("2-4M", 2e6, 4e6), ("4M+", 4e6, np.inf)):
        g, c = cells[(cells["rb"] >= lo) & (cells["rb"] < hi)], comp.loc[name]
        t = tally.loc[reviewed[name]]
        obs = g["resales"].sum() / g["years"].iloc[0] * c["repeat_factor"] * t["judged_sales"].sum() / t["reviewed"].sum()
        exp_ = g["expected"].sum() * (1 - c["developer_share"] - c["related_share"])
        out[name] = {"homes": float(g["homes"].sum()), "oahu_resales_per_year": float(obs),
                     "at_maui_rates_per_year": float(exp_), "ratio": float(obs / exp_),
                     "rel_se": float(np.sqrt(1 / g["resales"].sum() + 1 / c["n"]))}
    return out


def implied_oahu_turnover(sm: dict, from_value: float = 4e6) -> float:
    """How much the band calibration raises Oahu's sales of homes assessed at
    *from_value* or more, relative to Maui's rates, net of Maui's own scale."""
    def lift(syn: pd.DataFrame, cal: pd.DataFrame) -> float:
        top = syn["rb"] >= from_value
        return float(cal.loc[top, "count"].sum() / syn.loc[top, "count"].sum())
    maui = sm["synthetic"]["Maui"]
    return lift(sm["synthetic"]["Honolulu"], sm["calibrated"]["Honolulu"]) / lift(maui, weighted(maui, sm["weights"]["Maui"]))


def benchmarks(sales: pd.DataFrame) -> dict:
    """Official and sponsor figures for related bills, against this model
    scored the same way (static, no behavioral response).

    House: Rep. Evslin put HB 2049 HD3 at about $170M a year, and Chair Todd
    said SB 3028 HD2 raises about $20M less, with 91% of sales paying the same
    or less (Evslin: 75% under HB 2049). DOTAX's fiscal table for HB 1410 HD2
    (2025) implies +$57.9M in FY2026 on a $102.1M base; that bill also moves
    nonresidential property to marginal rates."""
    homes = maui_homes(sales)
    out = {}
    for fy, label in ((TARGET_YEARS[0], "fy2028"), (None, "base_prices")):
        chg = {}
        for bill in ("SB3028 HD2", "HB2049 HD3"):
            if fy is None:
                parts = [by_county_change(components(sales[sales["fy"] == b], b, STATIC, bill),
                                          stock_method(b, STATIC, bill=bill)).sum() for b in MAUI_BASE_FY]
                chg[bill] = float(np.mean(parts))
            else:
                chg[bill] = float(by_county_change(components(sales, fy, STATIC, bill),
                                                   stock_method(fy, STATIC, bill=bill)).sum())
        out[label] = {"sb3028_hd2_$M": chg["SB3028 HD2"], "hb2049_hd3_$M": chg["HB2049 HD3"],
                      "difference_$M": chg["HB2049 HD3"] - chg["SB3028 HD2"],
                      "ratio_hd2_over_hd3": chg["SB3028 HD2"] / chg["HB2049 HD3"],
                      "house_level_ratio_hd2": 150.0 / chg["SB3028 HD2"],
                      "house_level_ratio_hd3": 170.0 / chg["HB2049 HD3"]}
    # Share of home sales paying the same or less (counts, not revenue):
    # Maui's every sale plus the other counties' calibrated synthetic sales.
    sm = stock_method(TARGET_YEARS[0], STATIC)
    rows = pd.concat([homes, *(sm["calibrated"][c] for c in STOCK_COUNTIES)], ignore_index=True)
    share = {}
    for bill in ("SB3028 HD2", "HB2049 HD3"):
        parts = []
        for b in sorted(rows["fy"].unique()):
            sc = score(rows[rows["fy"] == b], TARGET_YEARS[0], STATIC, bill)
            w = sc["count"] / (len(MAUI_BASE_FY) if b != SALES_BASE_FY else 1)
            parts.append(pd.DataFrame({"w": w, "le": (sc["tax_hd2_static"] - sc["tax_now"]) / sc["count"] <= 0.5}))
        p = pd.concat(parts)
        share[bill] = float(p.loc[p["le"], "w"].sum() / p["w"].sum())
    out["share_same_or_less"] = {"sb3028_hd2": share["SB3028 HD2"], "hb2049_hd3": share["HB2049 HD3"],
                                 "house_sb3028_hd2": 0.91, "house_hb2049": 0.75}
    # DOTAX, HB 1410 HD2, FY2026: homes here; nonresidential as Maui's own
    # nonresidential change scaled by the state's current-law tax over Maui's.
    hb = "HB1410 HD2"
    maui = components(homes, 2026, STATIC, hb)
    res = float(maui["hd2"] - maui["now"] + sum(stock_method(2026, STATIC, bill=hb)[c] for c in STOCK_COUNTIES))
    nonres_maui = components(sales[sales["category"].isin(["nonres", "mf"])], 2026, STATIC, hb)
    scale = np.mean([DOTAX_COLLECTIONS_M[b] / maui_recorded_m(b) for b in STATE_BASE_FY])
    nonres = float((nonres_maui["hd2"] - nonres_maui["now"]) * scale)
    out["dotax_hb1410_hd2_fy2026"] = {"dotax_increase_$M": 57.9, "dotax_base_$M": 102.1,
                                      "model_homes_$M": res, "model_nonresidential_scaled_$M": nonres,
                                      "dotax_over_model": 57.9 / (res + nonres)}
    return out


def dotax_residual(sm_base: dict) -> dict:
    """DOTAX collections less Maui's recorded tax, FY2023-25, against the
    model's current-law tax on home sales in the other three counties at base
    prices (scaled like the change: Maui's exact current-law tax on home sales
    over its calibrated synthetic): the gap is nonresidential conveyances
    there, whose share should resemble Maui's."""
    k_now = sm_base["exact"]["now"] / sm_base["raw"]["Maui"]["now"]
    other = float(sum(k_now * sm_base["raw"][c]["now"] for c in STOCK_COUNTIES))
    rest = float(np.mean([DOTAX_COLLECTIONS_M[b] - maui_recorded_m(b) for b in STATE_BASE_FY]))
    t = pd.read_csv(DATA / "maui_sales_totals_fy2016_2026.csv").query("fy in @STATE_BASE_FY")
    maui_nonres = float(t.loc[t["category"].isin(["nonres", "unmatched"]), "sum_tax_recorded"].sum()
                        / t["sum_tax_recorded"].sum())
    return {"dotax_less_maui_$M": rest, "model_homes_current_law_$M": other, "current_law_scale": float(k_now),
            "implied_nonresidential_share": 1 - other / rest, "maui_nonresidential_share": maui_nonres}


# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sales = load_sales()
    tiers = pd.read_csv(DATA / "rpt_residential_tiers_fy2027.csv")
    bases = county_bases(honolulu_alpha_mean_excess(tiers))
    mult = multipliers(bases)
    t0 = TARGET_YEARS[0]

    # Revenue by year; current law from DOTAX.
    rows, comp_cache, sm_cache = [], {}, {}
    state_now = {t: state_current_law(sales, t) for t in TARGET_YEARS}
    for t in TARGET_YEARS:
        for label, bh in CASES.items():
            comp = comp_cache[(t, label)] = components(sales, t, bh)
            sm = sm_cache[(t, label)] = stock_method(t, bh)
            ch = by_county_change(comp, sm)
            rows.append({"fy": t, "case": label, "maui_now_$M": comp["now"], "maui_hd2_$M": comp["hd2"],
                         "state_now_$M": state_now[t], "state_change_$M": ch.sum(),
                         **{f"change_{c.lower()}_$M": ch[c] for c in COUNTIES}})
    rev = pd.DataFrame(rows)
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)

    st, bh_ = comp_cache[(t0, "static")], comp_cache[(t0, "behavioral")]
    sm_st, sm_bh = sm_cache[(t0, "static")], sm_cache[(t0, "behavioral")]
    stock_summary = {}
    for c in (*STOCK_COUNTIES, "Maui"):
        stk = county_stock(c)
        top = stk[stk["value_lo"] >= 2e6]
        stock_summary[c] = {"homes": float(stk["count"].sum()), "homes_2m_plus": float(top["count"].sum()),
                            "homes_2m_plus_not_owner_occupied": float(top.loc[top["owner_occupied"] == 0, "count"].sum())}
    tg = pd.read_csv(DATA / "county_home_sales_tg_2015_2025.csv").query("year in @TG_YEARS")
    by_county = bases.join(mult).assign(
        method=pd.Series({"Honolulu": "housing stock", "Hawaii": "housing stock", "Kauai": "housing stock",
                          "Maui": "every sale"}),
        homes=pd.Series({c: v["homes"] for c, v in stock_summary.items()}),
        homes_2m_plus=pd.Series({c: v["homes_2m_plus"] for c, v in stock_summary.items()}),
        homes_2m_plus_not_owner_occupied=pd.Series({c: v["homes_2m_plus_not_owner_occupied"]
                                                    for c, v in stock_summary.items()}),
        change_static_fy2028_M=by_county_change(st, sm_st), change_behavioral_fy2028_M=by_county_change(bh_, sm_bh),
        tiers_static_fy2028_M=county_change(st, mult), tiers_behavioral_fy2028_M=county_change(bh_, mult))
    by_county.index.name = "county"
    by_county.to_csv(OUT_DIR / "by_county.csv")

    # Band calibration: synthetic and recorded-equivalent home sales a year.
    homes = maui_homes(sales)
    rec = by_band(homes)
    labels = ["Under $1M", "$1M-$3M", "$3M-$6M", "$6M-$10M", "$10M+"]
    mls_raw = pd.read_csv(DATA / "mls_sales_by_band_fy2023_2026.csv")
    mls_raw["band"] = _cal_band(mls_raw["band_lo"])
    cal_rows = []
    for c in ("Maui", *STOCK_COUNTIES):
        syn = sm_st["synthetic"][c]
        w = sm_st["weights"][c]
        raw_b = by_band(syn)
        for j, lab in enumerate(labels):
            mr = mls_raw[(mls_raw["county"] == c) & (mls_raw["band"] == j)]
            cal_rows.append({"county": c, "band": lab, "synthetic_per_year": raw_b[j],
                             "weight": w[j], "calibrated_per_year": raw_b[j] * w[j],
                             "maui_recorded_per_year": rec[j] if c == "Maui" else np.nan,
                             "mls_per_year": mr["mls_per_year"].iloc[0] if len(mr) else np.nan,
                             "coverage_rel_maui": mr["coverage_rel_maui"].iloc[0] if len(mr) else np.nan})
    pd.DataFrame(cal_rows).to_csv(OUT_DIR / "band_calibration.csv", index=False)
    # Published with the page: the company-owned shares behind entity_share().
    pd.read_csv(DATA / "honolulu_entity_share_by_band.csv").to_csv(OUT_DIR / "honolulu_entity_share_by_band.csv", index=False)

    # Sales by price band, FY2028 prices, before any change in sales: Maui
    # sale by sale; the other counties' calibrated synthetic sales.
    band_edges = [0, 1e6, 2e6, 3e6, 4e6, 6e6, 1e7, np.inf]
    band_labels = ["Under $1M", "$1M-$2M", "$2M-$3M", "$3M-$4M", "$4M-$6M", "$6M-$10M", "$10M+"]
    band_rows = []
    for c in (*STOCK_COUNTIES, "Maui"):
        src = sales[sales["category"] != "nonres"] if c == "Maui" else sm_st["calibrated"][c]
        parts = [score(src[src["fy"] == b], t0) for b in sorted(src["fy"].unique())]
        scd = pd.concat(parts)
        scd["band"] = pd.cut(scd["price_t"], band_edges, labels=band_labels, right=False)
        g = (scd.groupby(["band", "category"], observed=True)
                .agg(sales=("count", "sum"), tax_now=("tax_now", "sum"), tax_hd2=("tax_hd2_static", "sum")).reset_index())
        g[["sales", "tax_now", "tax_hd2"]] /= len(parts)
        if c != "Maui":                       # revenue carries the calibration residual; counts do not
            g[["tax_now", "tax_hd2"]] *= sm_st["residual"]
        band_rows.append(g.assign(county=c))
    county_bands = pd.concat(band_rows)[["county", "band", "category", "sales", "tax_now", "tax_hd2"]]
    county_bands.to_csv(OUT_DIR / "county_bands.csv", index=False)

    # Checks: modeled home sales against Title Guaranty (TG Buyer Stats, which
    # count fewer sales than Maui's recorded file: compare ratios, not levels).
    checks = {}
    for c in (*STOCK_COUNTIES, "Maui"):
        src = homes if c == "Maui" else sm_st["calibrated"][c]
        n_b = src["fy"].nunique()
        t = tg[tg["county"] == c]
        checks[c] = {"model_home_sales_per_year": float(src["count"].sum() / n_b),
                     "model_value_$B": float((src["count"] * src["price"]).sum() / n_b / 1e9),
                     "tg_sales_per_year": float(t["n_total"].mean()),
                     "tg_value_$B": float((t["n_total"] * t["avg_total"]).mean() / 1e9),
                     **{f"model_sales_{int(x / 1e6)}m_plus": float(src.loc[src["price"] >= x, "count"].sum() / n_b)
                        for x in (2e6, 3e6, 5e6, 10e6)}}
        checks[c]["tg_over_model"] = checks[c]["tg_sales_per_year"] / checks[c]["model_home_sales_per_year"]
    sm_base = stock_method(SALES_BASE_FY, STATIC)
    checks["dotax_residual"] = dotax_residual(sm_base)
    checks["oahu_turnover_vs_maui_rates"] = oahu_turnover_ratio()
    checks["oahu_turnover_4m_plus_implied_by_calibration"] = implied_oahu_turnover(sm_st)
    fitted_sigma = fit_price_sigma()

    # Sensitivity, FY2028 statewide change.
    central_st, central_bh = by_county_change(st, sm_st), by_county_change(bh_, sm_bh)
    oahu_ratio = checks["oahu_turnover_vs_maui_rates"]["4M+"]["ratio"]

    def variant(name: str, **kw) -> dict:
        return {"variant": name, **{lab: float(by_county_change(comp_cache[(t0, lab)], stock_method(t0, bh, **kw)).sum())
                                    for lab, bh in (("static", STATIC), ("behavioral", CENTRAL))}}

    def behavior(name: str, bh: Behavior) -> dict:
        return {"variant": name, "static": np.nan,
                "behavioral": float(by_county_change(components(sales, t0, bh), stock_method(t0, bh)).sum())}

    kauai_tiers = {lab: float(central.drop("Kauai").sum() + county_change(comp_cache[(t0, lab)], mult)["Kauai"])
                   for lab, central in (("static", central_st), ("behavioral", central_bh))}
    sens = [{"variant": "Central estimate", "static": central_st.sum(), "behavioral": central_bh.sum()},
            variant("Without calibration to each county's sales by price", calibrate=False),
            variant("County sales by price at the low end of the market data", mls_col="mls_low"),
            variant("County sales by price at the high end of the market data", mls_col="mls_high"),
            variant(f"Oahu from its own ownership turnover ({oahu_ratio:.2f}x Maui's rates at $4M+)",
                    oahu_turnover=oahu_ratio),
            variant("No spread of sale prices around assessed value", sigma=0.0),
            variant("Hawaii Island homeowner values as assessed (capped)", hawaii_capped=True),
            {"variant": "Kauai from tax-roll value above $2M", **kauai_tiers},
            behavior(f"Sales fall {ELASTICITY_RANGE[0]}% per point of added tax", replace(CENTRAL, elasticity=ELASTICITY_RANGE[0])),
            behavior(f"Sales fall {ELASTICITY_RANGE[1]}% per point of added tax", replace(CENTRAL, elasticity=ELASTICITY_RANGE[1])),
            behavior("Sales fall 6% per point for owner-occupants, 8% for other buyers",
                     replace(CENTRAL, elasticity={"owner": 6.0, "nonowner": 8.0, "mf": 8.0})),
            behavior("No sales of entities in place of homes", replace(CENTRAL, entity_takeup=0.0)),
            behavior("Half of entity-held sales of $4M+ sold as entities", replace(CENTRAL, entity_takeup=0.5)),
            behavior("10% of other buyers at $4M+ claim the owner-occupant rates", replace(CENTRAL, cert_shift=0.1)),
            behavior("First year after a rush of sales before the law takes effect",
                     replace(CENTRAL, forestall=(0.25, 1.5))),
            behavior("Previous assumptions: sales fall 10% per point, no entity sales",
                     Behavior(10.0, 0.0))]
    sens = pd.DataFrame(sens)
    sens.to_csv(OUT_DIR / "sensitivity.csv", index=False)

    # Maui by band, first scored year, static and behavioral, averaged over base years.
    parts = [score(sales[sales["fy"] == b], t0, CENTRAL).assign(base_fy=b) for b in MAUI_BASE_FY]
    sc = pd.concat(parts)
    edges = [0, 6e5, 1e6, 2e6, 3e6, 4e6, 6e6, 1e7, np.inf]
    labels8 = ["Under $600K", "$600K-$1M", "$1M-$2M", "$2M-$3M", "$3M-$4M", "$4M-$6M", "$6M-$10M", "$10M+"]
    sc["band"] = pd.cut(sc["price_t"], edges, labels=labels8, right=False)
    n_years = len(MAUI_BASE_FY)
    maui_band = (sc.groupby(["band", "category"], observed=True)
                   .agg(sales=("count", "sum"), tax_now=("tax_now", "sum"),
                        tax_hd2_static=("tax_hd2_static", "sum"), tax_hd2=("tax_hd2", "sum"))
                   .reset_index())
    for col in ["sales", "tax_now", "tax_hd2_static", "tax_hd2"]:
        maui_band[col] = maui_band[col] / n_years
    maui_band["avg_change_per_sale"] = (maui_band["tax_hd2_static"] - maui_band["tax_now"]) / maui_band["sales"]
    maui_band.to_csv(OUT_DIR / "maui_by_band.csv", index=False)

    # Headline shares and the $3-4M spotlight (Maui, first scored year, static).
    sc["d_per_sale"] = (sc["tax_hd2_static"] - sc["tax_now"]) / sc["count"]
    n = sc["count"].sum()
    spot = sc[(sc["price_t"] >= 3e6) & (sc["price_t"] < 4e6) & (sc["category"] != "nonres")]
    at_base = [components(sales[sales["fy"] == b], b) for b in MAUI_BASE_FY]
    summary = {
        "first_year": t0,
        "maui_share_of_sales_pay_less": float(sc.loc[sc["d_per_sale"] < -0.5, "count"].sum() / n),
        "maui_share_of_sales_pay_same": float(sc.loc[sc["d_per_sale"].abs() <= 0.5, "count"].sum() / n),
        "maui_share_of_sales_pay_more": float(sc.loc[sc["d_per_sale"] > 0.5, "count"].sum() / n),
        "maui_share_of_gain_from_nonowner": float(
            (sc.loc[sc["category"] == "nonowner", "tax_hd2_static"] - sc.loc[sc["category"] == "nonowner", "tax_now"]).sum()
            / (sc["tax_hd2_static"] - sc["tax_now"]).sum()),
        "spotlight_3_4m": {
            cat: {"sales_per_year": float(g["count"].sum() / n_years),
                  "avg_tax_now": float(g["tax_now"].sum() / g["count"].sum()),
                  "avg_tax_hd2": float(g["tax_hd2_static"].sum() / g["count"].sum()),
                  "share_of_maui_gain": float((g["tax_hd2_static"] - g["tax_now"]).sum()
                                              / (sc["tax_hd2_static"] - sc["tax_now"]).sum())}
            for cat, g in spot.groupby("category")},
        "breakeven": {c: breakeven_price(c) for c in ("owner", "nonowner")},
        "pays_more_below_2m_from": {c: lower_breakeven(c) for c in ("owner", "nonowner")},
        # Like-for-like with the House Finance figure (~$150M): static, at the
        # base years' own prices and law (no aging, no indexing).
        "state_static_change_at_base_prices_$M": float(np.mean(
            [by_county_change(at_b, stock_method(b, STATIC)).sum() for at_b, b in zip(at_base, MAUI_BASE_FY, strict=True)])),
        "state_nonowner_share_of_increase": nonowner_share(st, sm_st),
        "calibration_residual": {"static": sm_st["residual"], "behavioral": sm_bh["residual"]},
        "price_sigma": {"used": PRICE_SIGMA, "fitted_on_maui": fitted_sigma},
        "entity_takeup": ENTITY_TAKEUP,
        "checks": checks,
        "benchmarks": benchmarks(sales),
        "tg_home_sales_years": TG_YEARS,
    }

    ex = pd.DataFrame([{"price": p, **{f"{c}_{law}": float(f(p, c)) for c in ("owner", "nonowner")
                                        for law, f in (("now", current_law_tax), ("hd2", BILLS[BILL][0]))}}
                       for p in (800e3, 1.5e6, 2e6, 2.5e6, 3e6, 3.5e6, 3.999e6, 4e6, 5e6, 10e6)])
    ex.to_csv(OUT_DIR / "examples.csv", index=False)

    # Where the money goes, first scored year, with the behavioral response:
    # central, and the top of the range.
    central = rev[(rev["fy"] == t0) & (rev["case"] == "behavioral")].iloc[0]
    high = rev[(rev["fy"] == t0) & (rev["case"] == "behavioral_high")].iloc[0]
    now_m = central["state_now_$M"]
    d_now = disposition(now_m, DISPOSITION_NOW)
    d_central = disposition(now_m + central["state_change_$M"], DISPOSITION_HD2)
    d_high = disposition(now_m + high["state_change_$M"], DISPOSITION_HD2)
    funds = [name for name, _, _ in DISPOSITION_HD2] + ["General fund"]
    disp = pd.DataFrame([{"fund": f, "current_law_$M": d_now.get(f, 0.0),
                          "hd2_$M": d_central[f], "hd2_high_$M": d_high[f]} for f in funds])
    disp.to_csv(OUT_DIR / "disposition.csv", index=False)
    # HD2 earmarks 75% of collections (up to $150M) before the general fund sees
    # any; the general fund comes out ahead only past this much new revenue.
    grid = np.arange(0.0, 400.0, 0.1)
    summary["general_fund_breakeven_gain_$M"] = float(grid[np.argmax(
        [disposition(now_m + g, DISPOSITION_HD2)["General fund"] > d_now["General fund"] for g in grid])])
    tot = pd.read_csv(DATA / "maui_sales_totals_fy2016_2026.csv").query("fy in @MAUI_BASE_FY")
    summary["maui_share_schedule_matched"] = float(tot.loc[tot["category"] != "unmatched", "count"].sum()
                                                   / tot["count"].sum())
    summary["maui_share_of_state_collections"] = {str(b): maui_recorded_m(b) / DOTAX_COLLECTIONS_M[b]
                                                  for b in STATE_BASE_FY}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    from tax_modeler.runs import write_run_manifest
    write_run_manifest(OUT_DIR, script="forecast_conveyance_sb3028.py",
                       params={"target_years": TARGET_YEARS, "maui_base_fy": MAUI_BASE_FY,
                               "state_base_fy": STATE_BASE_FY, "tg_years": TG_YEARS, "tail_at": TAIL_AT,
                               "price_growth": PRICE_GROWTH, "cpi_growth": CPI_GROWTH,
                               "volume_semi_elasticity": VOLUME_SEMI_ELASTICITY,
                               "elasticity_range": ELASTICITY_RANGE,
                               "entity_takeup": ENTITY_TAKEUP, "entity_takeup_range": ENTITY_TAKEUP_RANGE,
                               "entity_share": [list(x) for x in entity_share()],
                               "rate_edges": RATE_EDGES, "sales_base_fy": SALES_BASE_FY,
                               "stock_groups": STOCK_GROUPS, "price_sigma": PRICE_SIGMA,
                               "calibration_edges": [e for e in CAL_EDGES if np.isfinite(e)],
                               "calibration_residual": round(sm_st["residual"], 3),
                               "breakeven": {c: breakeven_price(c) for c in ("owner", "nonowner")},
                               "dotax_collections_m": DOTAX_COLLECTIONS_M},
                       inputs={"maui_sales": "County of Maui RPT Sales Data File (DocumentCenter/View/8070) and full "
                                             "assessment listing (View/8079); see data/raw/conveyance/maui_sources.json",
                               "dotax": "DOTAX Annual Report FY2024-25, Table 1.11",
                               "county_home_sales": "Title Guaranty of Hawaii from Bureau of Conveyances data, via "
                                                    "DBEDT Quarterly Statistical and Economic Report 2026 Q3, "
                                                    "Tables E-11, E-12, G-49 to G-56",
                               "mls_sales_by_band": "Title Guaranty of Hawaii monthly and year-end Residential Sales "
                                                    "Reports (MLS), Hawaii Life luxury market reports and List "
                                                    "Sotheby's International Realty quarterly reports, FY2023-26; see "
                                                    "scripts/conveyance/mls_sales_by_band.py",
                               "tax_rolls": "Real Property Tax Valuations, tax year 2026-27, statewide report "
                                            "(Honolulu RPAD, July 2026)",
                               "owner_values": "ACS PUMS 2020-2024 5-year housing file (VALP, ADJHSG)",
                               "parcel_stock": "Honolulu RPAD ASMTPITT table (City open data hub, CadastralTables/"
                                               "FeatureServer/10, tax year 2026); Hawaii County parcels (State GIS "
                                               "ParcelsZoning/MapServer/5, 4/27/2026); Kauai County TY26_PropertyTaxData "
                                               "and BUILDINGS_public (County open data hub); dwelling units per TMK "
                                               "(tmk_state_2025_dwelling_data)",
                               "entity_share": "Honolulu OWNDAT (CadastralTables/FeatureServer/6) x ASMTPITT; see "
                                               "scripts/conveyance/honolulu_entity_share.py",
                               "oahu_turnover": "Honolulu owner rolls: PropertyReportInfo/FeatureServer/2 (tax year "
                                                "2024, edited 2023-12-05) and OWNINFO/FeatureServer/0 (current); see "
                                                "scripts/conveyance/oahu_owner_turnover.py"})
    pd.set_option("display.width", 220)
    print(rev.round(1).to_string(index=False))
    print(by_county.round(2).to_string())
    print(pd.DataFrame(cal_rows).round(2).to_string(index=False))
    print(sens.round(1).to_string(index=False))
    print(json.dumps({k: summary[k] for k in ("state_static_change_at_base_prices_$M", "state_nonowner_share_of_increase",
                                              "calibration_residual", "price_sigma", "general_fund_breakeven_gain_$M")},
                     indent=1, default=float))
    print(json.dumps(checks, indent=1, default=float))
    print(json.dumps(summary["benchmarks"], indent=1, default=float))
    print(disp.round(1).to_string(index=False))


if __name__ == "__main__":
    sys.exit(run())
