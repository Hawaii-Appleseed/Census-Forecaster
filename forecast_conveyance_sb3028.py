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
No other county publishes sales with prices in bulk, so each county is built
from official statewide data, with Maui's sales as the template:

  - HD2's increase falls on the part of a home's price above about $2M, and
    per dollar of that value it is nearly flat in price (4-6% for non-owner
    buyers from $3M to $20M). So each county's increase on non-owner sales
    is Maui's times the ratio of the county's non-owner residential value
    above $2M to Maui's, from the 2026-27 property tax rolls (the counties'
    tiered classes; rpt_residential_tiers_fy2027.csv, via value_above()).
  - Owner-occupant sales the same way, with owner-occupied value above $2M
    from ACS PUMS 2020-2024 (pums_owner_value_tail_2020_2024.csv), as ratios
    to Maui's (whose tiers give its level).
  - HD2's small cuts below ~$2.2M scale instead with each county's total home
    sales value (Title Guaranty / Bureau of Conveyances, via DBEDT;
    county_home_sales_tg_2015_2025.csv).
  - Check: Oahu rebuilt from its MLS sales by price (Honolulu Board of
    Realtors via the State Data Book), scaled to every recorded sale.

The assumption doing the work is that high-end homes sell at Maui's rate in
every county. Current-law collections statewide are DOTAX's.

Behavioral response: sales volume falls by VOLUME_SEMI_ELASTICITY percent
per percentage point of price added in tax (rises where HD2 cuts the tax).

Outputs (runs/conveyance_sb3028/):
  revenue_by_year.csv   FY2028-FY2031: current law, HD2 change, Maui and each
                        county, static and with the sales response
  by_county.csv         FY2028: each county's bases, multipliers and change
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

def load_sales() -> pd.DataFrame:
    """One row per bin (or per $10M+ sale): fy, category, count, mean price."""
    b = pd.read_csv(DATA / "maui_sales_bins_fy2023_2026.csv")
    b["price"] = b["sum_price"] / b["count"]
    t = pd.read_csv(DATA / "maui_sales_over_10m_fy2023_2026.csv").assign(count=1, bin_lo=10_000_000)
    t["sum_price"] = t["price"]
    return pd.concat([b[["fy", "category", "bin_lo", "count", "price"]],
                      t[["fy", "category", "bin_lo", "count", "price"]]], ignore_index=True)


def score(sales: pd.DataFrame, target_fy: int, *, elasticity: float = 0.0) -> pd.DataFrame:
    """Current-law and HD2 tax for each base-year row aged to *target_fy*.

    *fy* may be fractional (a calendar year y is fiscal y + 0.5)."""
    s = sales.copy()
    s["price_t"] = s["price"] * (1 + PRICE_GROWTH) ** (target_fy - s["fy"])
    index = (1 + CPI_GROWTH) ** max(target_fy - 2027, 0)
    cur = np.zeros(len(s))
    new = np.zeros(len(s))
    for cat in CATEGORIES:
        m = (s["category"] == cat).to_numpy()
        cur[m] = current_law_tax(s.loc[m, "price_t"].to_numpy(), cat)
        new[m] = hd2_tax(s.loc[m, "price_t"].to_numpy(), cat, index=index)
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

    # Revenue by year: Maui exact; counties from Maui's components; current law from DOTAX.
    rows, comp_cache = [], {}
    for t in TARGET_YEARS:
        for label, e in cases:
            comp = comp_cache[(t, label)] = components(sales, t, e)
            base_now = [components(sales[sales["fy"] == b], t, e)["now"] * DOTAX_COLLECTIONS_M[b] / maui_recorded_m(b)
                        for b in STATE_BASE_FY]
            ch = county_change(comp, mult)
            rows.append({"fy": t, "case": label, "maui_now_$M": comp["now"], "maui_hd2_$M": comp["hd2"],
                         "state_now_$M": float(np.mean(base_now)), "state_change_$M": ch.sum(),
                         **{f"change_{c.lower()}_$M": ch[c] for c in COUNTIES}})
    rev = pd.DataFrame(rows)
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)

    t0 = TARGET_YEARS[0]
    st, bh = comp_cache[(t0, "static")], comp_cache[(t0, "behavioral")]
    by_county = bases.join(mult).assign(
        change_static_fy2028_M=county_change(st, mult), change_behavioral_fy2028_M=county_change(bh, mult))
    by_county.index.name = "county"
    by_county.to_csv(OUT_DIR / "by_county.csv")

    # Oahu check: rebuilt from MLS sales, against the tax-roll build-up.
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
    alt = multipliers(county_bases(a_sf))
    sens = [{"variant": "Central: tax-roll build-up", "static": county_change(st, mult).sum(),
             "behavioral": county_change(bh, mult).sum()},
            {"variant": "Honolulu tail from MLS single-family sales", "static": county_change(st, alt).sum(),
             "behavioral": county_change(bh, alt).sum()}]
    for tail, name in (("mls", "listed sales"), ("recorded", "recorded sales value")):
        d_st = oahu_check[f"{tail}_static"] - county_change(st, mult)["Honolulu"]
        d_bh = oahu_check[f"{tail}_behavioral"] - county_change(bh, mult)["Honolulu"]
        sens.append({"variant": f"Oahu from MLS sales (condo tail fitted to {name})",
                     "static": county_change(st, mult).sum() + d_st, "behavioral": county_change(bh, mult).sum() + d_bh})
    for label, e in cases[2:]:
        c = comp_cache[(t0, label)]
        sens.append({"variant": f"Sales response {e:g}% per point", "static": np.nan,
                     "behavioral": county_change(c, mult).sum()})
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
    at_base = [components(sales[sales["fy"] == b], b, 0.0) for b in MAUI_BASE_FY]   # no aging, no indexing
    at_base = {k: float(np.mean([c[k] for c in at_base])) for k in at_base[0]}
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
        "state_static_change_at_base_prices_$M": float(county_change(at_base, mult).sum()),
        "state_nonowner_share_of_increase": float(
            (mult["m_nonowner"] * st["gain_nonowner"]).sum()
            / ((mult["m_nonowner"] * st["gain_nonowner"]).sum() + (mult["m_owner"] * st["gain_owner"]).sum())),
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
                                           "Tables 21.33 and 21.34"})
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
