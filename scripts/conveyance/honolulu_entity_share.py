"""Oʻahu: how many high-value home sales have a company as the seller, the
sales that could close as a sale of the company instead of a deed.

Usage:
  python scripts/conveyance/honolulu_entity_share.py            # cached pulls
  python scripts/conveyance/honolulu_entity_share.py --refresh  # new OWNDAT pull, dated today

Writes packages/tax_modeler/src/tax_modeler/data/raw/conveyance/
honolulu_entity_share_by_band.csv, which forecast_conveyance_sb3028.py reads
(entity_share(): seller_weighted_entity_share by band_lo). One row per
assessed-value band ($4-6M, $6-10M, $10M+, the model's entity bands):
  homes_nonowner_occupied, entity_share_nonowner_occupied,
  homes_owner_occupied, entity_share_owner_occupied
      the model's Oʻahu homes and the share whose tax-bill owner is an entity;
  resales_nonowner_occupied, resales_owner_occupied, developer_first_sales
      Oʻahu's sales of those homes, December 2023 to September 2026, from
      oahu_owner_turnover_cells_2023_2026.csv: the seller weights;
  seller_weighted_entity_share
      the share of sales whose seller is an entity (the central estimate);
  seller_entity_share_resales
      a check: the same share read from the sellers' own names;
  owndat_taxyr, owndat_pulled, resale_window_start, resale_window_end.
Aggregates only: no names, no parcel IDs. The raw pulls carry owner names and
are cached under runs/conveyance_sb3028/parcel_cache/ (gitignored); the
latest honolulu_owndat_<pull date>.parquet is used.

Sources (public; no key):
  - OWNDAT, the City's tax-bill owner table: https://services.arcgis.com/
    tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer/6
    (one row per parcel or condominium unit, tax year 2027, updated daily;
    pulled 2026-09-25, 326,770 rows). The name is the tax-bill owner (the
    lessee on a leasehold) with its second line, if any.
  - The model's Oʻahu homes: ASMTPITT via fetch_parcel_stock.honolulu(),
    STOCK_GROUPS["Honolulu"] ("residential"), improved; owner-occupied as
    defined there (a home exemption, tax year 2026).
  - Sales: oahu_owner_turnover_cells_2023_2026.csv, and for the check the two
    owner rolls and transfer rules of oahu_owner_turnover.py.

Owners (kind): "entity" for a legal form or company word (LLC, Inc, Corp,
LP, LLP, LLLP, Ltd, Limited, partnership, company, KK, ...; words such as
Holdings, Properties or Co only in a name that is not a person's SURNAME,GIVEN),
"trust" for a trustee designation (TR, TRS, TTEE, TRUSTEE, which wins over an
entity word: a trust company or an LLC can be trustee) or the word TRUST, and
"individual" for everything else (people, estates, the few government and
charitable owners left in the stock). An entity holding DEVELOPER_UNITS or more
records in the home's project (a condo's 8-digit TMK, a parcel's plat), as the
turnover script's developer rule, is a developer or bulk holder, not an
entity here: its units sell one by one, by deed.

Seller weights. HRS chapter 247 taxes the deed, so a sale can escape HD2 when
the seller is an entity that holds the one home and the buyer buys the entity.
The model needs, per price band, the share of its sales with such a seller.
Non-owner-occupied homes are far more often entity-held (45/52/62%) than
owner-occupied ones (4/9/22%), so the answer turns on how many sales come
from each. Three weightings:
  - stock (the homes in each class): ignores that non-owner-occupied homes
    change hands far more often (26/38/56%; the hard-coded ENTITY_SHARE
    .258/.381/.498 was this, on all residential-class records);
  - Maui's buyer mix (maui_rates(), the model's own split of non-owner
    purchases by occupancy): Maui's owner_occupied_now comes from the April
    2026 listing, whose exemptions were claimed by December 31, 2025, after
    nearly all FY2023-26 sales, so it records the buyer's status, not the
    seller's. Under $2M, 8% of Maui's non-owner purchases and 66% of its owner
    purchases are of homes owner-occupied now; at $4-6M, 4% of non-owner
    purchases. As seller weights it has non-owner buyers buy almost only from
    non-owner-occupied homes (39/47/62%);
  - Oʻahu's own sales (used): the resales of the model's homes between the two
    owner rolls, by occupancy, plus developer first sales, whose seller holds
    a whole project and cannot sell it for one unit (a zero share; 70 of 221
    sales at $4-6M, all condominium units; without them $4-6M is 32%). Oʻahu's
    occupancy is also read inside the window (tax year 2026), but over all
    buyers it measures which homes sell, not who buys them.
The check reads each seller's kind from the older roll for the same sales:
22/39/58% against 22/38/60% from the weights (221, 71 and 19 sales). Both are
shares of all sales. Among non-owner purchases alone the share is not observed
(neither roll records the buyer's schedule): it is the same if entity sellers
sell to non-owner buyers as often as other sellers do, and higher by at most
the inverse of the non-owner share of purchases (about 1.2 on Maui at $4M+) if
they sell only to them. The bands are assessed values; the model applies them
to sale prices.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "runs" / "conveyance_sb3028" / "parcel_cache"
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT = DATA / "honolulu_entity_share_by_band.csv"
CELLS = DATA / "oahu_owner_turnover_cells_2023_2026.csv"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Read-only: owner rolls and transfer rules; with them fetch_parcel_stock (the
# Oʻahu homes) and the forecast (STOCK_GROUPS, RATE_EDGES, maui_rates).
ot = _load("oahu_owner_turnover", REPO / "scripts" / "conveyance" / "oahu_owner_turnover.py")
fps, fc = ot.fps, ot.fc

OWNDAT = "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer/6"
OWNDAT_FIELDS = "parid,taxyr,taxbillowner,own2"
BANDS = ((4e6, 6e6), (6e6, 10e6), (10e6, np.inf))   # assessed value; the model's entity bands
DEVELOPER_UNITS = ot.DEVELOPER_UNITS                 # records one owner holds in a project

# ─────────────────────────────────────────────────────────────────────────────
# Owners

TRUSTEE = re.compile(r"\b(?:TRS?|TTEES?|TRUSTEES?)\b")
LEGAL_FORM = re.compile(r"\b(?:LLC|L L C|PLLC|LLLP|LLP|LP|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|"
                        r"PARTNERSHIP|PTNSHP|COMPANY|KK|GK|PTY|PLC|GMBH)\b")
BUSINESS_WORD = re.compile(r"\b(?:CO|PARTNERS|PTNRS|ASSOCIATES|HOLDINGS?|PROPERTIES|INVESTMENTS?|VENTURES?|"
                           r"ENTERPRISES?|GROUP)\b")
TRUST = re.compile(r"\bTRUSTS?\b")
PERSON = re.compile(r"[A-Z],[A-Z]")          # the roll writes people SURNAME,GIVEN


def kind(name: str | None) -> str:
    """'entity', 'trust' or 'individual' for an owner name as the roll writes
    it. Words count whole (INCE, CORPUZ, TRAN, COLTON are people) and dotted
    abbreviations are closed up (L.L.C. is LLC, L.P. is LP)."""
    raw = name.upper() if isinstance(name, str) else ""
    n = re.sub(r"\b(?:[A-Z]\.){2,}", lambda m: m.group(0).replace(".", ""), raw)
    n = re.sub(r"[.,;:()\"]", " ", n)
    if TRUSTEE.search(n):
        return "trust"
    if LEGAL_FORM.search(n) or (BUSINESS_WORD.search(n) and not PERSON.search(raw)):
        return "entity"
    return "trust" if TRUST.search(n) else "individual"


def project(parid: pd.Series) -> pd.Series:
    return pd.Series(ot.project(pd.Index(parid)), index=parid.index)


def owner_kinds(roll: pd.DataFrame) -> pd.Series:
    """Per parid of OWNDAT: the tax-bill owner's kind, 'holder' for an entity
    with DEVELOPER_UNITS or more records in the project."""
    parid = roll["parid"].astype(str)
    name = (roll["taxbillowner"].fillna("").str.strip() + " " + roll["own2"].fillna("").str.strip()).str.strip()
    k = name.map({v: kind(v) for v in name.unique()})
    key = name.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    held = key.groupby([project(parid), key]).transform("size")
    k[(k == "entity") & (held >= DEVELOPER_UNITS) & (key != "")] = "holder"
    return pd.Series(k.to_numpy(), index=parid.to_numpy())


def seller_kinds(roll: pd.DataFrame) -> pd.Series:
    """Per parid of an owner roll of oahu_owner_turnover.py: the kind of its
    first principal owner (lessee rows if any, else fee, owner and untyped
    rows; Condo Master rows dropped, as that script's principal())."""
    d = roll[roll["owntype_desc"].ne("Condo Master")]
    lessee = d["owntype_desc"].eq("Lessee")
    fee = d["owntype_desc"].isna() | d["owntype_desc"].isin(ot.OWNER_TYPES)
    d = d[lessee | (~lessee.groupby(d["parid"]).transform("any") & fee)]
    first = d.sort_values(["parid", "ownseq"]).drop_duplicates("parid").set_index("parid")
    name = first["taxbillown1"].fillna(first["taxbillown"])
    return name.map({v: kind(v) for v in name.dropna().unique()}).fillna("individual")


# ─────────────────────────────────────────────────────────────────────────────
# Pull

def owndat(refresh: bool) -> tuple[str, pd.DataFrame]:
    """(pull date, roll): the latest cached OWNDAT pull, or a new one (with
    --refresh or no cache), paged by oahu_owner_turnover._arcgis, which stops
    unless it has as many rows and unique object IDs as the service counts. A
    pull is cached only if its parids are unique too (one row per record)."""
    cached = sorted(CACHE.glob("honolulu_owndat_*.parquet"))
    if cached and not refresh:
        return cached[-1].stem[-10:], pd.read_parquet(cached[-1])
    df = ot._arcgis(OWNDAT, f"objectid,{OWNDAT_FIELDS}").drop(columns="objectid")
    if df["parid"].nunique() != len(df):
        raise SystemExit(f"OWNDAT: {len(df):,} rows but {df['parid'].nunique():,} parids; not cached")
    pulled = date.today().isoformat()
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"honolulu_owndat_{pulled}.parquet"
    df.to_parquet(path)
    print(f"OWNDAT: {len(df):,} rows -> {path.relative_to(REPO)}")
    return pulled, df


def homes(kinds: pd.Series) -> pd.DataFrame:
    """The model's Oʻahu homes (parid, value, owner-occupied) with their
    owner's kind; a home missing from OWNDAT counts as an individual's."""
    hon = fps.fetch("honolulu", False)
    s = fps.honolulu(hon, fps.fetch("units", False)).assign(parid=hon["parid"].astype(str))
    s = s[s["group"].isin(fc.STOCK_GROUPS["Honolulu"]) & s["improved"]]
    k = s["parid"].map(kinds)
    print(f"Oʻahu homes: {len(s):,}; not in OWNDAT: {k.isna().sum():,}")
    return pd.DataFrame({"parid": s["parid"], "value": s["value"], "owner_occupied": s["owner"].astype(int),
                         "kind": k.fillna("individual")})


# ─────────────────────────────────────────────────────────────────────────────
# Shares

def entity_by_band(h: pd.DataFrame, cells: pd.DataFrame) -> pd.DataFrame:
    """Per band: homes and entity share by occupancy, the sales that weight
    them (resales by occupancy; developer first sales at a zero share) and the
    seller-weighted share."""
    rows = []
    for lo, hi in BANDS:
        x = h[(h["value"] >= lo) & (h["value"] < hi)]
        c = cells[(cells["rb"] >= lo) & (cells["rb"] < hi)]
        r = {"band_lo": lo, "band_hi": hi}
        for occ, tag in ((0, "nonowner_occupied"), (1, "owner_occupied")):
            k = x.loc[x["owner_occupied"] == occ, "kind"]
            r[f"homes_{tag}"] = len(k)
            r[f"entity_share_{tag}"] = float((k == "entity").mean()) if len(k) else 0.0
        for occ, tag in ((0, "nonowner_occupied"), (1, "owner_occupied")):
            r[f"resales_{tag}"] = int(c.loc[c["owner_occupied"] == occ, "resales"].sum())
        r["developer_first_sales"] = int(c["developer_first_sales"].sum())
        sales = r["resales_nonowner_occupied"] + r["resales_owner_occupied"] + r["developer_first_sales"]
        r["seller_weighted_entity_share"] = (
            (r["resales_nonowner_occupied"] * r["entity_share_nonowner_occupied"]
             + r["resales_owner_occupied"] * r["entity_share_owner_occupied"]) / sales if sales else 0.0)
        rows.append(r)
    return pd.DataFrame(rows)


def resale_check(h: pd.DataFrame, window: tuple[str, str]) -> pd.Series:
    """Per band (band_lo): the share of the same sales (resales and developer
    first sales) whose seller, the principal owner in the older roll, is an
    entity; developer first sales count as not. The cached owner rolls must be
    the ones behind the turnover cells (*window*)."""
    (start, old), (end, new) = ot.owner_rolls(False)
    if (start, end) != window:
        raise SystemExit(f"owner rolls {start} to {end}, turnover cells {window[0]} to {window[1]}: "
                         "rerun scripts/conveyance/oahu_owner_turnover.py")
    t = ot.transfers(old, new)
    m = h.join(t, on="parid", how="inner")
    m = m[m["resale"] | m["developer_first_sale"]]
    ent = m["resale"] & m["parid"].map(seller_kinds(old)).eq("entity")
    return pd.Series({lo: float(ent[(m["value"] >= lo) & (m["value"] < hi)].mean()) for lo, hi in BANDS})


def alternatives(h: pd.DataFrame, tab: pd.DataFrame) -> pd.DataFrame:
    """The weight on non-owner-occupied homes, and the share it gives, under
    stock weights, Maui's buyer mix and Oʻahu's sales (printed only)."""
    edges = np.array(fc.RATE_EDGES)
    rate = (fc.maui_rates().query("category == 'nonowner'").astype({"rb": float, "owner_occupied": int})
            .groupby(["rb", "owner_occupied"])["sales_per_home"].sum())
    rb = edges[np.searchsorted(edges, h["value"].to_numpy(), side="right") - 1]
    n = pd.Series(pd.MultiIndex.from_arrays([rb, h["owner_occupied"]]).map(rate), index=h.index).fillna(0)
    out = {}
    for r in tab.itertuples():
        inb = (h["value"] >= r.band_lo) & (h["value"] < r.band_hi)
        own = h["owner_occupied"] == 1
        sales = r.resales_nonowner_occupied + r.resales_owner_occupied + r.developer_first_sales
        w = {"stock": r.homes_nonowner_occupied / (r.homes_nonowner_occupied + r.homes_owner_occupied),
             "maui_nonowner_buyers": n[inb & ~own].sum() / n[inb].sum(),
             "oahu_sales": r.resales_nonowner_occupied / sales}
        wo = {"stock": 1 - w["stock"], "maui_nonowner_buyers": 1 - w["maui_nonowner_buyers"],
              "oahu_sales": r.resales_owner_occupied / sales}
        out[r.band_lo] = {**{f"w_{k}": v for k, v in w.items()},
                          **{f"share_{k}": w[k] * r.entity_share_nonowner_occupied + wo[k] * r.entity_share_owner_occupied
                             for k in w}}
    return pd.DataFrame(out).T


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="new OWNDAT pull")
    args = ap.parse_args()
    pulled, roll = owndat(args.refresh)
    taxyr = "/".join(str(int(y)) for y in sorted(roll["taxyr"].dropna().unique()))
    print(f"OWNDAT pulled {pulled}: {len(roll):,} rows, tax year {taxyr}")
    h = homes(owner_kinds(roll))
    cells = pd.read_csv(CELLS)
    window = (cells["window_start"].iloc[0], cells["window_end"].iloc[0])
    tab = entity_by_band(h, cells)
    tab["seller_entity_share_resales"] = tab["band_lo"].map(resale_check(h, window))
    tab = tab.assign(owndat_taxyr=taxyr, owndat_pulled=pulled, resale_window_start=window[0], resale_window_end=window[1])
    share_cols = [c for c in tab.columns if "share" in c]
    tab[share_cols] = tab[share_cols].round(4)
    out = tab.assign(band_lo=tab["band_lo"].astype(int),
                     band_hi=["inf" if np.isinf(x) else str(int(x)) for x in tab["band_hi"]])
    out.to_csv(OUT, index=False)
    pd.set_option("display.width", 250)
    print(tab.drop(columns=list(tab.columns[-4:])).to_string(index=False))
    print("\nWeight on non-owner-occupied homes, and the share it gives:")
    print(alternatives(h, tab).round(3).to_string())
    print(f"-> {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
