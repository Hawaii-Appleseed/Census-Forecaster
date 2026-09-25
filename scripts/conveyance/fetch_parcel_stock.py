"""Residential housing stock by assessed value and owner-occupancy, by county.

Usage:
  python scripts/conveyance/fetch_parcel_stock.py            # fetch (cached) and bin
  python scripts/conveyance/fetch_parcel_stock.py --refresh  # re-download

Writes packages/tax_modeler/src/tax_modeler/data/raw/conveyance/
parcel_stock_by_value.csv (county, group, value_lo, owner_occupied, count,
sum_value), which forecast_conveyance_sb3028.py reads. Raw pulls are cached
under runs/conveyance_sb3028/parcel_cache/ (gitignored).

Sources, all public ArcGIS REST services (no key; paged queries):
  - Honolulu: the Real Property Assessment Division's ASMTPITT table on the
    City's open data hub (CadastralTables/FeatureServer/10): one row per
    parcel or condominium unit, tax year 2026. Residential = tax rate code 1
    (incl. override 11 Residential A and 14 transient vacation). Owner-
    occupied = any land or building exemption on a residential record that
    is not Residential A or TVR (both are non-owner-occupied by definition).
    The Revenue-Modeling-TOD project uses the same tables.
  - Hawaii County: the county's parcel layer on the State GIS portal
    (ParcelsZoning/MapServer/5, county data of April 27, 2026): pittcode and
    the homeowner flag. Parcels only; condominium units are not separate.

Definitions match maui_residential_stock_2026.csv (the model's template,
from Maui's full assessment listing): owner-occupied = a homeowner
exemption (Honolulu: an exemption of $300,000 or less on a residential
record that is not Residential A or TVR; Hawaii/Maui: the homeowner flag);
fully exempt records (exemption >= value: government, charitable) dropped;
"improved" = building value > 0. Groups: "residential"; "ag_dwelling"
(agricultural class with a building value of $100,000 or more, kept apart
because Maui's rates cover residential classes only); "condo_unit"
(Hawaii's apartment-class project parcels, which hold all their units'
value, split into units: see split_condo_masters); "multifamily" (a parcel
with 5+ dwelling units, which HD2 values per unit) and "large_other" (a
non-condo residential record of $20M+ without a unit count, other than
Honolulu's Residential A): both left out of the stock method, as in the
Maui extract, whose actual sales of such property stay in Maui's total.
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
OUT = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "raw" / "conveyance" / "parcel_stock_by_value.csv"

SOURCES = {
    "honolulu": ("https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/CadastralTables/FeatureServer/10",
                 "parid,taxratecode,ovrclass,landvalue,buildingvalue,landexemption,buildingexemption", 2000),
    "hawaii": ("https://geodata.hawaii.gov/arcgis/rest/services/ParcelsZoning/MapServer/5",
               "tmk,pittcode,homeowner,landvalue,bldgvalue,landexempt,bldgexempt", 1000),
    # Dwelling units per TMK (statewide parcels of June 2025 joined to county
    # dwelling records): Hawaii's condominium projects, and 5+ unit buildings.
    "hawaii_units": ("https://services1.arcgis.com/x4h61KaW16vFs7PM/arcgis/rest/services/tmk_state_2025_dwelling_data/FeatureServer/0",
                     "TMK,COUNT_Units", 2000),
    "units": ("https://services1.arcgis.com/x4h61KaW16vFs7PM/arcgis/rest/services/tmk_state_2025_dwelling_data/FeatureServer/0",
              "TMK,county,COUNT_Units", 2000),
}
WHERE = {"hawaii_units": "county='Hawaii' AND COUNT_Units>1", "units": "COUNT_Units>=5"}
MULTIFAMILY_UNITS = 5          # HD2 values these per unit
LARGE_NON_CONDO = 20_000_000   # probable apartment/resort parcels without a unit count; left to the Maui calibration
HAWAII_HOMEOWNER_RECORDS = 45_162   # tax year 2026-27 statewide report, records by class

# Same value bins as the Maui extract (maui_sales_extract.js).
EDGES = [0, 3e5, 6e5, 8e5, 1e6, *np.arange(1.25e6, 6e6 + 1, 2.5e5), *np.arange(6.5e6, 1e7 + 1, 5e5),
         12.5e6, 15e6, 20e6, 30e6]
AG_DWELLING_MIN_BLDG = 100_000


def fetch(name: str, refresh: bool) -> pd.DataFrame:
    path = CACHE / f"{name}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    url, fields, page = SOURCES[name]
    rows, offset = [], 0
    while True:
        for attempt in range(5):
            try:
                r = requests.get(f"{url}/query", timeout=120, params={
                    "where": WHERE.get(name, "1=1"), "outFields": fields, "returnGeometry": "false", "orderByFields": "OBJECTID",
                    "resultOffset": offset, "resultRecordCount": page, "f": "json"})
                d = r.json()
                break
            except (requests.RequestException, json.JSONDecodeError):
                time.sleep(2 * (attempt + 1))
        else:
            raise SystemExit(f"{name}: failed at offset {offset}")
        feats = d.get("features", [])
        rows += [f["attributes"] for f in feats]
        if not feats or not d.get("exceededTransferLimit"):
            break
        offset += len(feats)
    df = pd.DataFrame(rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    print(f"{name}: {len(df):,} rows")
    return df


HOME_EXEMPTION_MAX = 300_000      # a homeowner exemption, not a government/charitable full exemption


def _units_map(units: pd.DataFrame, county: str) -> pd.Series:
    u = units[units["county"] == county]
    return u.groupby("TMK")["COUNT_Units"].max()


def honolulu(df: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    v = df["landvalue"].fillna(0) + df["buildingvalue"].fillna(0)
    ex = df["landexemption"].fillna(0) + df["buildingexemption"].fillna(0)
    ovr = df["ovrclass"].fillna("").astype(str).str.strip()
    code = df["taxratecode"].astype(str).str.strip()
    parcel = df["parid"].astype(str).str[8:] == "0000"                     # not a condo unit
    n_units = ("1" + df["parid"].astype(str).str[:8]).astype(int).map(_units_map(units, "Honolulu")).fillna(0)
    full = (v > 0) & (ex >= v)
    res = (code == "1") & ~full
    owner = res & (ex > 0) & (ex <= HOME_EXEMPTION_MAX) & ~ovr.isin(["11", "14"])
    mf = res & parcel & (n_units >= MULTIFAMILY_UNITS)
    large = res & parcel & ~mf & (v >= LARGE_NON_CONDO) & (ovr != "11")   # Residential A is a single home
    ag = code.isin(["5", "0"]) & (df["buildingvalue"].fillna(0) >= AG_DWELLING_MIN_BLDG) & ~full
    return pd.DataFrame({"value": v, "owner": owner, "improved": df["buildingvalue"].fillna(0) > 0,
                         "group": np.select([mf, large, res, ag],
                                            ["multifamily", "large_other", "residential", "ag_dwelling"], "other")})


def county_gis(df: pd.DataFrame, units: pd.DataFrame, county: str) -> pd.DataFrame:
    """State-portal parcel layer (Hawaii County). Condominium units are not
    separate records: a project is one master parcel holding every unit's
    value (the apartment class, pittcode 200), split later into units."""
    v = df["landvalue"].fillna(0) + df["bldgvalue"].fillna(0)
    ex = df["landexempt"].fillna(0) + df["bldgexempt"].fillna(0)
    code = df["pittcode"].fillna(-1).astype(int)
    n_units = df["tmk"].map(_units_map(units, county)).fillna(0)
    full = (v > 0) & (ex >= v)
    res = code.isin([100, 900, 1000, 1100, 1200]) & ~full       # Maui-style codes: homeowner 900, rentals 1000-1200
    mf = res & (n_units >= MULTIFAMILY_UNITS)
    large = res & ~mf & (v >= LARGE_NON_CONDO)
    condo = (code == 200) & ~full
    ag = (code == 500) & (df["bldgvalue"].fillna(0) >= AG_DWELLING_MIN_BLDG) & ~full
    return pd.DataFrame({"value": v, "owner": df["homeowner"].eq("Y"), "improved": df["bldgvalue"].fillna(0) > 0,
                         "group": np.select([mf, large, res, ag, condo],
                                            ["multifamily", "large_other", "residential", "ag_dwelling", "condo_master"],
                                            "other")})


def split_condo_masters(s: pd.DataFrame, raw: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """Hawaii: replace each condominium project parcel with its units, each at
    the project's average value per unit. Unit counts come from the dwelling
    layer where present; otherwise from the median value per unit of projects
    that have counts. Units in projects flagged homeowner are owner-occupied in
    the proportion that closes the gap between the county's homeowner-class
    records and the homeowner parcels in the layer."""
    m = s[s["group"] == "condo_master"].join(raw[["tmk"]]).merge(
        units.rename(columns={"TMK": "tmk", "COUNT_Units": "units"}), on="tmk", how="left")
    per_unit = (m["value"] / m["units"]).median()
    m["units"] = m["units"].fillna((m["value"] / per_unit).round().clip(lower=1))
    gis_owner = int((s["owner"] & s["group"].isin(["residential", "ag_dwelling", "condo_master"])).sum())
    owner_units = float(m.loc[m["owner"], "units"].sum())
    share = min(1.0, max(0.0, (HAWAII_HOMEOWNER_RECORDS - gis_owner) / owner_units)) if owner_units else 0.0
    rows = []
    for _, r in m.iterrows():
        n, v = int(r["units"]), r["value"] / r["units"]
        k = round(n * share) if r["owner"] else 0
        rows += [{"value": v, "owner": True, "improved": True, "group": "condo_unit"}] * k
        rows += [{"value": v, "owner": False, "improved": True, "group": "condo_unit"}] * (n - k)
    print(f"hawaii condo projects: {len(m)} -> {int(m['units'].sum())} units; owner share in homeowner projects {share:.2f}")
    return pd.concat([s[s["group"] != "condo_master"], pd.DataFrame(rows)], ignore_index=True)


def binned(county: str, s: pd.DataFrame) -> pd.DataFrame:
    s = s[s["group"] != "other"].copy()
    s["value_lo"] = np.array(EDGES)[np.searchsorted(EDGES, s["value"].to_numpy(), side="right") - 1].astype(int)
    g = (s.groupby(["group", "value_lo", "owner", "improved"]).agg(count=("value", "size"), sum_value=("value", "sum"))
           .reset_index().rename(columns={"owner": "owner_occupied"}))
    g[["owner_occupied", "improved"]] = g[["owner_occupied", "improved"]].astype(int)
    g["sum_value"] = g["sum_value"].round().astype("int64")
    return g.assign(county=county)[["county", "group", "value_lo", "owner_occupied", "improved", "count", "sum_value"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    units = fetch("units", args.refresh)
    out = pd.concat([binned("Honolulu", honolulu(fetch("honolulu", args.refresh), units)),
                     binned("Hawaii", split_condo_masters(county_gis(haw := fetch("hawaii", args.refresh), units, "Hawaii"),
                                                          haw, fetch("hawaii_units", args.refresh)))],
                    ignore_index=True)
    out.to_csv(OUT, index=False)
    summ = out.groupby(["county", "group", "owner_occupied", "improved"]).agg(n=("count", "sum"), v=("sum_value", "sum"))
    summ["v_$B"] = summ["v"] / 1e9
    print(summ[["n", "v_$B"]].round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
