"""Test B: LODES8 RAC low-earnings resident workers by district vs the population-proportional within-PUMA split.

Usage: python -I lodes_rac_districts.py <rac_csv.gz> <cache_dir> <out_csv>
Blocks -> PUMA/district via the repo's own build_puma_district_crosswalk.py logic (2020 PL pop, 2022 BEF).
"Actual" = sum of RAC CE01 (earnings <= $1,250/month) over a district's blocks. "Pop-proportional" =
PUMA total of CE01 x the district's share of the PUMA's 2020 population (what apportion() does).
"""
import importlib.util, io, sys
from pathlib import Path
import pandas as pd

rac, cache, out = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("b", Path(__file__).resolve().parents[1] / "build_puma_district_crosswalk.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
pop = b.block_population(cache)
tp = pd.read_csv(io.BytesIO(b._fetch(b.TRACT_PUMA_URL, cache)), dtype=str, encoding="utf-8-sig")
tp = tp[tp["STATEFP"] == "15"]
puma = pd.Series(tp["PUMA5CE"].astype(int).to_numpy(), index=tp["STATEFP"] + tp["COUNTYFP"] + tp["TRACTCE"])
B = pd.DataFrame({"pop": pop})
B["PUMA"] = B.index.str[:11].map(puma)
B["house"] = b.district_of_block(cache, "sldl"); B["senate"] = b.district_of_block(cache, "sldu")
r = pd.read_csv(rac, dtype={"h_geocode": str}).set_index("h_geocode")[["C000", "CE01", "CE02", "CE03"]]
B = B.join(r, how="left").fillna({"C000": 0, "CE01": 0, "CE02": 0, "CE03": 0})
lost = r.index.difference(B.index)
print("RAC blocks not in PL block list:", len(lost), "workers", int(r.loc[lost, "C000"].sum()))
B = B.dropna(subset=["PUMA", "house", "senate"])
print("totals", B[["pop", "C000", "CE01", "CE02", "CE03"]].sum().to_dict())
rows = []
for lvl in ("house", "senate"):
    for m in ("CE01", "C000"):
        g = B.groupby(["PUMA", lvl]).agg(pop=("pop", "sum"), v=(m, "sum")).reset_index()
        g["share"] = g["pop"] / g.groupby("PUMA")["pop"].transform("sum")
        tot = g.groupby("PUMA")["v"].sum().rename("puma_v")
        g = g.join(tot, on="PUMA"); g["prop"] = g["puma_v"] * g["share"]
        d = g.groupby(lvl).agg(actual=("v", "sum"), prop=("prop", "sum"), pop=("pop", "sum"))
        d["chamber"], d["measure"] = lvl, m
        rows.append(d.reset_index().rename(columns={lvl: "district"}))
D = pd.concat(rows); D["diff"] = D["actual"] / D["prop"] - 1
D.to_csv(out, index=False, float_format="%.4f")
for (c, m), x in D.groupby(["chamber", "measure"]):
    x = x[x["pop"] > 0]
    w = (x["actual"] - x["prop"]).abs().sum() / x["actual"].sum()
    print(c, m, "n", len(x), "median|diff| %.3f" % x["diff"].abs().median(), "mean|diff| %.3f" % x["diff"].abs().mean(),
          "p90 %.3f" % x["diff"].abs().quantile(.9), "max %.3f" % x["diff"].abs().max(),
          "share>20%%: %.2f" % (x["diff"].abs() > .2).mean(), "weighted misallocation %.3f" % w)
# low-earner share of resident workers, by district, vs PUMA-implied
