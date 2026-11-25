"""Test A: does QWI county earnings growth predict ACS county growth better than the QCEW statewide anchor?

Usage: python -I qwi_vs_qcew_anchor.py <qwi_sa_csv.gz> <repo_data_dir>
Walk-forward: for each test year t (from 2014), fit growth_acs = a + b*growth_x on pooled county-years < t,
predict year t. Baseline = mean of prior ACS growth. Years adjacent to the missing 2020 ACS 1y are skipped.
"""
import csv, gzip, json, sys, math
from collections import defaultdict
import numpy as np

qwi_path, data = sys.argv[1:3]
R = list(csv.DictReader(gzip.open(qwi_path, "rt")))
q = defaultdict(dict)  # geo -> year -> {quarter: (earn, emp)}
for x in R:
    if x["sex"] == "0" and x["agegrp"] == "A00" and x["industry"] == "00" and x["EarnS"]:
        q[x["geography"]].setdefault(int(x["year"]), {})[int(x["quarter"])] = (float(x["EarnS"]), float(x["Emp"] or 0))
qwi = {g: {y: 12 * np.mean([v[0] for v in qs.values()]) for y, qs in ys.items() if len(qs) == 4} for g, ys in q.items()}
qcew = {int(k): v for k, v in json.load(open(f"{data}/anchors/qcew_hawaii_wages.json"))["values_by_year"].items()}
P = json.load(open(f"{data}/calibration_panel/acs_panel.json"))["observations"]
acs = defaultdict(dict)
for o in P:
    if o["geoid"] in ("15001", "15003", "15007", "15009") and o["indicator"] in ("B19013_001E", "B20002_001E") and o["estimate"]:
        acs[(o["indicator"], o["geoid"])][o["year"]] = o["estimate"]

def g(s, y):
    return math.log(s[y] / s[y - 1]) if y in s and y - 1 in s else None

out = {"qwi_annual_years": {k: [min(v), max(v)] for k, v in qwi.items()}}
for ind in ("B19013_001E", "B20002_001E"):
    rows = []
    for (i, geo), s in acs.items():
        if i != ind: continue
        for y in sorted(s):
            a, xq, xs = g(s, y), g(qwi[geo], y), g(qcew, y)
            xst = g(qwi["15"], y)
            if None not in (a, xq, xs, xst): rows.append((y, geo, a, xq, xs, xst))
    res = {}
    for name, col in (("QWI_county", 3), ("QCEW_state", 4), ("QWI_state", 5)):
        errs, base = [], []
        for t in range(2014, 2026):
            tr = [r for r in rows if r[0] < t]; te = [r for r in rows if r[0] == t]
            if len(tr) < 8 or not te: continue
            X = np.array([r[col] for r in tr]); Y = np.array([r[2] for r in tr])
            b, a = np.polyfit(X, Y, 1)
            for r in te:
                errs.append(r[2] - (a + b * r[col])); base.append(r[2] - Y.mean())
        res[name] = dict(n=len(errs), rmse=float(np.sqrt(np.mean(np.square(errs)))), baseline_rmse=float(np.sqrt(np.mean(np.square(base)))))
    # no-fit "pass-through" (predict ACS growth = anchor growth), and correlations
    for name, col in (("QWI_county", 3), ("QCEW_state", 4), ("QWI_state", 5)):
        e = [r[2] - r[col] for r in rows if r[0] >= 2014]
        res[name]["passthrough_rmse"] = float(np.sqrt(np.mean(np.square(e))))
        res[name]["corr_all"] = float(np.corrcoef([r[2] for r in rows], [r[col] for r in rows])[0, 1])
    # demeaned-by-year: does county-specific QWI explain cross-county dispersion within year?
    out[ind] = dict(n_obs=len(rows), res=res)
print(json.dumps(out, indent=1))
