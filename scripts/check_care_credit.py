"""Validate the care-credit model against what the tax authorities report (nothing here is fitted).

    python scripts/check_care_credit.py

Reads the Act 24 calibrated base (``chain/forecast_sb3125_enhanced.py --cd 2`` writes it) and prints, writing
``runs/care_credit_check/*.csv``:

1. **Hawaiʻi credit, TY2023** (HRS §235-55.6): the model's credit on the base at the TY2023 limits, expected
   over the CPS ASEC donors' spending, with no take-up factor, against the Department of Taxation's
   "Tax Credits Claimed by Hawaiʻi Taxpayers" Table A-1 and A-2 ($14.82M on 18,119 claims). The pass band is
   plus or minus 15%.
2. **Federal credit, TY2023** (IRC §21, ``credits/federal_cdcc``): claims and dollars by AGI class against
   IRS SOI Table 2 for Hawaiʻi, "Child and dependent care credit" rows, raw and scaled to the Department's
   resident universe class by class. The federal credit is non-refundable, so under about $50,000 of AGI it
   is mostly unusable and the classes below are not comparable; only $50,000 and above is judged.
3. The family counts the rake targets, against their IRS targets.
"""
from __future__ import annotations

import dataclasses
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
OUT = REPO / "runs" / "care_credit_check"
TOLERANCE = 0.15


def main() -> int:
    from _forecast_common import open_calibrated
    from tax_modeler.calibration import dotax_credits_claimed as claimed
    from tax_modeler.calibration import family_margins as fm
    from tax_modeler.calibration import irs_soi_hi_table2 as irs
    from tax_modeler.config.tax_system_config import (
        TaxCalculator,
        TaxSystemRegistry,
        care_credit_inputs,
    )
    from tax_modeler.credits.federal_cdcc import federal_cdcc_expected
    from tax_modeler.scenarios.act24_population import MID_ALPHA, MID_TOP_PREMIUM

    OUT.mkdir(parents=True, exist_ok=True)
    base = open_calibrated(shared=False)
    units, _ = base.units(alpha=MID_ALPHA, top_premium=MID_TOP_PREMIUM, enrich=True)
    w = units["weight"].to_numpy(float)

    # 1. the Hawaii credit
    cfg = dataclasses.replace(TaxSystemRegistry.get_act46_system(2025), year=2023, name="act46_as_2023")
    hi_m = TaxCalculator().calculate_revenue_vectorized(units, cfg)["total_cdcc_millions"]
    kids, earn = care_credit_inputs(units)
    from tax_modeler.credits import hi_cdcc
    donors = hi_cdcc.default_donors()
    share = np.where(kids >= 2, donors[2][1].sum(), donors[1][1].sum())
    eligible = (kids > 0) & (earn > 0)
    claims = float((w * share)[eligible].sum())
    target_m, target_claims = claimed.dollars_M("cdcc", 2023), claimed.claims("cdcc", 2023)
    off = hi_m / target_m - 1
    print("Hawaii care credit, TY2023 (no take-up factor)")
    print(f"  dollars  ${hi_m:,.2f}M  vs Department ${target_m:,.2f}M  ({off:+.1%})  "
          f"{'WITHIN' if abs(off) <= TOLERANCE else 'OUTSIDE'} +/-{TOLERANCE:.0%}")
    print(f"  claims   {claims:,.0f}  vs Department {target_claims:,.0f}  ({claims / target_claims - 1:+.1%})")
    pd.DataFrame([{"check": "hawaii care credit $M", "model": hi_m, "dotax": target_m, "pct": 100 * off},
                  {"check": "hawaii care credit claims", "model": claims, "dotax": target_claims,
                   "pct": 100 * (claims / target_claims - 1)}]).to_csv(OUT / "hawaii_care_credit.csv", index=False)

    # 2. the federal credit by IRS class
    fed = federal_cdcc_expected(units)
    agi = units["federal_agi"].fillna(0).to_numpy(float)
    rows = []
    irs_n, irs_d = irs.values("cdcc_returns", 2023), irs.values("cdcc_amount_k", 2023)
    from tax_modeler.calibration.family_margins import _IRS_EDGES, _aligned_index
    for (lo, hi), n, d in zip(_IRS_EDGES, irs_n, irs_d, strict=True):
        m = (agi >= lo) & (agi < hi)
        ratio = fm.universe_ratios()[_aligned_index(lo)]["ratio"]
        rows.append({"class": f"{lo:,.0f} to {hi:,.0f}" if np.isfinite(hi) else f"{lo:,.0f} or more",
                     "model_claims": float((w * fed["federal_cdcc_claim_prob"])[m].sum()),
                     "irs_claims": n, "irs_claims_resident": n * ratio,
                     "model_$k": float((w * fed["federal_cdcc"])[m].sum() / 1e3),
                     "irs_$k": d, "irs_$k_resident": d * ratio,
                     "judged": bool(lo >= 50_000 and hi <= 1_000_000)})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "federal_care_credit_by_class.csv", index=False)
    print("\nFederal care credit, TY2023, by federal AGI class (judged: $50,000 to $1M)")
    print(t.round(0).to_string(index=False))
    j = t[t["judged"]]
    for col, tgt in (("model_claims", "irs_claims_resident"), ("model_$k", "irs_$k_resident")):
        print(f"  {col} {j[col].sum():,.0f} vs {j[tgt].sum():,.0f} resident-scaled ({j[col].sum() / j[tgt].sum() - 1:+.1%})")

    # 3. the family counts the rake targets
    w_ = units["weight"].to_numpy(float)
    codes = fm.margin_codes(units)
    print("\nFamily margins (model vs IRS-scaled target)")
    for key, (c, tg) in codes.items():
        edges = fm.MARGIN_EDGES[key]
        print("  " + key + ": " + "; ".join(
            f"[{edges[i]:,.0f}..) {w_[c == i + 1].sum():,.0f}/{tg[str(i + 1)]:,.0f} ({w_[c == i + 1].sum() / tg[str(i + 1)] - 1:+.1%})"
            for i in range(len(edges))))
    return 0 if abs(off) <= TOLERANCE else 1


if __name__ == "__main__":
    sys.exit(main())
