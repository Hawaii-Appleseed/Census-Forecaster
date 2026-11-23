#!/usr/bin/env python3
"""Population-weighted Hawaiʻi PUMA → county, House district and Senate district crosswalk.

Each row is one piece of a 2020 PUMA that falls in one county, one House district and one Senate district of the
2022 plan (still in force: Hawaiʻi is not in the Census Bureau's 2024 block equivalency release, which lists only
states that redrew), with its 2020 census population and its share of the PUMA's population. Built from blocks:

* block population: the 2020 Census redistricting file (P.L. 94-171), table P1;
* block → 2022 House and Senate districts: the Census Bureau's 2022 state legislative block equivalency files;
* block → PUMA: the 2020 census tract to 2020 PUMA relationship file (blocks nest in tracts, tracts in PUMAs);
* county from the block's code, Kalawao (a few dozen people) counted with Maui as the ACS does.

``tax_modeler.analysis.puma_crosswalk.population_crosswalk`` reads the output. Unlike the older
``hawaii_puma_districts_official_2022.csv`` (35 House and 18 Senate districts, each PUMA split evenly among them)
it has every district and weights each by where people live.

    .venv/bin/python scripts/build_puma_district_crosswalk.py                  # downloads about 60 MB
    .venv/bin/python scripts/build_puma_district_crosswalk.py --cache-dir DIR  # keeps the downloads in DIR
"""
from __future__ import annotations

import argparse
import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "crosswalks" / \
    "hawaii_puma_district_population_2022.csv"
PL_URL = ("https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171/"
          "Hawaii/hi2020.pl.zip")
TRACT_PUMA_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt"
BEF_URL = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2023/2022-state-legislative-bef/"
COUNTY = {"001": "Hawaii", "003": "Honolulu", "005": "Maui", "007": "Kauai", "009": "Maui"}   # 005 = Kalawao


def _fetch(url: str, cache: Path) -> bytes:
    path = cache / url.rsplit("/", 1)[-1]
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            path.write_bytes(resp.read())
    return path.read_bytes()


def block_population(cache: Path) -> pd.Series:
    """2020 population of every Hawaiʻi block, indexed by the 15-digit block code."""
    z = zipfile.ZipFile(io.BytesIO(_fetch(PL_URL, cache)))
    geo = pd.read_csv(z.open("higeo2020.pl"), sep="|", header=None, dtype=str, encoding="latin-1",
                      usecols=[2, 7, 9])
    geo.columns = ["sumlev", "logrecno", "block"]
    geo = geo[geo["sumlev"] == "750"]
    seg = pd.read_csv(z.open("hi000012020.pl"), sep="|", header=None, dtype={4: str}, usecols=[4, 5])
    seg.columns = ["logrecno", "pop"]
    out = geo.merge(seg, on="logrecno", how="left")
    return out.set_index("block")["pop"].fillna(0).astype(int)


def district_of_block(cache: Path, chamber: str) -> pd.Series:
    z = zipfile.ZipFile(io.BytesIO(_fetch(f"{BEF_URL}{chamber}_2022.zip", cache)))
    name = next(n for n in z.namelist() if n.endswith(f"15_HI_{chamber.upper()}22.txt"))
    bef = pd.read_csv(z.open(name), dtype=str)
    return bef.set_index("GEOID").iloc[:, 0].astype(int)


def build(cache: Path) -> pd.DataFrame:
    pop = block_population(cache)
    tp = pd.read_csv(io.BytesIO(_fetch(TRACT_PUMA_URL, cache)), dtype=str, encoding="utf-8-sig")
    tp = tp[tp["STATEFP"] == "15"]
    puma = pd.Series(tp["PUMA5CE"].astype(int).to_numpy(), index=tp["STATEFP"] + tp["COUNTYFP"] + tp["TRACTCE"])
    blocks = pd.DataFrame({"population": pop})
    blocks["PUMA"] = blocks.index.str[:11].map(puma)
    blocks["county"] = blocks.index.str[2:5].map(COUNTY)
    blocks["house_district"] = district_of_block(cache, "sldl")
    blocks["senate_district"] = district_of_block(cache, "sldu")
    if blocks[["PUMA", "county", "house_district", "senate_district"]].isna().any().any():
        bad = blocks[blocks[["PUMA", "county", "house_district", "senate_district"]].isna().any(axis=1)]
        if bad["population"].sum() > 0:
            raise SystemExit(f"{len(bad)} populated blocks lack a PUMA, county or district: {bad.head()}")
        blocks = blocks.dropna()
    out = (blocks.groupby(["PUMA", "county", "house_district", "senate_district"], as_index=False)["population"].sum())
    out = out[out["population"] > 0]
    out[["PUMA", "house_district", "senate_district"]] = out[["PUMA", "house_district", "senate_district"]].astype(int)
    out["share_of_puma"] = out["population"] / out.groupby("PUMA")["population"].transform("sum")
    return out.sort_values(["PUMA", "house_district", "senate_district"]).reset_index(drop=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        cache = args.cache_dir or Path(tmp)
        cache.mkdir(parents=True, exist_ok=True)
        out = build(cache)
    out.to_csv(args.out, index=False, float_format="%.8f")
    print(f"{len(out)} pieces, {out['PUMA'].nunique()} PUMAs, {out['house_district'].nunique()} House and "
          f"{out['senate_district'].nunique()} Senate districts, {out['population'].sum():,} people -> {args.out}")


if __name__ == "__main__":
    main()
