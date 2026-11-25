"""Pull the Low-Income Household Renters credit line from DOTAX 'Tax Credits Claimed', TY2018-2023.

Writes packages/tax_modeler/src/tax_modeler/data/raw/dotax_renters_credit_claims.csv
(one row per year x income class; ALL row included). Dollars are $1,000 (Table A-5),
claims are counts (A-6). Suppressed cells ('d') are blank with suppressed=True; '-' and '.'
(no claims) are recorded as 0 only in the classes above $30K where DOTAX prints them.
Same URLs as scripts/fetch_dotax_credit_claims.py. Exploratory: not consumed by the model.
"""
from __future__ import annotations

import csv
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = (REPO_ROOT / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw"
           / "dotax_renters_credit_claims.csv")
DL_DIR = Path("/tmp/dotax_credits")
URLS = (
    "https://files.hawaii.gov/tax/stats/stats/credits/archive/{year}credit_tables.xlsx",
    "https://files.hawaii.gov/tax/stats/stats/credits/{year}credit_tables.xlsx",
)
YEARS = range(2018, 2024)
CLASSES = ["ALL", "<$10K", "$10K-$30K", "$30K-$60K", "$60K-$100K", "$100K-$200K", "$200K+"]
NEEDLE = "Low-Income Household Renters"


def _download(year: int) -> Path:
    DL_DIR.mkdir(parents=True, exist_ok=True)
    dest = DL_DIR / f"{year}.xlsx"
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    for fmt in URLS:
        try:
            urllib.request.urlretrieve(fmt.format(year=year), dest)
            return dest
        except urllib.error.HTTPError:
            continue
    raise FileNotFoundError(f"TY{year} credit tables not found")


def _cell(v):
    s = "" if v is None else str(v).strip()
    try:
        return float(s), ""
    except ValueError:
        return None, s          # 'd', '-', '.', 'na'


def _row(ws):
    for r in ws.iter_rows(values_only=True):
        if r and r[0] and NEEDLE in str(r[0]):
            return r
    raise LookupError(f"no renters row in {ws.title}")


def main() -> int:
    import openpyxl
    rows = []
    for y in YEARS:
        wb = openpyxl.load_workbook(_download(y), read_only=True, data_only=True)
        amt, cnt = _row(wb["A-5"]), _row(wb["A-6"])
        for i, cls in enumerate(CLASSES):
            a, araw = _cell(amt[1 + i])
            c, craw = _cell(cnt[1 + i])
            rows.append({"year": y, "income_class": cls, "claims": c, "amount_$K": a,
                         "raw_claims": craw, "raw_amount": araw,
                         "suppressed": "d" in (araw, craw)})
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {OUT_CSV.relative_to(REPO_ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
