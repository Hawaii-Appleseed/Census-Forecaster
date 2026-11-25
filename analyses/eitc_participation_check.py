#!/usr/bin/env python3
"""Read-only: the model's implied EITC participation vs the IRS/Census rate (TY2022, TY2024).

Does not change any factor. Eligible = weighted units with federal eitc_amount > 0 on the
chain's tax units (data/artifacts/working_family_units.pkl), no IRS rake. Run from the repo root.
"""
import pickle
import sys

sys.path[:0] = ["scripts"]
import numpy as np  # noqa: E402
import poverty_impact_report as pir  # noqa: E402

HI_RATE_2022 = 0.833
SOI_2022 = {0: 26_900, 1: 27_820, 2: 18_460, 3: 11_790}
IRS_2024 = {"returns": 81_800, "dollars_M": 206.2}   # IRS state table, TY2024 processed 2025
DOTAX_2024 = {"claims": 78_399, "dollars_M": 76.981}

base = pickle.load(open("data/artifacts/working_family_units.pkl", "rb"))[0]
for yr in (2022, 2024):
    u = pir._build_units_for_tax_year(base, yr, project=(yr != 2022), use_cbo_aging=True)
    w = u["weight"].to_numpy(float)
    amt = u["eitc_amount"].to_numpy(float)
    e = amt > 0
    k = u["eitc_qualifying_children"].fillna(0).clip(0, 3).astype(int).to_numpy()
    raw = {b: w[e & (k == b)].sum() for b in range(4)}
    u2 = pir._apply_eitc_reweight(u)
    w2 = u2["weight"].to_numpy(float)
    rw = {b: w2[e & (k == b)].sum() for b in range(4)}
    print(f"TY{yr} raw eligible {w[e].sum():,.0f} (${(amt*w).sum()/1e6:.1f}M) by kids "
          f"{ {b: round(v) for b, v in raw.items()} }")
    print(f"TY{yr} reweighted eligible {w2[e].sum():,.0f} (${(amt*w2).sum()/1e6:.1f}M) by kids "
          f"{ {b: round(v) for b, v in rw.items()} }")
    if yr == 2022:
        tot = sum(SOI_2022.values())
        need = tot / HI_RATE_2022
        print(f"  IRS-implied eligible {need:,.0f}; raw short {need-w[e].sum():,.0f} "
              f"({1-w[e].sum()/need:.1%}); reweighted short {need-w2[e].sum():,.0f} "
              f"({1-w2[e].sum()/need:.1%})")
        for b in range(4):
            nb = SOI_2022[b] / HI_RATE_2022
            print(f"  kids {b}: claims {SOI_2022[b]:,} model-implied participation raw "
                  f"{SOI_2022[b]/raw[b]:.1%} reweighted {SOI_2022[b]/rw[b]:.1%}; "
                  f"needed at {HI_RATE_2022:.1%}: {nb:,.0f} (raw short {nb-raw[b]:,.0f})")
    else:
        need = IRS_2024["returns"] / HI_RATE_2022
        print(f"  claims {IRS_2024['returns']:,} -> implied participation raw "
              f"{IRS_2024['returns']/w[e].sum():.1%}, reweighted {IRS_2024['returns']/w2[e].sum():.1%}; "
              f"IRS-implied eligible (2022 rate) {need:,.0f}")
        need_d = IRS_2024["dollars_M"] / HI_RATE_2022
        print(f"  eligible $ implied {need_d:.1f}M vs raw {(amt*w).sum()/1e6:.1f}M, "
              f"reweighted {(amt*w2).sum()/1e6:.1f}M")
        for lab, d in (("raw", (amt * w).sum()), ("reweighted", (amt * w2).sum())):
            print(f"  state factor if fit to {lab} eligible: {DOTAX_2024['dollars_M']/(0.4*d/1e6):.3f}")
print("HI rate mean 2014-22", np.mean([.825,.809,.817,.778,.778,.836,.768,.816,.833]),
      "sd", np.std([.825,.809,.817,.778,.778,.836,.768,.816,.833], ddof=1))
