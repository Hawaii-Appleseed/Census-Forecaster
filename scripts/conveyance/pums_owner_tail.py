"""Owner-occupied home values above $2 million, by county group, from ACS PUMS.

Usage:
  python scripts/conveyance/pums_owner_tail.py

Reads the 2020-2024 five-year housing file (packages/data/raw/pums_2020_2024/
psam_h15.csv, gitignored; from https://www2.census.gov/programs-surveys/acs/
data/pums/2024/5-Year/csv_hhi.zip) and writes
packages/tax_modeler/src/tax_modeler/data/raw/conveyance/
pums_owner_value_tail_2020_2024.csv, which forecast_conveyance_sb3028.py reads.

PUMS has no county field. Hawaii's 2020 PUMAs nest in counties except that
Maui, Kalawao and Kauai share one (Census 2020 tract-to-PUMA relationship
file): 00100 Maui + Kalawao + Kauai, 00200 Hawaii County, 00301-00308
Honolulu. Values are owner-reported (VALP), adjusted to 2024 dollars with
ADJHSG; the forecast uses them only as ratios between areas.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "packages" / "data" / "raw" / "pums_2020_2024" / "psam_h15.csv"
OUT = (REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
       / "pums_owner_value_tail_2020_2024.csv")
AREA = {100: "Maui+Kauai", 200: "Hawaii", **{p: "Honolulu" for p in range(301, 309)}}


def main() -> None:
    h = pd.read_csv(SRC, usecols=["PUMA", "ADJHSG", "WGTP", "TEN", "VALP"])
    own = h[h["TEN"].isin([1, 2]) & h["VALP"].notna()].copy()      # owned with or without a mortgage
    own["value"] = own["VALP"] * own["ADJHSG"] / 1e6
    own["area"] = own["PUMA"].map(AREA)
    rows = []
    for area, g in own.groupby("area"):
        v, w = g["value"].to_numpy(), g["WGTP"].to_numpy(float)
        rows.append({"area": area, "owner_units": round(w.sum()),
                     "units_ge_2m": round(w[v >= 2e6].sum()), "sample_ge_2m": int((v >= 2e6).sum()),
                     "excess_over_2m": round(float((np.clip(v - 2e6, 0, None) * w).sum()))})
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
