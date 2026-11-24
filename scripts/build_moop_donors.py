#!/usr/bin/env python3
"""Build the MOOP donors: Hawaii SPM units from Census's ACS SPM research extracts.

    .venv/bin/python scripts/build_moop_donors.py [--cache DIR]

The Census Bureau publishes, for each ACS year, a person-level extract with its
Supplemental Poverty Measure built on the ACS: resources, thresholds and the
expenses the ACS does not ask about, medical out-of-pocket spending (MOOP)
among them, imputed by Census from the CPS ASEC (``spm-asc-readme.pdf`` in the
same directory). This script reads Hawaii's records (``st == 15``) from the
2021-2023 extracts, rolls them up to SPM units and writes
``packages/tax_modeler/src/tax_modeler/data/cps_donor/acs_spm_hawaii_moop_donors.csv``:
one row per Hawaii SPM unit with its year, weight (the first member's person
weight), size, members 65 and over, money income (``SPM_Totval``) and MOOP
(``SPM_MedXpns``), in that year's dollars. ``benefits.moop`` matches the model's
SPM units to them.

Each extract is about 1.1 GB. ``--cache`` keeps Hawaii's rows of each one
(``hi_<year>.parquet``) so a rerun does not download it again; the full file is
deleted once its rows are read. 2020 has no extract (the pandemic year's ACS).
"""
from __future__ import annotations

import argparse
import tempfile
import urllib.request
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
BASE_URL = "https://www2.census.gov/programs-surveys/supplemental-poverty-measure/datasets/spm"
YEARS = (2021, 2022, 2023)
HAWAII = 15
OUT = REPO / "packages/tax_modeler/src/tax_modeler/data/cps_donor/acs_spm_hawaii_moop_donors.csv"
COLUMNS = ["SPM_ID", "sporder", "wt", "Age", "SPM_Totval", "SPM_MedXpns", "FILEDATE"]


def hawaii_rows(year: int, cache: Path) -> pd.DataFrame:
    """Hawaii's person records of the ``year`` extract, from ``cache`` or downloaded."""
    kept = cache / f"hi_{year}.parquet"
    if kept.exists():
        return pd.read_parquet(kept)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        dta = Path(tmp) / f"spm_{year}_pu.dta"
        req = urllib.request.Request(f"{BASE_URL}/spm_{year}_pu.dta", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r, open(dta, "wb") as f:
            while chunk := r.read(1 << 22):
                f.write(chunk)
        parts = [ch.loc[ch["st"] == HAWAII] for ch in pd.read_stata(dta, chunksize=200_000, convert_categoricals=False)]
    rows = pd.concat(parts)
    rows.to_parquet(kept)
    return rows


def spm_units(rows: pd.DataFrame, year: int) -> pd.DataFrame:
    rows = rows.sort_values(["SPM_ID", "sporder"])
    g = rows.groupby("SPM_ID", sort=True)
    return pd.DataFrame({
        "year": year,
        "weight": g["wt"].first(),
        "n_persons": g.size(),
        "n_65": g["Age"].apply(lambda a: int((a >= 65).sum())),
        "money_income": g["SPM_Totval"].first().round(0),
        "moop": g["SPM_MedXpns"].first().round(0),
        "filedate": g["FILEDATE"].first().astype("int64"),
    }).reset_index(drop=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=REPO / "data" / "artifacts" / "acs_spm_hawaii",
                    help="where Hawaii's rows of each extract are kept between runs")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    donors = pd.concat([spm_units(hawaii_rows(y, args.cache)[COLUMNS], y) for y in YEARS], ignore_index=True)
    donors.to_csv(OUT, index=False)
    for y, d in donors.groupby("year"):
        print(f"{y}: {len(d):,} SPM units, {d['weight'].sum():,.0f} weighted, "
              f"mean MOOP ${(d['moop'] * d['weight']).sum() / d['weight'].sum():,.0f}")
    print(f"-> {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
