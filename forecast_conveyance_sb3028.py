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

Data
----
Maui County's public sales file (every recorded conveyance, with price and
the conveyance tax paid), extracted to data/raw/conveyance/ by
scripts/conveyance/maui_sales_extract.js. The tax paid identifies which
schedule applied, so owner-occupancy is observed, not assumed: of priced,
taxed conveyances in FY2023-FY2026, 97% match schedule (1) or (2) to within
0.5% (the rest are treated as nonresidential, which HD2 leaves unchanged).
Schedule-(1) sales on residential parcels are owner-occupant; on other
parcels, nonresidential. Current law applied to the binned data reproduces
the tax Maui recorded on those sales exactly.

Statewide: no other county publishes sales in bulk. DOTAX publishes only
fiscal-year collections. So:
  - central: Maui's gain per dollar of current-law collections, applied to
    the state's collections (the county's share each year, 18-27%);
  - low: the same, but with the rest of the state's high-end intensity cut
    to THETA_LOW of Maui's, from 2025 counts of $3M+ sales (Oahu, Kauai)
    against Maui's own count (see _theta_from_anchors).

Behavioral response: sales volume falls by VOLUME_SEMI_ELASTICITY percent
per percentage point of price added in tax (rises where HD2 cuts the tax).

Outputs (runs/conveyance_sb3028/):
  revenue_by_year.csv   FY2028-FY2031: current law, HD2, change; Maui and
                        statewide; static and behavioral; central and low
  maui_by_band.csv      Maui, average year: sales, tax now vs HD2, by price
                        band and purchaser category
  examples.csv          tax on example prices under both laws
  disposition.csv       where the money goes, FY2028 with the sales response:
                        current law, HD2 central, HD2 low
  summary.json          shares of sales paying less / same / more; $3-4M
                        spotlight; breakevens; general-fund breakeven
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
from tax_modeler.conveyance import CATEGORIES, breakeven_price, current_law_tax, hd2_tax

REPO = Path(__file__).parent
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT_DIR = REPO / "runs" / "conveyance_sb3028"

TARGET_YEARS = [2028, 2029, 2030, 2031]   # first full year if enacted in the 2027 session
MAUI_BASE_FY = [2023, 2024, 2025, 2026]
STATE_BASE_FY = [2023, 2024, 2025]        # DOTAX FY2026 not yet published

PRICE_GROWTH = 0.04          # nominal; FHFA Hawaii HPI +3.9% 2023-24, 6.5%/yr 2014-24
CPI_GROWTH = 0.03            # Urban Hawaii CPI 2.9%/yr 2015-25; drives HD2 bracket indexing
VOLUME_SEMI_ELASTICITY = 10  # % fewer sales per 1 point of price in added tax (range 5-15)
ELASTICITY_RANGE = (5, 15)

# DOTAX Annual Report FY2024-25, Table 1.11 (conveyance tax collections, $M).
DOTAX_COLLECTIONS_M = {2016: 66.083, 2017: 94.537, 2018: 100.603, 2019: 85.965, 2020: 61.110,
                       2021: 62.725, 2022: 188.418, 2023: 92.132, 2024: 97.411, 2025: 96.045}

# 2025 sales of $3M+ reported by brokerage market reports (for the low case):
# Oahu single-family 150 (List Sotheby's quarterly reports), Oahu condos 23 at
# $5M+ (none published at $3-5M; doubled here), Kauai residential homes 66
# (Kauai luxury-market analysis). Hawaii Island unpublished; set equal to Kauai.
ANCHOR_3M_PLUS_2025 = {"oahu_sf": 150, "oahu_condo": 46, "kauai": 66, "hawaii_island": 66}

# Disposition, HRS §247-7 today and as HD2 would amend it: (share, cap $M).
DISPOSITION_NOW = [("Land conservation fund", .10, 5.1), ("Rental housing revolving fund", .50, 38.0)]
DISPOSITION_HD2 = [("Land conservation fund", .05, 10.0), ("Rental housing revolving fund", .20, 40.0),
                   ("Hawaiian home lands infrastructure and housing fund", .30, 60.0),
                   ("Transit-oriented development infrastructure (dwelling unit revolving fund)", .20, 40.0)]


# ─────────────────────────────────────────────────────────────────────────────

def load_sales() -> pd.DataFrame:
    """One row per bin (or per $10M+ sale): fy, category, count, mean price."""
    b = pd.read_csv(DATA / "maui_sales_bins_fy2023_2026.csv")
    b["price"] = b["sum_price"] / b["count"]
    t = pd.read_csv(DATA / "maui_sales_over_10m_fy2023_2026.csv").assign(count=1, bin_lo=10_000_000)
    t["sum_price"] = t["price"]
    return pd.concat([b[["fy", "category", "bin_lo", "count", "price"]],
                      t[["fy", "category", "bin_lo", "count", "price"]]], ignore_index=True)


def score(sales: pd.DataFrame, target_fy: int, *, elasticity: float = 0.0) -> pd.DataFrame:
    """Current-law and HD2 tax for each base-year row aged to *target_fy*."""
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
    return s


def maui_recorded_m(fy: int) -> float:
    t = pd.read_csv(DATA / "maui_sales_totals_fy2016_2026.csv")
    return float(t.loc[t["fy"] == fy, "sum_tax_recorded"].sum() / 1e6)


def _theta_from_anchors(sales: pd.DataFrame) -> tuple[float, dict]:
    """Rest-of-state high-end intensity relative to Maui's.

    Maui's residential $3M+ sales in FY2025 against the anchored rest-of-state
    count, compared with the two areas' shares of collections. Under a
    Maui-shaped state (the central case), the rest of the state would have
    (state/Maui collections - 1) times Maui's $3M+ sales; the anchors say it
    has fewer.
    """
    s25 = sales[(sales["fy"] == 2025) & (sales["category"] != "nonres") & (sales["price"] >= 3e6)]
    maui_3m = float(s25["count"].sum())
    ros_3m = float(sum(ANCHOR_3M_PLUS_2025.values()))
    scale = DOTAX_COLLECTIONS_M[2025] / maui_recorded_m(2025) - 1
    theta = min(1.0, (ros_3m / maui_3m) / scale)
    return theta, {"maui_residential_3m_plus_fy2025": maui_3m, "rest_of_state_3m_plus_2025": ros_3m,
                   "maui_shaped_rest_of_state_multiple": round(scale, 3)}


def disposition(total_m: float, plan) -> dict:
    out, left = {}, total_m
    for name, share, cap in plan:
        amt = min(share * total_m, cap, left)
        out[name] = amt
        left -= amt
    out["General fund"] = left
    return out


def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sales = load_sales()
    theta, theta_diag = _theta_from_anchors(sales)

    rows = []
    for t in TARGET_YEARS:
        for label, e in [("static", 0.0), ("behavioral", VOLUME_SEMI_ELASTICITY),
                         ("behavioral_low_e", ELASTICITY_RANGE[0]), ("behavioral_high_e", ELASTICITY_RANGE[1])]:
            per_base = []
            for b in MAUI_BASE_FY:
                sc = score(sales[sales["fy"] == b], t, elasticity=e)
                now, hd2 = sc["tax_now"].sum() / 1e6, sc["tax_hd2"].sum() / 1e6
                row = {"base_fy": b, "maui_now": now, "maui_hd2": hd2, "maui_change": hd2 - now}
                if b in STATE_BASE_FY:
                    # State-to-Maui multiple of collections in the base year, applied to
                    # the aged Maui figures (price growth scales both alike).
                    k = DOTAX_COLLECTIONS_M[b] / maui_recorded_m(b)
                    row["state_now"] = now * k
                    row["state_change_central"] = (hd2 - now) * k
                    row["state_change_low"] = (hd2 - now) * (1 + theta * (k - 1))
                per_base.append(row)
            df = pd.DataFrame(per_base)
            rows.append({"fy": t, "case": label,
                         "maui_now_$M": df["maui_now"].mean(), "maui_hd2_$M": df["maui_hd2"].mean(),
                         "maui_change_$M": df["maui_change"].mean(),
                         "state_now_$M": df["state_now"].mean(),
                         "state_change_central_$M": df["state_change_central"].mean(),
                         "state_change_low_$M": df["state_change_low"].mean()})
    rev = pd.DataFrame(rows)
    rev.to_csv(OUT_DIR / "revenue_by_year.csv", index=False)

    # Maui by band, first scored year, static and behavioral, averaged over base years.
    t0 = TARGET_YEARS[0]
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
        # base years' own prices and law (no aging, no indexing), central scaling.
        "state_static_change_at_base_prices_$M": float(np.mean([
            (lambda s: (s["tax_hd2_static"].sum() - s["tax_now"].sum()) / 1e6)(score(sales[sales["fy"] == b], b))
            * DOTAX_COLLECTIONS_M[b] / maui_recorded_m(b) for b in STATE_BASE_FY])),
        "theta_low": theta, **theta_diag,
    }

    ex = pd.DataFrame([{"price": p, **{f"{c}_{law}": float(f(p, c)) for c in ("owner", "nonowner")
                                        for law, f in (("now", current_law_tax), ("hd2", hd2_tax))}}
                       for p in (800e3, 1.5e6, 2e6, 2.5e6, 3e6, 3.5e6, 3.999e6, 4e6, 5e6, 10e6)])
    ex.to_csv(OUT_DIR / "examples.csv", index=False)

    # Where the money goes, first scored year, with the sales response.
    central = rev[(rev["fy"] == t0) & (rev["case"] == "behavioral")].iloc[0]
    now_m = central["state_now_$M"]
    d_now = disposition(now_m, DISPOSITION_NOW)
    d_central = disposition(now_m + central["state_change_central_$M"], DISPOSITION_HD2)
    d_low = disposition(now_m + central["state_change_low_$M"], DISPOSITION_HD2)
    funds = [name for name, _, _ in DISPOSITION_HD2] + ["General fund"]
    disp = pd.DataFrame([{"fund": f, "current_law_$M": d_now.get(f, 0.0),
                          "hd2_central_$M": d_central[f], "hd2_low_$M": d_low[f]} for f in funds])
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
                               "state_base_fy": STATE_BASE_FY, "price_growth": PRICE_GROWTH,
                               "cpi_growth": CPI_GROWTH, "volume_semi_elasticity": VOLUME_SEMI_ELASTICITY,
                               "elasticity_range": ELASTICITY_RANGE, "theta_low": round(theta, 3),
                               "theta_diag": theta_diag, "anchors": ANCHOR_3M_PLUS_2025,
                               "breakeven": {c: breakeven_price(c) for c in ("owner", "nonowner")},
                               "dotax_collections_m": DOTAX_COLLECTIONS_M},
                       inputs={"maui_sales": "County of Maui RPT Sales Data File (DocumentCenter/View/8070), "
                                             "extracted 2026-09-24; assessment listing as of 2026-04-06",
                               "dotax": "DOTAX Annual Report FY2024-25, Table 1.11"})
    pd.set_option("display.width", 200)
    print(rev.round(1).to_string(index=False))
    print(f"\ntheta (low case) = {theta:.2f}  {theta_diag}")
    print(by_band.round(0).to_string(index=False))
    print(disp.round(1).to_string(index=False))


if __name__ == "__main__":
    run()
    sys.exit(0)
