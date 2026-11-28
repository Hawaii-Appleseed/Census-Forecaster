"""Backfill the DOTAX monthly collections bundle from DOTAX's PDF archive.

``refresh_dotax_collections`` reads the XLSX reports, which DOTAX keeps up for
about a year. The same report is archived as a PDF for every month back to
2010 (https://tax.hawaii.gov/stats/a5_3txcolrptarchive/,
``YYYYMMcollec.pdf``). This reads those PDFs (``pdftotext -layout``, from
poppler) and fills months and series the bundle lacks. It never overwrites a
value the bundle already holds: the XLSX refresh carries DOTAX's revisions.

Each report holds its month and the same month a year earlier, so one fiscal
year of reports fills two. Through FY2018 the fuel row includes the
environmental response tax, as in the annual reports.

    python -m census_forecaster.scripts.backfill_dotax_collections --from 2014-07 --to 2025-06
    python -m census_forecaster.scripts.backfill_dotax_collections --cache /tmp/collec
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from census_forecaster.scripts.refresh_dotax_collections import (
    _SECTION_ROWS,
    PACE_SECONDS,
    TAX_LINE_KEYS,
    _default_out,
)

ARCHIVE_PAGE = "https://tax.hawaii.gov/stats/a5_3txcolrptarchive/"
LINK = re.compile(r"https://files\.hawaii\.gov/tax/stats/monthly/(?:archive/)?(\d{6})collec(?:_rev\d*)?\.pdf")

COMBINED_FUEL = "_fuel_and_environmental"

#: PDF row label (normalized by :func:`_norm`) -> series key. The PDFs' labels
#: changed over the years; every spelling met from 2014 on is here.
PDF_ROWS = {
    "banks/financial corp.": "banks",
    "conveyance": "conveyance",
    "employment sec. contri.": "unemployment",
    "employment security contributions": "unemployment",
    "fuel": "fuel",
    "environmental": "environmental_response",
    # FY2020's reports print one row for both; it is used for the total check
    # and not stored (the next year's reports give the two apart).
    "fuel & environmental": COMBINED_FUEL,
    "ge license/fees": "ge_licenses",
    "ge licenses": "ge_licenses",
    "general excise & use": "ge_use",
    "county surcharges": "county_surcharge",
    "honolulu county surcharge": "county_surcharge",
    "inheritance/estate": "estate",
    "insurance premiums": "insurance_premiums",
    "liquor and permits": "liquor",
    "liquor tax and permits": "liquor",
    "mtr. vehicle tax/fees": "motor_vehicle",
    "mtr. vehicle tax and fees": "motor_vehicle",
    "motor vehicle tax/fees": "motor_vehicle",
    "rental vehicle tax and fees": "rental_vehicle",
    "rental vehicle tax": "rental_vehicle",
    "public service co.": "public_service_companies",
    "tobacco and licenses": "tobacco",
    "tobacco tax and licenses/permits": "tobacco",
    "trans. accomm./time share occup. fees": "tat_fees",
    "trans. accomm. fees": "tat_fees",
    "trans. accomm. tax/time share occup. tax": "tat",
    "trans. accomm. tax": "tat",
    "all others": "all_others",
}
_AMOUNT = r"\(?-?[\d,]+(?:\.\d+)?\)?"
_ROW = re.compile(rf"^\s*(?P<label>[A-Za-z][A-Za-z .&/()]*?)\s*(?:\d{{1,2}}/,?\s*)*\s+"
                  rf"(?P<nums>{_AMOUNT}(?:\s+{_AMOUNT})+)")


def _norm(label: str) -> str:
    s = re.sub(r"[‐‑–—]", "-", label)                 # the PDFs use a Unicode hyphen
    return re.sub(r"\s+", " ", s).strip().lower()


def _num(tok: str) -> float:
    neg = tok.startswith("(") or tok.startswith("-")
    v = float(tok.strip("()-").replace(",", ""))
    return -v if neg else v


def parse_collec_text(text: str, year: int, month: int) -> tuple[dict[str, dict[str, float]], list[str]]:
    """``({"YYYY-MM": {series: dollars}}, unmatched labels)`` for the report's
    month and the same month a year before. Rows after the first TOTAL (the
    distribution of collections) are not read."""
    cur, prior = f"{year:04d}-{month:02d}", f"{year - 1:04d}-{month:02d}"
    out: dict[str, dict[str, float]] = {cur: {}, prior: {}}
    unmatched: list[str] = []
    text = text.replace("‐", "-")
    # Through FY2021 the report leads with four tax-district columns; the
    # month and the same month a year before are then the fifth and sixth.
    at = 4 if "ALL DISTRICTS" in text[:3000].upper() else 0
    section = None
    for line in text.splitlines():
        head = _norm(line)
        if head.startswith("income-corp") or head.startswith("income - corp"):
            section = "corp"
            continue
        if head.startswith("income-ind") or head.startswith("income - ind"):
            section = "ind"
            continue
        if head.startswith("total"):                    # "TOTAL - MARCH 2024   730,211,276 ..."
            nums = re.findall(_AMOUNT, re.sub(r"^.*?\b(?:19|20)\d\d\b", "", line, count=1))
            if len(nums) > at:
                out[cur]["total"] = _num(nums[at])
            break
        m = _ROW.match(line)
        if not m:
            if re.match(r"^\s*[A-Za-z]", line) and re.search(r"\d,\d{3}", line):
                unmatched.append("?" + head[:40])        # a row with figures the pattern missed
            continue
        nums = re.findall(_AMOUNT, m["nums"])
        label = _norm(m["label"]).replace("w/returns", "w/ returns")
        if (section, label) in _SECTION_ROWS:
            key = _SECTION_ROWS[(section, label)]
        elif label in PDF_ROWS:
            key, section = PDF_ROWS[label], None
        else:
            unmatched.append(label)
            continue
        if len(nums) < at + 2:
            unmatched.append("?" + label)
            continue
        out[cur][key] = _num(nums[at])
        out[prior][key] = _num(nums[at + 1])
    return out, unmatched


def check(month_values: dict[str, float]) -> float | None:
    """TOTAL minus the tax lines, when the month has a total (rounding aside, zero)."""
    if "total" not in month_values:
        return None
    return month_values["total"] - sum(v for k, v in month_values.items()
                                       if k in TAX_LINE_KEYS or k == COMBINED_FUEL)


def _get(url: str) -> bytes:
    time.sleep(PACE_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": "census-forecaster/refresh"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="first", default="2014-07", help="first report month, YYYY-MM")
    ap.add_argument("--to", dest="last", default="2025-06", help="last report month, YYYY-MM")
    ap.add_argument("--cache", type=Path, help="keep downloaded PDFs here (reused if present)")
    ap.add_argument("--out", type=Path, default=_default_out())
    args = ap.parse_args(argv)
    exe = shutil.which("pdftotext")
    if exe is None:
        print("ERROR: pdftotext not found (install poppler)", file=sys.stderr)
        return 2
    cache = args.cache or Path(tempfile.mkdtemp(prefix="collec_"))
    cache.mkdir(parents=True, exist_ok=True)

    page = _get(ARCHIVE_PAGE).decode("utf-8", "replace")
    urls: dict[str, str] = {}
    for m in LINK.finditer(page):
        ym = f"{m[1][:4]}-{m[1][4:]}"
        if ym not in urls or "_rev" in m[0]:            # a revised report replaces the first
            urls[ym] = m[0]
    bundle = json.loads(args.out.read_text())
    monthly, asof = bundle["monthly"], bundle["value_asof"]
    # A month appears twice: in its own report and, a year later, as the
    # prior-year column. The later report wins key by key: it carries DOTAX's
    # revisions and its row definitions (rental vehicles left the motor vehicle
    # row in the FY2020 reports, restated for the year before). Mixing the two
    # would count such a row twice.
    own: dict[str, dict[str, float]] = {}
    later: dict[str, dict[str, float]] = {}
    source: dict[str, str] = {}
    for ym in sorted(urls):
        if not args.first <= ym <= args.last:
            continue
        pdf = cache / urls[ym].rsplit("/", 1)[-1]
        if not pdf.exists():
            pdf.write_bytes(_get(urls[ym]))
        text = subprocess.run([exe, "-layout", str(pdf), "-"], check=True, capture_output=True,
                              text=True).stdout
        parsed, unmatched = parse_collec_text(text, int(ym[:4]), int(ym[5:]))
        gap = check(parsed[ym])
        if unmatched or gap is None or abs(gap) > 5:
            print(f"ERROR: {pdf.name}: unmatched rows {unmatched}, total minus lines {gap}",
                  file=sys.stderr)
            return 1
        prior = f"{int(ym[:4]) - 1}{ym[4:]}"
        own[ym] = parsed[ym]
        later[prior] = parsed[prior]
        source.setdefault(ym, pdf.name)
        source[prior] = pdf.name
    filled = 0
    for month in sorted(set(own) | set(later)):
        values = dict(own.get(month, {}))
        revised = later.get(month, {})
        if COMBINED_FUEL not in revised:
            # The later report splits rows this month's own report combined.
            for parent, child in (("motor_vehicle", "rental_vehicle"), ("fuel", "environmental_response")):
                if child in revised and child not in values:
                    values.pop(parent, None)
            values.update(revised)
            values.pop(COMBINED_FUEL, None)
        if "total" in values:                           # the own report's total; re-derive it
            values["total"] = round(sum(v for k, v in values.items() if k in TAX_LINE_KEYS), 2)
        have = monthly.setdefault(month, {})
        new = {k: v for k, v in values.items() if k not in have and k != COMBINED_FUEL}
        if new:
            have.update(new)
            asof.setdefault(month, source[month])
            filled += len(new)
    bundle["monthly"] = dict(sorted(monthly.items()))
    bundle["value_asof"] = dict(sorted(asof.items()))
    bundle["series_keys"] = sorted({k for v in monthly.values() for k in v})
    args.out.write_text(json.dumps(bundle, indent=2, sort_keys=True))
    print(f"[backfill] {filled} values filled; bundle spans {min(monthly)} to {max(monthly)} "
          f"({len(monthly)} months)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
