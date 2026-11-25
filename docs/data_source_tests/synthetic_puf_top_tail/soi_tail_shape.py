"""National SOI Table 1.4 (2022) and Hawaii SOI Table 2 (2022) tail shape from the bundled files.
Run from the repo root: uv run python docs/data_source_tests/synthetic_puf_top_tail/soi_tail_shape.py"""
import json
import numpy as np
import pandas as pd

s = pd.read_csv("data/tax_modeler/irs_soi/national_soi_table_1_4_2022.csv")
s["lo"] = s.agi_lo
def above(t): return s.n_returns[s.lo >= t].sum()
print("national N(>=t):", {t: int(above(t)) for t in (2e5, 5e5, 1e6, 2e6, 5e6, 1e7)})
for a, b in ((2e5, 5e5), (5e5, 1e6), (1e6, 2e6), (1e6, 5e6), (2e6, 1e7)):
    print(f"national slope {a:g}->{b:g}: {np.log(above(a)/above(b))/np.log(b/a):.2f}")
for lo, hi in ((2e5, 5e5), (5e5, 1e6), (1e6, 1e12), (1e6, 2e6), (2e6, 5e6), (5e6, 1e7), (1e7, 1e12)):
    m = (s.lo >= lo) & (s.lo < hi); A = s.agi_less_deficit_M[m].sum()
    mean = A * 1e6 / s.n_returns[m].sum()
    print(f"national [{lo:g},{hi:g}) N={s.n_returns[m].sum():,.0f} meanAGI={mean:,.0f}",
          {c: round(s[c][m].sum() / A, 3) for c in ("wages_M", "capgain_M", "business_M", "dividends_M", "taxable_interest_M", "retirement_M", "itemized_M")})
d = json.load(open("packages/tax_modeler/src/tax_modeler/data/calibration/irs_soi_hi_table2.json"))
y = d["years"]["2022"]
lo = [None, 1, 10000, 25000, 50000, 75000, 100000, 200000, 500000, 1000000]
ret, agi, wage = np.array(y["returns"]), np.array(y["agi_amount_k"]), np.array(y["wage_amount_k"])
Nb = {t: ret[i:].sum() for i, t in ((7, 2e5), (8, 5e5), (9, 1e6))}
print("Hawaii SOI T2 N(>=t):", Nb)
for a, b in ((2e5, 5e5), (5e5, 1e6)):
    print(f"Hawaii slope {a:g}->{b:g}: {np.log(Nb[a]/Nb[b])/np.log(b/a):.2f}")
for i, name in ((7, "200-500K"), (8, "500K-1M"), (9, "1M+")):
    print(f"Hawaii {name}: N={ret[i]:,.0f} meanAGI={agi[i]*1e3/ret[i]:,.0f} wage/AGI={wage[i]/agi[i]:.3f}")
m = agi[9] * 1e3 / ret[9]
print("Hawaii 1M+ mean-ratio alpha = m/(m-1e6):", round(m / (m - 1e6), 2))
