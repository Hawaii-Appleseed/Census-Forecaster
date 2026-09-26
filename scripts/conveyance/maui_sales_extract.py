"""Maui County conveyance sales and residential housing stock, by value.

Usage:
  python scripts/conveyance/maui_sales_extract.py            # cached downloads
  python scripts/conveyance/maui_sales_extract.py --refresh  # re-download

Writes the Maui inputs of forecast_conveyance_sb3028.py (listed below) to
packages/tax_modeler/src/tax_modeler/data/raw/conveyance/, with
maui_sources.json: each raw file's URL, how it was obtained, retrieval time,
SHA-256 and row count. Raw files are cached under
runs/conveyance_sb3028/maui_cache/ (gitignored). For other scripts,
used_sales() returns the documents of the sales by value, one row each.

Sources (public, no key; retrieved 2026-09-25):
  - County of Maui, Real Property Assessment Division, Document Center
    (https://www.mauicounty.gov/DocumentCenter/Index/229): View/8070 "RPT
    Sales Data File" (sales.csv: every recorded conveyance with its price and
    the conveyance tax paid; cumulative, updated every Tuesday; this is the
    2026-09-22 file; field layout in View/8069) and View/8079 "RPT Full
    Assessment Listing as of 4/06/2026" (fullasmt26.txt; layout
    data_FULLASMT.pdf in the zip).
  - State of Hawaiʻi, tmk_state_2025_dwelling_data (ArcGIS FeatureServer, as in
    fetch_parcel_stock.py): dwelling units per TMK parcel. A pull is cached
    only if complete: as many rows, and unique OBJECTIDs, as the service
    counts for the same where clause (returnCountOnly).
The county site answers python-requests with HTTP 404 (it did on 2026-09-25),
and this script does not pose as a browser. If --refresh fails, save the two
zips from a browser as maui_cache/sales.zip and maui_cache/assessment.zip and
run without --refresh; a cached file's modification time is recorded as its
retrieval time. The zips behind the committed files are the 2026-09-25
research round's downloads, copied into maui_cache: maui_sources.json says so
under "obtained" (OBTAINED, by SHA-256), and "retrieved" is when they were
cached.

Per recorded document (sales.csv has one row per parcel; a multi-parcel deed
repeats its price and tax on each row):
  1. Rows are grouped by instrument number (INSTRUNO) or, for a Land Court
     document, by Land Court document number (LANDCOURT_NO); a row carrying
     both ties the two together. So a multi-parcel Land Court deed counts once
     (T13017068: one $12.9M sale of three Wailea units, not three). A row with
     neither is a document of its own (date and parcel). A document is dated
     by its record date (RECORDDATE). A row without one is kept, dated by its
     sale date (SALEDATE), when it carries a price and a tax that differ
     (Land Court T11911108: $7.2M, recorded without a record date); price =
     tax marks a route slip, death or divorce record, not a sale.
  2. A document's price and tax are the values its rows repeat. When the
     rows differ, the tax is the sum of the positive values, and the price is
     whichever of that sum and the distinct values the tax fits most closely
     to a schedule (step 3), the sum if it fits none: four parcels at [$4.2M,
     $5.2M, $4.2M, $4.2M] taxed $36,400, 0.7% of $5.2M, are one $5.2M sale,
     not $17.8M. Transposed fields are fixed: when "tax" exceeds "price" they
     are swapped (a $19.5M lease recorded as price 195,000 / tax 19,500,016).
  3. The schedule applied is the nearer of HRS §247-2 (1) and (2) at the
     price, if the tax is within 2% of it (the two schedules' rates differ by
     20% or more at every price); failing that, for a tax of a few dollars,
     the first within $2. (2) is a condo or single-family
     sale to a buyer not eligible for the homeowner exemption: "nonowner".
     (1) on improved residential parcels (every parcel on land class 1, 2 or
     10-12, one with a building value) is "owner". (1) elsewhere is "nonres",
     including vacant residential land, which HD2 §247-2(a)(3) keeps on
     schedule (1) as property with no dwelling unit. No match is
     "unmatched", counted as "nonres" in the FY2023-26 files.
  4. Sales of $600K+ are binned at edges that include every break point of
     both the current and SB 3028 HD2 schedules, so bin count and summed price
     reproduce either law's tax exactly. Sales of $10M+ are listed singly.
  5. The county's validity code (SA: 0 valid, 4 related individuals, 8
     developer sale, ...) is the one the document's rows carry most often.

Stock: each parcel of the assessment listing on a residential land class
(value = land + building; owner-occupied = tax rate class 9, which the layout
defines as a land class with a homeowner exemption, with an exemption and not
fully exempt; improved = building value > 0; condo = a CPR unit, TMK suffix
other than 0000). There is no cap on the exemption: Maui's base homeowner
exemption is itself $300,000, and about 1,800 homes carry more (an add-on or a
second owner's exemption). Left out: fully exempt records, buildings with 5+
dwelling units (State GIS layer), non-condo records of $20M+, and CPR master
records (suffix 0000 on a TMK that also has unit records), whose value is
already in their units. Sales by value: FY2023-26 "owner" and "nonowner"
sales whose parcels are all listed and none left out, arm's-length only
(price 0.5-2.5x the parcels' combined assessed value). The 5+ unit sales are
listed in the multifamily file, which the forecast scores under HD2's
per-unit rule, and each is in exactly one other file: its price bin when
under $10M (the forecast moves it out), none at $10M+ (the $10M+ list leaves
them out).

Output files:
  maui_sales_bins_fy2023_2026.csv         fy,category,bin_lo,count,sum_price
  maui_sales_over_10m_fy2023_2026.csv     fy,category,price (5+ unit sales aside)
  maui_sales_totals_fy2016_2026.csv       fy,category,count,sum_price,sum_tax_recorded
  maui_residential_stock_2026.csv         value_lo,owner_occupied,improved,condo,count,sum_value
  maui_sales_by_value_fy2023_2026.csv     fy,category,value_lo,owner_occupied_now,improved,condo,
                                          count,sum_price,sum_value
  maui_sales_value_price_fy2023_2026.csv  fy,category,owner_occupied_now,condo,value_lo,price_lo,
                                          count,sum_price,sum_value (the improved sales of
                                          by_value, by price bin; $10M+ at 10,000,000)
  maui_multifamily_sales_fy2023_2026.csv  fy,category,price,units
  maui_sales_history_fy2016_2026.csv      fy,kind,category,bin_lo,count,sum_price,units: every
                                          fiscal year's sales as the forecast scores them (kind
                                          "bin": price bins under $10M; "top": each $10M+ sale;
                                          "mf": each 5+ unit sale), for scoring past markets
  maui_developer_share_fy2023_2026.csv    band_lo,band_hi,count,developer_count,sum_price,
                                          developer_price: home sales (owner and non-owner
                                          schedules) and those the county codes as developer
                                          sales (validity code 8), by price band

This replaces maui_sales_extract.js, a browser-console script, and keeps its
logic but for these changes: it split sales.csv on commas, so a recorded tax
of "5,243.79" read as $5; it counted a multi-parcel Land Court deed once per
parcel, each at the full price; its stock kept Maui's CPR masters; it counted
schedule (1) purchases of vacant residential land as "owner"; its price
bins read only a parcel's first listing line for the land class, where its
stock read them all (both now read them all). Since the port (review of
2026-09-25), four more of its rules have changed: it matched schedules
within 0.5% and in order, summed a document's differing prices, dropped rows
without a record date, and capped the homeowner exemption at $300,000.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "runs" / "conveyance_sb3028" / "maui_cache"
OUT = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"

DOCUMENT_CENTER = "https://www.mauicounty.gov/DocumentCenter/View/"
DOWNLOADS = {"sales": (DOCUMENT_CENTER + "8070/RPT-Sales-Data-File-", "sales.csv"),
             "assessment": (DOCUMENT_CENTER + "8079/RPT-Full-Assessment-Listing-as-of-4062026", "fullasmt26.txt")}
UNITS_URL = ("https://services1.arcgis.com/x4h61KaW16vFs7PM/arcgis/rest/services/"
             "tmk_state_2025_dwelling_data/FeatureServer/0/query")
UNITS_QUERY = {"where": "county='Maui' AND COUNT_Units>=5", "outFields": "TMK,COUNT_Units",
               "returnGeometry": "false", "orderByFields": "OBJECTID", "resultRecordCount": 2000, "f": "json"}
# How cached zips this script did not download were obtained, by SHA-256.
RESEARCH_ROUND = ("the 2026-09-25 research round's download, copied into maui_cache; not fetched by this script, "
                  "whose python-requests client the county site answers with HTTP 404. 'retrieved' is when it "
                  "was cached")
OBTAINED = {"c5055a6d9b019b2d8c149816f90415dc7d294b144671ab6fd61c4ceb26a9c934": RESEARCH_ROUND,     # sales.zip
            "43bfe1c42da1606fadd1311e05ee0d5c0c649e811cd4f177001f7b12ea928e9d": RESEARCH_ROUND}     # assessment.zip

FIRST_FY, BASE_FY, LAST_FY = 2016, 2023, 2026
# Land classes 1 non-owner-occupied residential, 2 apartment, 10 commercialized
# residential, 11 TVR-STRH, 12 long-term rental. Tax rate class 9 is homeowner.
RESIDENTIAL = {"1", "2", "10", "11", "12"}
HOMEOWNER = "9"
MULTIFAMILY_UNITS = 5             # HD2 values these per unit
LARGE_NON_CONDO = 20_000_000      # probable apartment/resort parcels without a unit count
ARMS_LENGTH = (0.5, 2.5)          # price over assessed value
TOP = 10_000_000                  # sales listed singly
DEVELOPER = "8"                   # county validity code: developer sale
DEVELOPER_BANDS = [0, 1e6, 2e6, 4e6, 6e6, 1e7, math.inf]

# HRS §247-2 schedules (1) and (2): (price below, rate on the whole price).
SCHEDULES = {1: ((6e5, .001), (1e6, .002), (2e6, .003), (4e6, .005), (6e6, .007), (1e7, .009), (math.inf, .010)),
             2: ((6e5, .0015), (1e6, .0025), (2e6, .004), (4e6, .006), (6e6, .0085), (1e7, .011), (math.inf, .0125))}
SCHEDULE_TOLERANCE = .02          # of the tax due; (2)'s rate is 20-50% above (1)'s at every price
PRICE_EDGES = [0, 6e5, 8e5, 1e6, *np.arange(1.25e6, 6e6 + 1, 2.5e5), *np.arange(6.5e6, 1e7 + 1, 5e5)]
VALUE_EDGES = [0, 3e5, 6e5, 8e5, 1e6, *np.arange(1.25e6, 6e6 + 1, 2.5e5), *np.arange(6.5e6, 1e7 + 1, 5e5),
               12.5e6, 15e6, 20e6, 30e6]
CATEGORIES = ["owner", "nonowner", "nonres", "unmatched"]

SALES_FIELDS = ("PARID", "SALEDATE", "RECORDDATE", "INSTRUNO", "LANDCOURT_NO", "PRICE", "CONV_TAX", "SA")
RECORD_DATE = re.compile(r"\d{4}/\d{2}")
AMOUNT = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


# ─────────────────────────────────────────────────────────────────────────────
# Raw files

def download(name: str, refresh: bool) -> Path:
    """A county zip from the cache or, with *refresh*, the Document Center
    (plain python-requests; a marker file records that this script fetched it)."""
    path = CACHE / f"{name}.zip"
    if path.exists() and not refresh:
        return path
    url = DOWNLOADS[name][0]
    r = requests.get(url, timeout=300)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        raise SystemExit(f"{name}: HTTP {r.status_code} from {url}. Save the file from a browser as "
                         f"{path.relative_to(REPO)} and run without --refresh.")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    _marker(path).write_text(_sha256(r.content))
    return path


def _marker(path: Path) -> Path:
    return path.with_name(path.name + ".downloaded")


def _obtained(path: Path) -> str:
    sha = _sha256(path.read_bytes())
    if _marker(path).exists() and _marker(path).read_text() == sha:
        return "downloaded by this script (--refresh)"
    return OBTAINED.get(sha, "saved into maui_cache by hand (from a browser); 'retrieved' is when it was saved")


def _arcgis(params: dict, want: str, what: str) -> dict:
    """One query of the dwelling layer, retried. An HTTP-200 body with an
    'error' key, or without the key asked for (want), is a failure like a
    timeout."""
    err: object = None
    for attempt in range(5):
        try:
            d = requests.get(UNITS_URL, timeout=120, params={**params, "f": "json"}).json()
            if "error" not in d and want in d:
                return d
            err = d.get("error", f"no '{want}' in the response")
        except (requests.RequestException, json.JSONDecodeError) as e:
            err = e
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"dwelling units: failed at {what}: {err}")


def dwelling_units(refresh: bool) -> Path:
    """Maui parcels with 5+ dwelling units (UNITS_QUERY, paged), cached as JSON
    only if complete: as many rows, and unique OBJECTIDs, as the service counts
    for the same where clause. The cache keeps UNITS_QUERY's fields."""
    path = CACHE / "units_ge5.json"
    if path.exists() and not refresh:
        return path
    n = int(_arcgis({"where": UNITS_QUERY["where"], "returnCountOnly": "true"}, "count", "the count")["count"])
    rows: list[dict] = []
    while len(rows) < n:
        feats = _arcgis({**UNITS_QUERY, "outFields": "OBJECTID," + UNITS_QUERY["outFields"],
                         "resultOffset": len(rows)}, "features", f"offset {len(rows):,}")["features"]
        if not feats:
            break
        rows += [f["attributes"] for f in feats]
    ids = len({r.get("OBJECTID") for r in rows})
    if len(rows) != n or ids != n:
        raise SystemExit(f"dwelling units: {len(rows):,} rows ({ids:,} unique OBJECTID) of {n:,}; not cached")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{k: v for k, v in r.items() if k != "OBJECTID"} for r in rows]))
    return path


def member(path: Path, name: str) -> str:
    return zipfile.ZipFile(path).read(name).decode("latin-1")      # ASCII in practice; one byte per column


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retrieved(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sources(raw: dict[str, Path], units: Path, texts: dict[str, str], kept: list[dict[str, str]]) -> dict:
    """URLs, how obtained, retrieval times (UTC), SHA-256 and row counts of the
    raw files; file_date is the zip entry's timestamp (the county's export).
    *kept*: sales_rows()."""
    dated = sum(1 for r in kept if r["DATE"] == r["RECORDDATE"])
    rows = {"sales": {"rows": len(sales_lines(texts["sales"])[1]), "rows_with_record_date": dated,
                      "rows_dated_by_sale_date": len(kept) - dated},
            "assessment": {"rows": sum(1 for line in texts["assessment"].splitlines() if len(line) >= 79)}}
    out = {}
    for name, path in raw.items():
        url, inner = DOWNLOADS[name]
        out[name] = {"url": url, "obtained": _obtained(path), "retrieved": _retrieved(path),
                     "bytes": path.stat().st_size, "sha256": _sha256(path.read_bytes()), "file": inner,
                     "file_date": datetime(*zipfile.ZipFile(path).getinfo(inner).date_time).isoformat(),
                     "file_sha256": _sha256(texts[name].encode("latin-1")), **rows[name]}
    out["dwelling_units"] = {"url": UNITS_URL, "query": UNITS_QUERY,
                             "obtained": "paged query by this script",
                             "retrieved": _retrieved(units), "bytes": units.stat().st_size,
                             "sha256": _sha256(units.read_bytes()), "rows": len(json.loads(units.read_text()))}
    return {"script": "scripts/conveyance/maui_sales_extract.py", "files": out}


# ─────────────────────────────────────────────────────────────────────────────
# Sales: rows, documents, schedules

def sales_lines(text: str) -> tuple[dict[str, slice], list[str]]:
    """sales.csv's columns and data lines. The file is fixed width: the header's
    commas mark the column breaks, and a comma inside a value (CONV_TAX
    '5,243.79', some CERT_NO, LANDCOURT_NO and DOC_TYPE values) is not one.
    The header repeats every 32,999 lines; blank lines and the footer
    ("520135 rows selected.") are not data."""
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("PARID"))
    cuts = [i for i, c in enumerate(header) if c == ","]
    cols = {header[a:b].strip(): slice(a, b)
            for a, b in zip([0, *(c + 1 for c in cuts)], [*cuts, None], strict=True)}
    return cols, [line for line in lines if len(line) > cuts[-1] and not line.startswith("PARID")]


def sales_rows(text: str) -> list[dict[str, str]]:
    """The data rows that date a sale, each with DATE: its record date or,
    for a row without one that carries a price and a tax that differ, its
    sale date (module docstring, step 1)."""
    cols, lines = sales_lines(text)
    out = []
    for r in ({f: line[cols[f]].strip() for f in SALES_FIELDS} for line in lines):
        if RECORD_DATE.match(r["RECORDDATE"]):
            out.append({**r, "DATE": r["RECORDDATE"]})
        elif RECORD_DATE.match(r["SALEDATE"]):
            price, tax = amount(r["PRICE"]), amount(r["CONV_TAX"])
            if price > 0 and tax > 0 and price != tax:
                out.append({**r, "DATE": r["SALEDATE"]})
    return out


def amount(s: str) -> float:
    """A money field as the extractor has always read it (JavaScript parseFloat:
    the leading number, NaN if none), less thousands separators."""
    m = AMOUNT.match(s.replace(",", ""))
    return float(m.group()) if m else math.nan


@dataclass
class Document:
    date: str                     # record date; the sale date if no row has one
    recorded: bool                # a row has a record date
    parids: list[str] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)
    taxes: list[float] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)       # validity (SA), a row each

    @property
    def validity(self) -> str:
        """The validity code its rows carry most often, blanks aside (a tie
        goes to the earlier row); '' if none."""
        c = Counter(x for x in self.codes if x)
        return c.most_common(1)[0][0] if c else ""


def _doc_number(s: str) -> str:
    return s if re.search(r"[1-9]", s) else ""        # '0000000000', '0' and '-' are placeholders


def documents(rows: list[dict[str, str]]) -> list[Document]:
    """Rows grouped into recorded documents (module docstring, step 1)."""
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = []
    for r in rows:
        inst, lc = _doc_number(r["INSTRUNO"]), _doc_number(r["LANDCOURT_NO"])
        ids = [k for k in (inst, lc and "LC:" + lc) if k] or [r["DATE"] + "|" + r["PARID"]]
        for other in ids[1:]:
            parent[root(other)] = root(ids[0])
        keys.append(ids[0])
    docs: dict[str, Document] = {}
    for r, k in zip(rows, keys, strict=True):
        recorded = r["DATE"] == r["RECORDDATE"]
        d = docs.setdefault(root(k), Document(r["DATE"], recorded))
        if recorded and not d.recorded:
            d.date, d.recorded = r["DATE"], True
        d.parids.append(r["PARID"])
        d.prices.append(amount(r["PRICE"]))
        d.taxes.append(amount(r["CONV_TAX"]))
        d.codes.append(r["SA"])
    return list(docs.values())


def one(xs: list[float], tax: float = math.nan) -> float:
    """A document's price or tax (module docstring, step 2): the value its rows
    repeat. When they differ, the sum of the positive values, unless *tax* (for
    a price) fits a schedule (fit()) at one of the distinct values at least as
    closely as at the sum: then that value."""
    if len({"nan" if math.isnan(x) else x for x in xs}) == 1:
        return xs[0]
    total = sum(x for x in xs if x > 0)
    err, x = min((fit(tax, x)[0], x) for x in [total, *dict.fromkeys(x for x in xs if x > 0)])
    return x if err < math.inf else total


def _due(price: float, s: int) -> float:
    return price * next(rate for below, rate in SCHEDULES[s] if price < below)


def fit(tax: float, price: float) -> tuple[float, int]:
    """(relative error, schedule): the HRS §247-2 schedule whose rate at this
    price comes nearest the recorded tax, if within SCHEDULE_TOLERANCE of the
    tax due; else, for a small tax, the first schedule within $2 (a $1 deed
    taxed $1 stays on (1)); (inf, 0) if neither."""
    if not (price > 0 and tax > 0):
        return math.inf, 0
    errs = {s: abs(tax - _due(price, s)) / _due(price, s) for s in SCHEDULES}
    s = min(errs, key=errs.get)
    if errs[s] <= SCHEDULE_TOLERANCE:
        return errs[s], s
    s = next((s for s in SCHEDULES if abs(tax - _due(price, s)) <= 2), 0)
    return (errs[s], s) if s else (math.inf, 0)


def schedule(tax: float, price: float) -> int:
    """The schedule the recorded tax fits (fit()); 0 when neither does."""
    return fit(tax, price)[1]


# ─────────────────────────────────────────────────────────────────────────────
# Assessment listing

@dataclass
class Parcel:
    value: float = 0.0            # land + building, summed over the parcel's lines
    building: float = 0.0
    exempt: float = 0.0
    homeowner: bool = False       # a line in tax rate class 9
    residential: bool = False     # a line on a residential land class
    condo: bool = False           # a CPR unit (TMK suffix other than 0000)
    master: bool = False          # suffix 0000 on a TMK that also has unit records
    units: int = 0                # dwelling units (suffix 0000 only, State GIS layer)

    @property
    def fully_exempt(self) -> bool:
        return self.value > 0 and self.exempt >= self.value

    @property
    def owner(self) -> bool:
        """Owner-occupied: homeowner class, any exemption (no cap: the base one is $300,000), not fully exempt."""
        return self.homeowner and self.exempt > 0 and not self.fully_exempt

    @property
    def improved(self) -> bool:
        return self.building > 0

    @property
    def multifamily(self) -> bool:
        return self.units >= MULTIFAMILY_UNITS

    @property
    def left_out(self) -> bool:
        """Out of the stock and the sales by value (besides non-residential land)."""
        large = not self.condo and not self.multifamily and self.value >= LARGE_NON_CONDO
        return self.fully_exempt or self.multifamily or large or self.master


def parcels(listing: str, units: dict[str, int]) -> dict[str, Parcel]:
    """The assessment listing by 12-digit TMK. Fixed width (data_FULLASMT.pdf):
    county digit and TMK cols 1-13, land class 19-22, tax rate class 23-26,
    land value 27-39, land exemption 40-52, building value 53-65, building
    exemption 66-78; a parcel has a line per land class. *units*: dwelling
    units by 8-digit TMK."""
    par: dict[str, Parcel] = {}
    for line in listing.splitlines():
        if len(line) < 79:
            continue
        land, land_ex, bldg, bldg_ex = (float(line[a:a + 13].strip() or 0) for a in (26, 39, 52, 65))
        p = par.setdefault(line[1:13], Parcel())
        p.value += land + bldg
        p.building += bldg
        p.exempt += land_ex + bldg_ex
        p.homeowner |= line[22:26].strip() == HOMEOWNER
        p.residential |= line[18:22].strip() in RESIDENTIAL
    projects = {k[:8] for k in par if k[8:] != "0000"}
    for k, p in par.items():
        p.condo = k[8:] != "0000"
        p.master = not p.condo and k[:8] in projects
        p.units = 0 if p.condo else units.get(k[:8], 0)
    return par


def units_by_tmk(features: list[dict]) -> dict[str, int]:
    return {str(a["TMK"])[1:]: int(a["COUNT_Units"]) for a in features}     # less the county digit


# ─────────────────────────────────────────────────────────────────────────────
# Tables

def sales(docs: list[Document], par: dict[str, Parcel], last_fy: int = LAST_FY) -> pd.DataFrame:
    """One row per FY2016-26 (to *last_fy*) document with a price and a tax:
    fy, category, price, tax; for the stock method, units (in 5+ unit
    buildings), used (in the sales by value), value, owner_occupied_now,
    improved, condo; and date, validity (SA), parids."""
    out = []
    for d in docs:
        tax = one(d.taxes)
        price = one(d.prices, tax)
        if not (tax > 0 and price > 0):
            continue
        if tax > price:
            tax, price = price, tax
        y, m = int(d.date[:4]), int(d.date[5:7])
        fy = y + 1 if m >= 7 else y
        if not FIRST_FY <= fy <= last_fy:
            continue
        ps = [par.get(k) for k in dict.fromkeys(d.parids)]
        known = [p for p in ps if p]
        listed = len(known) == len(ps)
        improved = any(p.improved for p in known)
        s = schedule(tax, price)
        cat = ("nonowner" if s == 2 else "unmatched" if s == 0
               else "owner" if listed and improved and all(p.residential for p in known) else "nonres")
        value = sum(p.value for p in known)
        units = sum(p.units for p in known if p.multifamily)
        ratio = price / value if value > 0 else math.inf
        used = (cat in ("owner", "nonowner") and units < MULTIFAMILY_UNITS and listed
                and not any(p.left_out for p in known) and ARMS_LENGTH[0] <= ratio <= ARMS_LENGTH[1])
        out.append({"fy": fy, "category": cat, "price": price, "tax": tax, "units": units, "used": used,
                    "value": value, "owner_occupied_now": int(any(p.owner for p in known)),
                    "improved": int(improved), "condo": int(bool(known) and all(p.condo for p in known)),
                    "date": d.date, "validity": d.validity, "parids": list(dict.fromkeys(d.parids))})
    return pd.DataFrame(out, columns=SALES_COLUMNS)


SALES_COLUMNS = ["fy", "category", "price", "tax", "units", "used", "value", "owner_occupied_now", "improved", "condo",
                 "date", "validity", "parids"]


USED_COLUMNS = ["fy", "date", "category", "price", "value", "improved", "owner_occupied_now", "condo", "validity",
                "parids"]


def used(s: pd.DataFrame) -> pd.DataFrame:
    """The FY2023+ documents of the sales by value (sales() rows with used:
    arm's-length "owner" and "nonowner" sales, improved or not)."""
    return s.loc[s["used"] & (s["fy"] >= BASE_FY), USED_COLUMNS].reset_index(drop=True)


def _lo(edges: list[float], x: pd.Series) -> np.ndarray:
    return np.array(edges)[np.searchsorted(edges, x.to_numpy(float), side="right") - 1].astype("int64")


def _round(x: pd.Series) -> pd.Series:
    return np.floor(x + 0.5).astype("int64")                  # half up, as JavaScript's Math.round


def _count_sum(df: pd.DataFrame, keys: list[str], **sums: str) -> pd.DataFrame:
    g = df.groupby(keys, as_index=False).agg(count=(keys[0], "size"), **{k: (v, "sum") for k, v in sums.items()})
    return g.assign(**{k: _round(g[k]) for k in sums})


def tables(s: pd.DataFrame, par: dict[str, Parcel]) -> dict[str, pd.DataFrame]:
    """The output files, from the document table (sales()) and the parcels."""
    base = s[s["fy"] >= BASE_FY].replace({"category": {"unmatched": "nonres"}})
    top = _round(base["price"]) >= TOP                  # on the price the files print, as the forecast reads it
    mf = base["units"] >= MULTIFAMILY_UNITS             # 5+ unit sales: in the bins if under TOP, never in the top list
    used = base[base["used"]].assign(value_lo=lambda d: _lo(VALUE_EDGES, d["value"]),
                                     price_lo=lambda d: _lo(PRICE_EDGES, d["price"]))
    homes = pd.DataFrame([{"value": p.value, "owner_occupied": int(p.owner), "improved": int(p.improved),
                           "condo": int(p.condo)} for p in par.values() if p.residential and not p.left_out])
    out = {
        "maui_sales_bins_fy2023_2026.csv": _count_sum(
            base[~top].assign(bin_lo=lambda d: _lo(PRICE_EDGES, _round(d["price"]))),
            ["fy", "category", "bin_lo"], sum_price="price"),
        "maui_sales_over_10m_fy2023_2026.csv": base[top & ~mf].assign(price=lambda d: _round(d["price"]))[
            ["fy", "category", "price"]],
        "maui_sales_totals_fy2016_2026.csv": _count_sum(s, ["fy", "category"], sum_price="price",
                                                        sum_tax_recorded="tax"),
        "maui_residential_stock_2026.csv": _count_sum(homes.assign(value_lo=lambda d: _lo(VALUE_EDGES, d["value"])),
                                                      ["value_lo", "owner_occupied", "improved", "condo"],
                                                      sum_value="value"),
        "maui_sales_by_value_fy2023_2026.csv": _count_sum(used, ["fy", "category", "value_lo", "owner_occupied_now",
                                                                 "improved", "condo"],
                                                          sum_price="price", sum_value="value"),
        "maui_sales_value_price_fy2023_2026.csv": _count_sum(used[used["improved"] == 1],
                                                             ["fy", "category", "owner_occupied_now", "condo",
                                                              "value_lo", "price_lo"],
                                                             sum_price="price", sum_value="value"),
        "maui_multifamily_sales_fy2023_2026.csv": base[mf].assign(
            price=lambda d: _round(d["price"]))[["fy", "category", "price", "units"]],
        "maui_sales_history_fy2016_2026.csv": history(s),
        "maui_developer_share_fy2023_2026.csv": developer_share(base),
    }
    return {name: df.sort_values([c for c in df.columns if c != "count" and not c.startswith("sum_")],
                                 key=lambda c: c.map(CATEGORIES.index) if c.name == "category" else c,
                                 ignore_index=True)
            for name, df in out.items()}


def history(s: pd.DataFrame) -> pd.DataFrame:
    """Every fiscal year's priced, taxed documents in the form the forecast
    scores: price bins under $10M ("bin"), each $10M+ sale ("top") and each
    sale of a 5+ unit building ("mf"), the last two aside from the bins."""
    x = s.replace({"category": {"unmatched": "nonres"}}).assign(price=lambda d: _round(d["price"]))
    mf = x["units"] >= MULTIFAMILY_UNITS
    top = ~mf & (x["price"] >= TOP)
    bins = _count_sum(x[~mf & ~top].assign(bin_lo=lambda d: _lo(PRICE_EDGES, d["price"])),
                      ["fy", "category", "bin_lo"], sum_price="price").assign(kind="bin", units=1)
    one = x[mf | top].assign(kind=np.where(mf[mf | top], "mf", "top"), bin_lo=lambda d: _lo(PRICE_EDGES, d["price"]),
                             count=1, sum_price=lambda d: d["price"], units=lambda d: d["units"].clip(lower=1))
    cols = ["fy", "kind", "category", "bin_lo", "count", "sum_price", "units"]
    return pd.concat([bins[cols], one[cols]], ignore_index=True).sort_values(
        ["fy", "kind", "category", "bin_lo", "sum_price"], ignore_index=True)


def developer_share(base: pd.DataFrame) -> pd.DataFrame:
    """FY2023+ home sales (owner and non-owner schedules) and those the county
    codes as developer sales, by price band."""
    h = base[base["category"].isin(["owner", "nonowner"]) & (base["units"] < MULTIFAMILY_UNITS)]
    h = h.assign(band_lo=_lo(DEVELOPER_BANDS, h["price"]), dev=h["validity"] == DEVELOPER)
    g = h.groupby("band_lo").agg(count=("price", "size"), developer_count=("dev", "sum"), sum_price=("price", "sum"),
                                 developer_price=("price", lambda p: p[h.loc[p.index, "dev"]].sum())).reset_index()
    g["band_hi"] = [str(int(h)) if math.isfinite(h) else "inf"
                    for h in (DEVELOPER_BANDS[DEVELOPER_BANDS.index(lo) + 1] for lo in g["band_lo"])]
    g[["sum_price", "developer_price"]] = g[["sum_price", "developer_price"]].map(lambda v: int(round(v)))
    return g[["band_lo", "band_hi", "count", "developer_count", "sum_price", "developer_price"]].astype(
        {"developer_count": int})


def inputs(refresh: bool = False) -> tuple[dict[str, Path], Path, dict[str, str], list[dict[str, str]],
                                            dict[str, Parcel]]:
    """(raw zips, dwelling-units file, the zips' texts, sales_rows(), parcels()),
    from the cache unless *refresh*."""
    raw = {name: download(name, refresh) for name in DOWNLOADS}
    units = dwelling_units(refresh)
    texts = {name: member(path, DOWNLOADS[name][1]) for name, path in raw.items()}
    par = parcels(texts["assessment"], units_by_tmk(json.loads(units.read_text())))
    return raw, units, texts, sales_rows(texts["sales"]), par


def used_sales(refresh: bool = False, last_fy: int = LAST_FY) -> pd.DataFrame:
    """The documents behind maui_sales_by_value, one row each (used(): fy,
    date, category, price, value, improved, owner_occupied_now, condo,
    validity, parids), for other scripts such as oahu_owner_turnover.py.
    *last_fy* past LAST_FY adds the sales recorded since, on the same rules."""
    *_, rows, par = inputs(refresh)
    return used(sales(documents(rows), par, last_fy))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    raw, units, texts, rows, par = inputs(args.refresh)
    docs = documents(rows)
    s = sales(docs, par)
    for name, df in tables(s, par).items():
        df.to_csv(OUT / name, index=False)
        sums = ", ".join(f"{c} {df[c].sum():,}" for c in df.columns if c in ("count", "price") or c.startswith("sum_"))
        print(f"{name}: {len(df)} rows; {sums}")
    (OUT / "maui_sources.json").write_text(json.dumps(sources(raw, units, texts, rows), indent=2) + "\n")
    undated = sum(1 for r in rows if r["DATE"] != r["RECORDDATE"])
    print(f"{len(rows):,} sales rows ({undated} dated by sale date); {len(docs):,} documents, {len(s):,} priced "
          f"and taxed in FY{FIRST_FY}-{LAST_FY % 100}; {len(par):,} parcels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
