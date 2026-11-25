"""CBO's interest-income path against its own interest-rate path (check, changes nothing the model reads).

    python scripts/diag_interest_rates_check.py [CBO_DOWNLOADS_DIR]

Reads CBO's Economic Projections workbook (2. Calendar Year: 3-month bill, 10-year note,
personal interest and dividend income, nominal GDP, CPI-U) and Revenue workbook (sheet 3: the
tax-return line the model ages interest on), plus the SOI US series written by
diag_interest_soi_history.py. Writes data/tax_modeler/cbo/interest_rates_check_2022_2030.csv.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent.parent
DL = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "runs" / "cbo_downloads"
SOI = REPO / "data" / "tax_modeler" / "cbo" / "interest_soi_history_hi_us.csv"
OUT = REPO / "data" / "tax_modeler" / "cbo" / "interest_rates_check_2022_2030.csv"


def econ() -> dict[str, dict[int, float]]:
    wb = openpyxl.load_workbook(DL / "51135-2026-02-Economic-Projections.xlsx", data_only=True, read_only=True)
    rows = list(wb["2. Calendar Year"].iter_rows(values_only=True))
    years = [int(y) for y in rows[6][2:16]]
    want = {"10-Year Treasury note": "t10", "3-Month Treasury bill": "t3m", "Interest income, personal": "nipa_int",
            "Dividend income, personal": "nipa_div", "Gross domestic product (GDP)": "gdp",
            "Consumer price index, all urban consumers (CPI-U)": "cpi"}
    out: dict[str, dict[int, float]] = {}
    for r in rows:
        k = want.get(str(r[0] or "").strip())
        if k and k not in out:
            out[k] = dict(zip(years, (float(v) for v in r[2:16]), strict=True))
    return out


def cbo_line() -> dict[int, float]:
    wb = openpyxl.load_workbook(DL / "51138-2026-02-Revenue.xlsx", data_only=True, read_only=True)
    rows = list(wb["3.Individual Income Tax Details"].iter_rows(values_only=True))
    hdr = next(r for r in rows if r[0] == "Calendar year")
    cols = [(i, int(v)) for i, v in enumerate(hdr) if isinstance(v, (int, float))]
    row = next(r for r in rows if str(r[0]).startswith("Taxable interest and ordinary"))
    return {y: float(row[i]) for i, y in cols}


def main() -> None:
    e, c = econ(), cbo_line()
    soi = {}
    with SOI.open() as fh:
        for r in csv.DictReader(fh):
            if r["geo"] == "US":
                soi[int(r["year"])] = float(r["int_plus_nonqual_div_k"]) / 1e6
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "cbo_taxreturn_int_nonqual_div_B", "soi_us_int_nonqual_div_B", "nipa_personal_interest_B",
                    "nipa_personal_dividend_B", "t3m_pct", "t10_pct", "avg_rate_pct", "implied_asset_base_B",
                    "nominal_gdp_B", "cpi_u"])
        for y in range(2022, 2031):
            ni = e["nipa_int"].get(y)
            ar = (e["t3m"][y] + e["t10"][y]) / 2 if y in e["t3m"] else None
            w.writerow([y, c[y], round(soi[y], 1) if y in soi else "", ni or "", e["nipa_div"].get(y, ""),
                        e["t3m"].get(y, ""), e["t10"].get(y, ""), round(ar, 3) if ar else "",
                        round(ni / ar * 100, 0) if ni and ar else "", e["gdp"].get(y, ""), e["cpi"].get(y, "")])
    print(OUT.relative_to(REPO))


if __name__ == "__main__":
    main()
