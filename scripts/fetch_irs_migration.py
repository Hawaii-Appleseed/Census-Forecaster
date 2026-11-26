#!/usr/bin/env python3
"""Hawaiʻi rows of the IRS SOI gross migration files by size of AGI (``<yy><yy>inmigall``), 2015-16 onward.

Each workbook covers a pair of filing years and gives, by state and AGI class, the returns, individuals and AGI of
non-migrants, out-migrants, in-migrants and same-state movers. AGI is reported for both years, so out-migrants'
AGI in year 1 is what they reported while still filing from Hawaiʻi. The workbooks go to
``data/tax_modeler/external/irs_migration/`` (gitignored); the Hawaiʻi rows to a tidy, tracked CSV:

    data/tax_modeler/irs_soi/migration_hawaii_by_agi.csv
    columns: year1, year2, flow (nonmigrant|outflow|inflow|same_state), agi_class, returns, individuals,
             agi_year1_k, agi_year2_k   (AGI in thousands of nominal dollars)

The pre-2020 workbooks are .xls, which pandas reads only with xlrd (not a project dependency):

    uv run --with xlrd python scripts/fetch_irs_migration.py
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "tax_modeler" / "external" / "irs_migration"
OUT = REPO / "data" / "tax_modeler" / "irs_soi" / "migration_hawaii_by_agi.csv"
BASE = "https://www.irs.gov/pub/irs-soi/"
YEARS = ["1516", "1617", "1718", "1819", "1920", "2021", "2122", "2223", "2324"]
FLOWS = {"Non-migrant": "nonmigrant", "Outflow": "outflow", "Inflow": "inflow", "Same State": "same_state"}


def _name(pair: str) -> str:
    return f"{pair}inmigall.{'xls' if int(pair[:2]) < 20 else 'xlsx'}"


def download(pair: str) -> Path:
    path = RAW / _name(pair)
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(BASE + path.name, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            path.write_bytes(resp.read())
    return path


def hawaii_rows(path: Path, pair: str) -> pd.DataFrame:
    """Tidy Hawaiʻi rows: each flow's block starts at its header cell (row 3); its all-ages columns come first."""
    sheet = pd.read_excel(path, header=None)
    starts = {}
    for col, cell in sheet.iloc[3].items():
        for label, flow in FLOWS.items():
            if isinstance(cell, str) and cell.strip().lower().startswith(label.lower()):
                starts[flow] = col
    hi = sheet[sheet.iloc[:, 1].astype(str).str.strip() == "HI"]
    rows = []
    for _, r in hi.iterrows():
        agi_class = "All" if str(r.iloc[2]).strip() == "Hawaii" else str(r.iloc[2]).strip()
        for flow, c in starts.items():
            rows.append({"year1": 2000 + int(pair[:2]), "year2": 2000 + int(pair[2:]), "flow": flow,
                         "agi_class": agi_class, "returns": int(r.iloc[c]), "individuals": int(r.iloc[c + 1]),
                         "agi_year1_k": float(r.iloc[c + 2]), "agi_year2_k": float(r.iloc[c + 3])})
    if len(rows) != 8 * len(FLOWS):
        raise ValueError(f"{path.name}: expected 8 Hawaiʻi rows x {len(FLOWS)} flows, got {len(rows)}")
    return pd.DataFrame(rows)


def main() -> None:
    tidy = pd.concat([hawaii_rows(download(p), p) for p in YEARS], ignore_index=True)
    tidy.to_csv(OUT, index=False)
    print(f"wrote {len(tidy)} rows to {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
