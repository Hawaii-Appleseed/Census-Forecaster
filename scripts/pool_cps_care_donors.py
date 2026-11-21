"""Pool Hawaii CPS ASEC years into the donor families the care credit's expense model draws on.

    python scripts/build_cps_asec_slice.py --year 2024 --local-csv asecpub24csv.zip --out /tmp/asec/hi_2024.parquet
    ... (one per ASEC year)
    python scripts/pool_cps_care_donors.py 2022=/tmp/asec/hi_2022.parquet 2023=... 2024=... 2025=... --zip-dir /tmp/asec

Each ASEC file (March of year Y) reports income and childcare spending for calendar year Y-1. A donor is a
household (``PH_SEQ``) with at least one person under 13, as in :func:`tax_modeler.credits.hi_cdcc.donor_expense_distribution`:
its childcare spending (``SPM_CHILDCAREXPNS``, a family aggregate, taken from the household's first person), the
number of its people under 13, its weight and its household money income. Dollars are put in calendar-2023
dollars with the CPI-U (U.S. city average, all items, not seasonally adjusted, annual average; BLS series CUUR0000SA0,
read from FRED's CPIAUCNS monthly values); weights are scaled to sum to one within each year so that every year
counts equally. ASEC 2021 (calendar 2020, pandemic childcare use) is left out. Hawaii only: 4 years give a few hundred
families with a child under 13 and about a hundred that pay.

Writes ``cps_asec_hawaii_care_families.parquet`` and ``cps_care_donors.json`` (provenance: years, CPI, counts, SHA-256 of
each zip when ``--zip-dir`` is given) beside the single-year slice in ``data/cps_donor``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "cps_donor"
#: CPI-U annual averages by calendar year (1982-84=100), the mean of the twelve monthly values.
CPI_U = {2021: 270.970, 2022: 292.655, 2023: 304.702, 2024: 313.689}
BASE_YEAR = 2023


def families(slice_path: Path, asec_year: int) -> pd.DataFrame:
    raw = pd.read_parquet(slice_path)
    cy = asec_year - 1
    deflator = CPI_U[BASE_YEAR] / CPI_U[cy]
    g = raw.groupby("PH_SEQ")
    fam = pd.DataFrame({
        "expense": g["childcare_expense"].first().astype(float) * deflator,
        "weight": g["weight"].first().astype(float),
        "n13": g["age"].apply(lambda a: int((a < 13).sum())),
        "income": g["income"].sum().astype(float) * deflator,
    })
    fam = fam[fam["n13"] > 0].reset_index(drop=True)
    fam["weight"] = fam["weight"] / fam["weight"].sum()
    fam.insert(0, "asec_year", asec_year)
    return fam


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slices", nargs="+", help="ASEC_YEAR=PATH to a single-year slice from build_cps_asec_slice.py")
    ap.add_argument("--zip-dir", type=Path, default=None, help="directory holding asecpub<yy>csv.zip, for the SHA-256s")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    frames, meta = [], {}
    for spec in args.slices:
        year, _, path = spec.partition("=")
        year = int(year)
        f = families(Path(path).expanduser(), year)
        frames.append(f)
        meta[str(year)] = {"calendar_year": year - 1, "cpi_u": CPI_U[year - 1],
                           "families_with_child_under_13": int(len(f)), "families_paying": int((f["expense"] > 0).sum())}
        if args.zip_dir:
            z = args.zip_dir / f"asecpub{year % 100:02d}csv.zip"
            meta[str(year)]["zip"] = z.name
            meta[str(year)]["sha256"] = hashlib.sha256(z.read_bytes()).hexdigest()
            meta[str(year)]["url"] = f"https://www2.census.gov/programs-surveys/cps/datasets/{year}/march/{z.name}"
    pooled = pd.concat(frames, ignore_index=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pooled.to_parquet(args.out_dir / "cps_asec_hawaii_care_families.parquet", index=False)
    summary = {"dollar_year": BASE_YEAR, "cpi_u_annual_average": CPI_U, "years": meta,
               "families": int(len(pooled)), "families_paying": int((pooled["expense"] > 0).sum()),
               "note": "weights sum to one within each ASEC year; expense, income in calendar-2023 dollars (CPI-U)"}
    (args.out_dir / "cps_care_donors.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"{len(pooled)} families with a child under 13, {summary['families_paying']} paying; "
          + "; ".join(f"{y}: {m['families_with_child_under_13']}/{m['families_paying']}" for y, m in meta.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
