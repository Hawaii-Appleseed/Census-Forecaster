#!/usr/bin/env python3
"""Fetch the three public inputs of the general excise tax incidence model.

    .venv/bin/python scripts/fetch_get_incidence_inputs.py            # all three
    .venv/bin/python scripts/fetch_get_incidence_inputs.py --only bea dotax

Writes ``packages/tax_modeler/src/tax_modeler/data/excise/``:

* ``cex_income_deciles.json``: Consumer Expenditure Survey means by decile of income before
  taxes, national, from the BLS public API (the ``CX`` series; :data:`CEX_ITEMS`). One request
  holds 25 series and an unregistered caller gets 25 requests a day; a full fetch is 9. Set
  ``BLS_API_KEY`` to lift the limit. bls.gov refuses scripted downloads of the survey's
  microdata, which is why the model starts from the published decile means.
* ``bea_pce_hawaii.json``: BEA's personal consumption expenditures by state, Hawaiʻi, by function
  (table SAPCE4), every line, from the regional bulk file.
* ``dotax_get_by_activity.json``: the Department of Taxation's general excise and use tax
  collections and base by activity, fiscal-year totals, from the June report
  (``{YYYY}06ge.xlsx``; its cumulative columns are the fiscal year and the one before).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "excise"
USER_AGENT = "census-forecaster/1.0 (+https://github.com/Hawaii-Appleseed/Census-Forecaster)"

# CX item codes (series CXU<item>LB15<decile>M). INCBEFTX is income before taxes; GASFUEL is gasoline and
# other fuels from 2023 (GASOIL before).
CEX_ITEMS = ("INCBEFTX", "TOTALEXP", "FOODHOME", "FOODAWAY", "ALCBEVG", "TOBACCO", "APPAREL", "VEHPURCH",
             "GASFUEL", "CAREPAIR", "HHFURNSH", "HKPGSUPP", "ENTRTAIN", "READING", "PERSCARE", "MEDSERVS",
             "MISC", "HHOPER", "RNTDWELL")
CEX_GROUPS = ("all",) + tuple(f"decile_{i}" for i in range(1, 11))     # LB1501 is all consumer units
BEA_ZIP = "https://apps.bea.gov/regional/zip/SAPCE.zip"
BEA_MEMBER = "SAPCE4_HI_"
DOTAX_URL = "https://files.hawaii.gov/tax/stats/monthly/{ym}ge.xlsx"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _write(name: str, payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(REPO)}", flush=True)


def fetch_cex(years: tuple[int, int]) -> dict:
    """The decile means, kept from the file already on disk where it has them.

    Each batch is written as it arrives, so a fetch cut short by the daily limit resumes where it
    stopped. A series the API has nothing for in the last year is reported, not dropped silently.
    """
    from census_forecaster.bls.client import fetch_cpi_data

    path = OUT_DIR / "cex_income_deciles.json"
    out: dict[str, dict[str, dict[str, float]]] = json.loads(path.read_text())["years"] if path.exists() else {}
    last = str(years[1])
    ids = {f"CXU{item}LB15{g + 1:02d}M": (item, group)
           for item in CEX_ITEMS for g, group in enumerate(CEX_GROUPS)
           if group not in out.get(last, {}).get(item, {})}
    names = sorted(ids)

    def payload() -> dict:
        return {"source": "BLS Consumer Expenditure Surveys, CX series by deciles of income before taxes "
                          "(api.bls.gov/publicAPI/v2)",
                "units": "dollars per consumer unit per year", "fetch_date": date.today().isoformat(),
                "items": list(CEX_ITEMS), "years": out}

    for i in range(0, len(names), 25):
        got = fetch_cpi_data(names[i:i + 25], years[0], years[1])
        for sid, points in got.items():
            item, group = ids[sid]
            for p in points:
                out.setdefault(str(p["year"]), {}).setdefault(item, {})[group] = p["value"]
        _write(path.name, payload())
        time.sleep(1.0)
    short = {item: sorted(set(CEX_GROUPS) - set(out.get(last, {}).get(item, {}))) for item in CEX_ITEMS}
    short = {k: v for k, v in short.items() if v}
    if short:
        raise SystemExit(f"BLS returned no {last} value for {short}; what did arrive is in {path.name}")
    return payload()


def fetch_bea() -> dict:
    raw = _get(BEA_ZIP)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        member = next(n for n in z.namelist() if n.startswith(BEA_MEMBER) and n.endswith(".csv"))
        text = z.read(member).decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    year_cols = [(i, h) for i, h in enumerate(header) if re.fullmatch(r"\d{4}", h)]
    line_i, desc_i = header.index("LineCode"), header.index("Description")
    lines = {}
    for r in rows[1:]:
        if len(r) != len(header) or not r[line_i].strip().isdigit():
            continue
        values = {}
        for i, y in year_cols[-6:]:
            try:
                values[y] = float(r[i])
            except ValueError:      # "(NA)", "(D)"
                continue
        lines[r[line_i].strip()] = {"description": r[desc_i].strip(), "millions": values}
    return {"source": f"BEA regional accounts, table SAPCE4 (personal consumption expenditures by function), "
                      f"Hawaii: {BEA_ZIP}, member {member}",
            "sha256_zip": hashlib.sha256(raw).hexdigest(), "units": "millions of current dollars",
            "fetch_date": date.today().isoformat(), "lines": lines}


def _norm(label) -> str:
    return re.sub(r"\s+", " ", str(label or "").strip()).lower()


def parse_dotax(workbook) -> dict[str, dict[str, dict[str, float]]]:
    """{fiscal year: {"tax" | "base": {activity: dollars}}} from one June ``ge.xlsx``.

    The sheet holds a collections block and a base block, each with a header row of
    ``month, same month last year, fiscal year to date, last fiscal year to date``.
    """
    ws = workbook[workbook.sheetnames[0]]
    out: dict[str, dict[str, dict[str, float]]] = {}
    block, fiscal_years = None, None
    for row in ws.iter_rows(values_only=True):
        cells = list(row[:5]) + [None] * (5 - len(row[:5]))
        title = _norm(cells[1])
        if "tax collections" in title:
            block = "tax"
        elif "tax base" in title:
            block = "base"
        elif block and re.fullmatch(r"\d{4}-\d{4}", str(cells[3] or "").strip()):
            fiscal_years = [str(c).strip()[-4:] for c in cells[3:5]]
        elif block and fiscal_years and cells[0] and all(isinstance(c, int | float) for c in cells[3:5]):
            if _norm(cells[0]) == "sub-total":      # two of them, one per rate group
                continue
            for fy, value in zip(fiscal_years, cells[3:5], strict=True):
                out.setdefault(fy, {}).setdefault(block, {})[_norm(cells[0])] = float(value)
    return out


def fetch_dotax(fiscal_year: int) -> dict:
    import openpyxl

    url = DOTAX_URL.format(ym=f"{fiscal_year}06")
    raw = _get(url)
    years = parse_dotax(openpyxl.load_workbook(io.BytesIO(raw), data_only=True))
    if not years or any(set(v) != {"tax", "base"} for v in years.values()):
        raise SystemExit(f"{url}: the sheet did not parse into a tax block and a base block")
    return {"source": f"Hawaii Department of Taxation, General Excise and Use Tax Collections and Base: {url}",
            "sha256": hashlib.sha256(raw).hexdigest(), "units": "dollars, fiscal year ending June 30",
            "basis": "cash; the base is of allocated collections only, state tax (county surcharges are apart)",
            "fetch_date": date.today().isoformat(), "fiscal_years": years}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", choices=("cex", "bea", "dotax"), default=("cex", "bea", "dotax"))
    p.add_argument("--cex-years", nargs=2, type=int, default=(2023, 2024), metavar=("FIRST", "LAST"))
    p.add_argument("--dotax-fiscal-year", type=int, default=2026,
                   help="the June report to read; it carries this fiscal year and the one before")
    args = p.parse_args(argv)
    if "bea" in args.only:
        _write("bea_pce_hawaii.json", fetch_bea())
    if "dotax" in args.only:
        _write("dotax_get_by_activity.json", fetch_dotax(args.dotax_fiscal_year))
    if "cex" in args.only:
        _write("cex_income_deciles.json", fetch_cex(tuple(args.cex_years)))


if __name__ == "__main__":
    main()
