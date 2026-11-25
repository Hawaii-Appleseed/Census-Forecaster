"""Hawaii vs US taxable interest and dividends from IRS SOI Historic Table 2 (TY2018-TY2023).

Check script for docs/data_source_tests/interest-growth-check.md; changes nothing the model reads.

    python scripts/diag_interest_soi_history.py SOI_DIR [--download]

SOI_DIR holds {YY}in12hi.xlsx (Hawaii) and {YY}in54us.xlsx (United States). With --download the
files are fetched from https://www.irs.gov/pub/irs-soi/ (use a new, empty directory).
Writes data/tax_modeler/cbo/interest_soi_history_hi_us.csv. Money in $ thousands in the sources.
"""
from __future__ import annotations

import argparse
import csv
import urllib.request
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "tax_modeler" / "cbo" / "interest_soi_history_hi_us.csv"
OUT_G = REPO / "data" / "tax_modeler" / "cbo" / "interest_soi_growth_hi_vs_us.csv"
SERIES = ("interest_k", "ord_div_k", "qual_div_k", "int_plus_nonqual_div_k", "rent_royalty_k", "agi_k")
YEARS = range(2018, 2024)
ROWS = {
    "interest": "taxable interest:",
    "ord_div": "ordinary dividends:",
    "qual_div": "qualified dividends:",
    "rent_royalty": "total rental and royalty net income",
    "agi": "adjusted gross income (agi)",
}


def read(path: Path) -> dict[str, float]:
    """All-returns amounts ($K) by item; amount sits on the row after a 'Number' label."""
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(values_only=True)]
    out: dict[str, float] = {}
    for i, r in enumerate(rows):
        lab = " ".join(str(r[0] or "").lower().split())
        for key, pat in ROWS.items():
            if key in out or not lab.startswith(pat):
                continue
            if key == "agi":
                out[key] = float(r[1])
            elif str(rows[i + 1][0] or "").strip().lower() == "amount":
                out[key] = float(rows[i + 1][1])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("soi_dir", type=Path)
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    a.soi_dir.mkdir(parents=True, exist_ok=True)
    data: dict[tuple[str, int], dict[str, float]] = {}
    for y in YEARS:
        for geo, code in (("HI", "12hi"), ("US", "54us")):
            f = a.soi_dir / f"{y % 100}in{code}.xlsx"
            if a.download and not f.exists():
                urllib.request.urlretrieve(f"https://www.irs.gov/pub/irs-soi/{f.name}", f)
            data[(geo, y)] = read(f)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["geo", "year", "interest_k", "ord_div_k", "qual_div_k",
                    "int_plus_nonqual_div_k", "rent_royalty_k", "agi_k"])
        for (geo, y), d in sorted(data.items()):
            nq = d["interest"] + d["ord_div"] - d["qual_div"]
            w.writerow([geo, y, d["interest"], d["ord_div"], d["qual_div"], nq,
                        d.get("rent_royalty", ""), d["agi"]])
    write_growth()
    print(f"wrote {OUT.relative_to(REPO)} and {OUT_G.relative_to(REPO)}")


def write_growth() -> None:
    """Growth since the prior year and since 2018 for HI and US, and the ratio of the HI to US increments."""
    tab: dict[tuple[str, int], dict[str, float]] = {}
    with OUT.open() as fh:
        for r in csv.DictReader(fh):
            tab[(r["geo"], int(r["year"]))] = {k: float(r[k]) for k in SERIES if r[k]}
    with OUT_G.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "year", "hi_yoy_pct", "us_yoy_pct", "yoy_ratio_hi_to_us", "hi_since_2018_pct",
                    "us_since_2018_pct", "cum_ratio_hi_to_us"])
        for k in SERIES:
            for y in range(2019, 2024):
                if k not in tab[("HI", y)] or k not in tab[("HI", y - 1)]:
                    continue
                g = {geo: tab[(geo, y)][k] / tab[(geo, y - 1)][k] - 1 for geo in ("HI", "US")}
                row = [k, y, round(g["HI"] * 100, 1), round(g["US"] * 100, 1),
                       round(g["HI"] / g["US"], 2) if abs(g["US"]) > 0.02 else ""]
                if k in tab[("HI", 2018)]:
                    c = {geo: tab[(geo, y)][k] / tab[(geo, 2018)][k] - 1 for geo in ("HI", "US")}
                    row += [round(c["HI"] * 100, 1), round(c["US"] * 100, 1),
                            round(c["HI"] / c["US"], 2) if abs(c["US"]) > 0.02 else ""]
                else:
                    row += ["", "", ""]
                w.writerow(row)


if __name__ == "__main__":
    main()
