"""Fetch the general excise model's two outside inputs into ``data/``.

``io_2022.json``
    DBEDT's 2022 Hawaiʻi input-output table, detailed (62 producing sectors):
    the transactions between sectors, final demand by purchaser (resident
    households, visitors, investment, government, exports), output, and the
    state taxes each sector pays (general excise, transient accommodations,
    individual income, other). $ millions. From
    https://dbedt.hawaii.gov/economic/input-output-study/.
``visitor_spending.json``
    Visitor spending by category (HTA Annual Visitor Research Report, Table
    71: air, cruise and supplemental business visitors), every year the two
    newest workbooks carry. $ millions, calendar years.

    python -m state_revenue.scripts.refresh_get_inputs --cache /tmp/get_inputs
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from state_revenue.scripts.refresh_annual_collections import _fetch

DATA = Path(__file__).resolve().parent.parent / "data"
IO_URL = "https://files.hawaii.gov/dbedt/economic/reports/IO/2022-State-IO-Detailed_Final.xlsx"
HTA_PAGE = "https://hta.hawaii.gov/research/annual-visitor-research-reports/"
HTA_LINK = re.compile(r'href="([^"]*(\d{4})-annual-visitor-research-report-tables[^"]*\.xlsx)"', re.I)

#: The input-output table's final demand columns, as the workbook heads them.
FINAL_DEMAND = (
    "PCE", "VE", "Change in inventories", "Gross private investment",
    "State and local gov't investment", "State and local gov't consumption",
    "Federal defense consumption", "Federal defense investment",
    "Federal nondefense consumption", "Federal nondefense investment", "Exports",
)
#: Rows under the sectors: label in the workbook -> key.
IO_ROWS = {
    "Imports": "imports",
    "Compensation of employees": "compensation",
    "Taxes on Production and Imports less Subsidies": "taxes_on_production",
    "State GET": "state_get",
    "State TAT": "state_tat",
    "State individual income tax": "state_iit",
    "State other taxes": "state_other",
}

#: HTA Table 71 row (lower case, stripped) -> key.
VISITOR_ROWS = {
    "grand total": "total",
    "restaurant food": "restaurants",
    "dinner shows and cruises": "dinner_shows",
    "groceries and snacks": "groceries",
    "entertainment & recreation": "entertainment",
    "interisland airfare": "interisland_air",
    "ground transportation": "ground_transport",
    "rental vehicles": "rental_vehicles",
    "gasoline, parking, etc.": "gasoline_parking",
    "fashion and clothing": "clothing",
    "jewelry and watches": "jewelry",
    "cosmetics, perfume": "cosmetics",
    "leather goods": "leather",
    "hawaiʻi food products": "hawaii_food_products",
    "hawaii food products": "hawaii_food_products",
    "souvenirs": "souvenirs",
    "lodging": "lodging",
    "all other expenses 1/": "all_other",
    "all other expenses": "all_other",
    "supplemental business": "supplemental_business",
}


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def parse_io(content: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    rows = list(wb["I-O"].iter_rows(values_only=True))
    header = next(r for r in rows if r and "Industry" in r[:2])
    col = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
    sectors = [r for r in rows if isinstance(r[0], str) and re.fullmatch(r"S\d+", r[0])]
    n = len(sectors)
    flows = [[round(_num(x), 4) for x in r[2:2 + n]] for r in sectors]
    final = {k: [round(_num(r[col[k]]), 4) for r in sectors] for k in FINAL_DEMAND}
    output = [round(_num(r[col["Output"]]), 4) for r in sectors]
    by_label = {str(r[1]).strip(): r for r in rows if r and r[0] is None and r[1] is not None}
    extra = {}
    for label, key in IO_ROWS.items():
        match = next((v for k, v in by_label.items() if k.startswith(label)), None)
        if match is None:
            raise SystemExit(f"input-output table: no row {label!r}")
        extra[key] = [round(_num(x), 4) for x in match[2:2 + n]]
    for i, r in enumerate(sectors):
        gap = sum(flows[i]) + sum(final[k][i] for k in FINAL_DEMAND) - output[i]
        if abs(gap) > 0.05:
            raise SystemExit(f"input-output table: {r[1]} uses miss output by {gap:.3f}")
    return {
        "year": 2022,
        "units": "$ millions",
        "sectors": [{"code": r[0], "name": str(r[1]).strip()} for r in sectors],
        "output": output,
        "flows": flows,
        "final_demand": final,
        "rows": extra,
    }


def parse_visitor_totals(content: bytes) -> dict[int, dict[str, float]]:
    """The "Total Visitor Expenditures by Category" table, found by its title
    (its number changes from year to year)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = next((w for w in wb.worksheets
               if "total visitor expenditures by category" in " ".join(
                   str(c) for r in w.iter_rows(min_row=1, max_row=2, values_only=True)
                   for c in r if c).lower()), None)
    if ws is None:
        raise SystemExit("visitor workbook: no 'Total Visitor Expenditures by Category' table")
    rows = list(ws.iter_rows(values_only=True))
    header = next(r for r in rows if r and str(r[0]).strip().lower() == "expenditure type")
    years = [int(h) for h in header[1:3]]
    out: dict[int, dict[str, float]] = {y: {} for y in years}
    for r in rows:
        if not r or r[0] is None:
            continue
        key = VISITOR_ROWS.get(str(r[0]).strip().lower())
        if key:
            for y, v in zip(years, r[1:3], strict=True):
                out[y][key] = round(float(v), 3)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, help="keep downloads here (reused if present)")
    args = ap.parse_args(argv)
    cache = args.cache or Path(tempfile.mkdtemp(prefix="get_inputs_"))
    cache.mkdir(parents=True, exist_ok=True)

    def get(url: str) -> bytes:
        f = cache / url.rsplit("/", 1)[-1]
        if not f.exists():
            f.write_bytes(_fetch(url))
        return f.read_bytes()

    content = get(IO_URL)
    table = parse_io(content)
    table.update(source=IO_URL, sha256=hashlib.sha256(content).hexdigest(),
                 fetch_date=date.today().isoformat())
    (DATA / "io_2022.json").write_text(json.dumps(table, separators=(",", ":")) + "\n")
    print(f"[get] input-output: {len(table['sectors'])} sectors, state GET "
          f"{sum(table['rows']['state_get']):,.1f}M", file=sys.stderr)

    links = sorted({(int(y), u) for u, y in HTA_LINK.findall(_fetch(HTA_PAGE).decode("utf-8", "replace"))},
                   reverse=True)[:2]
    if not links:
        print(f"ERROR: no annual visitor research tables on {HTA_PAGE}", file=sys.stderr)
        return 1
    years: dict[int, dict[str, float]] = {}
    sources = []
    for _, url in reversed(links):                  # newer workbook wins on overlap
        content = get(url)
        years.update(parse_visitor_totals(content))
        sources.append({"url": url, "sha256": hashlib.sha256(content).hexdigest()})
    payload = {
        "source": "Hawaii Tourism Authority, Annual Visitor Research Report, 'Total Visitor "
                  "Expenditures by Category' (air, cruise and supplemental business visitors)",
        "fetch_date": date.today().isoformat(),
        "units": "$ millions, calendar year",
        "reports": sources,
        "years": {str(y): v for y, v in sorted(years.items())},
    }
    (DATA / "visitor_spending.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(f"[get] visitor spending: {sorted(years)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
