#!/usr/bin/env python3
"""Hawaii EITC history: federal (IRS SOI Table 2, by state) and state (DOTAX credit tables), with sources.

Analysis only: reads official workbooks, writes one small CSV, changes no model behaviour.

    uv run --with xlrd python analyses/hi_eitc_history.py fetch  --dir /tmp/hi_eitc_src
    uv run --with xlrd python analyses/hi_eitc_history.py build  --dir /tmp/hi_eitc_src \
        --out docs/data_source_tests/tax-unit-claimant-assignment_hi_eitc_history.csv

``fetch`` downloads (urllib, one request per file, no retries; a 403/429 is recorded and that route stops):
  * IRS SOI Historic Table 2, Hawaii workbook per tax year, TY2010-TY2023 (``{yy}in12hi.xls[x]``). The files named
    13in12hi and 16in12hi are, as published, Table 11/12 (a different table), so TY2013 and TY2016 are missing.
  * DOTAX "Tax Credits Claimed" Tables A-1/A-2, TY2018-TY2023.
``build`` reads them and the bundled TY2024 DOTAX EITC report (``dotax_eitc_2024.json``) and writes the long CSV
(year, series, value, unit, source). Federal maximum credits by number of qualifying children are transcribed from
the IRS Revenue Procedures cited in ``FED_MAX_CREDIT`` (parsed from the PDFs on 2026-11-25; TY2018 from IRB 2018-10).
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLED_2024 = REPO / "packages/tax_modeler/src/tax_modeler/data/calibration/dotax_eitc_2024.json"
SOI_URL = "https://www.irs.gov/pub/irs-soi/{yy}in12hi.{ext}"
DOTAX_URL = {2023: "https://files.hawaii.gov/tax/stats/stats/credits/2023credit_tables.xlsx"}
DOTAX_ARCHIVE = "https://files.hawaii.gov/tax/stats/stats/credits/archive/{y}credit_tables.xlsx"
SOI_YEARS = list(range(2010, 2024))
SOI_NOT_TABLE2 = {2013, 2016}          # the published "in12hi" file is Table 11/12, not Table 2
DOTAX_YEARS = list(range(2018, 2024))

#: Federal EITC maximum credit by qualifying children (0, 1, 2, 3+), and the document it comes from.
FED_MAX_CREDIT = {
    2010: ((457, 3050, 5036, 5666), "Rev. Proc. 2009-50"), 2011: ((464, 3094, 5112, 5751), "Rev. Proc. 2011-12"),
    2012: ((475, 3169, 5236, 5891), "Rev. Proc. 2011-52"), 2013: ((487, 3250, 5372, 6044), "Rev. Proc. 2013-15"),
    2014: ((496, 3305, 5460, 6143), "Rev. Proc. 2013-35"), 2015: ((503, 3359, 5548, 6242), "Rev. Proc. 2014-61"),
    2016: ((506, 3373, 5572, 6269), "Rev. Proc. 2015-53"), 2017: ((510, 3400, 5616, 6318), "Rev. Proc. 2016-55"),
    2018: ((519, 3461, 5716, 6431), "Rev. Proc. 2018-18 (IRB 2018-10)"),
    2019: ((529, 3526, 5828, 6557), "Rev. Proc. 2018-57"), 2020: ((538, 3584, 5920, 6660), "Rev. Proc. 2019-44"),
    2021: ((543, 3618, 5980, 6728), "Rev. Proc. 2020-45 (pre-ARPA: ARPA raised the childless maximum for TY2021 only)"),
    2022: ((560, 3733, 6164, 6935), "Rev. Proc. 2021-45"), 2023: ((600, 3995, 6604, 7430), "Rev. Proc. 2022-38"),
    2024: ((632, 4213, 6960, 7830), "IRS EITC tables page"), 2025: ((649, 4328, 7152, 8046), "IRS EITC tables page"),
    2026: ((664, 4427, 7316, 8231), "IRS EITC tables page"),
}
CENTRAL_URL = ("https://www.irs.gov/credits-deductions/individuals/earned-income-tax-credit/"
               "statistics-for-tax-returns-with-the-earned-income-tax-credit-eitc")


def _get(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 research"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def fetch(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for y in SOI_YEARS:
        for ext in ("xlsx", "xls"):
            f = d / f"soi_{y}.{ext}"
            code = _get(SOI_URL.format(yy=str(y)[2:], ext=ext), f)
            print(y, ext, code)
            if code == 200:
                break
            f.unlink(missing_ok=True)
            if code in (403, 429):
                print("blocked: stop this route")
                return
        time.sleep(0.5)
    print("eitc central", _get(CENTRAL_URL, d / "irs_eitc_central.html"))
    for y in DOTAX_YEARS:
        url = DOTAX_URL.get(y) or DOTAX_ARCHIVE.format(y=y)
        code = _get(url, d / f"dotax_{y}.xlsx")
        print("dotax", y, code)
        if code in (403, 429):
            print("blocked: stop this route")
            return
        time.sleep(0.5)


def _rows(path: Path) -> list[list]:
    if path.suffix == ".xlsx":
        import openpyxl

        ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    import xlrd

    sh = xlrd.open_workbook(str(path)).sheet_by_index(0)
    return [sh.row_values(i) for i in range(sh.nrows)]


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


def parse_soi(path: Path) -> dict[str, float]:
    """Column 1 ("All returns") of the EITC rows: count and amount (thousands of dollars), total and by children."""
    rows = _rows(path)
    out: dict[str, float] = {}
    keys = [("Earned income credit:", "total"), ("Earned income credit with no qualifying", "kids0"),
            ("Earned income credit with one qualifying", "kids1"), ("Earned income credit with two qualifying", "kids2"),
            ("Earned income credit with three or more", "kids3")]
    for i, r in enumerate(rows):
        label = " ".join(str(r[0] or "").split())
        for prefix, k in keys:
            if label.startswith(prefix) and k not in out:
                out[f"{k}_n"] = _num(r[1]) or float("nan")
                nxt = " ".join(str(rows[i + 1][0] or "").split())
                if nxt.startswith("Amount"):
                    out[f"{k}_k"] = _num(rows[i + 1][1]) or float("nan")
        if label.startswith("Number of returns") and "all_returns" not in out:
            out["all_returns"] = _num(r[1]) or float("nan")
    return out


def parse_dotax(path: Path) -> dict[str, float]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = {}
    for sheet, key in (("A-1", "dollars_k"), ("A-2", "claims")):
        for r in wb[sheet].iter_rows(values_only=True):
            if r and r[0] and str(r[0]).strip() == "Earned Income Tax Credit":
                out[key] = float(r[1])
    return out


def parse_eitc_central(path: Path) -> dict[int, tuple[float, float, float]]:
    """Hawaii row of each year's table on the IRS "Statistics for tax returns with the EITC" page: claims processed
    that calendar year after the tax year (rounded as printed), ``{tax_year: (claims, dollars, average)}``."""
    import html
    import re

    t = path.read_text(errors="ignore")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}
    out = {}
    parts = re.split(r"(20\d\d) EITC tax returns by state processed in", t)
    for i in range(1, len(parts), 2):
        m = re.search(r"Hawaii ([\d.]+) ?([KM]) \$([\d.]+) ?([KMB]) \$([\d,]+)", parts[i + 1])
        if m:
            out[int(parts[i])] = (float(m[1]) * mult[m[2]], float(m[3]) * mult[m[4]], float(m[5].replace(",", "")))
    return out


def build(d: Path, out: Path) -> None:
    recs: list[tuple] = []
    for y in SOI_YEARS:
        f = next(iter(d.glob(f"soi_{y}.*")), None)
        if y in SOI_NOT_TABLE2 or f is None:
            continue
        p = parse_soi(f)
        src = f"IRS SOI Historic Table 2, Hawaii, {SOI_URL.format(yy=str(y)[2:], ext=f.suffix[1:])}"
        if "all_returns" in p:
            recs.append((y, "fed_all_returns", p["all_returns"], "returns", src))
        recs.append((y, "fed_eitc_returns", p["total_n"], "returns", src))
        recs.append((y, "fed_eitc_dollars_k", p["total_k"], "$thousand", src))
        for k in range(4):
            if f"kids{k}_n" in p:
                recs.append((y, f"fed_eitc_returns_kids{k}", p[f"kids{k}_n"], "returns", src))
                recs.append((y, f"fed_eitc_dollars_k_kids{k}", p[f"kids{k}_k"], "$thousand", src))
    for y in DOTAX_YEARS:
        f = d / f"dotax_{y}.xlsx"
        if not f.exists():
            continue
        p = parse_dotax(f)
        src = f"DOTAX Tax Credits Claimed Tables A-1/A-2 TY{y}, " + (DOTAX_URL.get(y) or DOTAX_ARCHIVE.format(y=y))
        recs.append((y, "dotax_eitc_claims", p["claims"], "claims", src))
        recs.append((y, "dotax_eitc_dollars_k", p["dollars_k"], "$thousand", src))
    central = d / "irs_eitc_central.html"
    if central.exists():
        src = f"IRS EITC Central statistics by state (claims processed in the following year, rounded), {CENTRAL_URL}"
        for y, (n, dol, avg) in sorted(parse_eitc_central(central).items()):
            recs += [(y, "irs_central_eitc_claims", n, "claims", src), (y, "irs_central_eitc_dollars_k", dol / 1e3, "$thousand", src),
                     (y, "irs_central_eitc_avg", avg, "$", src)]
    b = json.loads(BUNDLED_2024.read_text())["table_1_by_federal_agi"]["total"]
    src = "DOTAX EITC Report TY2024 (bundled dotax_eitc_2024.json), " + json.loads(BUNDLED_2024.read_text())["source"]["url"]
    recs += [(2024, "dotax_eitc_new_claims", b["new"]["claims"], "claims", src),
             (2024, "dotax_eitc_new_dollars_k", b["new"]["dollars"] / 1000, "$thousand", src),
             (2024, "dotax_eitc_applied_claims", b["applied"]["claims"], "claims", src),
             (2024, "dotax_eitc_applied_dollars_k", b["applied"]["dollars"] / 1000, "$thousand", src)]
    for y, (vals, doc) in FED_MAX_CREDIT.items():
        for k, v in enumerate(vals):
            recs.append((y, f"fed_max_credit_kids{k}", v, "$", doc))
    rate = {y: (0.20, "Act 107 SLH 2017: 20% of federal, nonrefundable (hi_eitc.py)") for y in range(2018, 2023)}
    rate |= {y: (0.40, "Act 114 SLH 2022 / Act 163 SLH 2023: 40% of federal, refundable (hi_eitc.py)") for y in (2023, 2024)}
    recs += [(y, "hi_statutory_rate_of_federal", r, "ratio", s) for y, (r, s) in rate.items()]
    recs.sort(key=lambda r: (r[1], r[0]))
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "series", "value", "unit", "source"])
        w.writerows(recs)
    print(f"wrote {len(recs)} rows to {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "build"])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    fetch(a.dir) if a.cmd == "fetch" else build(a.dir, a.out)


if __name__ == "__main__":
    main()
