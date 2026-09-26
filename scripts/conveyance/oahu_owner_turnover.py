"""Oʻahu's own home turnover, December 2023 to September 2026, from two
snapshots of the City's owner roll, and a like-for-like check against the
Maui sale rates the conveyance model applies to Oʻahu.

Usage:
  python scripts/conveyance/oahu_owner_turnover.py             # cached pulls
  python scripts/conveyance/oahu_owner_turnover.py --refresh   # new OWNINFO snapshot (dated today)
  python scripts/conveyance/oahu_owner_turnover.py --review-sample 80 [--review-out PATH]

Writes to packages/tax_modeler/src/tax_modeler/data/raw/conveyance/:
  oahu_owner_turnover_cells_2023_2026.csv   Oʻahu homes in both rolls and their
      ownership changes by the model's rate bin (2026 assessed value,
      RATE_EDGES lower edge), owner-occupied and condo unit (parid suffix not
      0000); window dates and length in years
  maui_sale_composition_fy2023_2026.csv     Maui's arm's-length improved home
      sales by assessed-value band: count, developer and related-party shares,
      and priced sales per parcel sold over a window as long as Oʻahu's; the
      date of the Maui sales file they come from
and prints the pooled Oʻahu/Maui turnover ratio by band. It reads
oahu_turnover_review_tallies.csv, the hand review of the random resales that
--review-sample 80 draws (80 below $1M, 80 at $2M+; ambiguous cases count
half; $1-2M takes both samples pooled). The sample, with owner names, goes to
runs/ or --review-out, never to the repo. Raw pulls are cached under
runs/conveyance_sb3028/parcel_cache/ (gitignored); they carry owner names and
mailing addresses and are not committed.

Sources (public; no key):
  - Honolulu owner roll, tax year 2024, frozen: "Parcel Ownership",
    https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/PropertyReportInfo/FeatureServer/2
    (ArcGIS item d7134069108040e280f804ad04e6891f; data last edited
    2023-12-05; 565,251 rows, still served on 2026-09-25).
  - Honolulu owner roll, live: OWNINFO,
    https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/OWNINFO/FeatureServer/0
    (tax year 2027, updated daily; pulled 2026-09-25, 587,309 rows). Each
    pull is cached as honolulu_owners_<date>.parquet and the latest is used.
  - Honolulu assessed values and exemptions, tax year 2026: ASMTPITT via
    fetch_parcel_stock.py, with its residential and owner-occupied
    definitions. CPR masters (suffix-0000 records whose 8-digit TMK also has
    unit records) are dropped: they carry their units' value a second time.
  - Maui: the sales behind maui_sales_by_value, one row per recorded
    document, from maui_sales_extract.used_sales() on that script's cache
    (runs/conveyance_sb3028/maui_cache/: the county sales file of 2026-09-22
    and the April 2026 full assessment listing). This script never
    refreshes them: the cached files must be the ones maui_sources.json
    records by SHA-256, the snapshot behind maui_rates() (after replacing
    them, rerun maui_sales_extract.py first).

Method. For each parcel, the principal owners are the lessee rows if there
are any, else the fee, owner and untyped rows ("Condo Master" rows dropped).
A transfer is sale-like when the principal owners changed and the old and new
names share no surname or entity token (taken from every '/' or '&' part), so
moves into a family trust or between spouses do not count. Not sales: a
lessee row appearing or disappearing while the fee owner is unchanged, and
records whose owner names are all blank in either roll. Counted apart from
resales: first sales by developers or other holders of 10 or more units in
the project in the 2024 roll, and purchases of 5 or more units in one
project by one buyer (the owner set is the holder; the project is a condo's
8-digit TMK, a fee-simple parcel's plat: zone, section and plat, the first 5
digits). A hand review of random resales found the share that are real
sales (precision; oahu_turnover_review_tallies.csv).

Maui's rates count every arm's-length improved sale the model uses
(maui_sales_extract.py's documents: priced, taxed on schedule (1) or (2),
residential, 0.5-2.5 times assessed value), which includes developer first
sales (county validity code 8) and sales between relatives (code 4); the
Oʻahu resales exclude both. A document carries one code, so the two shares
are disjoint, and the part of Maui's rate the owner diff can see is
1 - developer share - related share of it:

  ratio = resales / T x repeat x precision
          / (sum(homes x Maui sales per home) x (1 - developer share - related share))

where repeat converts parcels that changed hands into sales. Assumes RPAD
posts deeds with the same lag at both ends of the window (a month's
difference moves T by about 3%). On the 2026-09-25 roll and the 2026-09-22
Maui file the ratio is about 1 below $4M (1.05, 0.95, 1.02 for <$1M, $1-2M,
$2-4M) and 1.63 at $4M+ (sampling standard error 9%, from 241 Oʻahu resales
and 274 Maui sales).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "runs" / "conveyance_sb3028" / "parcel_cache"
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT_CELLS = DATA / "oahu_owner_turnover_cells_2023_2026.csv"
OUT_MAUI = DATA / "maui_sale_composition_fy2023_2026.csv"
TALLIES = DATA / "oahu_turnover_review_tallies.csv"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fps = _load("fetch_parcel_stock", REPO / "scripts" / "conveyance" / "fetch_parcel_stock.py")
fc = _load("forecast_conveyance_sb3028", REPO / "forecast_conveyance_sb3028.py")     # read-only: RATE_EDGES, maui_rates
mx = _load("maui_sales_extract", REPO / "scripts" / "conveyance" / "maui_sales_extract.py")    # Maui's sales, cached

ARCGIS = "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services"
FROZEN = (f"{ARCGIS}/PropertyReportInfo/FeatureServer/2", "2023-12-05", 565_251)
LIVE = f"{ARCGIS}/OWNINFO/FeatureServer/0"
OWNER_FIELDS = ("objectid,parid,taxyr,taxbillown,taxbillown1,taxbillown2,pctowned,owntype1,owntype2,owntype3,owntype4,"
                "grpflag,ownseq,owntble,ownfld,address1,address2,address3,owntype_desc,has_cpr")
MAUI_DEVELOPER, MAUI_RELATED = "8", "4"     # Maui validity codes (SA): developer sale, related individuals

OWNER_TYPES = ("Fee Owner", "Owner", "Owner/Multiclaimant", "Owner-A/S On Fee", "Owner-Sub A/S on Fee")
DEVELOPER_UNITS = 10     # units one holder owned in a project in the 2024 roll
BULK_BUYER_UNITS = 5     # units one buyer took in a project over the window
GENERIC = frozenset("""TR TRS TTEE TTEES TRUST TRUSTS TRUSTEE TRUSTEES SUCCESSOR REV REVOC REVOCABLE LIV LIVING FAMILY FAM
ESTATE EST ESTATES OF THE AND JR SR II III IV ET AL ETAL UX VIR LLC INC CORP CORPORATION CO COMPANY LTD LP LLP LIMITED
LIABILITY PARTNERSHIP PTNSHP PARTNERS HAWAII HI HONOLULU OAHU PROPERTIES PROPERTY HOLDINGS HOLDING INVESTMENTS INVESTMENT
GROUP ASSOCIATION ASSN AOAO C/O DTD DATED UA UAD AGMT AGREEMENT REALTY MGMT MANAGEMENT SEPARATE SEP DECL DECLARATION
IRREVOCABLE IRREV QUALIFIED PERSONAL RESIDENCE QPRT JOINT SURVIVORS SURVIVOR MARITAL BYPASS CREDIT SHELTER GST EXEMPT
EXEMPTION NON NONEXEMPT PURPOSES CREATED ONLY DESCENDANTS CHILDRENS CHILDREN HEIRS DEVISEES DECEASED DECD LAND HOMES HOME
HOUSE HALE VENTURES VENTURE ENTERPRISES ENTERPRISE CAPITAL PACIFIC ISLAND ISLANDS OCEAN KAI BAY BEACH FUND FUNDS SERIES
DBA NA AS FOR BY IN TO ON AT MR MRS MS DR USA US AMERICA NATIONAL BANK FSB""".split())  # noqa: SIM905

BANDS = ("<1M", "1-2M", "2-4M", "4M+")
REVIEW_BANDS = {"<1M": (0, 1e6), "2M+": (2e6, np.inf)}      # the hand-reviewed samples
PRECISION_OF = {"<1M": ("<1M",), "1-2M": ("<1M", "2M+"), "2-4M": ("2M+",), "4M+": ("2M+",)}
REVIEW_KEY = "oahu-turnover-26"      # 16-character hash key that fixes the review draw
Num = float | pd.Series


def band(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return np.select([v < 1e6, v < 2e6, v < 4e6], BANDS[:3], BANDS[3])


def rate_bin(v) -> np.ndarray:
    """The model's rate bin: lower edge of RATE_EDGES."""
    edges = np.array(fc.RATE_EDGES)
    return edges[np.searchsorted(edges, np.asarray(v, dtype=float), side="right") - 1].astype(int)


# ─────────────────────────────────────────────────────────────────────────────
# Pulls

def _arcgis(url: str, fields: str, page: int = 2000) -> pd.DataFrame:
    q = f"{url}/query"
    n = requests.get(q, params={"where": "1=1", "returnCountOnly": "true", "f": "json"}, timeout=120).json()["count"]

    def get(offset: int) -> list[dict]:
        for attempt in range(6):
            try:
                d = requests.get(q, timeout=180, params={
                    "where": "1=1", "outFields": fields, "returnGeometry": "false", "orderByFields": "objectid",
                    "resultOffset": offset, "resultRecordCount": page, "f": "json"}).json()
                if "features" in d:
                    return [f["attributes"] for f in d["features"]]
            except (requests.RequestException, json.JSONDecodeError):
                pass
            time.sleep(2 * (attempt + 1))
        raise SystemExit(f"{url}: failed at offset {offset}")

    with ThreadPoolExecutor(8) as ex:
        df = pd.DataFrame([r for chunk in ex.map(get, range(0, n, page)) for r in chunk])
    if len(df) != n or df["objectid"].nunique() != n:
        raise SystemExit(f"{url}: {len(df):,} rows ({df['objectid'].nunique():,} unique) of {n:,}")
    return df


def owner_rolls(refresh: bool) -> tuple[tuple[str, pd.DataFrame], tuple[str, pd.DataFrame]]:
    """(date, roll) at each end of the window: the frozen tax-year-2024 roll
    and the latest cached OWNINFO pull (a new one with --refresh)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    url, start, n_frozen = FROZEN
    path = CACHE / f"honolulu_owners_{start}.parquet"
    if not path.exists():
        _arcgis(url, OWNER_FIELDS).to_parquet(path)
    old = pd.read_parquet(path)
    if len(old) != n_frozen:
        raise SystemExit(f"frozen roll: {len(old):,} rows, expected {n_frozen:,}")
    live = sorted(p for p in CACHE.glob("honolulu_owners_*.parquet") if p.stem[-10:] > start)
    if refresh or not live:
        live.append(CACHE / f"honolulu_owners_{date.today().isoformat()}.parquet")
        _arcgis(LIVE, OWNER_FIELDS).to_parquet(live[-1])
    return (start, old), (live[-1].stem[-10:], pd.read_parquet(live[-1]))


# ─────────────────────────────────────────────────────────────────────────────
# Oʻahu: owner-roll diff

def tokens(name) -> frozenset[str]:
    """Distinctive tokens of an owner name: the surname of each person
    ('SURNAME,GIVEN' part; a one-word surname is kept even when it is a
    generic word, e.g. TO or HALE) and the significant words of an entity.
    A part without a comma in a name that has one is a given-name
    continuation ('LOUIE,THOMAS/CYNTHIA') and adds nothing."""
    if not isinstance(name, str) or not name.strip():
        return frozenset()
    n = name.upper().replace("'", "").replace(".", "")
    person = "," in n
    out: set[str] = set()
    for part in re.split(r"[/&]| AND ", n):
        if "," in part:
            words = [w for w in re.split(r"[\s\-]+", part.split(",")[0]) if w]
            if len(words) == 1 and len(words[0]) > 1:
                out.add(words[0])
                continue
        elif person:
            continue
        else:
            words = re.split(r"[\s\-,]+", part)
        out |= {w for w in words if len(w) > 1 and not w.isdigit() and w not in GENERIC}
    return frozenset(out)


def principal(roll: pd.DataFrame) -> pd.DataFrame:
    """Per parid: principal owners (names, tokens, and the whole set as the
    holder key: a master lessee listed beside each unit's own lessee must not
    make the units one holder's), whether a lessee is listed, and the fee
    side's names and tokens."""
    d = roll[roll["owntype_desc"].ne("Condo Master")].sort_values(["parid", "ownseq"]).copy()
    d["name"] = d["taxbillown1"].fillna(d["taxbillown"])
    d["nn"] = d["name"].fillna("").str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    uniq = d["name"].dropna().unique()
    d["tok"] = d["name"].map({n: tokens(n) for n in uniq}).apply(lambda t: t if isinstance(t, frozenset) else frozenset())
    lessee = d["owntype_desc"].eq("Lessee")
    fee = d["owntype_desc"].isna() | d["owntype_desc"].isin(OWNER_TYPES)
    has_lessee = lessee.groupby(d["parid"]).transform("any")

    def agg(x: pd.DataFrame) -> pd.DataFrame:
        g = x.groupby("parid")
        names = g["nn"].agg(lambda s: frozenset(v for v in s if v))
        return pd.DataFrame({"names": names, "toks": g["tok"].agg(lambda s: frozenset().union(*s)),
                             "holder": names.map(lambda s: "|".join(sorted(s)))})

    p = agg(d[lessee | (~has_lessee & fee)])
    f = agg(d[fee]).rename(columns={"names": "fee_names", "toks": "fee_toks"})[["fee_names", "fee_toks"]]
    out = p.join(f, how="outer").join(has_lessee.groupby(d["parid"]).first().rename("lessee"), how="outer")
    for c in ("names", "toks", "fee_names", "fee_toks"):
        out[c] = out[c].apply(lambda s: s if isinstance(s, frozenset) else frozenset())
    out["holder"] = out["holder"].fillna("")
    return out


def project(parid: pd.Index) -> np.ndarray:
    """A condo unit's project is its 8-digit TMK; a fee-simple parcel's is its
    plat (5 digits: zone, section, plat), where a builder's subdivision lots
    sit (D R Horton, Gentry), each on its own TMK."""
    return np.where(parid.str[8:] == "0000", "plat " + parid.str[:5], parid.str[:8])


def transfers(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Per parid in both rolls: sale-like change and how it is counted."""
    a, b = principal(old), principal(new)
    j = b.join(a, lsuffix="_new", rsuffix="_old", how="inner")

    def pairs(col: str, test) -> np.ndarray:
        return np.array([test(p, q) for p, q in zip(j[f"{col}_new"], j[f"{col}_old"], strict=True)])

    def count(holder: str, n: pd.Series) -> np.ndarray:
        return pd.MultiIndex.from_arrays([j["proj"], j[holder]]).map(n).fillna(0).to_numpy()

    changed, blank = pairs("names", lambda p, q: p != q), pairs("names", lambda p, q: not p or not q)
    raw = changed & ~pairs("toks", lambda p, q: bool(p & q)) & ~blank
    fee_same = pairs("fee_names", lambda p, q: p == q and bool(p)) | pairs("fee_toks", lambda p, q: bool(p & q))
    j["lessee_change"] = raw & (j["lessee_new"] != j["lessee_old"]).to_numpy() & fee_same
    j["sale_like"] = raw & ~j["lessee_change"]
    j["proj"] = project(j.index)
    held = a[a["holder"] != ""].assign(proj=lambda x: project(x.index)).groupby(["proj", "holder"]).size()
    bought = j[j["sale_like"] & (j["holder_new"] != "")].groupby(["proj", "holder_new"]).size()
    j["from_developer"] = count("holder_old", held) >= DEVELOPER_UNITS
    j["bulk_buyer"] = j["sale_like"] & (count("holder_new", bought) >= BULK_BUYER_UNITS)
    j["developer_first_sale"] = j["sale_like"] & j["from_developer"] & ~j["bulk_buyer"]
    j["resale"] = j["sale_like"] & ~j["from_developer"] & ~j["bulk_buyer"]
    return j[["sale_like", "resale", "developer_first_sale", "bulk_buyer", "lessee_change"]]


def oahu_stock() -> pd.DataFrame:
    """Honolulu's improved residential homes (the model's stock), CPR masters
    dropped (as fetch_parcel_stock.honolulu does since September 2026)."""
    hon = fps.fetch("honolulu", False)
    s = fps.honolulu(hon, fps.fetch("units", False)).assign(parid=hon["parid"].astype(str))
    tmk, suffix = s["parid"].str[:8], s["parid"].str[8:]
    master = (suffix == "0000") & tmk.isin(set(tmk[suffix != "0000"]))
    s = s[(s["group"] == "residential") & s["improved"] & ~master]
    print(f"Oʻahu stock: {len(s):,} improved residential homes")
    return pd.DataFrame({"parid": s["parid"], "value": s["value"], "owner_occupied": s["owner"].astype(int),
                         "condo": (s["parid"].str[8:] != "0000").astype(int)})


def cells(stock: pd.DataFrame, t: pd.DataFrame, start: str, end: str, years: float) -> pd.DataFrame:
    m = stock.join(t, on="parid", how="inner")
    m["rb"] = rate_bin(m["value"])
    g = (m.groupby(["rb", "owner_occupied", "condo"])
          .agg(homes=("parid", "size"), resales=("resale", "sum"), developer_first_sales=("developer_first_sale", "sum"),
               bulk_buyer=("bulk_buyer", "sum"), lessee_changes=("lessee_change", "sum")).reset_index())
    print(f"homes in both rolls: {len(m):,} of {len(stock):,}")
    return g.assign(window_start=start, window_end=end, years=round(years, 3))


def review_sample(stock: pd.DataFrame, t: pd.DataFrame, rolls, n: int, path: Path) -> None:
    """A random sample of resales per review band (the n lowest seeded hashes
    of parid, so a rule change only swaps the cases it touches), with every
    owner row in both rolls. Contains names and mailing addresses: gitignored
    or scratch only."""
    m = stock.join(t, on="parid", how="inner")
    m = m[m["resale"]].assign(draw=lambda x: pd.util.hash_pandas_object(x["parid"], index=False, hash_key=REVIEW_KEY))
    rows = {d: r.set_index("parid").sort_values("ownseq") for d, r in rolls}
    cols = ["owntype_desc", "taxbillown", "address1", "address2", "address3"]
    with open(path, "w") as fh:
        for b, (lo, hi) in REVIEW_BANDS.items():
            pick = m[(m["value"] >= lo) & (m["value"] < hi)].nsmallest(n, "draw")
            for i, r in enumerate(pick.itertuples(), 1):
                fh.write(f"{b} #{i} {r.parid} ${r.value / 1e6:.2f}M owner_occupied={r.owner_occupied} condo={r.condo}\n")
                for d, x in rows.items():
                    for _, z in x.loc[[r.parid], cols].iterrows():
                        fh.write(f"   {d} [{z.owntype_desc}] {z.taxbillown} | "
                                 f"{' '.join(str(v) for v in z.iloc[2:] if isinstance(v, str))}\n")
    print(f"review sample ({n} per band) -> {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Maui: composition of the sales behind maui_rates()

def maui_snapshot() -> str:
    """The date of the Maui sales file in maui_sales_extract's cache, once
    each cached raw file is checked against the SHA-256 that
    maui_sources.json records for the committed Maui files, and so for
    maui_rates(). The files come through the extractor's own cache readers
    (never refreshed), so a missing zip fails with its 'save the file from a
    browser' message."""
    recorded = json.loads((mx.OUT / "maui_sources.json").read_text())["files"]
    cached = {**{name: mx.download(name, False) for name in mx.DOWNLOADS}, "dwelling_units": mx.dwelling_units(False)}
    for name, path in cached.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != recorded[name]["sha256"]:
            raise SystemExit(f"{path.relative_to(REPO)} is not the {name} file maui_sources.json records; "
                             "rerun scripts/conveyance/maui_sales_extract.py first")
    return recorded["sales"]["file_date"]


def maui_composition(u: pd.DataFrame, years: float, file_date: str) -> pd.DataFrame:
    """Maui's improved sales behind maui_rates() by assessed-value band (*u*:
    maui_sales_extract.used_sales(), through the latest recorded sale): the
    FY2023-26 count and shares of developer first sales and of sales between
    related individuals (disjoint: a document carries one validity code); and
    repeat_factor, sales (document and parcel) per parcel sold over the
    latest window *years* long. *file_date*: the sales file's (maui_snapshot())."""
    u = u[u["improved"] == 1].assign(band=lambda x: band(x["value"]),
                                     when=lambda x: pd.to_datetime(x["date"], format="%Y/%m/%d"))
    base = u[u["fy"].isin(fc.MAUI_BASE_FY)]
    end = u["when"].max()
    start = end - pd.Timedelta(days=round(years * 365.25))
    w = u[u["when"] > start].explode("parids")
    rep = w.groupby("band").agg(sales=("parids", "size"), parcels=("parids", "nunique"))
    out = base.groupby("band").agg(n=("validity", "size"),
                                   developer_share=("validity", lambda s: (s == MAUI_DEVELOPER).mean()),
                                   related_share=("validity", lambda s: (s == MAUI_RELATED).mean()))
    out["repeat_factor"] = rep["sales"] / rep["parcels"]
    out = out.reindex(list(BANDS)).reset_index()
    for c in ("developer_share", "related_share", "repeat_factor"):
        out[c] = out[c].round(4)
    return out.assign(repeat_window_start=start.date().isoformat(), repeat_window_end=end.date().isoformat(),
                      maui_sales_file_date=file_date)


# ─────────────────────────────────────────────────────────────────────────────
# Oʻahu vs Maui, like for like

def precision() -> dict[str, float]:
    t = pd.read_csv(TALLIES).set_index("band")
    return {b: t.loc[list(src), "judged_sales"].sum() / t.loc[list(src), "reviewed"].sum() for b, src in PRECISION_OF.items()}


def like_for_like(resales_per_year: Num, expected: Num, repeat: Num, precision: Num, developer_share: Num,
                  related_share: Num) -> Num:
    """Oʻahu's resales on Maui's definition over the sales Maui's rates give:
    resales a year x repeat x precision over expected x (1 - developer share
    - related share), the part of Maui's rate the owner diff can see. Floats
    or aligned Series."""
    return resales_per_year * repeat * precision / (expected * (1 - developer_share - related_share))


def pooled_ratio(c: pd.DataFrame, comp: pd.DataFrame, years: float) -> pd.DataFrame:
    rate = (fc.maui_rates().astype({"rb": int, "owner_occupied": int})
            .groupby(["rb", "owner_occupied"])["sales_per_home"].sum().rename("maui_rate"))
    x = c.join(rate, on=["rb", "owner_occupied"]).assign(band=lambda z: band(z["rb"]))
    x["expected"] = x["homes"] * x["maui_rate"].fillna(0)
    r = x.groupby("band")[["homes", "resales", "expected"]].sum().join(comp.set_index("band"))
    r["precision"] = pd.Series(precision())
    r["resale_only"] = r["resales"] / years / r["expected"]
    r["ratio"] = like_for_like(r["resales"] / years, r["expected"], r["repeat_factor"], r["precision"],
                               r["developer_share"], r["related_share"])
    r["rel_se"] = np.sqrt(1 / r["resales"] + 1 / r["n"])
    return r.loc[list(BANDS)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="new OWNINFO snapshot (Maui's files come from maui_sales_extract.py's cache)")
    ap.add_argument("--review-sample", type=int, default=0, help="write N random resales per review band")
    ap.add_argument("--review-out", type=Path, default=REPO / "runs" / "conveyance_sb3028" / "oahu_turnover_review_sample.txt")
    args = ap.parse_args()

    (start, old), (end, new) = owner_rolls(args.refresh)
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    print(f"owner rolls: {start} {len(old):,} rows; {end} {len(new):,} rows; T = {years:.3f} years")
    stock, t = oahu_stock(), transfers(old, new)
    if args.review_sample:
        review_sample(stock, t, [(start, old), (end, new)], args.review_sample, args.review_out)
        return 0
    c = cells(stock, t, start, end, years)
    c.to_csv(OUT_CELLS, index=False)
    tot = c[["homes", "resales", "developer_first_sales", "bulk_buyer", "lessee_changes"]].sum()
    print((tot / [1, years, years, years, years]).round(1).rename(lambda s: s if s == "homes" else f"{s}/yr").to_string())

    file_date = maui_snapshot()
    print(f"Maui sales file of {file_date}")
    comp = maui_composition(mx.used_sales(last_fy=mx.LAST_FY + 1), years, file_date)
    comp.to_csv(OUT_MAUI, index=False)
    print(comp.to_string(index=False))

    r = pooled_ratio(c, comp, years)
    print("\nOʻahu/Maui turnover, pooled over occupancy and condo (like for like = ratio):")
    print(r[["homes", "resales", "expected", "repeat_factor", "precision", "developer_share", "related_share",
             "resale_only", "ratio", "rel_se"]].round(3).to_string())
    return 0

if __name__ == "__main__":
    sys.exit(main())
