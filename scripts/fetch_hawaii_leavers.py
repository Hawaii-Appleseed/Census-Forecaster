#!/usr/bin/env python3
"""Pull the households of people who left Hawaiʻi from the national ACS 5-year PUMS.

A "leaver" is a person living outside Hawaiʻi whose residence one year ago (``MIGSP``) was Hawaiʻi. Their records
sit in the other states' files, so this reads the national person and household zips (``csv_pus.zip``,
``csv_hus.zip``) straight from the archive without extracting them, and keeps every person and household record
of a household with at least one leaver (household mates are needed to build tax units).

Pass 1 reads three columns of the person file to find those households; pass 2 keeps all columns of their
person and household rows. Output, beside the zips, in the layout of the state files:

    psam_p_leavers.csv   psam_h_leavers.csv

    uv run python scripts/fetch_hawaii_leavers.py [--zip-dir packages/data/raw/pums_2020_2024_us]

The zips come from https://www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year/ (about 3.2 GB).
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
ZIP_DIR = REPO / "packages" / "data" / "raw" / "pums_2020_2024_us"
HAWAII = 15
CHUNK = 100_000  # rows; all ~290 columns of a chunk stay under ~250 MB on the 8 GB mini


def _members(zf: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in zf.namelist() if n.endswith(".csv"))


def leaver_households(pus: Path) -> set[str]:
    """SERIALNOs of households outside Hawaiʻi with a member who lived in Hawaiʻi a year ago."""
    serials: set[str] = set()
    with zipfile.ZipFile(pus) as zf:
        for name in _members(zf):
            with zf.open(name) as fh:
                header = pd.read_csv(fh, nrows=0).columns
            state = "STATE" if "STATE" in header else "ST"
            with zf.open(name) as fh:
                for chunk in pd.read_csv(fh, usecols=["SERIALNO", state, "MIGSP"], dtype={"SERIALNO": str},
                                         chunksize=CHUNK):
                    hit = (chunk["MIGSP"] == HAWAII) & (chunk[state] != HAWAII)
                    serials.update(chunk.loc[hit, "SERIALNO"])
            print(f"  {name}: {len(serials):,} households so far")
    return serials


def keep_rows(archive: Path, serials: set[str], out: Path) -> int:
    """Write every row of ``archive`` whose SERIALNO is in ``serials`` to ``out``; return the row count."""
    kept = []
    with zipfile.ZipFile(archive) as zf:
        for name in _members(zf):
            with zf.open(name) as fh:
                for chunk in pd.read_csv(fh, dtype={"SERIALNO": str}, chunksize=CHUNK, low_memory=False):
                    kept.append(chunk[chunk["SERIALNO"].isin(serials)])
    rows = pd.concat(kept, ignore_index=True)
    rows.to_csv(out, index=False)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip-dir", type=Path, default=ZIP_DIR)
    args = ap.parse_args()

    print("pass 1: finding leaver households")
    serials = leaver_households(args.zip_dir / "csv_pus.zip")
    print(f"{len(serials):,} households")
    print("pass 2: keeping their records")
    n_p = keep_rows(args.zip_dir / "csv_pus.zip", serials, args.zip_dir / "psam_p_leavers.csv")
    n_h = keep_rows(args.zip_dir / "csv_hus.zip", serials, args.zip_dir / "psam_h_leavers.csv")
    print(f"wrote {n_p:,} person and {n_h:,} household records to {args.zip_dir}")


if __name__ == "__main__":
    main()
