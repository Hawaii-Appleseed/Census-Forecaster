"""Download and parse DOTAX 'Tax Credits Claimed' XLSX files for TY2018–2022.

Outputs a normalized CSV at packages/tax_modeler/src/tax_modeler/data/raw/
dotax_reec_historical.csv consumed by sb3125_cd1_credits.py to replace the
3%/yr synthetic back-cast.

Public URL pattern (verified May 14, 2026):
    https://files.hawaii.gov/tax/stats/stats/credits/archive/<YEAR>credit_tables.xlsx

Sheets parsed:
    A-1 — DOLLAR AMOUNTS OF TAX CREDITS CLAIMED BY TYPE OF CREDIT AND
          TYPE OF TAXPAYER (in $1,000)
    A-5 — DOLLAR AMOUNTS OF TAX CREDITS CLAIMED BY INDIVIDUALS BY TYPE
          AND INCOME CLASS (in $1,000)

The REEC row title varies slightly across years:
    TY2018, TY2022:           'Renewable Energy Technologies Tax Credit'
    TY2019, TY2020, TY2021:   'Renewable Energy Technologies Income Tax Credit'

Suppressed cells appear as 'd' (disclosure-protected) — we record 0 and flag
the year as having suppressed data. The whole-table 'ALL' total in column 1
of A-1 includes the suppressed cells, so `all_total - ind_total - corp_total -
fid_total` gives an upper bound on the suppressed financial-corp / exempt-org
pool.
"""
from __future__ import annotations

import csv
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = REPO_ROOT / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "dotax_reec_historical.csv"
DL_DIR = Path("/tmp/dotax_credits")

URL_FMT = "https://files.hawaii.gov/tax/stats/stats/credits/archive/{year}credit_tables.xlsx"
YEARS = [2018, 2019, 2020, 2021, 2022]

# A-5 AGI bin labels (same across years 2018-2022)
AGI_BIN_LABELS = ["<$10K", "$10K-$30K", "$30K-$60K", "$60K-$100K", "$100K-$200K", "$200K+"]


def _download(year: int) -> Path:
    """Download a single year file into DL_DIR (cached)."""
    DL_DIR.mkdir(parents=True, exist_ok=True)
    dest = DL_DIR / f"{year}.xlsx"
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    url = URL_FMT.format(year=year)
    print(f"Downloading {url}...")
    urllib.request.urlretrieve(url, dest)
    return dest


def _to_float(cell) -> tuple[float, bool]:
    """Parse a DOTAX cell. Returns (value, was_suppressed).

    'd' / 'na' / '-' / blank → (0.0, True). The suppressed flag rolls up to
    a per-year data-quality marker.
    """
    if cell is None:
        return 0.0, True
    s = str(cell).strip()
    if not s or s in {"d", "na", "n.a.", "-"} or s.startswith("d"):
        return 0.0, True
    if s in {".", "*"}:
        return 0.0, True
    try:
        return float(s), False
    except (TypeError, ValueError):
        return 0.0, True


def _find_reec_row(ws):
    """Locate the REEC row in an A-1 or A-5 sheet by title prefix match."""
    for row in ws.iter_rows(values_only=True):
        first = row[0]
        if first and "enewable Energy Technologies" in str(first):
            return row
    raise LookupError(f"No REEC row found in {ws.title}")


def parse_year(year: int) -> dict:
    """Parse one DOTAX year's REEC data. Values returned in DOLLARS (not $K).

    Returns dict with keys:
        year, all_total, individual_total, corporate_total, financial_corp,
        fiduciaries, exempt_orgs, individual_<bin>, suppressed (bool flag).
    """
    import openpyxl  # imported here so unit tests can run without it

    path = _download(year)
    wb = openpyxl.load_workbook(path, data_only=True)

    # A-1: dollar amounts by taxpayer type. Header at row 5, REEC at row 20
    # (varies, so we search). Columns: ALL, Individuals, Corporations,
    # Financial Corporations, Fiduciaries, Exempt Organizations, Insurance.
    a1 = wb["A-1"]
    row = _find_reec_row(a1)
    suppressed = False
    all_total, s = _to_float(row[1]); suppressed |= s
    ind_total, s = _to_float(row[2]); suppressed |= s
    corp_total, s = _to_float(row[3]); suppressed |= s
    fin_corp, s = _to_float(row[4]); suppressed |= s
    fid_total, s = _to_float(row[5]); suppressed |= s
    exempt, s = _to_float(row[6]); suppressed |= s

    # A-5: individual dollar amounts by AGI bin
    a5 = wb["A-5"]
    row5 = _find_reec_row(a5)
    # Column 1 should be ALL (= ind_total from A-1 modulo rounding), then 6 AGI bins
    bins = []
    for col in range(2, 8):  # cols 2-7 inclusive
        v, s = _to_float(row5[col]); suppressed |= s
        bins.append(v)

    # DOTAX figures are in $1,000s -> convert to $M (millions)
    scale = 1e-3  # $K → $M

    return {
        "year": year,
        "all_total_$M":        round(all_total * scale, 3),
        "individual_total_$M": round(ind_total * scale, 3),
        "corporate_total_$M":  round(corp_total * scale, 3),
        "financial_corp_$M":   round(fin_corp * scale, 3),
        "fiduciaries_$M":      round(fid_total * scale, 3),
        "exempt_orgs_$M":      round(exempt * scale, 3),
        **{f"individual_{label}_$M": round(b * scale, 3) for label, b in zip(AGI_BIN_LABELS, bins, strict=False)},
        "suppressed_cells":    suppressed,
    }


def main() -> int:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for y in YEARS:
        try:
            rows.append(parse_year(y))
            print(f"TY{y}: ind=${rows[-1]['individual_total_$M']:.2f}M  "
                  f"corp=${rows[-1]['corporate_total_$M']:.2f}M  "
                  f"all=${rows[-1]['all_total_$M']:.2f}M  "
                  f"suppressed={rows[-1]['suppressed_cells']}")
        except Exception as e:
            print(f"TY{y} FAILED: {e}", file=sys.stderr)

    if not rows:
        print("No data parsed.", file=sys.stderr)
        return 1

    fieldnames = list(rows[0].keys())
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {OUT_CSV} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
