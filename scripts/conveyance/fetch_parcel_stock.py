"""Residential housing stock by value and owner-occupancy, by county.

Usage:
  python scripts/conveyance/fetch_parcel_stock.py            # fetch (cached) and bin
  python scripts/conveyance/fetch_parcel_stock.py --refresh  # re-download

Writes, under packages/tax_modeler/src/tax_modeler/data/raw/conveyance/:
  - parcel_stock_by_value.csv (county, group, value_lo, owner_occupied,
    improved, condo, count, sum_value) for Honolulu, Hawaii and Kauai, which
    forecast_conveyance_sb3028.py reads;
  - parcel_stock_by_value_hawaii_capped.csv, the same for Hawaiʻi County at
    its as-assessed (capped) homeowner values, for a sensitivity;
  - hawaii_homeowner_cap_factors.csv (zone, pittcode, factor, weight), the
    factors that uncap Hawaiʻi County's homeowner values.
Raw pulls are cached under runs/conveyance_sb3028/parcel_cache/ (gitignored).

Sources, all public ArcGIS REST services (no key; paged queries), pulled
September 2026:
  - Honolulu: the Real Property Assessment Division's ASMTPITT table on the
    City's open data hub (CadastralTables/FeatureServer/10): one row per
    parcel or condominium unit, tax year 2026. Residential = tax rate code 1
    (incl. override 11 Residential A and 14 transient vacation). Owner-
    occupied = an exemption of $300,000 or less on a residential record that
    is not Residential A or TVR (both are non-owner-occupied by definition);
    that flags 99% of RPAD's FY2027 home exemptions. The Revenue-Modeling-TOD
    project uses the same tables.
  - Hawaiʻi County: the county's parcel layer on the State GIS portal
    (geodata.hawaii.gov ParcelsZoning/MapServer/5, county data of April 27,
    2026): pittcode and the homeowner flag. Parcels only; a condominium
    project is one master parcel holding all its units' value.
  - Kauaʻi: the County's TY26_PropertyTaxData layer (services1.arcgis.com/
    0DaVqrPt2eyXUS9g, item fe46d9ae37e6471e84bd14baf1df42e1, public on the
    County's open data hub; last edited February 11, 2026, so the notice-stage
    roll before appeals): one row per parcel or CPR unit. Value = MODTOT,
    total market value; ASSDTOT is capped for homesteads (3% a year; median
    0.74 of market in the owner-occupied class). Residential = CLASS 1
    (non-owner-occupied) or 2 (vacation rental); owner-occupied = OVRCLASS 8
    or 10 (owner-occupied, mixed-use); records flagged non-taxable in
    RESTRICT3 (government, DHHL, right of way, utilities, condo masters) and
    fully exempt ones (TOTEXEMPT >= ASSDTOT) dropped. The same County's
    BUILDINGS_public layer (item 809e5cd7ba3f45548d5c16dc6503be08) gives the
    building cards. That item is public and listed on the County's open data
    hub, but its service description says it is limited to internal and
    partner official use; the improved flag is the only thing taken from it.
  - Maui (a control for the cap check only): State GIS ParcelsZoning/
    MapServer/30, county data of April 15, 2026.
  - Dwelling units per TMK: the statewide parcels of June 2025 joined to
    county dwelling records (UH tmk_state_2025_dwelling_data).

Definitions match maui_residential_stock_2026.csv (the model's template, from
Maui's full assessment listing). Fully exempt records (exemption >= value:
government, charitable) are dropped; "improved" = building value > 0
(Kauaʻi: a building card, or a CPR unit in a multi-unit complex). Groups:
  - "residential";
  - "ag_dwelling": an agricultural parcel with a home (building value of
    $100,000 or more; Kauaʻi: a dwelling card). Kept as its own group, but
    the stock method counts these as homes on Hawaiʻi Island and Kauaʻi
    (STOCK_GROUPS in forecast_conveyance_sb3028.py), scored at Maui's
    residential rates; Oʻahu's stay out, as Maui's own ag-land homes do;
  - "condo_unit": Hawaiʻi's apartment-class project parcels split into units
    (split_condo_masters);
  - "multifamily" (a parcel with 5+ dwelling units, which HD2 values per
    unit) and "large_other" (a non-condo residential record of $20M+, other
    than Honolulu's Residential A): both left out of the stock method, as in
    the Maui extract, whose actual sales of such property stay in Maui's total;
  - "other": everything else, incl. Honolulu's CPR master records, which
    repeat their units' value (RPAD's FY2027 Residential net is $200.65B;
    ASMTPITT gives $201.61B without the masters and $223.14B with them).
condo = 1 for a condominium or CPR unit: Honolulu parid suffix not 0000;
Kauaʻi PARTXT suffix not 0000 or TYPE 'CPRX Multi-Unit Complex'; Hawaiʻi's
condo_unit group. On Oʻahu and Kauaʻi that includes horizontal CPR units,
which are usually houses (Kauaʻi: 3,736 of its 8,765 improved residential
CPR records).

Hawaiʻi County's homeowner class carries a 3% assessment cap (the County's
Homeowner Program sheet, hawaiipropertytax.com), so its homeowner values sit
below market, which Maui's and Honolulu's do not. Within the same TMK plat,
homeowner parcels are valued at 0.78 of non-owner neighbors (pittcode 100;
0.70-0.93 by zone) and 0.87 (pittcode 500); the same test gives 0.99 on Maui
and 1.00 on Honolulu. cap_factors estimates that ratio per zone and
pittcode, and uncap divides owner-occupied residential and ag-dwelling
values by it. A condominium project's units share its average value, only
the owner units' part of which is capped, so uncap divides every unit of
the project by s·f + 1 − s (s = its owner share, f = the zone's
residential factor).

Changes (September 2026): Honolulu's CPR masters (533 improved residential
records, $1.07B) moved to "other"; Hawaiʻi condo owners set project by
project from exemptions, replacing a share that closed the gap to the
county's homeowner-record count (0.67, an incomplete subtraction; now about
0.26); Hawaiʻi homeowner values uncapped (condominium units project by
project, not owner units by the full factor); Kauaʻi added; condo column
added; a pull is cached only if complete (fetch).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "runs" / "conveyance_sb3028" / "parcel_cache"
DATA = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance"
OUT = DATA / "parcel_stock_by_value.csv"
OUT_CAPPED = DATA / "parcel_stock_by_value_hawaii_capped.csv"
OUT_FACTORS = DATA / "hawaii_homeowner_cap_factors.csv"

PARCELS = "https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer"
KAUAI = "https://services1.arcgis.com/0DaVqrPt2eyXUS9g/arcgis/rest/services"
DWELLINGS = "https://services1.arcgis.com/x4h61KaW16vFs7PM/arcgis/rest/services/tmk_state_2025_dwelling_data/FeatureServer/0"
GIS_FIELDS = "tmk,pittcode,homeowner,landvalue,bldgvalue,landexempt,bldgexempt"
SOURCES = {
    "honolulu": ("https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer/10",
                 "parid,taxratecode,ovrclass,landvalue,buildingvalue,landexemption,buildingexemption", 2000),
    "hawaii": (f"{PARCELS}/5", GIS_FIELDS, 1000),
    "maui_gis": (f"{PARCELS}/30", GIS_FIELDS, 1000),
    "kauai": (f"{KAUAI}/TY26_PropertyTaxData/FeatureServer/0",
              "PARTXT,TMK,TYPE,CLASS,OVRCLASS,TAXCLASS,MODTOT,ASSDTOT,TOTEXEMPT,TAXABLE,RESTRICT3,TAXYR", 2000),
    "kauai_bldg": (f"{KAUAI}/BUILDINGS_public/FeatureServer/0", "PARTXT,FLAG,TABLE_", 2000),
    # Dwelling units per TMK: Hawaii's condominium projects, and 5+ unit buildings.
    "hawaii_units": (DWELLINGS, "TMK,COUNT_Units", 2000),
    "units": (DWELLINGS, "TMK,county,COUNT_Units", 2000),
}
WHERE = {"hawaii_units": "county='Hawaii' AND COUNT_Units>1", "units": "COUNT_Units>=5"}
MULTIFAMILY_UNITS = 5          # HD2 values these per unit
LARGE_NON_CONDO = 20_000_000   # probable apartment/resort parcels without a unit count; left to the Maui calibration
HOME_EXEMPTION_MAX = 300_000   # Honolulu: a homeowner exemption, not a government/charitable full exemption
HAWAII_HOMEOWNER_RECORDS = 45_162   # tax year 2026-27 statewide report, Homeowner-class records (a check only)
AG_DWELLING_MIN_BLDG = 100_000
GIS_RES_CODES = (100, 900, 1000, 1100, 1200)   # State GIS pittcodes; Maui-style: homeowner 900, rentals 1000-1200

# Hawaii homeowner cap: within-plat comparison of improved homes.
CAP_PITTCODES = (100, 500)     # residential, agricultural
CAP_MIN_BLDG = 100_000
CAP_MIN_PER_SIDE = 3           # homeowner and non-owner parcels a plat needs
CAP_MIN_WEIGHT = 30            # below this, a zone takes the county pool for its pittcode

KAUAI_TAXYR = 2026
KAUAI_CONDO_TYPE = "CPRX Multi-Unit Complex"
KAUAI_BUILDING_FLAGS = (0, 20, 30, 40, 60)   # a card, multi-unit complex or outbuilding (not 10 missing, 50 untaxed)

# Same value bins as the Maui extract (maui_sales_extract.py).
EDGES = [0, 3e5, 6e5, 8e5, 1e6, *np.arange(1.25e6, 6e6 + 1, 2.5e5), *np.arange(6.5e6, 1e7 + 1, 5e5),
         12.5e6, 15e6, 20e6, 30e6]
COLUMNS = ["county", "group", "value_lo", "owner_occupied", "improved", "condo", "count", "sum_value"]


def _get(name: str, url: str, params: dict, want: str, what: str) -> dict:
    """One ArcGIS REST request, retried. An HTTP-200 body with an 'error'
    key, or without the key asked for (want), is a failure like a timeout."""
    err: object = None
    for attempt in range(5):
        try:
            d = requests.get(url, timeout=120, params={**params, "f": "json"}).json()
            if "error" not in d and want in d:
                return d
            err = d.get("error", f"no '{want}' in the response")
        except (requests.RequestException, json.JSONDecodeError) as e:
            err = e
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"{name}: failed at {what}: {err}")


def fetch(name: str, refresh: bool) -> pd.DataFrame:
    """A source's rows (SOURCES, WHERE), from the cache or a paged pull. A
    pull is cached only if it is complete: as many rows, and as many unique
    object IDs, as the service counts for the same where clause
    (returnCountOnly). The cache keeps the requested fields only."""
    path = CACHE / f"{name}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    url, fields, page = SOURCES[name]
    where = WHERE.get(name, "1=1")
    oid = next(f["name"] for f in _get(name, url, {}, "fields", "the layer description")["fields"]
               if f["type"] == "esriFieldTypeOID")                   # OBJECTID or objectid, by service
    n = int(_get(name, f"{url}/query", {"where": where, "returnCountOnly": "true"}, "count", "the count")["count"])
    rows, offset = [], 0
    while offset < n:
        feats = _get(name, f"{url}/query", {
            "where": where, "outFields": f"{oid},{fields}", "returnGeometry": "false", "orderByFields": oid,
            "resultOffset": offset, "resultRecordCount": page}, "features", f"offset {offset:,}")["features"]
        if not feats:
            break
        rows += [f["attributes"] for f in feats]
        offset += len(feats)
    df = pd.DataFrame(rows)
    ids = df[oid].nunique() if len(df) else 0
    if len(df) != n or ids != n:
        raise SystemExit(f"{name}: {len(df):,} rows ({ids:,} unique {oid}) of {n:,}; not cached")
    df = df.drop(columns=oid)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    print(f"{name}: {len(df):,} rows")
    return df


def _units_map(units: pd.DataFrame, county: str) -> pd.Series:
    u = units[units["county"] == county]
    return u.groupby("TMK")["COUNT_Units"].max()


def _zone(tmk: pd.Series) -> pd.Series:
    return (tmk // 10**7) % 10          # 9-digit TMK: island, zone, section, plat (3), parcel (3)


def _cpr_master(parid: pd.Series) -> pd.Series:
    """Honolulu: a parcel record (suffix 0000) whose TMK also has unit rows.
    It repeats its units' value, so it is not a home."""
    parcel = parid.str[8:] == "0000"
    return parcel & parid.str[:8].isin(parid[~parcel].str[:8])


def honolulu(df: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    v = df["landvalue"].fillna(0) + df["buildingvalue"].fillna(0)
    ex = df["landexemption"].fillna(0) + df["buildingexemption"].fillna(0)
    ovr = df["ovrclass"].fillna("").astype(str).str.strip()
    code = df["taxratecode"].astype(str).str.strip()
    parid = df["parid"].astype(str)
    parcel = parid.str[8:] == "0000"                                        # not a condo unit
    n_units = ("1" + parid.str[:8]).astype(int).map(_units_map(units, "Honolulu")).fillna(0)
    full = (v > 0) & (ex >= v)
    res = (code == "1") & ~full
    owner = res & (ex > 0) & (ex <= HOME_EXEMPTION_MAX) & ~ovr.isin(["11", "14"])
    mf = res & parcel & (n_units >= MULTIFAMILY_UNITS)
    large = res & parcel & ~mf & (v >= LARGE_NON_CONDO) & (ovr != "11")   # Residential A is a single home
    ag = code.isin(["5", "0"]) & (df["buildingvalue"].fillna(0) >= AG_DWELLING_MIN_BLDG) & ~full
    return pd.DataFrame({"value": v, "owner": owner, "improved": df["buildingvalue"].fillna(0) > 0,
                         "condo": (~parcel).astype(int),
                         "group": np.select([_cpr_master(parid), mf, large, res, ag],
                                            ["other", "multifamily", "large_other", "residential", "ag_dwelling"],
                                            "other")})


def county_gis(df: pd.DataFrame, units: pd.DataFrame, county: str) -> pd.DataFrame:
    """State-portal parcel layer (Hawaii County; Maui for the cap check).
    Condominium units are not separate records: a project is one master
    parcel holding every unit's value (the apartment class, pittcode 200),
    split later into units."""
    v = df["landvalue"].fillna(0) + df["bldgvalue"].fillna(0)
    ex = df["landexempt"].fillna(0) + df["bldgexempt"].fillna(0)
    code = df["pittcode"].fillna(-1).astype(int)
    n_units = df["tmk"].map(_units_map(units, county)).fillna(0)
    full = (v > 0) & (ex >= v)
    res = code.isin(GIS_RES_CODES) & ~full
    mf = res & (n_units >= MULTIFAMILY_UNITS)
    large = res & ~mf & (v >= LARGE_NON_CONDO)
    condo = (code == 200) & ~full
    ag = (code == 500) & (df["bldgvalue"].fillna(0) >= AG_DWELLING_MIN_BLDG) & ~full
    return pd.DataFrame({"value": v, "owner": df["homeowner"].eq("Y"), "improved": df["bldgvalue"].fillna(0) > 0,
                         "condo": 0, "tmk": df["tmk"],
                         "group": np.select([mf, large, res, ag, condo],
                                            ["multifamily", "large_other", "residential", "ag_dwelling", "condo_master"],
                                            "other")})


def home_exemption(raw: pd.DataFrame) -> float:
    """Hawaii: mean home exemption, over homeowner-flagged residential
    (pittcode 100) parcels that are not fully exempt."""
    v = raw["landvalue"].fillna(0) + raw["bldgvalue"].fillna(0)
    ex = raw["landexempt"].fillna(0) + raw["bldgexempt"].fillna(0)
    return float(ex[(raw["pittcode"] == 100) & raw["homeowner"].eq("Y") & ~((v > 0) & (ex >= v))].mean())


def split_condo_masters(s: pd.DataFrame, raw: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """Hawaii: replace each condominium project parcel with its units, each at
    the project's average value per unit. Unit counts come from the dwelling
    layer where present; otherwise from the median value per unit of projects
    that have counts. A project flagged homeowner has as many owner-occupied
    units as its exemptions hold home exemptions (at the county mean), up to
    its unit count; an unflagged project has none."""
    m = s[s["group"] == "condo_master"].join(raw[["landexempt", "bldgexempt"]]).merge(
        units.rename(columns={"TMK": "tmk", "COUNT_Units": "units"}), on="tmk", how="left")
    per_unit = (m["value"] / m["units"]).median()
    m["units"] = m["units"].fillna((m["value"] / per_unit).round().clip(lower=1)).astype(int)
    ex = m["landexempt"].fillna(0) + m["bldgexempt"].fillna(0)
    avg_ex = home_exemption(raw)
    k = np.where(m["owner"], np.minimum(m["units"], (ex / avg_ex).round()), 0).astype(int)
    n = m["units"].to_numpy()
    reps = np.r_[k, n - k]
    units_out = pd.DataFrame({"value": np.repeat(np.r_[m["value"] / n, m["value"] / n], reps),
                              "owner": np.repeat(np.r_[np.ones(len(m), bool), np.zeros(len(m), bool)], reps),
                              "improved": True, "condo": 1, "tmk": np.repeat(np.r_[m["tmk"], m["tmk"]], reps),
                              "group": "condo_unit"})
    room = HAWAII_HOMEOWNER_RECORDS - int((raw["homeowner"].eq("Y") & (s["group"] != "condo_master")).sum())
    print(f"hawaii condo projects: {len(m)} -> {n.sum():,} units; home exemption ${avg_ex:,.0f}; "
          f"owner units {k.sum():,} (share in flagged projects {k.sum() / n[m['owner'].to_numpy()].sum():.3f}); "
          f"homeowner records left after non-condo parcels {room:,} {'>=' if room >= k.sum() else '< (CHECK)'} owner units")
    return pd.concat([s[s["group"] != "condo_master"], units_out], ignore_index=True)


def within_plat_ratio(plat: pd.Series, owner: pd.Series, value: pd.Series) -> tuple[float, int]:
    """Owner-occupied / non-owner value ratio within the same TMK plat:
    exp of the weighted mean, over plats with at least CAP_MIN_PER_SIDE of
    each, of the difference in median log value; weight = the smaller side."""
    d = pd.DataFrame({"plat": plat, "owner": owner.astype(bool), "lv": np.log(value)})
    g = (d.groupby(["plat", "owner"])["lv"].agg(["median", "size"]).unstack("owner")
         .reindex(columns=pd.MultiIndex.from_product([["median", "size"], [False, True]])).dropna())
    w = np.minimum(g[("size", True)], g[("size", False)])
    w = w[w >= CAP_MIN_PER_SIDE]
    if w.empty:
        return 1.0, 0
    diff = (g[("median", True)] - g[("median", False)])[w.index]
    return float(np.exp((diff * w).sum() / w.sum())), int(w.sum())


def _cap_sample(raw: pd.DataFrame, codes: tuple[int, ...]) -> pd.DataFrame:
    """State GIS parcels in the given pittcodes with a building of CAP_MIN_BLDG
    or more (fully exempt ones included): tmk, pittcode, plat, owner, value."""
    v = raw["landvalue"].fillna(0) + raw["bldgvalue"].fillna(0)
    x = raw.assign(value=v)[raw["pittcode"].isin(codes) & (raw["bldgvalue"].fillna(0) >= CAP_MIN_BLDG) & (v > 0)]
    return x.assign(plat=x["tmk"] // 1000, owner=x["homeowner"].eq("Y"))[["tmk", "pittcode", "plat", "owner", "value"]]


def cap_factors(raw: pd.DataFrame) -> pd.DataFrame:
    """Hawaii: homeowner/non-owner within-plat value ratio per zone x pittcode
    (_cap_sample). A zone with less than CAP_MIN_WEIGHT of weight takes the
    county pool for its pittcode."""
    def ratio(g: pd.DataFrame) -> tuple[float, int]:
        return within_plat_ratio(g["plat"], g["owner"], g["value"])
    rows = []
    for pc, xp in _cap_sample(raw, CAP_PITTCODES).groupby("pittcode"):
        pool = ratio(xp)
        for z, xz in xp.groupby(_zone(xp["tmk"])):
            f, w = ratio(xz)
            f, w = (f, w) if w >= CAP_MIN_WEIGHT else pool
            rows.append({"zone": int(z), "pittcode": int(pc), "factor": round(f, 4), "weight": w})
    return pd.DataFrame(rows)


def uncap(s: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Hawaii: owner-occupied residential and ag-dwelling values divided by
    their zone's factor f (pittcode 100 or 500). A condominium unit instead
    carries its project's average value (split_condo_masters), in which only
    the owner units are capped: if a project's units share one market value V
    and a share s of them are owner-occupied, the average is V(sf + 1 - s),
    so every unit of the project, owner or not, is divided by sf + 1 - s
    (f = the zone's residential factor)."""
    f = factors.set_index(["zone", "pittcode"])["factor"]
    key = pd.MultiIndex.from_arrays([_zone(s["tmk"]), np.where(s["group"] == "ag_dwelling", 500, 100)])
    fac = f.reindex(key).fillna(1.0).to_numpy()
    condo = s["group"].eq("condo_unit")
    share = s["owner"].astype(float).where(condo).groupby(s["tmk"]).transform("mean").to_numpy()
    hit = (s["owner"] & s["group"].isin(["residential", "ag_dwelling"])).to_numpy()
    div = np.where(condo, share * fac + 1 - share, np.where(hit, fac, 1.0))
    return s.assign(value=s["value"] / div)


def kauai(df: pd.DataFrame, bldg: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """County TY26 roll: CLASS 1 non-owner residential and 2 vacation rental
    are residential; OVRCLASS 8 owner-occupied and 10 owner-occupied mixed-use
    are the owners. Non-taxable records (RESTRICT3: government, DHHL, right of
    way, utilities, condo masters) are dropped."""
    df = df[df["PARTXT"].notna() & (df["TAXYR"] == KAUAI_TAXYR)]
    def code(c: str) -> pd.Series:
        return df[c].fillna("").astype(str).str.split(":").str[0].str.strip()
    cls, ovr = code("CLASS"), code("OVRCLASS")
    v = df["MODTOT"].fillna(0).astype(float)
    assd, ex = df["ASSDTOT"].fillna(0), df["TOTEXEMPT"].fillna(0)
    ok = (df["RESTRICT3"].fillna("").str.strip() == "") & (v > 0) & ~((assd > 0) & (ex >= assd))
    cprx = df["TYPE"].eq(KAUAI_CONDO_TYPE)
    condo = (df["PARTXT"].str[8:] != "0000") | cprx
    improved = cprx | df["PARTXT"].isin(bldg.loc[bldg["FLAG"].isin(KAUAI_BUILDING_FLAGS), "PARTXT"])
    dwelling = df["PARTXT"].isin(bldg.loc[bldg["TABLE_"] == "DWELDAT", "PARTXT"])
    n_units = df["TMK"].map(_units_map(units, "Kauai")).fillna(0)
    res = cls.isin(["1", "2"]) & ok
    mf = res & ~condo & (n_units >= MULTIFAMILY_UNITS)
    large = res & ~condo & ~mf & (v >= LARGE_NON_CONDO)
    ag = (cls == "5") & ok & dwelling
    return pd.DataFrame({"value": v, "owner": ovr.isin(["8", "10"]), "improved": improved, "condo": condo.astype(int),
                         "group": np.select([mf, large, res, ag],
                                            ["multifamily", "large_other", "residential", "ag_dwelling"], "other")})


def kauai_checks(df: pd.DataFrame) -> None:
    """The roll against the statewide valuation report (rpt_residential_tiers_fy2027.csv):
    non-owner taxable value above $2M, and owner-occupied taxable value."""
    t = df[(df["TAXYR"] == KAUAI_TAXYR) & (df["RESTRICT3"].fillna("").str.strip() == "")]
    tc = t["TAXCLASS"].fillna("").astype(str).str.split(":").str[0].str.strip()
    rpt = pd.read_csv(DATA / "rpt_residential_tiers_fy2027.csv").query("county == 'Kauai'")
    got = {"non-owner above $2M": (t.loc[tc == "1", "TAXABLE"] - 2e6).clip(lower=0).sum(),
           "owner-occupied": t.loc[tc == "8", "TAXABLE"].sum()}
    want = {"non-owner above $2M": 1e3 * rpt.query("`class` == 'Non Owner-Occupied' and tier_lo == 2e6")["tier_value_k"].sum(),
            "owner-occupied": 1e3 * rpt.query("`class` == 'Owner-Occupied'")["net_value_k"].iloc[0]}
    for (name, g), tol in zip(got.items(), (0.02, 0.01), strict=True):
        gap = g / want[name] - 1
        print(f"kauai check, {name} taxable: ${g / 1e9:.3f}B vs report ${want[name] / 1e9:.3f}B "
              f"({gap:+.1%}, {'ok' if abs(gap) <= tol else 'CHECK'} within {tol:.0%})")


def cap_checks(haw_raw: pd.DataFrame, factors: pd.DataFrame, maui_raw: pd.DataFrame, hon_raw: pd.DataFrame) -> None:
    """Within-plat owner/non-owner ratio on cap_factors' sample: Hawaii as
    assessed and uncapped (1 by construction unless a zone took the pool),
    and Maui and Honolulu, whose owner values are not capped, as controls."""
    def fmt(x: pd.DataFrame) -> str:
        return "{:.3f} (weight {:,})".format(*within_plat_ratio(x["plat"], x["owner"], x["value"]))
    f = factors.set_index(["zone", "pittcode"])["factor"]
    for pc in CAP_PITTCODES:
        x = _cap_sample(haw_raw, (pc,))
        fac = f.reindex(pd.MultiIndex.from_arrays([_zone(x["tmk"]), x["pittcode"]])).to_numpy()
        print(f"within-plat owner/non-owner, Hawaii pittcode {pc}: as assessed {fmt(x)}, "
              f"uncapped {fmt(x.assign(value=x['value'].where(~x['owner'], x['value'] / fac)))}")
    o = hon_raw
    v = o["landvalue"].fillna(0) + o["buildingvalue"].fillna(0)
    ex = o["landexemption"].fillna(0) + o["buildingexemption"].fillna(0)
    parid = o["parid"].astype(str)
    sel = ((o["taxratecode"].astype(str).str.strip() == "1") & (parid.str[8:] == "0000") & ~_cpr_master(parid)
           & (o["buildingvalue"].fillna(0) >= CAP_MIN_BLDG) & (v > 0))
    ovr = o["ovrclass"].fillna("").astype(str).str.strip()
    hon = pd.DataFrame({"plat": parid.str[:5], "value": v,
                        "owner": (ex > 0) & (ex <= HOME_EXEMPTION_MAX) & ~ovr.isin(["11", "14"])})[sel]
    print(f"within-plat owner/non-owner, controls: Maui {fmt(_cap_sample(maui_raw, GIS_RES_CODES))}, Honolulu {fmt(hon)}")


def binned(county: str, s: pd.DataFrame) -> pd.DataFrame:
    s = s[s["group"] != "other"].copy()
    s["value_lo"] = np.array(EDGES)[np.searchsorted(EDGES, s["value"].to_numpy(), side="right") - 1].astype(int)
    g = (s.groupby(["group", "value_lo", "owner", "improved", "condo"])
           .agg(count=("value", "size"), sum_value=("value", "sum"))
           .reset_index().rename(columns={"owner": "owner_occupied"}))
    g[["owner_occupied", "improved", "condo"]] = g[["owner_occupied", "improved", "condo"]].astype(int)
    g["sum_value"] = g["sum_value"].round().astype("int64")
    return g.assign(county=county)[COLUMNS]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    def get(name: str) -> pd.DataFrame:
        return fetch(name, args.refresh)
    units = get("units")
    hon_raw, haw_raw, maui_raw, kau_raw = get("honolulu"), get("hawaii"), get("maui_gis"), get("kauai")
    hon = honolulu(hon_raw, units)
    haw = county_gis(haw_raw, units, "Hawaii")
    factors = cap_factors(haw_raw)
    haw_units = split_condo_masters(haw, haw_raw, get("hawaii_units"))
    capped = binned("Hawaii", haw_units)
    out = pd.concat([binned("Honolulu", hon), binned("Hawaii", uncap(haw_units, factors)),
                     binned("Kauai", kauai(kau_raw, get("kauai_bldg"), units))], ignore_index=True)
    out.to_csv(OUT, index=False)
    capped.to_csv(OUT_CAPPED, index=False)
    factors.to_csv(OUT_FACTORS, index=False)
    cap_checks(haw_raw, factors, maui_raw, hon_raw)
    kauai_checks(kau_raw)
    summ = out.groupby(["county", "group", "owner_occupied", "improved"]).agg(n=("count", "sum"), v=("sum_value", "sum"))
    summ["v_$B"] = summ["v"] / 1e9
    print(summ[["n", "v_$B"]].round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
