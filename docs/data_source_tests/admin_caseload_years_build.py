"""Build hawaii_caseload_extended.csv from USDA-FNS state tables.

Usage: python -I admin_caseload_years_build.py RAW_DIR OUT_CSV BUNDLED_CSV
RAW_DIR holds the downloaded FNS workbooks (see admin_caseload_years.md for URLs).
"""
import csv
import sys
import warnings
from pathlib import Path

import openpyxl

warnings.simplefilter("ignore")
raw, out, bundled = map(Path, sys.argv[1:4])
BASE = "https://www.fns.usda.gov/sites/default/files/resource-files/"
ASOF = "data as of November 13, 2026"


def hawaii(fname):
    ws = openpyxl.load_workbook(raw / fname, data_only=True).active
    hdr = None
    for r in ws.iter_rows(values_only=True):
        if r[0] and str(r[0]).strip().startswith("State"):
            hdr = [str(c).strip().replace("\n", "") if c else "" for c in r]
        if r[0] and str(r[0]).strip() == "Hawaii":
            return {int(h.split()[1]): v for h, v in zip(hdr, r) if h.startswith("FY")}


part, cost = hawaii("26wifypart-11.xlsx"), hawaii("24wicfood-costs-11.xlsx")
nslp, cash = hawaii("01slfypart-11.xlsx"), hawaii("06slcash-11.xlsx")

new = []
for y in sorted(part):
    new.append(("wic", y, "person", round(part[y]), round(cost[y] / 1e6, 3),
                f"USDA-FNS WIC Program Data FY{y} ({ASOF}): Hawaii average monthly participants, "
                f"{BASE}26wifypart-11.xlsx; WIC food costs (gross, before infant-formula rebates; "
                f"excludes nutrition services and administration), {BASE}24wicfood-costs-11.xlsx"))
# FY2026: ten months (Oct 2025 - Jul 2026) from the agency monthly workbook, annualised x12/10
new.append(("wic", 2026, "person", 25084, round(19.138036 * 12 / 10, 3),
            "USDA-FNS WIC agency monthly data FY2026 (data as of October 09, 2026), Hawaii, October 2025 to "
            f"July 2026: average participation 25,083.9; food cost $19,138,036 in 10 months at an annual rate; "
            f"{BASE}wicagencies2026ytd.xlsx (sheets 'Total Number of Participants', 'Food Costs')"))
for y in sorted(nslp):
    new.append(("school_lunch_total", y, "person", round(nslp[y]), round(cash[y] / 1e6, 3),
                f"USDA-FNS NSLP FY{y} ({ASOF}): Hawaii average daily participation (all lunches: free, reduced, "
                f"paid), {BASE}01slfypart-11.xlsx; federal cash payments, {BASE}06slcash-11.xlsx"
                + ("; preliminary" if y == 2025 else "")
                + ("; COVID-era waivers (free meals for all) in FY2021-22" if y <= 2022 else "")))

rows = list(csv.DictReader(open(bundled)))
keep = [r for r in rows if not (r["program"] == "wic" and r["year"] == "2022")]
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["program", "year", "unit", "count", "annual_dollars_millions", "source"])
    for r in keep:
        w.writerow([r[k] for k in ("program", "year", "unit", "count", "annual_dollars_millions", "source")])
    for r in new:
        w.writerow(r)
print(f"{len(keep)} bundled rows kept (bundled wic 2022 replaced), {len(new)} rows added")
