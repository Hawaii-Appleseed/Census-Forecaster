"""Re-scrape Hawaiʻi DOH preliminary births and print the RxKids constants.

Source: Hawaiʻi Department of Health, Office of Health Status Monitoring,
"Preliminary Vital Statistics" — https://health.hawaii.gov/vitalstatistics/
One page per year, each carrying a *Month of Occurrence* table (births,
deaths, marriages) for Statewide plus each of the four counties.

Why this exists
---------------
``analyses/forecast_rxkids_2028.py`` pins the DOH snapshot in-code
(``HI_DOH_BIRTHS_MONTHLY``, ``DOH_COUNTY_SHARE``, ``DOH_SNAPSHOT``) so runs stay
byte-reproducible — the same discipline the anchor bundles follow. Pinning is
right, but hand-transcribing nine years × twelve months × five geographies on
each refresh is not. This script does the scrape and emits paste-ready literals,
so refreshing is one command plus one paste rather than an error-prone retype.

It deliberately does NOT write to the forecast script. The constants are a
reviewed input, and a silent auto-rewrite would defeat the point of pinning.

Usage
-----
    python scripts/refresh_doh_births.py
    python scripts/refresh_doh_births.py --json      # machine-readable dump

After pasting, in ``analyses/forecast_rxkids_2028.py``:

* bump ``DOH_SNAPSHOT`` to the "As of" date the script reports;
* DELETE any year NVSR has since finalised from ``HI_DOH_BIRTHS_MONTHLY``
  (NVSR always wins — ``_doh_nowcast_births`` skips years already in
  ``HI_BIRTHS_BY_YEAR``, so a stale duplicate is inert but misleading);
* re-run the RxKids forecast and the test suite.

Note on the county shares
-------------------------
This script does NOT produce ``NVSR_COUNTY_SHARE``. DOH counts by county of
*occurrence*; the model needs county of *residence*, and in Hawaiʻi the two
differ materially at county level even though they nearly coincide statewide —
neighbour-island mothers routinely deliver on Oʻahu, so occurrence overstates
Honolulu by ~2.2pp. The county shares come from CDC WONDER (dataset D66,
Hawaiʻi, grouped by county × year), which is residence-based and the same
source as the state anchor.

The DOH county totals ARE printed below, as an occurrence-basis cross-check:
they should sum to the statewide row in every year, and their divergence from
the WONDER residence shares should stay in the ~2pp range documented at
``NVSR_COUNTY_SHARE``. A sudden change there means one of the two sources
shifted definition and both should be re-examined.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request

BASE = "https://health.hawaii.gov/vitalstatistics/"

# DOH renames these slugs almost every year; there is no stable pattern, so the
# mapping is explicit. Add the new year's slug here at refresh time.
YEAR_SLUGS = {
    2018: "preliminary-2018-vital-statistics",
    2019: "preliminary-2019-vital-statistics",
    2020: "preliminary-vital-statistics-for-2020",
    2021: "preliminary-vital-statistics-for-2021",
    2022: "preliminary-vital-statistics-for-2022",
    2023: "preliminary-vital-statistics-for-2023",
    2024: "2024-preliminary-vital-stat",
    2025: "preliminary-vital-statistics-2025",
    2026: "preliminary-vital-statistics-2026",
}

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# Section headers as they appear in the rendered page. "Maui County" carries a
# "(Includes Kalawao)" suffix in some years, so match on prefix.
GEOGRAPHIES = ["Hawaii County", "Honolulu County", "Kauai County",
               "Maui County", "Statewide"]

# Map DOH's section header -> the county label the tax-unit frame uses.
COUNTY_LABEL = {
    "Hawaii County": "Hawaii",
    "Honolulu County": "Honolulu",
    "Kauai County": "Kauai",
    "Maui County": "Maui",
}


def _fetch(slug: str, timeout: int = 60) -> str:
    req = urllib.request.Request(BASE + slug + "/",
                                 headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def _to_lines(raw: str) -> list:
    t = re.sub(r"<script.*?</script>", "", raw, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    return [ln.strip() for ln in html.unescape(t).split("\n") if ln.strip()]


def _parse_section(lines: list, start: int, end: int) -> dict:
    """Pull {month: births} + published Total out of one geography's table.

    Each month row is ``Month / Births / Deaths / Marriages``; births is the
    first numeric cell after the month name.
    """
    seg = lines[start:end]
    months: dict = {}
    for i, ln in enumerate(seg):
        if ln in MONTHS and ln not in months:
            for cell in seg[i + 1: i + 5]:
                if re.fullmatch(r"[\d,]+", cell):
                    months[ln] = int(cell.replace(",", ""))
                    break
                break
    total = None
    for i, ln in enumerate(seg):
        if ln == "Total" and i + 1 < len(seg) and re.fullmatch(r"[\d,]+", seg[i + 1]):
            total = int(seg[i + 1].replace(",", ""))
            break
    return {"months": months, "total_published": total}


def scrape_year(year: int, slug: str) -> dict:
    lines = _to_lines(_fetch(slug))

    # Real table headers are "<Geography>" immediately followed by
    # "January to December <year>"; the same strings also appear in nav
    # breadcrumbs, so require the date line to disambiguate.
    idx: dict = {}
    for i, ln in enumerate(lines):
        for geo in GEOGRAPHIES:
            if ln.startswith(geo) and geo not in idx:
                nxt = lines[i + 1: i + 3]
                if any(x.startswith("January to December") for x in nxt):
                    idx[geo] = i
    if not idx:
        raise RuntimeError(f"{year}: no geography tables found (page layout changed?)")

    as_of = next((ln for ln in lines if ln.startswith("As of")), "")
    ordered = sorted(idx.items(), key=lambda kv: kv[1])
    out = {"as_of": as_of, "geographies": {}}
    for j, (geo, pos) in enumerate(ordered):
        end = ordered[j + 1][1] if j + 1 < len(ordered) else pos + 80
        out["geographies"][geo] = _parse_section(lines, pos, end)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="Dump the raw scrape as JSON instead of paste-ready literals.")
    args = ap.parse_args(argv)

    scraped: dict = {}
    for year, slug in sorted(YEAR_SLUGS.items()):
        try:
            scraped[year] = scrape_year(year, slug)
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  !! {year}: {exc}", file=sys.stderr)

    if not scraped:
        print("No years scraped; aborting.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(scraped, indent=1, sort_keys=True))
        return 0

    # ---- integrity checks -------------------------------------------------
    problems = []
    for year, rec in sorted(scraped.items()):
        geos = rec["geographies"]
        state = geos.get("Statewide", {})
        state_total = state.get("total_published")
        county_sum = sum(
            geos[g]["total_published"] or 0
            for g in COUNTY_LABEL if g in geos
        )
        if state_total is not None and county_sum != state_total:
            problems.append(
                f"{year}: counties sum to {county_sum:,} but Statewide reports "
                f"{state_total:,} (delta {county_sum - state_total:+,})")

    as_of_dates = {rec["as_of"] for rec in scraped.values() if rec["as_of"]}

    print("# --- paste into analyses/forecast_rxkids_2028.py ---")
    print(f"# Scraped from {BASE}")
    print(f"# 'As of' lines seen: {sorted(as_of_dates) or '(none published)'}")
    print()
    print("DOH_SNAPSHOT = \"YYYY-MM-DD\"   # <- set from the 'As of' date above")
    print("HI_DOH_BIRTHS_MONTHLY = {")
    for year, rec in sorted(scraped.items()):
        m = rec["geographies"].get("Statewide", {}).get("months", {})
        row = [m.get(mo, 0) for mo in MONTHS]
        print(f"    {year}: {row},")
    print("}")
    print()

    # ---- county shares, COMPLETE years only -------------------------------
    complete = [
        y for y, rec in scraped.items()
        if len(rec["geographies"].get("Statewide", {}).get("months", {})) == 12
        and all(v > 0 for v in rec["geographies"]["Statewide"]["months"].values())
    ]
    totals = {label: 0 for label in COUNTY_LABEL.values()}
    for y in complete:
        for geo, label in COUNTY_LABEL.items():
            rec = scraped[y]["geographies"].get(geo)
            if rec and rec["total_published"]:
                totals[label] += rec["total_published"]
    grand = sum(totals.values())

    print(f"# Aggregate county shares over COMPLETE years {sorted(complete)}")
    print("# (partial trailing years excluded — they bias toward fast-registering counties)")
    print("DOH_COUNTY_SHARE = {")
    for label, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f'    "{label}": {v / grand:.6f},')
    print("}")
    print(f"# shares sum to {sum(totals.values()) / grand:.6f}")

    if problems:
        print("\n!! INTEGRITY WARNINGS", file=sys.stderr)
        for p in problems:
            print("   " + p, file=sys.stderr)
        return 2
    print("\n# integrity: county totals reconcile to Statewide in every year OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
