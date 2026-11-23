#!/usr/bin/env python3
"""Hawaii's rows of the SNAP Quality Control public-use file, as two small CSVs the repo can track.

The QC file is USDA's monthly audit sample of active SNAP cases (Mathematica builds the public-use version,
https://snapqcdata.net/datafiles). Each row is one SNAP unit as the caseworker certified it: who is in it, each
member's age, disability, work registration, time-limit status, hours worked and income, and the unit's deductions
and benefit. It is the only public microdata that records SNAP units the way the program defines them, which the
ACS (households, self-reported receipt) cannot.

    .venv/bin/python scripts/fetch_snap_qc_hawaii.py              # downloads FY2024, about 6 MB
    .venv/bin/python scripts/fetch_snap_qc_hawaii.py --csv PATH   # an already-unzipped qc_pub_fy2024.csv

Writes ``packages/tax_modeler/src/tax_modeler/data/snap_qc/hawaii_qc_fy2024_{units,persons}.csv``. FY2024 is October
2023 to September 2024: after the pandemic emergency allotments ended (February 2023) and with the 2023 Fiscal
Responsibility Act's time-limit exceptions (veterans, homeless, former foster youth) already coded. FY2023 uses an
older time-limit coding and is not pooled.
"""
from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "snap_qc"
URL = "https://snapqcdata.net/sites/default/files/2026-08/qcfy2024_csv.zip"
HAWAII_FIPS = 15
MAX_PERSONS = 18

UNIT_COLS = [
    "HHLDNO", "YRMONTH", "FYWGT", "FSUSIZE", "CERTHHSZ", "CAT_ELIG", "TPOV", "FSELDER", "FSDIS",
    "FSBEN", "BENMAX", "FSMINBEN", "FSGRINC", "FSEARN", "FSUNEARN", "FSNETINC",
    "FSSTDDED", "FSERNDED", "FSMEDDED", "FSDEPDED", "FSCSDED", "FSSLTDED", "FSSLTEXP", "SHELCAP",
    "HOMELESS_DED", "FSTOTDED",
]
PERSON_STUBS = [
    "FSAFIL", "AGE", "REL", "SEX", "DIS64_", "WRKREG", "ABWDST", "EMPSTA", "EMPSTB", "EMPRG", "CTZN", "YRSED",
]
EARNED_STUBS = ["WAGES", "SLFEMP", "OTHERN"]
UNEARNED_STUBS = [
    "ALIMNY", "ANNUITY", "CONT", "CSUPRT", "DEEM", "DIVER", "EDLOAN", "ENERGY", "FOSTER", "GA", "GOVDIV",
    "GOVINTER", "GOVROY", "INTER", "OLDAGE", "OTHGOV", "OTHUN", "PENSION", "RENTINC", "RENTMINC", "SOCSEC",
    "SSI", "STRIKE", "SURV", "TANF", "TRUST", "UNEMP", "UNK", "VET", "WCOMP", "WGESUP",
]


def _read_national(csv: Path | None) -> pd.DataFrame:
    if csv is not None:
        return pd.read_csv(csv, low_memory=False)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        archive = zipfile.ZipFile(io.BytesIO(resp.read()))
    name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
    with archive.open(name) as fh:
        return pd.read_csv(fh, low_memory=False)


def extract(national: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hawaii's units and their members (one row per person position that holds a person)."""
    hi = national[national["STATE"] == HAWAII_FIPS].reset_index(drop=True)
    units = hi[UNIT_COLS].rename(columns={"HHLDNO": "unit_id"})
    if units["unit_id"].duplicated().any():
        raise SystemExit("HHLDNO is not unique among Hawaii units")
    frames = []
    for i in range(1, MAX_PERSONS + 1):
        if f"AGE{i}" not in hi.columns:
            continue
        person = pd.DataFrame({"unit_id": hi["HHLDNO"], "position": i})
        for stub in PERSON_STUBS:
            person[stub.rstrip("_")] = hi[f"{stub}{i}"]
        person["earned"] = sum(hi[f"{s}{i}"].fillna(0) for s in EARNED_STUBS)
        person["unearned"] = sum(hi[f"{s}{i}"].fillna(0) for s in UNEARNED_STUBS)
        frames.append(person[person["FSAFIL"].notna()])
    persons = pd.concat(frames, ignore_index=True).sort_values(["unit_id", "position"])
    return units, persons.reset_index(drop=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", type=Path, default=None, help="unzipped qc_pub_fy2024.csv (skips the download)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    units, persons = extract(_read_national(args.csv))
    args.out.mkdir(parents=True, exist_ok=True)
    units.to_csv(args.out / "hawaii_qc_fy2024_units.csv", index=False)
    persons.to_csv(args.out / "hawaii_qc_fy2024_persons.csv", index=False)
    members = persons[persons["FSAFIL"] == 1].merge(units[["unit_id", "FYWGT"]], on="unit_id")
    print(f"{len(units)} units, {len(persons)} person rows -> {args.out}")
    print(f"weighted: {units['FYWGT'].sum():,.0f} units, {members['FYWGT'].sum():,.0f} members, "
          f"${(units['FYWGT'] * units['FSBEN']).sum() * 12 / 1e6:,.1f}M a year")


if __name__ == "__main__":
    main()
