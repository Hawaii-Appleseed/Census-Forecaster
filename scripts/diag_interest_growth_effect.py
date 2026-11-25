"""What the interest growth path touches and how much income it adds (check; poverty pipeline not run).

    uv run python scripts/diag_interest_growth_effect.py

Reads the cached tax units read-only. Writes in-memory counterfactuals only; the one output is
data/tax_modeler/cbo/interest_growth_effect.csv. Aging is `age_filers_with_components` with
base_year=2024 exactly as `year_recalibrator` calls it (PUMS dollar-year, so growth is relative to 2024).
SPM ratio = unit income / Honolulu renter SPM threshold for the unit's filing composition
(tax-unit fallback of `threshold_for_units`; the cache has no tenure or county), a rough stand-in
for the SPM line, measured on the 2024 base incomes.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from tax_modeler.calibration.cbo_aging import (
    DEFAULT_HAWAII_FACTORS,
    age_filers_with_components,
    load_cbo_rates,
)
from tax_modeler.poverty.thresholds import threshold_for_units

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "artifacts" / "tax_units_cache.parquet"
OUT = REPO / "data" / "tax_modeler" / "cbo" / "interest_growth_effect.csv"
YEARS = (2025, 2027, 2030)
BASE = 2024


def load() -> pd.DataFrame:
    cols = [c for c in pd.read_parquet(CACHE).columns if not c.startswith("weight_r")]
    df = pd.read_parquet(CACHE, columns=cols)
    df["intp"] = df.primary_intp.fillna(0) + df.secondary_intp.fillna(0)
    df["spm_ratio"] = df.income / threshold_for_units(df, year=BASE)
    w = df.weight.to_numpy()
    order = np.argsort(df.income.to_numpy())
    cum = np.cumsum(w[order]) / w.sum()
    dec = np.empty(len(df), dtype=int)
    dec[order] = np.minimum((cum * 10).astype(int), 9) + 1
    df["decile"] = dec
    return df


def groups(df: pd.DataFrame) -> dict[str, np.ndarray]:
    r = df.spm_ratio
    g = {"all units": np.ones(len(df), bool)}
    for d in range(1, 11):
        g[f"income decile {d}"] = (df.decile == d).to_numpy()
    g["SPM ratio < 0.5"] = (r < 0.5).to_numpy()
    g["SPM ratio 0.5-1.5"] = ((r >= 0.5) & (r <= 1.5)).to_numpy()
    g["SPM ratio 0.5-1.0"] = ((r >= 0.5) & (r < 1.0)).to_numpy()
    g["SPM ratio 1.0-1.5"] = ((r >= 1.0) & (r <= 1.5)).to_numpy()
    g["SPM ratio > 1.5"] = (r > 1.5).to_numpy()
    g["head 65+"] = (df.primary_agep >= 65).to_numpy()
    g["head 65+, SPM ratio 0.5-1.5"] = ((df.primary_agep >= 65) & (r >= 0.5) & (r <= 1.5)).to_numpy()
    g["INTP>0"] = (df.intp > 0).to_numpy()
    g["INTP>0, SPM ratio 0.5-1.5"] = ((df.intp > 0) & (r >= 0.5) & (r <= 1.5)).to_numpy()
    return g


def aged_income(df: pd.DataFrame, year: int, int_factors: dict[int, float] | None, hi: float) -> pd.Series:
    """Aged income with the interest path swapped (int_factors) and/or its Hawaii factor set to hi."""
    rates = load_cbo_rates()
    if int_factors is not None:
        rates = replace(rates, factors={**rates.factors, "interest": int_factors})
    hf = {**DEFAULT_HAWAII_FACTORS, "interest": hi}
    return age_filers_with_components(df, year, base_year=BASE, cbo_rates=rates, hawaii_factors=hf)["income"]


def main() -> None:
    df = load()
    w = df.weight.to_numpy()
    old = load_cbo_rates("2025-01-cg2026-02").factors["interest"]
    cur = load_cbo_rates().factors["interest"]
    scen = {
        "current (2026-02 line, factor 1.0)": (None, 1.0),
        "pre-#110 line (2025-01), factor 1.0": (old, 1.0),
        "current line, factor 0.5": (None, 0.5),
        "current line, factor 0.0 (interest frozen at 2024)": (None, 0.0),
    }
    print("interest growth from 2024 (factor 1.0): current", {y: round(cur[y] / cur[BASE] - 1, 3) for y in YEARS},
          "old", {y: round(old[y] / old[BASE] - 1, 3) for y in YEARS})
    # Rent split: if a share s of INTP were net rent it would ride 'other' (CBO total income, factor 0.9), not interest.
    oth = load_cbo_rates().factors["other"]
    gr = groups(df)
    inc0 = df.income.to_numpy()
    paths = {k: {y: aged_income(df, y, f, hi).to_numpy() for y in YEARS} for k, (f, hi) in scen.items()}
    zero = age_filers_with_components(df, BASE, base_year=BASE)["income"].to_numpy()
    print("zero-growth drift of decomposition ($M):", round(((zero - inc0) * w).sum() / 1e6, 2))
    keys = list(scen)
    with OUT.open("w", newline="") as fh:
        wr = csv.writer(fh)
        head = ["group", "units", "income_M", "intp_M", "intp_share_of_income_pct", "share_units_with_intp_pct"]
        for y in YEARS:
            head += [f"{y}_current_vs_old_M", f"{y}_current_vs_old_pct_of_group_income",
                     f"{y}_current_vs_factor0.5_M", f"{y}_current_vs_frozen_M"]
        wr.writerow(head)
        for name, m in gr.items():
            inc_M = (inc0 * w)[m].sum() / 1e6
            intp_M = (df.intp.to_numpy() * w)[m].sum() / 1e6
            row = [name, round(w[m].sum()), round(inc_M, 1), round(intp_M, 1),
                   round(100 * intp_M / inc_M, 2) if inc_M else "",
                   round(100 * w[m & (df.intp.to_numpy() > 0)].sum() / w[m].sum(), 1) if w[m].sum() else ""]
            for y in YEARS:
                a = {k: (paths[k][y] * w)[m].sum() / 1e6 for k in keys}
                d_old = a[keys[0]] - a[keys[1]]
                row += [round(d_old, 1), round(100 * d_old / inc_M, 2) if inc_M else "",
                        round(a[keys[0]] - a[keys[2]], 1), round(a[keys[0]] - a[keys[3]], 1)]
            wr.writerow(row)
        # Rent-split counterfactual rows (assumed rent shares of INTP; not measured)
        wr.writerow([])
        wr.writerow(["rent split: assumed rent share of INTP", "year", "agg_income_M_change_vs_current",
                     "SPM0.5-1.5_income_M_change_vs_current"])
        band = gr["SPM ratio 0.5-1.5"]
        for share in (0.15, 0.25, 0.40):
            for y in YEARS:
                f_int = cur[y] / cur[BASE]
                f_oth = 1 + 0.9 * (oth[y] / oth[BASE] - 1)
                delta = df.intp.to_numpy() * share * (f_oth - f_int)
                wr.writerow([share, y, round((delta * w).sum() / 1e6, 1), round((delta * w)[band].sum() / 1e6, 1)])
    print(OUT.relative_to(REPO))


if __name__ == "__main__":
    main()
