"""Fetch Hawaii monthly SNAP (USDA FNS) and Medicaid/CHIP (CMS) caseloads.

Exploratory (docs/data_source_tests/caseload-leading-indicators.md). Writes
data/caseloads/hi_caseloads_monthly.json in the macro_monthly.json row shape
({"year", "period": "Mxx", "value"}); never touches data/markets/.

    uv run python scripts/refresh_caseloads.py [--workdir DIR]
Needs xlrd (FNS ships .xls before FY20).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import zipfile
from pathlib import Path

FNS_ZIP = ("https://www.fns.usda.gov/sites/default/files/resource-files/"
           "snap-zip-fy69tocurrent-11.zip")
CMS_META = ("https://data.medicaid.gov/api/1/metastore/schemas/dataset/"
            "items/6165f45b-ca93-5bb5-9d06-db29c692a360?show-reference-ids=false")
MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
OUT = Path(__file__).resolve().parents[1] / "data" / "caseloads"


def curl(url: str, dest: Path) -> None:
    subprocess.run(["curl", "-sfL", "-A", "Mozilla/5.0", "-o", str(dest), url],
                   check=True)


def sheet_rows(path: Path):
    if path.suffix == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        for ws in wb.sheets():
            yield [ws.row_values(i) for i in range(ws.nrows)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for ws in wb.worksheets:
            yield [list(r) for r in ws.iter_rows(values_only=True)]


def parse_fns(path: Path) -> dict[str, dict[tuple[int, int], float]]:
    out = {"households": {}, "persons": {}, "benefits": {}}
    for rows in sheet_rows(path):
        for i, r in enumerate(rows):
            if str(r[0]).strip() != "Hawaii":
                continue
            for r2 in rows[i + 1:i + 14]:
                m = re.match(r"([A-Z][a-z]{2})\w*\.? (\d{4})$", str(r2[0]).strip())
                if not m:
                    continue
                nums = [x for x in r2[1:] if isinstance(x, (int, float))]
                if len(nums) < 2:
                    continue
                key = (int(m.group(2)), MONTHS[m.group(1)])
                out["households"][key] = nums[0]
                out["persons"][key] = nums[1]
                big = [x for x in nums[2:] if x > 5e6]   # cost column
                if big:
                    out["benefits"][key] = big[0]
            return out
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/caseload_dl"))
    a = ap.parse_args()
    (a.workdir / "fns").mkdir(parents=True, exist_ok=True)
    (a.workdir / "cms").mkdir(parents=True, exist_ok=True)

    z = a.workdir / "fns" / "snap.zip"
    if not z.exists():
        curl(FNS_ZIP, z)
    ex = a.workdir / "fns" / "ex"
    zipfile.ZipFile(z).extractall(ex)
    series: dict[str, dict[tuple[int, int], float]] = {
        "SNAP_HI_HOUSEHOLDS": {}, "SNAP_HI_PERSONS": {}, "SNAP_HI_BENEFITS": {}}
    for f in sorted(ex.glob("FY*.xls*")):
        p = parse_fns(f)
        for k, nm in (("households", "SNAP_HI_HOUSEHOLDS"),
                      ("persons", "SNAP_HI_PERSONS"),
                      ("benefits", "SNAP_HI_BENEFITS")):
            series[nm].update(p[k])   # later FY files carry revised values

    meta = a.workdir / "cms" / "meta.json"
    curl(CMS_META, meta)
    url = json.load(open(meta))["distribution"][0]["data"]["downloadURL"]
    csvp = a.workdir / "cms" / "pi.csv"
    if not csvp.exists():
        curl(url, csvp)
    med: dict[str, dict[tuple[int, int], float]] = {}
    with open(csvp, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            if row.get("State Abbreviation") != "HI":
                continue
            y, m = row["Reporting Period"][:4], row["Reporting Period"][4:6]
            for col, nm in (("Total Medicaid and CHIP Enrollment", "MEDICAID_CHIP_HI_TOTAL"),
                            ("Total Medicaid Enrollment", "MEDICAID_HI_TOTAL"),
                            ("Total CHIP Enrollment", "CHIP_HI_TOTAL")):
                v = (row.get(col) or "").replace(",", "")
                if not v.isdigit():
                    continue
                d = med.setdefault(nm, {})
                k = (int(y), int(m))
                # several vintages per period (P/U): keep the Final=Y/U row
                rank = (row.get("Final Report") == "Y",
                        row.get("Preliminary or Updated") == "U")
                if k not in d or rank >= d[k][1]:
                    d[k] = (float(v), rank)
    med = {n: {k: v[0] for k, v in d.items()} for n, d in med.items()}
    series.update(med)
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"source": {"snap": FNS_ZIP, "cms": url},
               "series": {n: [{"year": y, "period": f"M{m:02d}", "value": v}
                              for (y, m), v in sorted(d.items())]
                          for n, d in series.items()}}
    (OUT / "hi_caseloads_monthly.json").write_text(json.dumps(payload))
    for n, d in series.items():
        ks = sorted(d)
        print(n, len(d), ks[0] if ks else None, ks[-1] if ks else None)


if __name__ == "__main__":
    main()
