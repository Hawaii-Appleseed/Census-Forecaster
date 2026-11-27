"""Fetch the excise taxes' bases (fuel gallons, tobacco by product) into ``data/``.

DOTAX's calendar-year summaries, from its archive of monthly reports
(https://tax.hawaii.gov/stats/a5_3txcolrptarchive/):

- ``YYYYcy-fuels.pdf``: "Liquid Fuel Tax Base & Tax Collections", gallons and
  state and county tax by fuel type, and the environmental response tax by
  barrel and MMBtu. The statewide SUMMARY block is read.
- ``YYYYcy-tob.pdf``: "Cigarette & Tobacco Tax Collections", net collections by
  product. The statewide calendar-year column is read.

Writes ``data/excise_bases.json`` from the newest year both reports cover.

    python -m state_revenue.scripts.refresh_excise_bases --cache /tmp/excise
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from state_revenue.scripts.refresh_annual_collections import _fetch, pdf_text

ARCHIVE_PAGE = "https://tax.hawaii.gov/stats/a5_3txcolrptarchive/"
LINK = re.compile(r"https://files\.hawaii\.gov/tax/stats/monthly/(?:archive/)?(\d{4})cy-(fuels|tob)\.pdf")
OUT = Path(__file__).resolve().parent.parent / "data" / "excise_bases.json"

FUEL_ROWS = {
    "gasoline": "gasoline",
    "diesel oil (off-highway)": "diesel_off_highway",
    "diesel oil (highway)": "diesel_highway",
    "liquefied petroleum gas (highway)": "lpg_highway",
    "small boats (gasoline)": "small_boats_gasoline",
    "small boats (diesel oil)": "small_boats_diesel",
    "aviation fuel": "aviation",
    "other fuel": "other",
    "ert - petroleum products (barrel)": "ert_barrel",
    "ert - fossil fuels (mmbtu)": "ert_mmbtu",
}
TOBACCO_ROWS = {
    "cigarettes": "cigarettes",
    "all other tobacco products": "other_tobacco",
    "e-liquids": "e_liquids",
    "cigars": "cigars",
    "little cigars": "little_cigars",
    "license fees": "license_fees",
    "retail permits": "retail_permits",
    "unallocated net collections": "unallocated",
}
_AMOUNT = r"\(?-?[\d,]+(?:\.\d+)?\)?"


def _num(tok: str) -> float:
    neg = tok.startswith("(") or tok.startswith("-")
    return -float(tok.strip("()-").replace(",", "")) if neg else float(tok.replace(",", ""))


def _label(raw: str) -> str:
    raw = re.sub(r"\s+\d/?$", "", raw.strip())          # footnote "1/", "2/"
    return re.sub(r"\s+", " ", raw).lower()


def parse_fuels(text: str) -> dict[str, dict[str, float]]:
    """The statewide SUMMARY block: ``{fuel: {"quantity", "state_tax", "county_tax"}}``.
    Quantity is gallons, barrels for ``ert_barrel``, MMBtu for ``ert_mmbtu``."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "SUMMARY"), None)
    if start is None:
        raise ValueError("fuel report: no SUMMARY block")
    out: dict[str, dict[str, float]] = {}
    for line in lines[start + 1: start + 20]:
        m = re.match(rf"^\s*([A-Za-z][A-Za-z ().\-]*?)(?:\s+\d/)?\s+((?:{_AMOUNT}\s*\*?\s+)+{_AMOUNT})\s*\**\s*$",
                     line)
        if not m:
            continue
        key = FUEL_ROWS.get(_label(m[1]))
        if key is None:
            continue
        nums = [_num(t) for t in re.findall(_AMOUNT, m[2])]
        if key.startswith("ert_"):
            quantity, state = nums[0], nums[1]
            county = 0.0
        elif len(nums) == 4:
            quantity, state, county = nums[0], nums[1], nums[2]
        else:                                   # no county tax: gallons, state, total
            quantity, state, county = nums[0], nums[1], 0.0
        out[key] = {"quantity": quantity, "state_tax": state, "county_tax": county}
    missing = set(FUEL_ROWS.values()) - set(out)
    if missing:
        raise ValueError(f"fuel report: rows not found: {sorted(missing)}")
    return out


def parse_tobacco(text: str) -> dict[str, float]:
    """Net collections by product, the statewide calendar-year column (dollars)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(rf"^\s*([A-Za-z][A-Za-z \-]*?)\s+((?:{_AMOUNT}\s+){{4}}{_AMOUNT})", line)
        if not m:
            continue
        key = TOBACCO_ROWS.get(_label(m[1]))
        if key and key not in out:
            out[key] = _num(re.findall(_AMOUNT, m[2])[4])     # four districts, then the year
    missing = set(TOBACCO_ROWS.values()) - set(out) - {"e_liquids"}   # taxed from 2024
    if missing:
        raise ValueError(f"tobacco report: rows not found: {sorted(missing)}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path)
    args = ap.parse_args(argv)
    cache = args.cache or Path(tempfile.mkdtemp(prefix="excise_"))
    cache.mkdir(parents=True, exist_ok=True)
    found: dict[tuple[int, str], str] = {}
    for m in LINK.finditer(_fetch(ARCHIVE_PAGE).decode("utf-8", "replace")):
        found[(int(m[1]), m[2])] = m[0]
    years = sorted({y for y, k in found if (y, "fuels") in found and (y, "tob") in found})
    if not years:
        print(f"ERROR: no calendar-year fuel and tobacco reports on {ARCHIVE_PAGE}", file=sys.stderr)
        return 1
    year = years[-1]
    texts, sources = {}, []
    for kind in ("fuels", "tob"):
        url = found[(year, kind)]
        pdf = cache / url.rsplit("/", 1)[-1]
        if not pdf.exists():
            pdf.write_bytes(_fetch(url))
        texts[kind] = pdf_text(pdf)
        sources.append({"url": url, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()})
    payload = {
        "source": "Hawaii Department of Taxation, calendar-year summaries: Liquid Fuel Tax Base & "
                  "Tax Collections; Cigarette & Tobacco Tax Collections and Allocations",
        "fetch_date": date.today().isoformat(),
        "calendar_year": year,
        "units": "fuel: gallons (barrels, MMBtu for the environmental response tax) and dollars; "
                 "tobacco: dollars",
        "reports": sources,
        "fuel": parse_fuels(texts["fuels"]),
        "tobacco": parse_tobacco(texts["tob"]),
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"[excise] calendar {year} -> {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
