"""Independent bound on nonresident sale proceeds behind DOTAX's nonresident gains.

DOTAX publishes no HARPTA withholding total (docs/data_source_tests/harpta-nonresident-bound.md),
so proceeds are built from files the repo already has:
  nonresident share of Oahu resales by value (nonresident_seller_origin_oahu.csv; Dec 2023-Sep 2026)
  x all-county home-sale dollars (county_home_sales_tg_2015_2025.csv)
and compared with Table 21 nonresident gains (dotax_indinc_<year>.json).
Read-only; feeds nothing.   Usage: python scripts/harpta_nonresident_bound.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

D = Path(__file__).resolve().parent.parent / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data"
RATE = 0.0725
LEVELS = {"low": 0.78, "central": 1.0, "high": 1.45}   # nonresident_cg level multipliers (vs TY2023)


def main() -> int:
    o = pd.read_csv(D / "calibration" / "nonresident_seller_origin_oahu.csv")
    tot, hi, unk = o.value_M.sum(), o[o.origin == "HI"].value_M.sum(), o[o.origin == "unknown"].value_M.sum()
    share_lo, share_hi = (tot - hi - unk) / tot, (tot - hi) / tot
    c = pd.read_csv(D / "raw" / "conveyance" / "county_home_sales_tg_2015_2025.csv")
    c["usd"] = c.n_total * c.avg_total / 1e6
    c["buyer_nl"] = (c.n_mainland * c.avg_mainland + c.n_foreign * c.avg_foreign) / 1e6
    by = c.groupby("year")[["n_total", "usd", "buyer_nl"]].sum()
    print(f"Oahu nonresident seller share of resale value: {share_lo:.3f}-{share_hi:.3f}")
    rows = []
    for y in range(2018, 2024):
        cl = json.load(open(D / "calibration" / f"dotax_indinc_{y}.json"))["table_21_nltcg"]["classes"]
        rets, gains = sum(x["nonres_returns"] for x in cl), sum(x["nonres_amount_M"] for x in cl)
        T = by.loc[y, "usd"]
        # low: Oahu share statewide; high: nonresident BUYER share (statewide, TG), a stand-in for
        # neighbor-island sellers who are mostly second-home owners
        s_lo, s_hi = share_lo * T, by.loc[y, "buyer_nl"]
        rows.append(dict(year=y, gain_returns=rets, gains_M=gains, home_sales_B=T / 1e3,
                         proceeds_lo_B=s_lo / 1e3, proceeds_hi_B=s_hi / 1e3,
                         gain_per_proceeds_hi=gains / s_lo, gain_per_proceeds_lo=gains / s_hi,
                         n288_lo_M=RATE * s_lo, n288_hi_M=RATE * s_hi))
    r = pd.DataFrame(rows).set_index("year")
    pd.set_option("display.width", 200)
    print(r.round(3).to_string())
    s23 = r.loc[2023]
    print("\nTY2023 model levels vs proceeds bound (gain/proceeds):")
    for k, m in LEVELS.items():
        g = m * s23.gains_M
        print(f"  {k:8s} gains ${g:,.0f}M -> {g / (s23.proceeds_hi_B * 1e3):.2f} to {g / (s23.proceeds_lo_B * 1e3):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
