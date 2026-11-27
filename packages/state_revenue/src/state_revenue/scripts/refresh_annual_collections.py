"""Fetch DOTAX annual reports and rebuild the annual collections file.

Each annual report carries every tax's collections for its fiscal year and
the one before, and the part of each the general fund kept. This downloads
the reports listed on DOTAX's annual reports page, reads both tables from
each (``pdftotext -layout``, from poppler), and writes one row per fiscal
year and table to ``data/annual_collections.json`` (``fiscal_years``: all
collections; ``general_fund``: the general fund's part).

Which report a year comes from: the newest one whose lines add to its
printed total (within :data:`TOLERANCE` thousand). The newer report carries
DOTAX's revisions; the check skips a column with a typo (the FY2008 report's
FY2007 tobacco figure is $4.0 million off its own total).

Run it from the repository root (needs ``pdftotext`` on the PATH):

    python -m state_revenue.scripts.refresh_annual_collections
    python -m state_revenue.scripts.refresh_annual_collections --since 2005 --cache /tmp/dotax
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from state_revenue.annual_report import HEADINGS, AnnualTable, parse_tax_collections, residuals

INDEX_URL = "https://tax.hawaii.gov/stats/a5_1annual/a5_1tax_annuals/"
REPORT_LINK = re.compile(
    r"https://files\.hawaii\.gov/tax/stats/stats/annual/(?:archive/)?(\d{2})annrpt[^\"']*\.pdf")
USER_AGENT = "census-forecaster/refresh"
PACE_SECONDS = 3.0          # files.hawaii.gov rate-limits bursts (see refresh_dotax_collections)
RETRY_WAITS = (10, 30, 60)
#: Thousands of dollars a year's lines may miss its printed total by (rounding).
TOLERANCE = 25

LIMITATIONS = [
    "Cash collections by fiscal year (July to June), in thousands of dollars, as DOTAX's annual "
    "report prints them. Includes taxes collected by the counties (motor vehicle), the Insurance "
    "Commissioner (insurance premiums) and the Bureau of Conveyances (conveyance).",
    "Rows change over time: the county surcharge starts in FY2007 (Honolulu only until FY2019); "
    "through FY2018 the fuel row includes the environmental response tax and motor vehicle "
    "includes rental vehicles (state_revenue.lines.HOST).",
    "The general fund table is what the general fund kept of each line after transfers to "
    "special funds and the counties; the transient accommodations, conveyance and fuel taxes "
    "are mostly elsewhere, and their shares follow the law of the year.",
    "Individual and corporate income tax for FY2017 on are not comparable to earlier years: DOTAX "
    "changed how it compiles them (FY2018 report, Table 1.8 note 4).",
]


def _fetch(url: str, *, sleep=time.sleep) -> bytes:
    for attempt in range(len(RETRY_WAITS) + 1):
        sleep(PACE_SECONDS)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == len(RETRY_WAITS):
                raise
            sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable")


def report_urls(index_html: str, since: int) -> dict[int, str]:
    """``{report_year: url}`` from the index page, newest link per year."""
    out: dict[int, str] = {}
    for m in REPORT_LINK.finditer(index_html):
        yy = int(m.group(1))
        year = 1900 + yy if yy >= 90 else 2000 + yy
        if year >= since:
            out[year] = m.group(0)
    return dict(sorted(out.items()))


def pdf_text(pdf: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise SystemExit("pdftotext not found: install poppler (brew install poppler)")
    return subprocess.run([exe, "-layout", str(pdf), "-"], check=True,
                          capture_output=True, text=True).stdout


def merge(tables: dict[str, AnnualTable]) -> dict[int, dict]:
    """One entry per fiscal year from the reports' tables (see module doc)."""
    by_fy: dict[int, list[tuple[str, AnnualTable]]] = {}
    for name, t in tables.items():
        for fy in t.years:
            by_fy.setdefault(fy, []).append((name, t))
    out: dict[int, dict] = {}
    for fy, cands in sorted(by_fy.items()):
        cands.sort(key=lambda c: c[1].years[0], reverse=True)       # newest report first
        ok = [c for c in cands if abs(residuals(c[1])[fy]) <= TOLERANCE]
        name, t = (ok or cands)[0]
        out[fy] = {
            "lines": dict(sorted(t.lines[fy].items())),
            "total": t.totals[fy],
            "report": name,
            "residual": residuals(t)[fy],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=int, default=2005, help="first report year to fetch")
    ap.add_argument("--cache", type=Path, help="keep downloaded PDFs here (reused if present)")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data" / "annual_collections.json")
    args = ap.parse_args(argv)

    urls = report_urls(_fetch(INDEX_URL).decode("utf-8", "replace"), args.since)
    if not urls:
        print(f"ERROR: no annual report links on {INDEX_URL}", file=sys.stderr)
        return 1
    cache = args.cache or Path(tempfile.mkdtemp(prefix="dotax_annual_"))
    cache.mkdir(parents=True, exist_ok=True)

    tables: dict[str, dict[str, AnnualTable]] = {name: {} for name in HEADINGS}
    sources: dict[str, dict] = {}
    for url in urls.values():
        pdf = cache / url.rsplit("/", 1)[-1]
        if not pdf.exists():
            pdf.write_bytes(_fetch(url))
        text = pdf_text(pdf)
        for name in HEADINGS:
            t = parse_tax_collections(text, name)
            if t.unmatched:
                print(f"ERROR: {pdf.name} {name}: rows with no line: {t.unmatched}", file=sys.stderr)
                return 1
            tables[name][pdf.name] = t
            print(f"[annual] {pdf.name} {name}: FY{t.years[1]}-FY{t.years[0]}, "
                  f"residuals {residuals(t)}", file=sys.stderr)
        sources[pdf.name] = {"url": url,
                             "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                             "fiscal_years": list(t.years)}

    merged = {name: merge(by_report) for name, by_report in tables.items()}
    for name, years in merged.items():
        bad = {fy: e["residual"] for fy, e in years.items() if abs(e["residual"]) > TOLERANCE}
        if bad:
            print(f"ERROR: {name}: years whose lines miss the total: {bad}", file=sys.stderr)
            return 1
    fiscal_years = merged["collections"]
    payload = {
        "source": "Hawaii Department of Taxation, Annual Report, tables 'Tax Collections' and "
                  f"'State General Fund' (index: {INDEX_URL})",
        "fetch_date": date.today().isoformat(),
        "units": "thousands of dollars, fiscal year ending June 30",
        "limitations": LIMITATIONS,
        "reports": sources,
        "fiscal_years": {str(fy): e for fy, e in fiscal_years.items()},
        "general_fund": {str(fy): e for fy, e in merged["general_fund"].items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[annual] FY{min(fiscal_years)}-FY{max(fiscal_years)} -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
