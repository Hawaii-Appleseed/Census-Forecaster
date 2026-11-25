"""1.5x-national unemployment test (H.R.1 sec. 10102(b), 7 USC 2015(o)(4)(A)(ii)) for Hawaii counties.
Usage: python -I waiver_unemployment_test.py <dir with BLS API json> <out.csv>
Input: BLS API v2 GET json per series (LAUCN15xxx0000000003 county NSA rate; LNU04000000 national NSA rate)."""
import json, sys, glob, os, csv
src, out = sys.argv[1], sys.argv[2]
names = {"15001": "Hawaii", "15003": "Honolulu", "15007": "Kauai", "15009": "Maui"}
def load(p):
    s = json.load(open(p))["Results"]["series"][0]["data"]
    return {(int(r["year"]), int(r["period"][1:])): float(r["value"]) for r in s if r["period"].startswith("M") and r["period"] != "M13" and r["value"] != "-"}
nat = load(os.path.join(src, "LNU04000000.json"))
cty = {n: load(os.path.join(src, "LAUCN%s0000000003.json" % f)) for f, n in names.items()}
months = sorted(k for k in nat if all(k in c for c in cty.values()) and k >= (2023, 1))
print("months", months[0], months[-1], len(months), "(months with no BLS value are skipped; windows average the months present)")
rows = []
for n, c in cty.items():
    for L in (3, 12, 24):
        for i in range(L - 1, len(months)):
            w = months[i - L + 1:i + 1]
            cr, nr = sum(c[m] for m in w) / L, sum(nat[m] for m in w) / L
            rows.append(dict(county=n, window=L, end="%d-%02d" % months[i], county_rate=round(cr, 2), national_nsa=round(nr, 2),
                             ratio=round(cr / nr, 3), threshold=round(1.5 * nr, 2), margin_pts=round(cr - 1.5 * nr, 2), qualifies=cr >= 1.5 * nr))
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)
for n in cty:
    for L in (3, 12, 24):
        r = [x for x in rows if x["county"] == n and x["window"] == L]
        best = max(r, key=lambda x: x["ratio"]); last = r[-1]
        print(n, L, "max ratio", best["ratio"], best["end"], "| any qualifies:", any(x["qualifies"] for x in r),
              "| latest end", last["end"], "ratio", last["ratio"], "rate", last["county_rate"], "need", last["threshold"], "rise", round(-last["margin_pts"], 2))
# single months
for n, c in cty.items():
    mr = max(months, key=lambda m: c[m] / nat[m]); print(n, "max single-month ratio", round(c[mr] / nat[mr], 3), mr, "county", c[mr], "nat", nat[mr])
for n in ("Maui",):
    for m in [(2023, 8), (2023, 9), (2023, 10), (2023, 11), (2023, 12), (2024, 1)]:
        print("Maui", m, cty[n][m], nat[m], round(cty[n][m] / nat[m], 2))
m = months[-1]; print("latest month", m, {n: cty[n][m] for n in cty}, nat[m])
