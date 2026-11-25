"""Test: does group quarters (barracks, dorms, prisons) explain the LODES-vs-population district gap?

Usage: python -I gq_district_base.py <rac_csv.gz> <cache_dir> <out_dir>
Reads the bundled 2020 PL 94-171 zip in <cache_dir> (file 02 = H1 housing units; file 03 = P5 group quarters by type),
the same block -> PUMA / House / Senate assignment as build_puma_district_crosswalk.py, and LODES8 RAC 2022.
Sign convention (as lodes_rac_districts.py): diff = LODES actual / implied - 1, where implied = PUMA's LODES total x the
district's within-PUMA share of the chosen base. +252% = LODES has 3.5x what the base implies, i.e. the base share is
too SMALL for the district; -80% = the base share is too LARGE.
Writes gq_district_gap.csv (per chamber x district) and gq_share_shifts.csv (PUMA x district cells moving >10%).
"""
import importlib.util, io, sys, zipfile
from pathlib import Path
import pandas as pd

rac, cache, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("b", Path(__file__).resolve().parents[1] / "build_puma_district_crosswalk.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
z = zipfile.ZipFile(io.BytesIO(b._fetch(b.PL_URL, cache)))
geo = pd.read_csv(z.open("higeo2020.pl"), sep="|", header=None, dtype=str, encoding="latin-1", usecols=[2, 7, 9])
geo.columns = ["sumlev", "logrecno", "block"]; geo = geo[geo["sumlev"] == "750"]
f1 = pd.read_csv(z.open("hi000012020.pl"), sep="|", header=None, dtype={4: str}, usecols=[4, 5]); f1.columns = ["logrecno", "pop"]
f2 = pd.read_csv(z.open("hi000022020.pl"), sep="|", header=None, dtype={4: str})
f2 = f2[[4, f2.shape[1] - 3, f2.shape[1] - 2]]; f2.columns = ["logrecno", "hu", "occ"]          # H1 = last 3 cols
f3 = pd.read_csv(z.open("hi000032020.pl"), sep="|", header=None, dtype={4: str}, usecols=[4] + list(range(5, 15)))
f3.columns = ["logrecno", "gq", "gq_inst", "gq_corr", "gq_juv", "gq_nursing", "gq_othinst", "gq_noninst", "gq_college", "gq_mil", "gq_othnon"]
B = geo.merge(f1, on="logrecno").merge(f2, on="logrecno").merge(f3, on="logrecno").set_index("block").drop(columns=["sumlev", "logrecno"])
B = B.astype(int); B["hhpop"] = B["pop"] - B["gq"]
tp = pd.read_csv(io.BytesIO(b._fetch(b.TRACT_PUMA_URL, cache)), dtype=str, encoding="utf-8-sig"); tp = tp[tp["STATEFP"] == "15"]
puma = pd.Series(tp["PUMA5CE"].astype(int).to_numpy(), index=tp["STATEFP"] + tp["COUNTYFP"] + tp["TRACTCE"])
B["PUMA"] = B.index.str[:11].map(puma)
B["house"] = b.district_of_block(cache, "sldl"); B["senate"] = b.district_of_block(cache, "sldu")
r = pd.read_csv(rac, dtype={"h_geocode": str}).set_index("h_geocode")[["C000", "CE01"]]
B = B.join(r, how="left").fillna({"C000": 0, "CE01": 0}).dropna(subset=["PUMA", "house", "senate"])
print("state totals:", B[["pop", "gq", "gq_mil", "gq_college", "gq_inst", "hhpop", "hu", "occ", "CE01", "C000"]].sum().astype(int).to_dict())
assert (B["gq"] == B["gq_inst"] + B["gq_noninst"]).all()
BASES = {"pop": "pop", "hhpop": "hhpop", "occ_hu": "occ"}
rows, shifts = [], []
for lvl in ("house", "senate"):
    d0 = B.groupby(lvl)[["pop", "gq", "gq_mil", "gq_college", "gq_inst", "hhpop", "occ", "CE01", "C000"]].sum()
    gp = B.groupby(["PUMA", lvl])[["pop", "hhpop", "occ", "CE01", "C000"]].sum().reset_index()
    for base, col in BASES.items():
        gp[f"sh_{base}"] = gp[col] / gp.groupby("PUMA")[col].transform("sum")
    for m in ("CE01", "C000"):
        tot = gp.groupby("PUMA")[m].transform("sum")
        for base in BASES: gp[f"imp_{m}_{base}"] = tot * gp[f"sh_{base}"]
    d = gp.groupby(lvl)[[c for c in gp if c.startswith("imp_")]].sum().join(d0)
    for m in ("CE01", "C000"):
        for base in BASES: d[f"diff_{m}_{base}"] = d[m] / d[f"imp_{m}_{base}"] - 1
    d["gq_share_of_pop"] = d["gq"] / d["pop"]; d["chamber"] = lvl
    rows.append(d.reset_index().rename(columns={lvl: "district"}))
    for base in ("hhpop", "occ_hu"):
        s = gp[["PUMA", lvl, "sh_pop", f"sh_{base}"]].rename(columns={lvl: "district", f"sh_{base}": "share_new"})
        s["rel_change"] = s["share_new"] / s["sh_pop"] - 1; s["chamber"], s["base"] = lvl, base
        shifts.append(s[(s["rel_change"].abs() > .10) & (s["sh_pop"] > 0)])
D = pd.concat(rows); D.to_csv(out / "gq_district_gap.csv", index=False, float_format="%.4f")
S = pd.concat(shifts).rename(columns={"sh_pop": "share_pop_base"}); S.to_csv(out / "gq_share_shifts.csv", index=False, float_format="%.4f")
for lvl, m in [(c, m) for c in ("house", "senate") for m in ("CE01", "C000")]:
    x = D[D["chamber"] == lvl]
    for base in BASES:
        v = x[f"diff_{m}_{base}"].abs()
        print(f"{lvl:6s} {m} {base:7s} median {v.median():.3f} p90 {v.quantile(.9):.3f} max {v.max():.3f} >20%: {(v > .2).mean():.2f} "
              f"weighted {(x[m] - x[f'imp_{m}_{base}']).abs().sum() / x[m].sum():.3f}")
pd.set_option("display.width", 250)
h = D[(D.chamber == "house") & D.district.isin([46, 2, 33, 36, 37])]
print(h[["district", "pop", "gq", "gq_mil", "gq_college", "gq_inst", "hhpop", "occ", "CE01", "diff_CE01_pop", "diff_CE01_hhpop", "diff_CE01_occ_hu"]].to_string())
for base in ("pop", "hhpop", "occ_hu"):
    x = D[D.chamber == "house"].assign(a=lambda t: t[f"diff_CE01_{base}"].abs()).nlargest(6, "a")
    print(base, "worst house:", [(int(d), round(v, 2)) for d, v in zip(x.district, x[f"diff_CE01_{base}"])])
print("cells shifting >10%:", S.groupby(["chamber", "base"]).size().to_dict())
print(D[D.chamber == "house"].nlargest(6, "gq_share_of_pop")[["district", "pop", "gq", "gq_mil", "gq_college", "gq_inst", "gq_share_of_pop"]].to_string())
