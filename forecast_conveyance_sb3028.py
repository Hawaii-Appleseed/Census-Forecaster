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
the conveyance tax paid), extracted to data/raw/conveyance/ by
scripts/conveyance/maui_sales_extract.js. The tax paid identifies which
schedule applied, so owner-occupancy is observed, not assumed: of priced,
taxed conveyances in FY2023-FY2026, 97% match schedule (1) or (2) to within
0.5% (the rest are treated as nonresidential, which HD2 leaves unchanged).
Schedule-(1) sales on residential parcels are owner-occupant; on other
parcels, nonresidential. Current law applied to the binned data reproduces
the tax Maui recorded on home sales to within a few hundred dollars a year.

Statewide: county by county
---------------------------
No other county publishes sales with prices, so each is built from official
data with Maui's sales as the template:

  - Honolulu and Hawaii counties: their housing stock, home by home, times
    Maui's sale rates. Maui's full assessment listing joined to every
    FY2023-26 sale (maui_sales_extract.js) gives, per assessed-value bin and
    owner-occupancy, arm's-length sales per home per year, the buyer mix
    (owner/non-owner schedule), and sale price over assessed value
    (maui_rates). Applied to each county's stock (parcel_stock_by_value.csv,
    from scripts/conveyance/fetch_parcel_stock.py: Honolulu's RPAD tables on
    the City's open data hub, one row per parcel or condo unit; Hawaii
    County's parcel layer on the State GIS portal, condo projects split into
    units) this gives synthetic sales to score. The same procedure applied to
    Maui's own stock recovers ~79% of Maui's sale-by-sale change (it leaves
    out nominal-price, new-construction and vacant-lot sales), so county
    results are scaled by that calibration factor (stock_method).
  - Kauai: no parcel values are published, so its increase is Maui's scaled
    by Kauai's non-owner residential value above $2M from the 2026-27 tax-roll
    tiers (value_above) and owner-occupied value above $2M from ACS PUMS
    (county_bases / multipliers). This was the first statewide method for all
    three counties and remains a sensitivity.
  - HD2's small cuts below ~$2.2M: included in the stock method's synthetic
    sales; for Kauai, scaled by home sales value (Title Guaranty via DBEDT).
  - Checks: modeled home sales against Title Guaranty's recorded home sales
    by county; Oahu's high end against its MLS sales (Honolulu Board of
    Realtors via the State Data Book), and Oahu rebuilt from those MLS sales.

The assumption doing the work is that homes of a given value and occupancy
sell as often, and to the same mix of buyers, as on Maui. Current-law
collections statewide are DOTAX's.

Behavioral response: sales volume falls by VOLUME_SEMI_ELASTICITY percent
per percentage point of price added in tax (rises where HD2 cuts the tax).

Outputs (runs/conveyance_sb3028/):
  revenue_by_year.csv   FY2028-FY2031: current law, HD2 change, Maui and each
                        county, static and with the sales response
  by_county.csv         FY2028: each county's method, stock, bases and change
  county_bands.csv      FY2028 prices: home sales a year by price band and buyer
                        type (Honolulu, Hawaii from stock; Maui every sale)
  sensitivity.csv       FY2028 statewide change under alternative data choices
  maui_by_band.csv      Maui, average year: sales, tax now vs HD2, by price
                        band and purchaser category
  examples.csv          tax on example prices under both laws
  disposition.csv       where the money goes, FY2028 with the sales response
  summary.json          shares of sales paying less / same / more; $3-4M
                        spotlight; breakevens; checks
  manifest.json
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from tax_modeler.conveyance import (
    CATEGORIES,
    breakeven_price,
    current_law_tax,
    hd2_tax,
    pareto_alpha_grouped,
    value_above,
)

REPO = Path(__file__).parent
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT_DIR = REPO / "runs" / "conveyance_sb3028"

TARGET_YEARS = [2028, 2029, 2030, 2031]   # first full year if enacted in the 2027 session
MAUI_BASE_FY = [2023, 2024, 2025, 2026]
STATE_BASE_FY = [2023, 2024, 2025]        # DOTAX FY2026 not yet published
TG_YEARS = [2023, 2024, 2025]             # county home sales, calendar years
OAHU_MLS_YEARS = [2023, 2024]
COUNTIES = ("Honolulu", "Hawaii", "Kauai", "Maui")
TAIL_AT = 2e6                             # HD2 raises the tax only above ~$2.1-2.2M

PRICE_GROWTH = 0.04          # nominal; FHFA Hawaii HPI +3.9% 2023-24, 6.5%/yr 2014-24
CPI_GROWTH = 0.03            # Urban Hawaii CPI 2.9%/yr 2015-25; drives HD2 bracket indexing
VOLUME_SEMI_ELASTICITY = 10  # % fewer sales per 1 point of price in added tax (range 5-15)
ELASTICITY_RANGE = (5, 15)

# DOTAX Annual Report FY2024-25, Table 1.11 (conveyance tax collections, $M).
DOTAX_COLLECTIONS_M = {2016: 66.083, 2017: 94.537, 2018: 100.603, 2019: 85.965, 2020: 61.110,
                       2021: 62.725, 2022: 188.418, 2023: 92.132, 2024: 97.411, 2025: 96.045}

# Disposition, HRS §247-7 today and as HD2 would amend it: (share, cap $M).
DISPOSITION_NOW = [("Land conservation fund", .10, 5.1), ("Rental housing revolving fund", .50, 38.0)]
DISPOSITION_HD2 = [("Land conservation fund", .05, 10.0), ("Rental housing revolving fund", .20, 40.0),
                   ("Hawaiian home lands infrastructure and housing fund", .30, 60.0),
                   ("Transit-oriented development infrastructure (dwelling unit revolving fund)", .20, 40.0)]


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
        i = b.index[(b["fy"] == r.fy) & (b["category"] == r.category) & (b["bin_lo"] == _bin_lo(r.price))]
        b.loc[i[0], ["count", "sum_price"]] -= (1, r.price)
    b = b[b["count"] > 0].copy()
    b["price"] = b["sum_price"] / b["count"]
    t = pd.read_csv(DATA / "maui_sales_over_10m_fy2023_2026.csv").assign(count=1, bin_lo=10_000_000)
    mf = mf.assign(category="mf", count=1, bin_lo=mf["price"].map(_bin_lo))
    cols = ["fy", "category", "bin_lo", "count", "price", "units"]
    return pd.concat([b.assign(units=1)[cols], t.assign(units=1)[cols], mf[cols]], ignore_index=True)


def score(sales: pd.DataFrame, target_fy: int, *, elasticity: float = 0.0) -> pd.DataFrame:
    """Current-law and HD2 tax for each base-year row aged to *target_fy*.

    *fy* may be fractional (a calendar year y is fiscal y + 0.5)."""
    s = sales.copy()
    if "units" not in s:
        s["units"] = 1
    s["price_t"] = s["price"] * (1 + PRICE_GROWTH) ** (target_fy - s["fy"])
    index = (1 + CPI_GROWTH) ** max(target_fy - 2027, 0)
    cur = np.zeros(len(s))
    new = np.zeros(len(s))
    for cat in CATEGORIES:
        m = (s["category"] == cat).to_numpy()
        cur[m] = current_law_tax(s.loc[m, "price_t"].to_numpy(), cat)
        new[m] = hd2_tax(s.loc[m, "price_t"].to_numpy(), cat, index=index)
    # Five or more units: schedule (1) on the whole price today; under HD2 the
    # rate is set by the price per unit (a non-owner purchase), applied to the whole.
    m = (s["category"] == "mf").to_numpy()
    if m.any():
        p, u = s.loc[m, "price_t"].to_numpy(), s.loc[m, "units"].to_numpy()
        cur[m] = current_law_tax(p, "nonres")
        new[m] = u * hd2_tax(p / u, "nonowner", index=index)
    s["tax_now"] = cur * s["count"]
    # Behavioral: sales volume responds to the change in tax as a share of price.
    d_pp = (new - cur) / s["price_t"].to_numpy() * 100
    s["count_hd2"] = s["count"] * np.exp(-elasticity / 100 * d_pp)
    s["tax_hd2"] = new * s["count_hd2"]
    s["tax_hd2_static"] = new * s["count"]
    s["gains"] = new > cur
    return s


def components(sales: pd.DataFrame, target_fy: float, elasticity: float) -> dict:
    """$M per year, averaged over base years: current law, HD2, and the change
    split into increases by purchaser category and cuts (all categories)."""
    out = []
    for b in sorted(sales["fy"].unique()):
        sc = score(sales[sales["fy"] == b], target_fy, elasticity=elasticity)
        d = sc["tax_hd2"] - sc["tax_now"]
        out.append({"now": sc["tax_now"].sum(), "hd2": sc["tax_hd2"].sum(),
                    "gain_owner": d[sc["gains"] & (sc["category"] == "owner")].sum(),
                    "gain_nonowner": d[sc["gains"] & (sc["category"] == "nonowner")].sum(),
                    "cut": d[~sc["gains"]].sum()})
    return {k: v / 1e6 for k, v in pd.DataFrame(out).mean().items()}


def maui_recorded_m(fy: int) -> float:
    t = pd.read_csv(DATA / "maui_sales_totals_fy2016_2026.csv")
    return float(t.loc[t["fy"] == fy, "sum_tax_recorded"].sum() / 1e6)


def disposition(total_m: float, plan) -> dict:
    out, left = {}, total_m
    for name, share, cap in plan:
        amt = min(share * total_m, cap, left)
        out[name] = amt
        left -= amt
    out["General fund"] = left
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Statewide bases

def oahu_mls() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_csv(DATA / "oahu_mls_by_price.csv"), pd.read_csv(DATA / "oahu_mls_summary.csv")


def oahu_sf_alpha() -> float:
    """Pareto index of Oahu single-family sales above $1M (MLS bands, pooled)."""
    m, _ = oahu_mls()
    top = m[(m["type"] == "sf") & (m["band_lo"] >= 1e6)].groupby(["band_lo", "band_hi"], dropna=False)["sales"].sum()
    return pareto_alpha_grouped([(lo, np.inf if pd.isna(hi) else hi, n) for (lo, hi), n in top.items()], 1e6)


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
            continue                       # untiered (Hawaii "Apartment"): no tail information; see sensitivity
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
    """HD2 change by county, $M, from Maui's components."""
    return (mult["m_nonowner"] * comp["gain_nonowner"] + mult["m_owner"] * comp["gain_owner"]
            + mult["m_cut"] * comp["cut"])


# ─────────────────────────────────────────────────────────────────────────────
# Statewide: each county's housing stock × Maui's sale rates

# Rate bins: the extract's value bins, merged above $2M so each holds enough
# Maui sales to estimate a rate.
RATE_EDGES = [0, 3e5, 6e5, 8e5, 1e6, 1.25e6, 1.5e6, 1.75e6, 2e6, 2.5e6, 3e6, 3.5e6, 4e6, 5e6, 6e6, 8e6,
              10e6, 15e6, 20e6]
SALES_BASE_FY = 2024.5    # Maui's FY2023-26 sale prices, relative to 2026 assessed values
STOCK_COUNTIES = ("Honolulu", "Hawaii")
STOCK_GROUPS = {"Honolulu": ("residential",), "Hawaii": ("residential", "condo_unit", "ag_dwelling")}


def _rate_bin(v) -> np.ndarray:
    return np.array(RATE_EDGES)[np.searchsorted(RATE_EDGES, np.asarray(v, dtype=float), side="right") - 1]


def maui_rates() -> pd.DataFrame:
    """Maui, improved residential homes, per (value bin, owner-occupied now):
    arm's-length sales per year per home by buyer category, and sale price
    over assessed value. From Maui's full assessment listing joined to every
    FY2023-26 sale (maui_sales_extract.js)."""
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


def stock_sales(stock: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Synthetic sales (fy, category, count, price) for a county: its homes by
    (value bin, owner-occupied) times Maui's rates for that cell."""
    s = stock.assign(rb=_rate_bin(stock["value_lo"]), mean_value=stock["sum_value"] / stock["count"])
    m = s.merge(rates, on=["rb", "owner_occupied"])
    return pd.DataFrame({"fy": SALES_BASE_FY, "category": m["category"],
                         "count": m["count"] * m["sales_per_home"], "price": m["mean_value"] * m["price_ratio"]})


def county_stock(county: str, groups: tuple[str, ...] | None = None) -> pd.DataFrame:
    if county == "Maui":
        return pd.read_csv(DATA / "maui_residential_stock_2026.csv").query("improved == 1")
    ps = pd.read_csv(DATA / "parcel_stock_by_value.csv")
    groups = groups or STOCK_GROUPS[county]
    return ps[(ps["county"] == county) & ps["group"].isin(groups) & (ps["improved"] == 1)]


def stock_method(target_fy: float, elasticity: float, *, hawaii_groups: tuple[str, ...] | None = None) -> dict:
    """HD2 change ($M) for Honolulu and Hawaii counties from their stock,
    calibrated on Maui: the same procedure applied to Maui's own stock, over
    Maui's sale-by-sale change, gives the factor (it covers sales the rates
    leave out: nominal-price, new-construction and vacant-lot sales)."""
    rates = maui_rates()
    sales = load_sales()
    raw = {c: components(stock_sales(county_stock(c, hawaii_groups if c == "Hawaii" else None), rates),
                         target_fy, elasticity) for c in (*STOCK_COUNTIES, "Maui")}
    exact = components(sales[sales["category"] != "mf"], target_fy, elasticity)    # like for like: no apartment buildings
    k = (exact["hd2"] - exact["now"]) / (raw["Maui"]["hd2"] - raw["Maui"]["now"])
    return {"calibration": k, **{c: k * (raw[c]["hd2"] - raw[c]["now"]) for c in STOCK_COUNTIES},
            "raw": raw}


def _nonowner_share(comp: dict, sm: dict, mult: pd.DataFrame) -> float:
    """Share of the statewide increase (gains only) from non-owner buyers."""
    non = comp["gain_nonowner"] + mult.loc["Kauai", "m_nonowner"] * comp["gain_nonowner"]
    own = comp["gain_owner"] + mult.loc["Kauai", "m_owner"] * comp["gain_owner"]
    for c in STOCK_COUNTIES:
        non += sm["calibration"] * sm["raw"][c]["gain_nonowner"]
        own += sm["calibration"] * sm["raw"][c]["gain_owner"]
    return float(non / (non + own))


# ─────────────────────────────────────────────────────────────────────────────
# Check: Oahu from its MLS sales

def _maui_nonowner_share(sales: pd.DataFrame, edges: list[float]) -> np.ndarray:
    res = sales[sales["category"] != "nonres"]
    band = pd.cut(res["price"], edges, right=False)
    g = res.groupby([band, "category"], observed=False)["count"].sum().unstack().fillna(0)
    return (g["nonowner"] / (g["nonowner"] + g["owner"])).to_numpy()


def oahu_sales_rows(sales: pd.DataFrame, target_nonowner_share: float, condo_tail: str,
                    sf_alpha: float) -> pd.DataFrame:
    """Synthetic Oahu home sales (fy, category, count, price) from MLS bands,
    scaled to every recorded sale (Title Guaranty counts). Above $1M prices
    follow a Pareto tail: single-family fitted to the MLS bands; condos fitted
    to the MLS mean ("mls") or to the recorded-sales value ("recorded"), since
    new-tower closings are recorded but not listed. Owner/non-owner split: the
    share Maui shows in each price band, shifted (in log-odds) so the
    non-owner share of sales above $2M is *target_nonowner_share*."""
    mls, summ = oahu_mls()
    tg = pd.read_csv(DATA / "county_home_sales_tg_2015_2025.csv").set_index(["county", "year"])
    q = (np.arange(400) + 0.5) / 400
    rows = []
    for y in OAHU_MLS_YEARS:
        for typ, n_col, avg_col in (("sf", "n_sf", "avg_sf"), ("condo", "n_condo", "avg_condo")):
            b = mls[(mls["type"] == typ) & (mls["year"] == y)]
            n_mls = b["sales"].sum()
            scale = tg.loc[("Honolulu", y), n_col] / n_mls
            low = b[b["band_lo"] < 1e6]
            for _, r in low.iterrows():
                p = 450e3 if r["band_lo"] == 0 else (r["band_lo"] + r["band_hi"]) / 2
                rows.append((y, typ, r["sales"] * scale, p))
            n_top = b.loc[b["band_lo"] >= 1e6, "sales"].sum()
            if typ == "sf":
                alpha = sf_alpha
            else:
                below = sum(r["sales"] * (450e3 if r["band_lo"] == 0 else (r["band_lo"] + r["band_hi"]) / 2)
                            for _, r in low.iterrows())
                if condo_tail == "mls":
                    total = summ.set_index(["type", "year"]).loc[(typ, y), "mean_price"] * n_mls
                    mean_top = (total - below) / n_top
                else:   # every recorded condo sale, with the unlisted ones' extra value in the tail
                    total = tg.loc[("Honolulu", y), n_col] * tg.loc[("Honolulu", y), avg_col]
                    mean_top = (total - below * scale) / (n_top * scale)
                alpha = mean_top / (mean_top - 1e6)
            for p in 1e6 * (1 - q) ** (-1 / alpha):
                rows.append((y, typ, n_top * scale / len(q), p))
    df = pd.DataFrame(rows, columns=["year", "type", "count", "price"])
    edges = [0, 1e6, 2e6, 3e6, 4e6, 5e6, np.inf]
    s_maui = np.clip(_maui_nonowner_share(sales, edges), 1e-3, 1 - 1e-3)
    band = np.searchsorted(edges, df["price"].to_numpy(), side="right") - 1
    logit = np.log(s_maui / (1 - s_maui))[band]
    top = df["price"].to_numpy() >= 2e6
    w = df["count"].to_numpy()
    lo, hi = -10.0, 10.0
    for _ in range(60):                   # bisection on the log-odds shift
        mid = (lo + hi) / 2
        share = (w[top] / (1 + np.exp(-(logit[top] + mid)))).sum() / w[top].sum()
        lo, hi = (mid, hi) if share < target_nonowner_share else (lo, mid)
    s = 1 / (1 + np.exp(-(logit + (lo + hi) / 2)))
    df["fy"] = df["year"] + 0.5
    return pd.concat([df.assign(category="nonowner", count=w * s), df.assign(category="owner", count=w * (1 - s))])


# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sales = load_sales()
    tiers = pd.read_csv(DATA / "rpt_residential_tiers_fy2027.csv")
    a_hon = honolulu_alpha_mean_excess(tiers)
    a_sf = oahu_sf_alpha()
    bases = county_bases(a_hon)
    mult = multipliers(bases)
    cases = [("static", 0.0), ("behavioral", VOLUME_SEMI_ELASTICITY),
             ("behavioral_low_e", ELASTICITY_RANGE[0]), ("behavioral_high_e", ELASTICITY_RANGE[1])]

    def by_county_change(comp: dict, sm: dict) -> pd.Series:
        """Maui sale by sale; Honolulu and Hawaii from their housing stock;
        Kauai (no parcel values published) from its tax-roll tiers."""
        return pd.Series({"Honolulu": sm["Honolulu"], "Hawaii": sm["Hawaii"],
                          "Kauai": county_change(comp, mult)["Kauai"], "Maui": comp["hd2"] - comp["now"]})

    # Revenue by year; current law from DOTAX.
    rows, comp_cache, sm_cache = [], {}, {}
    for t in TARGET_YEARS:
        for label, e in cases:
            comp = comp_cache[(t, label)] = components(sales, t, e)
            sm = sm_cache[(t, label)] = stock_method(t, e)
            base_now = [components(sales[sales["fy"] == b], t, e)["now"] * DOTAX_COLLECTIONS_M[b] / maui_recorded_m(b)
                        for b in STATE_BASE_FY]
            ch = by_county_change(comp, sm)
            rows.append({"fy": t, "case": label, "maui_now_$M": comp["now"], "maui_hd2_$M": comp["hd2"],
                         "state_now_$M": float(np.mean(base_now)), "state_change_$M": ch.sum(),
                         **{f"change_{c.lower()}_$M": ch[c] for c in COUNTIES}})
    rev = pd.DataFrame(rows)
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)

    t0 = TARGET_YEARS[0]
    st, bh = comp_cache[(t0, "static")], comp_cache[(t0, "behavioral")]
    sm_st, sm_bh = sm_cache[(t0, "static")], sm_cache[(t0, "behavioral")]
    stock_summary = {}
    for c in (*STOCK_COUNTIES, "Maui"):
        stk = county_stock(c)
        top = stk[stk["value_lo"] >= 2e6]
        stock_summary[c] = {"homes": float(stk["count"].sum()), "homes_2m_plus": float(top["count"].sum()),
                            "homes_2m_plus_not_owner_occupied": float(top.loc[top["owner_occupied"] == 0, "count"].sum())}
    by_county = bases.join(mult).assign(
        method=pd.Series({"Honolulu": "housing stock", "Hawaii": "housing stock", "Kauai": "tax-roll tiers",
                          "Maui": "every sale"}),
        homes=pd.Series({c: v["homes"] for c, v in stock_summary.items()}),
        homes_2m_plus=pd.Series({c: v["homes_2m_plus"] for c, v in stock_summary.items()}),
        homes_2m_plus_not_owner_occupied=pd.Series({c: v["homes_2m_plus_not_owner_occupied"]
                                                    for c, v in stock_summary.items()}),
        change_static_fy2028_M=by_county_change(st, sm_st), change_behavioral_fy2028_M=by_county_change(bh, sm_bh),
        tiers_static_fy2028_M=county_change(st, mult), tiers_behavioral_fy2028_M=county_change(bh, mult))
    by_county.index.name = "county"
    by_county.to_csv(OUT_DIR / "by_county.csv")

    # Sales by price band, FY2028 prices, before any change in sales: Maui
    # sale by sale; Honolulu and Hawaii from their stock (Maui-calibrated counts
    # are not rescaled; the calibration applies to revenue).
    band_edges = [0, 1e6, 2e6, 3e6, 4e6, 6e6, 1e7, np.inf]
    band_labels = ["Under $1M", "$1M-$2M", "$2M-$3M", "$3M-$4M", "$4M-$6M", "$6M-$10M", "$10M+"]
    rates = maui_rates()
    band_rows = []
    for c in (*STOCK_COUNTIES, "Maui"):
        src = sales[sales["category"] != "nonres"] if c == "Maui" else stock_sales(county_stock(c), rates)
        parts = [score(src[src["fy"] == b], t0).assign(w=1.0) for b in sorted(src["fy"].unique())]
        scd = pd.concat(parts)
        scd["band"] = pd.cut(scd["price_t"], band_edges, labels=band_labels, right=False)
        n_b = len(parts)
        g = (scd.groupby(["band", "category"], observed=True)
                .agg(sales=("count", "sum"), tax_now=("tax_now", "sum"), tax_hd2=("tax_hd2_static", "sum")).reset_index())
        g[["sales", "tax_now", "tax_hd2"]] /= n_b
        band_rows.append(g.assign(county=c))
    county_bands = pd.concat(band_rows)[["county", "band", "category", "sales", "tax_now", "tax_hd2"]]
    county_bands.to_csv(OUT_DIR / "county_bands.csv", index=False)

    # Checks: modeled home sales against Title Guaranty (recorded home sales),
    # and Oahu's high end against its MLS sales.
    tg = pd.read_csv(DATA / "county_home_sales_tg_2015_2025.csv").query("year in @TG_YEARS")
    checks = {}
    for c in (*STOCK_COUNTIES, "Maui"):
        src = sales[sales["category"] != "nonres"] if c == "Maui" else stock_sales(county_stock(c), rates)
        n_b = src["fy"].nunique()
        t = tg[tg["county"] == c]
        checks[c] = {"model_sales_per_year": float(src["count"].sum() / n_b),
                     "model_value_$B": float((src["count"] * src["price"]).sum() / n_b / 1e9),
                     "tg_sales_per_year": float(t["n_total"].mean()),
                     "tg_value_$B": float((t["n_total"] * t["avg_total"]).mean() / 1e9),
                     **{f"model_sales_{int(x / 1e6)}m_plus": float(src.loc[src["price"] >= x, "count"].sum() / n_b)
                        for x in (2e6, 3e6, 5e6)}}
    mls, _ = oahu_mls()
    sf = mls[(mls["type"] == "sf")].groupby("band_lo")["sales"].sum() / len(OAHU_MLS_YEARS)
    checks["Honolulu"]["mls_sf_sales_2m_plus"] = float(sf[sf.index >= 2e6].sum())
    checks["Honolulu"]["mls_sf_sales_3m_plus"] = float(sf[sf.index >= 3e6].sum())
    checks["Honolulu"]["mls_sf_sales_5m_plus"] = float(sf[sf.index >= 5e6].sum())

    # Oahu check: rebuilt from MLS sales.
    oahu_target = bases.loc["Honolulu", "nonowner_above_2m_$B"] / (
        bases.loc["Honolulu", "nonowner_above_2m_$B"] + bases.loc["Honolulu", "owner_above_2m_$B"])
    oahu_check = {}
    for tail in ("mls", "recorded"):
        rows_o = oahu_sales_rows(sales, oahu_target, tail, a_sf)
        for label, e in (("static", 0.0), ("behavioral", VOLUME_SEMI_ELASTICITY)):
            c = components(rows_o[["fy", "category", "count", "price"]], t0, e)
            oahu_check[f"{tail}_{label}"] = c["hd2"] - c["now"]
        oahu_check[f"{tail}_recorded_value_$B"] = float(
            (rows_o["count"] * rows_o["price"]).sum() / len(OAHU_MLS_YEARS) / 1e9)

    # Sensitivity, FY2028 statewide change.
    central_st, central_bh = by_county_change(st, sm_st), by_county_change(bh, sm_bh)
    no_ag = {lab: stock_method(t0, e, hawaii_groups=("residential", "condo_unit")) for lab, e in cases[:2]}

    def swap(base: pd.Series, **repl) -> float:
        return float(base.drop(list(repl)).sum() + sum(repl.values()))
    sens = [{"variant": "Central estimate", "static": central_st.sum(), "behavioral": central_bh.sum()},
            {"variant": "Without the Maui calibration",
             "static": swap(central_st, Honolulu=sm_st["Honolulu"] / sm_st["calibration"],
                            Hawaii=sm_st["Hawaii"] / sm_st["calibration"]),
             "behavioral": swap(central_bh, Honolulu=sm_bh["Honolulu"] / sm_bh["calibration"],
                                Hawaii=sm_bh["Hawaii"] / sm_bh["calibration"])},
            {"variant": "Hawaii Island without homes on agricultural land",
             "static": swap(central_st, Hawaii=no_ag["static"]["Hawaii"]),
             "behavioral": swap(central_bh, Hawaii=no_ag["behavioral"]["Hawaii"])},
            {"variant": "Oahu rebuilt from MLS sales (condos priced as listed)",
             "static": swap(central_st, Honolulu=oahu_check["mls_static"]),
             "behavioral": swap(central_bh, Honolulu=oahu_check["mls_behavioral"])},
            {"variant": "Oahu rebuilt from MLS sales (condos priced to all recorded sales)",
             "static": swap(central_st, Honolulu=oahu_check["recorded_static"]),
             "behavioral": swap(central_bh, Honolulu=oahu_check["recorded_behavioral"])},
            {"variant": "All three counties from tax-roll tiers (first statewide method)",
             "static": county_change(st, mult).sum(), "behavioral": county_change(bh, mult).sum()}]
    for label, e in cases[2:]:
        sens.append({"variant": f"Sales response {e:g}% per point", "static": np.nan,
                     "behavioral": by_county_change(comp_cache[(t0, label)], sm_cache[(t0, label)]).sum()})
    sens = pd.DataFrame(sens)
    sens.to_csv(OUT_DIR / "sensitivity.csv", index=False)

    # Maui by band, first scored year, static and behavioral, averaged over base years.
    parts = []
    for b in MAUI_BASE_FY:
        sc = score(sales[sales["fy"] == b], t0, elasticity=VOLUME_SEMI_ELASTICITY)
        parts.append(sc.assign(base_fy=b))
    sc = pd.concat(parts)
    edges = [0, 6e5, 1e6, 2e6, 3e6, 4e6, 6e6, 1e7, np.inf]
    labels = ["Under $600K", "$600K-$1M", "$1M-$2M", "$2M-$3M", "$3M-$4M", "$4M-$6M", "$6M-$10M", "$10M+"]
    sc["band"] = pd.cut(sc["price_t"], edges, labels=labels, right=False)
    n_years = len(MAUI_BASE_FY)
    by_band = (sc.groupby(["band", "category"], observed=True)
                 .agg(sales=("count", "sum"), tax_now=("tax_now", "sum"),
                      tax_hd2_static=("tax_hd2_static", "sum"), tax_hd2=("tax_hd2", "sum"))
                 .reset_index())
    for c in ["sales", "tax_now", "tax_hd2_static", "tax_hd2"]:
        by_band[c] = by_band[c] / n_years
    by_band["avg_change_per_sale"] = (by_band["tax_hd2_static"] - by_band["tax_now"]) / by_band["sales"]
    by_band.to_csv(OUT_DIR / "maui_by_band.csv", index=False)

    # Headline shares and the $3-4M spotlight (Maui, first scored year, static).
    sc["d_per_sale"] = (sc["tax_hd2_static"] - sc["tax_now"]) / sc["count"]
    n = sc["count"].sum()
    spot = sc[(sc["price_t"] >= 3e6) & (sc["price_t"] < 4e6) & (sc["category"] != "nonres")]
    # No aging, no indexing: each base year scored at its own prices and law.
    at_base_list = [components(sales[sales["fy"] == b], b, 0.0) for b in MAUI_BASE_FY]
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
        # Like-for-like with the House Finance figure (~$150M): static, at the
        # base years' own prices and law (no aging, no indexing).
        "state_static_change_at_base_prices_$M": float(sum(
            np.mean([by_county_change(at_b, stock_method(b, 0.0))[c] for at_b, b in zip(at_base_list, MAUI_BASE_FY, strict=True)])
            for c in COUNTIES)),
        "state_nonowner_share_of_increase": _nonowner_share(st, sm_st, mult),
        "stock_calibration_factor": {"static": sm_st["calibration"], "behavioral": sm_bh["calibration"]},
        "checks": checks,
        "alpha_honolulu_residential_a": a_hon,
        "alpha_oahu_sf_sales": a_sf,
        "oahu_nonowner_share_above_2m": float(oahu_target),
        "oahu_check_$M": oahu_check,
        "tg_home_sales_years": TG_YEARS,
    }

    ex = pd.DataFrame([{"price": p, **{f"{c}_{law}": float(f(p, c)) for c in ("owner", "nonowner")
                                        for law, f in (("now", current_law_tax), ("hd2", hd2_tax))}}
                       for p in (800e3, 1.5e6, 2e6, 2.5e6, 3e6, 3.5e6, 3.999e6, 4e6, 5e6, 10e6)])
    ex.to_csv(OUT_DIR / "examples.csv", index=False)

    # Where the money goes, first scored year, with the sales response: central,
    # and the top of the sales-response range.
    central = rev[(rev["fy"] == t0) & (rev["case"] == "behavioral")].iloc[0]
    high = rev[(rev["fy"] == t0) & (rev["case"] == "behavioral_low_e")].iloc[0]
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
                               "state_base_fy": STATE_BASE_FY, "tg_years": TG_YEARS,
                               "oahu_mls_years": OAHU_MLS_YEARS, "tail_at": TAIL_AT,
                               "price_growth": PRICE_GROWTH, "cpi_growth": CPI_GROWTH,
                               "volume_semi_elasticity": VOLUME_SEMI_ELASTICITY,
                               "elasticity_range": ELASTICITY_RANGE,
                               "alpha_honolulu_residential_a": round(a_hon, 3), "alpha_oahu_sf_sales": round(a_sf, 3),
                               "rate_edges": RATE_EDGES, "sales_base_fy": SALES_BASE_FY,
                               "stock_groups": STOCK_GROUPS,
                               "stock_calibration_factor": round(sm_st["calibration"], 3),
                               "breakeven": {c: breakeven_price(c) for c in ("owner", "nonowner")},
                               "dotax_collections_m": DOTAX_COLLECTIONS_M},
                       inputs={"maui_sales": "County of Maui RPT Sales Data File (DocumentCenter/View/8070), "
                                             "extracted 2026-09-24; assessment listing as of 2026-04-06",
                               "dotax": "DOTAX Annual Report FY2024-25, Table 1.11",
                               "county_home_sales": "Title Guaranty of Hawaii from Bureau of Conveyances data, via "
                                                    "DBEDT Quarterly Statistical and Economic Report 2026 Q3, "
                                                    "Tables E-11, E-12, G-49 to G-56",
                               "tax_rolls": "Real Property Tax Valuations, tax year 2026-27, statewide report "
                                            "(Honolulu RPAD, July 2026)",
                               "owner_values": "ACS PUMS 2020-2024 5-year housing file (VALP, ADJHSG)",
                               "oahu_mls": "Honolulu Board of Realtors MLS via State of Hawaii Data Book 2024, "
                                           "Tables 21.33 and 21.34",
                               "maui_stock": "County of Maui RPT Full Assessment Listing as of 4/06/2026 "
                                             "(DocumentCenter/View/8079), joined to the sales file",
                               "parcel_stock": "Honolulu RPAD ASMTPITT table (City open data hub, CadastralTables/"
                                               "FeatureServer/10, tax year 2026); Hawaii County parcels (State GIS "
                                               "ParcelsZoning/MapServer/5, 4/27/2026); dwelling units per TMK "
                                               "(tmk_state_2025_dwelling_data)"})
    pd.set_option("display.width", 220)
    print(rev.round(1).to_string(index=False))
    print(by_county.round(2).to_string())
    print(sens.round(1).to_string(index=False))
    print(json.dumps({k: summary[k] for k in ("state_static_change_at_base_prices_$M", "alpha_honolulu_residential_a",
                                              "alpha_oahu_sf_sales", "oahu_nonowner_share_above_2m", "oahu_check_$M",
                                              "state_nonowner_share_of_increase", "general_fund_breakeven_gain_$M")},
                     indent=1, default=float))
    print(disp.round(1).to_string(index=False))


if __name__ == "__main__":
    run()
    sys.exit(0)
