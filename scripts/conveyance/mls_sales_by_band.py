"""MLS sales of houses and condominiums by price band and county, FY2023-FY2026.

Usage:
  python3 scripts/conveyance/mls_sales_by_band.py          # parse the cached reports, write the table

Reading the reports needs PyMuPDF (fitz), which the repo's .venv does not
carry: run it with a Python that has it (the system python3 does). The
module imports fitz only when it opens a report, so its pure helpers import,
and are tested (tests/test_mls_sales_by_band.py), without it. Writes
packages/tax_modeler/src/tax_modeler/data/raw/conveyance/
mls_sales_by_band_fy2023_2026.csv, which forecast_conveyance_sb3028.py uses to calibrate each county's synthetic sales
by price band. Columns: county; band_lo, band_hi (dollars; 'inf' on the open
$10M+ band, which pandas reads as float inf); mls_per_year, the FY2023-26
mean of MLS sales of houses and condominiums (land removed), with mls_low
and mls_high; coverage_rel_maui; note.

Sources (read 2026-09-25):
  - Title Guaranty Hawaiʻi (TG), Residential Sales Reports (MLS data); the
    170 file names are in monthly_file() and eoy_file(). Monthly, Jan
    2022-Jun 2026, for Hawaiʻi, Maui (the county, with Lānaʻi and Molokaʻi)
    and Oʻahu: page 2 "Sales by price range", one 100%-stacked bar per
    region in <$300K, $300-649K, $650-999K, $1M-2.9M and $3M+, houses,
    condominiums and land combined. Year-end 2024 and 2025
    ("RSR-<island>-EOY-<year>"), all four islands: the "YTD trend"
    house/condo/land counts for 2022-2025 and the "Historical trend" house
    and condominium shares by band; for Hawaiʻi, Maui and Oʻahu also the
    year's price-range chart. See "Title Guaranty's terms" below.
  - luxury_report_counts.csv, hand-transcribed with document, PDF page and
    period for every number. Hawaiʻi Life luxury market reports (2022
    year-end, 2025 Q4 YTD and 2026 midyear, smarket.hawaiilife.com): sales
    at $3-5.99M, $6-9.99M and $10M+ by island and by type, and (basis
    'chart') the $10M+ monthly closed-sales lines of the midyear edition's
    "10 Year History" charts summed by half-year; those lines match the 2025
    edition's month for month over 2016-2025, and their H1 2026 totals match
    the midyear text. List Sotheby's International Realty's Oʻahu quarterly
    luxury reports, 2022 Q3-2026 Q2 (listsothebysrealty.com): houses at
    $2-3M, $3-5M, $5-10M and $10M+; condominiums at $1-2.5M, $2.5M+, $5M+.

Title Guaranty's terms and permission:
  The 170 reports in runs/conveyance_sb3028/mls_cache/ (gitignored) were not
  saved by hand. They are APFS clones (cp -c) of the research round's
  automated download, a script that fetched them with Python requests, six
  at a time, under a browser User-Agent string. TG's site terms (FNF Terms
  of Use, last updated August 15, 2021; read 2026-09-25) bar using "any
  robot, spider, or other automatic devise [sic]" to access or copy the
  site; copying it or using any tool or process "(manual or automatic) to
  gather, extract, monitor, or copy any information" on it; and creating or
  publishing "any link to any page of the Sites". The download was
  automatic, and parsing the reports, by this script or by hand, extracts
  their information. Hawaiʻi Appleseed has Title Guaranty's permission to
  use and publish data from these reports (confirmed 2026-09-25). This
  script still downloads nothing and carries no link to the site; if
  reports are missing it names the files and stops.

Method:
  1. Price-range charts are read from the drawn rectangles: fill colour ->
     band through the legend swatches (within 0.08 RGB); a bar's total is
     the sum of its printed segment labels, and segment width / bar width x
     total must reproduce those labels (as a set: labels of thin segments
     stray from them). Each bar must equal its region's "#" column, in order.
     Months are summed into July-June fiscal years.
  2. Land is removed by band. Houses+condos in a calendar year = each type's
     year-end share of sales in the band x its year-end count; land share =
     1 - houses+condos / all types, all types being the year-end chart (2024,
     2025) or the months' sum (2022, 2023). A fiscal year takes the mean of
     its two calendar years; FY2026's second half reuses 2025 at $1-3M and
     takes Hawaiʻi Life's H1 2026 land share at $3M+.
  3. Kauaʻi's monthly charts are not used: from December 2023 their bars
     contradict the same report's dollar table. Its houses+condos are the
     year-end shares x counts, each fiscal year half of each calendar year;
     FY2026's second half repeats half of 2025 at $1-3M and takes Hawaiʻi
     Life's H1 2026 houses+condos at $3M+.
  4. $10M+ houses+condos are Hawaiʻi Life's chart counts by fiscal year.
     Hawaiʻi's 2025 house months are scaled by 0.75, as its chart has 23
     houses and its text 17. Oʻahu's houses come from List Sotheby's (whose
     2026 Q1 post gives no $10M+ figure: 3 is taken, Hawaiʻi Life's H1 2026
     houses less List Sotheby's Q2).
  5. $3-10M = $3M+ less $10M+, split at $6M by Hawaiʻi Life's pooled share of
     $6-9.99M sales within $3-9.99M (all types; 2022, 2025, H1 2026).
  6. coverage_rel_maui: the county's recorded/MLS ratio over Maui's. The
     model takes Maui's ratio from its recorded file (owner + nonowner
     schedules, vacant residential lots included) over the Maui rows here;
     this script prints it for the file on disk (verified 2026-09-25 on the
     extract committed 2026-09-24: 1.137 at $1-3M, 1.23 at $3-10M, 1.27 at
     $10M+). Hawaiʻi and Kauaʻi 1.0: land shares like Maui's. Oʻahu x0.97 at
     $1-2M, x1.10 at $2-3M, x1.05 at $3-6M, x0.94 at $6-10M (tower closings
     off the MLS, such as Victoria Place and Kōʻula; late MLS entries; almost
     no vacant lots) and 1.06/1.27 at $10M+ (improved homes only), each
     rescaled for the vacant lots Maui's current extract still counts
     (VACANT_SHARE); its $1-3M
     factor weights $1-2M and $2-3M by MLS count, $2-3M being List Sotheby's
     houses + its condominiums at $2.5M+ less TG's at $3M+ + $2-2.5M on a
     Pareto tail through List Sotheby's $1M+ and $2.5M+ condominium counts.
  7. mls_low, mls_high: the recorded-sales range over the central coverage,
     so they carry count, split and coverage uncertainty in MLS units:
     counts +-3% ($1-3M), +-5% ($3-10M); Maui coverage 1.095-1.17 ($1-3M;
     the low end improved homes only), 1.128-1.36 ($3-10M), 1.06-1.45
     ($10M+; Oʻahu 1.0-1.2); Oʻahu multipliers 0.93-1.03, 0.97-1.22,
     0.94-1.15, 0.88-1.02; +10% on Hawaiʻi's high end at $3M+ (resort
     developer sales may bypass the MLS); the $6M split at the lowest and
     highest report's share; the $10M+ count between its readings (Hawaiʻi's
     chart as drawn; Oʻahu's houses from Hawaiʻi Life). Maui's rows give its
     fiscal-year minimum and maximum, as the model uses Maui's own sales.

Known defects:
  - TG's charts sometimes leave a region out: North Hilo in Hawaiʻi's Jul
    and Aug 2024 reports, Molokaʻi in Maui's Jul 2024 and Jul 2025 reports
    and its 2024 year-end chart (16 sales in FY2025-26, 71 in that chart,
    from districts with few $1M+ sales). They are dropped.
  - Monthly reports miss late MLS entries that the year-end editions pick up
    (Oʻahu 2025: 384 sales, 135 at $1-3M), so fiscal-year sums run low; this
    sits inside Oʻahu's multipliers. The 2022-2023 land shares set year-end
    house/condo counts against month sums.
  - Kauaʻi's fiscal years are calendar-year blends, and its FY2026 $1-3M
    second half is a copy of 2025's.
  - Coverage rests on Maui alone, and its recorded "residential" includes
    vacant residential lots and developer and related-party sales but not
    homes on agricultural land classes.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import fitz  # PyMuPDF: imported for real only in _open(), so the pure helpers import without it

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "runs" / "conveyance_sb3028" / "mls_cache"
RAW = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
LUXURY = RAW / "luxury_report_counts.csv"
OUT = RAW / "mls_sales_by_band_fy2023_2026.csv"

COUNTY = {"Oahu": "Honolulu", "Hawaii": "Hawaii", "Kauai": "Kauai", "Maui": "Maui"}   # TG island -> model county
MONTHLY = ("Hawaii", "Maui", "Oahu")          # Kauaʻi's monthly charts are not used (see Method 3)
FYS = (2023, 2024, 2025, 2026)
YEARS = (2022, 2023, 2024, 2025)
B13, B3P = 3, 4                               # TG's $1M-2.9M and $3M+ bands
SWATCH_TOL = 0.08                             # RGB distance, bar fill to legend swatch
HAWAII_2025_HOUSES_10M = 0.75                 # Hawaiʻi Life 2025: text 17 houses at $10M+, chart 23
OAHU_Q1_2026_HOUSES_10M = 3                   # Hawaiʻi Life H1 2026 houses (4) less List Sotheby's Q2 2026 (1)

BANDS = (("1_3", 1e6, 3e6), ("3_6", 3e6, 6e6), ("6_10", 6e6, 1e7), ("10p", 1e7, np.inf))    # the model's bands
GROUP = {"1_3": "1_3", "3_6": "3_10", "6_10": "3_10", "10p": "10p"}                       # coverage groups

# Verified coverage, as (central, low, high): Maui's recorded/MLS ratio by band group, Oʻahu's multipliers
# on it, and Oʻahu's own ratio at $10M+ (improved homes only, as it sells almost no $10M+ lots).
MAUI_COV = {"1_3": (1.137, 1.095, 1.17), "3_10": (1.23, 1.128, 1.36), "10p": (1.27, 1.06, 1.45)}
OAHU_ADJ = {"1_2": (0.97, 0.93, 1.03), "2_3": (1.10, 0.97, 1.22), "3_6": (1.05, 0.94, 1.15), "6_10": (0.94, 0.88, 1.02)}
OAHU_COV_10P = (1.06, 1.0, 1.2)
# Those were set against Maui's recorded file as committed 2026-09-24, whose
# home sales included every vacant residential lot. The current extract keeps
# only lots bought on schedule (2) (sales of parcels with no building value:
# 2.8% of $1-3M, 7.5% of $3-10M, 3.4% of $10M+ home sales, FY2023-26), so
# Oʻahu, which sells almost no lots, is rescaled by the change in Maui's
# vacant share (3.7%, 8.3% and 16% before).
VACANT_SHARE = {"1_3": (0.037, 0.028), "3_10": (0.083, 0.075), "10p": (0.16, 0.034)}   # (before, now)
HAWAII_HIGH_3P = 1.10                         # resort developer sales may bypass the MLS
COUNT_ERR = {"1_3": 0.03, "3_10": 0.05, "10p": 0.0}


# --------------------------------------------------------------------------- files

def monthly_file(island: str, year: int, month: int) -> str:
    """The monthly report's file name as published (a few break the pattern)."""
    stem = {("Maui", 2023, 11): "Maui-Islandv2-11.2023", ("Maui", 2025, 6): "Maui-Island-6.2025-Nicole"}.get(
        (island, year, month), f"{island}-Island-{month}.{year}")
    if (year, month) == (2022, 8):
        stem += "-1"
    if island == "Hawaii" and (year, month) >= (2026, 4):
        stem = f"Hawaii-{month}.{year}"
    return f"Residential-Sales-Report-{stem}.pdf"


def eoy_file(island: str, year: int) -> str:
    return f"RSR-{island}-EOY-{year}.pdf"


def months() -> list[tuple[int, int]]:
    return [(y, m) for y in range(2022, 2027) for m in range(1, 13) if (y, m) <= (2026, 6)]


def needed() -> list[str]:
    return ([monthly_file(i, y, m) for i in MONTHLY for y, m in months()]
            + [eoy_file(i, y) for i in COUNTY for y in (2024, 2025)])


def _open(name: str) -> fitz.Document:
    """A cached report. PyMuPDF is imported here, not at module load, so the pure helpers import without it."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        sys.exit("PyMuPDF (fitz) is required; the repo .venv lacks it, the system python3 has it")
    return fitz.open(CACHE / name)


def page_with(doc: fitz.Document, *words: str) -> fitz.Page:
    for p in doc:
        have = {w[4] for w in p.get_text("words")}
        if all(w in have for w in words):
            return p
    raise ValueError(f"{doc.name}: no page with {words}")


# --------------------------------------------------------------------------- parsers

def _num(w: tuple) -> bool:
    return re.fullmatch(r"[\d,]+", w[4]) is not None


def _clusters(vals: list[float], gap: float) -> list[float]:
    vals = sorted(vals)
    groups = [[vals[0]]]
    for v in vals[1:]:
        (groups[-1].append(v) if v - groups[-1][-1] <= gap else groups.append([v]))
    return [sum(g) / len(g) for g in groups]


def price_bands(page: fitz.Page) -> list[np.ndarray]:
    """Each region's sales in TG's five bands, top to bottom, from the 'Sales by price range' bars: a bar's
    total is the sum of its printed segment labels, and its drawn segment widths must reproduce them."""
    words = page.get_text("words")
    hdr = max((w for w in words if w[4] == "RANGE"), key=lambda w: w[1])
    legend_y = min(w[1] for w in words if w[1] > hdr[3] + 20 and w[4] in ("$3M+", "$3.1M+"))
    x_min = hdr[0] - 130
    draws = [d for d in page.get_drawings() if d.get("fill")]
    swatches = sorted((it[1].x0, tuple(d["fill"])) for d in draws for it in d["items"] if it[0] == "re"
                      and it[1].width < 8 and it[1].height < 8 and legend_y - 4 < it[1].y0 < legend_y + 14
                      and it[1].x0 > x_min)
    colours = [c for i, (x, c) in enumerate(swatches) if i == 0 or x - swatches[i - 1][0] > 5]
    if len(colours) != 5:
        raise ValueError(f"{page.parent.name}: {len(colours)} legend swatches")
    segs = []
    for d in draws:
        dist = [float(np.linalg.norm(np.subtract(d["fill"], c))) for c in colours]
        band = int(np.argmin(dist))
        if dist[band] <= SWATCH_TOL:
            segs += [(band, it[1]) for it in d["items"] if it[0] == "re" and it[1].y0 > hdr[3]
                     and it[1].y1 < legend_y and it[1].height > 4 and it[1].width > 0.05 and it[1].x0 > x_min]
    labels = [w for w in words if _num(w) and hdr[3] < w[1] < legend_y and w[0] >= min(r.x0 for _, r in segs) - 3]
    out = []
    for yc in _clusters([(r.y0 + r.y1) / 2 for _, r in segs], 3):      # one bar per region
        rs = [(b, r) for b, r in segs if abs((r.y0 + r.y1) / 2 - yc) < 3]
        x0, x1 = min(r.x0 for _, r in rs), max(r.x1 for _, r in rs)
        share = np.zeros(5)
        for b, r in rs:
            share[b] += r.width / (x1 - x0)
        printed = sorted(int(w[4].replace(",", "")) for w in labels if abs((w[1] + w[3]) / 2 - yc) <= 7)
        vals = np.round(share * sum(printed))
        if sorted(vals[vals > 0]) != printed:    # labels of thin segments stray, so match them as a set
            raise ValueError(f"{page.parent.name}: bar at y={yc:.0f} does not reproduce its labels")
        out.append(vals)
    return out


def _ytd_area(words: list[tuple]) -> tuple[float, float, float, float]:
    """(top, bottom, left, right) of the 'YTD trend' bar grid; the '#' column starts just right of it."""
    hash_w = min((w for w in words if w[4] == "#"), key=lambda w: w[1])
    sf_w = next(w for w in words if w[4] == "SINGLE" and abs(w[1] - hash_w[1]) < 5)
    return hash_w[3], min(w[1] for w in words if "Year-over-year" in w[4]), sf_w[0] - 15, hash_w[0] - 12


def hash_column(page: fitz.Page) -> list[int]:
    """The '#' column: each region's house, condominium and land sales in the report's period (the month,
    or the year in a year-end edition)."""
    words = page.get_text("words")
    top, bottom, _, x_hi = _ytd_area(words)
    return [int(w[4].replace(",", "")) for w in sorted(
        (w for w in words if _num(w) and x_hi < w[0] < x_hi + 34 and top < w[1] < bottom), key=lambda w: w[1])]


def ytd_trend(page: fitz.Page) -> dict[str, np.ndarray]:
    """Island house/condo/land sales in a year-end edition's year and the three before it, from the 'YTD
    trend' bar labels; each region's last year must equal its '#' column. (Monthly editions set year-to-date
    bars beside the month's '#' column, so this check holds only at year end.)"""
    words = page.get_text("words")
    top, bottom, x_lo, x_hi = _ytd_area(words)
    bars = [it[1] for d in page.get_drawings() if d.get("fill") for it in d["items"] if it[0] == "re"
            and x_lo < it[1].x0 and it[1].x1 < x_hi and top < it[1].y1 < bottom and 5 < it[1].width < 20]
    baselines = _clusters([r.y1 for r in bars], 3)                      # one per region
    rows: list[list[tuple]] = [[] for _ in baselines]
    for w in words:
        if _num(w) and x_lo < w[0] and w[2] < x_hi and top < w[1] < bottom:
            rows[next(i for i, b in enumerate(baselines) if b >= (w[1] + w[3]) / 2 - 1)].append(w)
    hash_vals = hash_column(page)
    grid = []
    for i, ws in enumerate(rows):
        cells: list[list] = []                  # [x centre, digits, bottom]; a wrapped label continues the one above
        for w in sorted(ws, key=lambda w: w[1]):
            xc = (w[0] + w[2]) / 2
            hit = next((c for c in cells if abs(c[0] - xc) < 3 and w[1] - c[2] < 12), None)
            if hit:
                hit[1] += w[4].replace(",", "")
                hit[2] = w[3]
            else:
                cells.append([xc, w[4].replace(",", ""), w[3]])
        vals = [int(c[1]) for c in sorted(cells)]
        if len(vals) != 12 or vals[3::4] != hash_vals[3 * i:3 * i + 3]:
            raise ValueError(f"{page.parent.name}: YTD trend region {i} does not match its '#' column")
        grid.append(vals)
    g = np.array(grid).sum(axis=0)
    return {"sf": g[0:4], "condo": g[4:8], "land": g[8:12]}


def hist_shares(page: fitz.Page) -> dict[tuple[str, int], np.ndarray]:
    """(type, year) -> % of that year's house (left) or condominium (right) sales in each TG band, from the
    'Historical trend' grouped bars, heights read against the % gridline labels."""
    words = page.get_text("words")
    top = max(w[3] for w in words if w[4] == "RANGE")
    ends = sorted((w for w in words if w[4] in ("<300K", "3M+") and w[1] > top), key=lambda w: w[0])
    mid = (ends[1][2] + ends[2][0]) / 2
    draws = page.get_drawings()
    out: dict[tuple[str, int], np.ndarray] = {}
    for kind, (first, last) in (("sf", ends[0:2]), ("condo", ends[2:4])):
        side = (lambda x: x < mid) if kind == "sf" else (lambda x: x >= mid)
        yrs = [w for w in words if re.fullmatch(r"20\d\d", w[4]) and w[1] > top and side(w[0])]
        legend_y = max(w[1] for w in yrs)
        yrs = [w for w in yrs if abs(w[1] - legend_y) < 3]
        colour_year = {}
        for d in draws:                         # each legend swatch sits just left of its year
            r = d["rect"]
            if d.get("fill") and r.width < 6 and r.height < 6 and abs(r.y0 - legend_y) < 8 and side(r.x0):
                nxt = min((w for w in yrs if w[0] > r.x0), key=lambda w: w[0] - r.x0, default=None)
                if nxt:
                    colour_year[tuple(round(c, 3) for c in d["fill"])] = int(nxt[4])
        pct = [w for w in words if re.fullmatch(r"\d+%", w[4]) and w[1] > top and side(w[0])]
        axis_x = Counter(round(w[2] / 3) for w in pct).most_common(1)[0][0]
        pct = [w for w in pct if abs(round(w[2] / 3) - axis_x) <= 1]
        pct_per_pt = np.polyfit([(w[1] + w[3]) / 2 for w in pct], [int(w[4][:-1]) for w in pct], 1)[0]
        bars = [(it[1], colour_year[col]) for d in draws if d.get("fill")
                for col in [tuple(round(c, 3) for c in d["fill"])] if col in colour_year
                for it in d["items"] if it[0] == "re" and it[1].y1 <= legend_y - 5 and it[1].height > 0.05
                and side(it[1].x0)]
        base = max(r.y1 for r, _ in bars)
        x0, x4 = (first[0] + first[2]) / 2, (last[0] + last[2]) / 2       # centres of the end bands
        for r, year in bars:
            band = int(np.clip(round(((r.x0 + r.x1) / 2 - x0) / ((x4 - x0) / 4)), 0, 4))
            out.setdefault((kind, year), np.zeros(5))[band] += (r.y0 - base) * pct_per_pt
    return out


# --------------------------------------------------------------------------- inputs

OMITTED: list[tuple[str, int]] = []           # (report, sales) of regions a price-range chart leaves out


def checked_bands(page: fitz.Page) -> np.ndarray:
    """The island's sales by band. Each bar must total its region's '#' column, in the same order; a
    region may be missing from the chart (TG drops one now and then) and is then recorded in OMITTED."""
    h = hash_column(page)
    regions = [sum(h[i:i + 3]) for i in range(0, len(h), 3)]
    bars, matched = price_bands(page), []
    for b in bars:
        i = next((i for i in range(matched[-1] + 1 if matched else 0, len(regions)) if regions[i] == b.sum()), None)
        if i is None:
            raise ValueError(f"{page.parent.name}: bars do not match the '#' column")
        matched.append(i)
    OMITTED.extend((Path(page.parent.name).name, n) for i, n in enumerate(regions) if n and i not in matched)
    return np.sum(bars, axis=0)


def tg_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(monthly all-types sales by band; calendar-year all-types sales by band; calendar-year sales by type
    and band, houses and condos only), the band columns numbered 0-4 as TG's bands."""
    rows = []
    for island in MONTHLY:
        for y, m in months():
            doc = _open(monthly_file(island, y, m))
            page = page_with(doc, "REGIONAL", "RANGE")
            rows.append({"island": island, "year": y, "month": m, **dict(enumerate(checked_bands(page)))})
    monthly = pd.DataFrame(rows)
    cal, typ = [], []
    for island in COUNTY:
        docs = {y: _open(eoy_file(island, y)) for y in (2024, 2025)}
        counts = ytd_trend(page_with(docs[2025], "REGIONAL", "RANGE"))
        counts24 = ytd_trend(page_with(docs[2024], "REGIONAL", "RANGE"))
        if any((counts[k][:3] != counts24[k][1:]).any() for k in counts):
            raise ValueError(f"{island}: the 2024 and 2025 year-end editions disagree on 2022-2024 counts")
        shares = hist_shares(page_with(docs[2025], "HISTORICAL", "RANGE"))
        for i, y in enumerate(YEARS):
            for t in ("sf", "condo"):
                if abs(shares[(t, y)].sum() - 100) > 0.5:
                    raise ValueError(f"{island} {t} {y}: shares sum to {shares[(t, y)].sum():.1f}%")
                n = shares[(t, y)] / 100 * counts[t][i]
                typ.append({"island": island, "year": y, "type": t, **dict(enumerate(n))})
    for island in MONTHLY:                      # all types: year-end charts, else the months' sum
        for y in (2024, 2025):
            page = page_with(_open(eoy_file(island, y)), "REGIONAL", "RANGE")
            cal.append({"island": island, "year": y, **dict(enumerate(checked_bands(page)))})
        for y in (2022, 2023):
            cal.append({"island": island, "year": y,
                        **monthly[(monthly.island == island) & (monthly.year == y)][list(range(5))].sum().to_dict()})
    return (monthly, pd.DataFrame(cal).set_index(["island", "year"]).sort_index(),
            pd.DataFrame(typ).set_index(["island", "year", "type"]).sort_index())


def month_fy(year: int | pd.Series, month: int | pd.Series) -> int | pd.Series:
    """The July-June fiscal year of a month (ints or Series): July 2025 -> 2026, June 2026 -> 2026."""
    return year + (month >= 7)


def fiscal_year(period: str) -> float:
    """'2025' -> nan (a calendar year), '2025H2' -> 2026, '2025Q3' -> 2026."""
    m = re.fullmatch(r"(\d{4})(?:H([12])|Q([1-4]))?", period)
    if m[2]:
        return int(m[1]) + (m[2] == "2")
    if m[3]:
        return int(m[1]) + (int(m[3]) >= 3)
    return np.nan


def fy_blend(cy: pd.Series, h1_2026: float | None = None) -> float:
    """FY2023-26 mean from calendar years 2022-25, each fiscal year half of each of its calendar years;
    FY2026's second half (H1 2026) is h1_2026, or half of 2025."""
    last = cy[2025] / 2 if h1_2026 is None else h1_2026
    return (cy[2022] / 2 + cy[2023] + cy[2024] + cy[2025] + last) / len(FYS)


def ten_plus(lx: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """$10M+ MLS houses+condos by fiscal year and county: (as used, the alternative reading). The
    alternative is Hawaiʻi's 2025 chart unscaled and Oʻahu's houses from Hawaiʻi Life's chart."""
    ch = lx[(lx.basis == "chart") & lx.fy.isin(FYS)]
    raw = ch.pivot_table(index="fy", columns=["county", "type"], values="count", aggfunc="sum")
    used = raw.copy()
    hi25 = ch[(ch.county == "Hawaii") & (ch.type == "house") & ch.period.str.startswith("2025")]
    scaled_out = hi25.groupby("fy")["count"].sum().reindex(used.index, fill_value=0) * (1 - HAWAII_2025_HOUSES_10M)
    used["Hawaii", "house"] -= scaled_out
    q = lx[(lx.source == "list_sothebys") & (lx.type == "house") & (lx.band_lo == 1e7) & lx.fy.isin(FYS)]
    lsir = q.groupby("fy")["count"].sum()
    lsir[2026] += OAHU_Q1_2026_HOUSES_10M
    out, alt = used.T.groupby(level="county").sum().T, raw.T.groupby(level="county").sum().T
    out["Honolulu"] = lsir + used["Honolulu", "condo"]
    return out, alt


def share_6_10(lx: pd.DataFrame) -> dict[str, tuple[float, float, float]]:
    """Share of $6-9.99M within $3-9.99M sales, all types, Hawaiʻi Life text: (pooled, lowest, highest)."""
    t = lx[(lx.source == "hawaii_life") & (lx.basis == "text") & (lx.type == "all") & (lx.band_lo < 1e7)]
    p = t.pivot_table(index=["county", "period"], columns="band_lo", values="count")
    s = p[6e6] / (p[3e6] + p[6e6])
    g = p.groupby("county").sum()
    return {c: (g.loc[c, 6e6] / g.loc[c].sum(), s[c].min(), s[c].max()) for c in g.index}


def oahu_2_3m(lx: pd.DataFrame, typ: pd.DataFrame) -> float:
    """Oʻahu MLS houses+condos a year at $2-3M: List Sotheby's houses; condominiums at $2.5M+ (List Sotheby's)
    less $3M+ (TG year-end), plus $2-2.5M interpolated on a Pareto tail through List Sotheby's $1M+ and $2.5M+."""
    q = lx[(lx.source == "list_sothebys") & lx.fy.isin(FYS)]
    by = q.pivot_table(index="period", columns=["type", "band_lo"], values="count")
    houses, c25 = by["house", 2e6].sum() / len(FYS), by["condo", 2.5e6].sum() / len(FYS)
    both = by["condo"][[1e6, 2.5e6]].dropna()
    n1, n25 = both.sum().sum(), both[2.5e6].sum()
    alpha = np.log(n1 / n25) / np.log(2.5)
    c2_25 = (n1 * 2 ** -alpha - n25) / len(both) * 4
    condo_3p = fy_blend(typ.xs(("Oahu", "condo"), level=["island", "type"])[B3P])
    return houses + c25 - condo_3p + c2_25


def _vacant_rescale(key: str) -> float:
    """Oʻahu's factor for the change in Maui's vacant-lot share of home sales in key's coverage group."""
    before, now = VACANT_SHARE[GROUP[key]]
    return (1 - now) / (1 - before)


def coverage(county: str, key: str, oahu_2_3_share: float) -> tuple[float, float, float]:
    """(coverage_rel_maui, low, high): the county's coverage relative to Maui's, and its low and high
    coverage as ratios to its central coverage."""
    cov, lo, hi = MAUI_COV[GROUP[key]]
    lo, hi = lo / cov, hi / cov
    if county == "Honolulu":
        v = _vacant_rescale(key)
        if key == "10p":
            return OAHU_COV_10P[0] / cov * v, OAHU_COV_10P[1] / OAHU_COV_10P[0], OAHU_COV_10P[2] / OAHU_COV_10P[0]
        adj = OAHU_ADJ[key] if key != "1_3" else \
            (1 - oahu_2_3_share) * np.array(OAHU_ADJ["1_2"]) + oahu_2_3_share * np.array(OAHU_ADJ["2_3"])
        return adj[0] * v, lo * adj[1] / adj[0], hi * adj[2] / adj[0]
    if county == "Hawaii" and key != "1_3":
        hi *= HAWAII_HIGH_3P
    return 1.0, lo, hi


def maui_recorded() -> pd.DataFrame:
    """Maui's recorded residential sales (owner + nonowner schedules) by fiscal year and model band."""
    b = pd.read_csv(RAW / "maui_sales_bins_fy2023_2026.csv")
    o = pd.read_csv(RAW / "maui_sales_over_10m_fy2023_2026.csv").assign(bin_lo=lambda d: d["price"], count=1)
    x = pd.concat([b[["fy", "category", "bin_lo", "count"]], o[["fy", "category", "bin_lo", "count"]]])
    x = x[x.category.isin(["owner", "nonowner"]) & (x.bin_lo >= 1e6)]
    x["band"] = pd.cut(x.bin_lo, [1e6, 3e6, 6e6, 1e7, np.inf], right=False, labels=["1_3", "3_6", "6_10", "10p"])
    return x.pivot_table(index="fy", columns="band", values="count", aggfunc="sum", observed=False)


# --------------------------------------------------------------------------- table

NOTES = {
    "Honolulu": {
        "1_3": "TG monthly FY sums less land; coverage weights $1-2M x0.97 and $2-3M x1.10 by MLS count, rescaled for Maui vacant lots",
        "3_6": "TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; coverage x1.05 (towers closing off the MLS), rescaled for Maui vacant lots",
        "6_10": "TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; coverage x0.94, rescaled for Maui vacant lots",
        "10p": "List Sotheby's houses + Hawaiʻi Life condos; coverage 1.06/1.27 (no vacant $10M+ lots), rescaled for Maui vacant lots"},
    "Hawaii": {
        "1_3": "TG monthly FY sums less land (South Hilo rows of Jun 2026 included)",
        "3_6": "TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; high end +10% for resort sales",
        "6_10": "TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; high end +10% for resort sales",
        "10p": "Hawaiʻi Life charts, 2025 houses x0.75 to match its text; high = chart as drawn"},
    "Kauai": {
        "1_3": "TG year-end house/condo shares x counts, calendar-year blend (monthly charts unusable from Dec 2023)",
        "3_6": "TG year-end shares x counts and Hawaiʻi Life H1 2026, less $10M+, split at $6M by Hawaiʻi Life",
        "6_10": "TG year-end shares x counts and Hawaiʻi Life H1 2026, less $10M+, split at $6M by Hawaiʻi Life",
        "10p": "Hawaiʻi Life charts (no condominium sales at $10M+)"},
    "Maui": {
        "1_3": "Maui's own MLS count (TG monthly FY sums less land); low/high = FY min/max",
        "3_6": "Maui's own MLS count; TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; low/high = FY min/max",
        "6_10": "Maui's own MLS count; TG $3M+ less land and $10M+, split at $6M by Hawaiʻi Life; low/high = FY min/max",
        "10p": "Maui's own MLS count, Hawaiʻi Life charts; low/high = FY min/max"},
}


def main() -> None:
    argparse.ArgumentParser(description="MLS sales by price band and county, FY2023-FY2026").parse_args()
    files = needed()
    missing = [f for f in files if not (CACHE / f).exists()]
    if missing:
        print("\n".join(missing), file=sys.stderr)
        sys.exit(f"{len(missing)} of {len(files)} Title Guaranty reports, listed above, are missing from "
                 f"{CACHE.relative_to(REPO)}; see \"Title Guaranty's terms\" in this script's docstring")

    monthly, cal, typ = tg_tables()
    hc = typ.groupby(level=["island", "year"]).sum()                     # houses+condos, calendar year
    land = 1 - hc / cal
    lx = pd.read_csv(LUXURY, dtype={"period": str})
    lx["fy"] = lx["period"].map(fiscal_year)
    h1 = lx[(lx.period == "2026H1") & (lx.basis == "text") & (lx.band_lo == 3e6) & (lx.band_hi == np.inf)]
    h1 = h1.pivot_table(index="county", columns="type", values="count")      # H1 2026 at $3M+ by type
    monthly["fy"] = month_fy(monthly.year, monthly.month)
    fy = monthly[monthly.fy.isin(FYS)].groupby(["island", "fy"])[[B13, B3P]].sum()
    ten, ten_alt = ten_plus(lx)
    s610 = share_6_10(lx)

    mls: dict[str, pd.DataFrame] = {}                                    # county -> FY x ($1-3M, $3M+)
    for island, county in COUNTY.items():
        if island in MONTHLY:
            rows = {}
            for f in FYS:
                later = land.loc[(island, f)] if f <= 2025 else land.loc[(island, 2025)].copy()
                if f == 2026:
                    later[B3P] = h1.loc[county, "land"] / h1.loc[county].sum()
                share = (land.loc[(island, f - 1)] + later) / 2
                rows[f] = fy.loc[(island, f)] * (1 - share[[B13, B3P]])
            mls[county] = pd.DataFrame(rows).T
        else:
            cy = hc.loc[island]
            h1_3p = h1.loc[county, ["house", "condo"]].sum()
            blend = {B13: fy_blend(cy[B13]), B3P: fy_blend(cy[B3P], h1_3p)}
            mls[county] = pd.DataFrame([blend] * len(FYS), index=list(FYS))   # only the mean is meaningful
    n23 = oahu_2_3m(lx, typ)

    out = []
    for county, m in mls.items():
        n13, t10, t10_alt = m[B13].mean(), ten[county].mean(), ten_alt[county].mean()
        n310 = m[B3P].mean() - t10
        s, s_lo, s_hi = s610[county]
        counts = {"1_3": (n13, n13, n13), "3_6": (n310 * (1 - s), n310 * (1 - s_hi), n310 * (1 - s_lo)),
                  "6_10": (n310 * s, n310 * s_lo, n310 * s_hi), "10p": (t10, min(t10, t10_alt), max(t10, t10_alt))}
        by_fy = {"1_3": m[B13], "3_6": (m[B3P] - ten[county]) * (1 - s), "6_10": (m[B3P] - ten[county]) * s,
                 "10p": ten[county]}
        for key, lo, hi in BANDS:
            (c, c_lo, c_hi), e = counts[key], COUNT_ERR[GROUP[key]]
            rel, r_lo, r_hi = coverage(county, key, n23 / n13)
            low, high = c_lo * (1 - e) * r_lo, c_hi * (1 + e) * r_hi
            if county == "Maui":
                low, high = by_fy[key].min(), by_fy[key].max()
            out.append({"county": county, "band_lo": int(lo), "band_hi": hi, "mls_per_year": c, "mls_low": low,
                        "mls_high": high, "coverage_rel_maui": rel, "note": NOTES[county][key]})
    table = pd.DataFrame(out)
    table["band_hi"] = table["band_hi"].map(lambda v: "inf" if np.isinf(v) else str(int(v)))
    table.round({"mls_per_year": 1, "mls_low": 1, "mls_high": 1, "coverage_rel_maui": 3}).to_csv(OUT, index=False)

    pd.set_option("display.width", 200)
    names = {B13: "$1M-2.9M", B3P: "$3M+"}
    print("TG monthly, all types, July-June fiscal years:")
    print(fy.rename(columns=names).unstack("island").to_string())
    print("Regions left out of a chart: " + ", ".join(f"{f} ({n} sales)" for f, n in OMITTED))
    print("\nLand share of MLS sales by calendar year:")
    print(land[[B13, B3P]].rename(columns=names).unstack("island").round(3).to_string())
    print("\n$10M+ houses+condos by fiscal year (as used):")
    print(ten.round(2).to_string())
    print(f"\nOʻahu $2-3M houses+condos: {n23:.1f} a year; $6-10M share of $3-10M: "
          + ", ".join(f"{c} {v[0]:.3f}" for c, v in s610.items()))
    rec = maui_recorded()
    maui = table[table.county == "Maui"].set_index("band_lo")["mls_per_year"]
    cov = {"$1-3M": rec["1_3"].mean() / maui[1e6],
           "$3-10M": (rec["3_6"] + rec["6_10"]).mean() / (maui[3e6] + maui[6e6]),
           "$10M+": rec["10p"].mean() / maui[1e7]}
    print("\nMaui recorded (owner + nonowner) / MLS: " + ", ".join(f"{k} {v:.3f}" for k, v in cov.items()))
    band_cov = table.band_lo.map({1_000_000: cov["$1-3M"], 3_000_000: cov["$3-10M"], 6_000_000: cov["$3-10M"],
                                  10_000_000: cov["$10M+"]})
    implied = (table.mls_per_year * table.coverage_rel_maui * band_cov).where(table.county != "Maui")
    print("Implied recorded sales a year (MLS x coverage_rel_maui x Maui's ratio):")
    print(table.assign(recorded=implied).pivot(index="county", columns="band_lo", values="recorded")
          .dropna().round(1).to_string())
    print(f"\nWrote {OUT.relative_to(REPO)} ({len(table)} rows):")
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
