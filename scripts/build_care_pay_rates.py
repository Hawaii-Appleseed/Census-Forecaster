"""Set the probability that a family with a child under 13 pays for work-related care, by AGI class.

    python scripts/build_care_pay_rates.py            # reads the Act 24 calibrated base (chain/forecast_sb3125_enhanced.py --cd 2)

Writes packages/tax_modeler/src/tax_modeler/data/cps_donor/care_pay_rates.json, read by
:func:`tax_modeler.credits.hi_cdcc.pay_probability`.

* **Below $50,000**: the pooled CPS ASEC Hawaii donors' rate, the weighted share of families with a child under 13 and
  household money income under $50,000 (calendar-2023 dollars) that report any childcare spending. There the federal
  credit is mostly unusable (non-refundable, little tax left) and the IRS's claim counts say little about who pays.
* **$50,000 and up**: in each IRS class (50-75k, 75-100k, 100-200k, 200-500k, 500k-1M), the claim count that makes the
  model's federal credit match IRS SOI Table 2 for Hawaii, "Child and dependent care credit" returns, scaled to the
  Department's resident universe class by class as the family margins are
  (:func:`tax_modeler.calibration.family_margins.universe_ratios`). The model's claims are the weighted units with a
  child under 13, a filing status that may claim, a lower earner with earnings and federal tax before credits above
  zero (so a positive credit is possible), times the rate; the rate is the target over that weighted count. Rates are
  capped at one. The $500,000 and up class uses the 500k-1M count (the synthetic tail is left out).

The expense amounts a paying family has stay the donors' (:func:`hi_cdcc.donor_expense_distribution`). Nothing about the
Hawaii credit is fitted: the Hawaii check (DOTAX TY2023 $14.82M on 18,119 claims, TY2022 $6.48M) and the federal dollars by
class are out of sample (``scripts/check_care_credit.py``).
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
OUT = REPO / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "cps_donor" / "care_pay_rates.json"
EDGES = [50_000.0, 75_000.0, 100_000.0, 200_000.0, 500_000.0]


def donor_rate_below_50k() -> dict:
    from tax_modeler.credits import hi_cdcc

    fam = pd.read_parquet(hi_cdcc.DONOR_FAMILIES_PATH)
    low = fam[fam["income"] < 50_000.0]
    w = low["weight"].to_numpy(float)
    pays = (low["expense"] > 0).to_numpy()
    rate = float((w * pays).sum() / w.sum())
    # effective sample size for the standard error
    ess = float(w.sum() ** 2 / (w ** 2).sum())
    return {"rate": rate, "families": int(len(low)), "paying": int(pays.sum()),
            "se": float(np.sqrt(rate * (1 - rate) / ess))}


def main() -> int:
    from _forecast_common import open_calibrated
    from tax_modeler.calibration import family_margins as fm
    from tax_modeler.calibration import irs_soi_hi_table2 as irs
    from tax_modeler.scenarios.act24_population import MID_ALPHA, MID_TOP_PREMIUM
    from tax_modeler.units.earnings import lower_earner_income

    base = open_calibrated(shared=False)
    units, _ = base.units(alpha=MID_ALPHA, top_premium=MID_TOP_PREMIUM, enrich=True)
    w = units["weight"].to_numpy(float)
    n13 = units["num_children_under_13"].fillna(0).to_numpy(float)
    agi = units["federal_agi"].fillna(0).to_numpy(float)
    tax = units["federal_tax_before_credits"].fillna(0).to_numpy(float)
    earn = np.maximum(lower_earner_income(units), 0.0)
    can = (n13 > 0) & (units["filing_status"].to_numpy() != "married_filing_separately") & (earn > 0) & (tax > 0)

    ratios = fm.universe_ratios()
    irs_n = irs.values("cdcc_returns", 2023)
    low = donor_rate_below_50k()
    rates, rows = [low["rate"]], []
    for (lo, hi), n in zip(fm._IRS_EDGES, irs_n, strict=True):
        if lo < 50_000.0 or lo >= 1_000_000.0:
            continue
        target = n * ratios[fm._aligned_index(lo)]["ratio"]
        m = can & (agi >= lo) & (agi < hi)
        den = float((w * m).sum())
        r = min(target / den, 1.0)
        rates.append(r)
        rows.append({"class": f"{lo:,.0f}-{hi:,.0f}", "irs_claims": n, "irs_claims_resident": target,
                     "model_units_that_could_claim": den, "rate": r})
    # the last class ($500,000 and up) is the IRS $500k-$1M class: the synthetic tail is left out
    doc = {"edges": EDGES, "rates": rates,
           "note": "rates[0] below $50,000 (pooled CPS ASEC donors); rates[1..] for [50k,75k), [75k,100k), [100k,200k), "
                   "[200k,500k), $500k and up from IRS SOI Table 2 Hawaii TY2023 child and dependent care credit returns, "
                   "resident-scaled class by class",
           "below_50k": low, "classes_50k_up": rows, "irs_year": 2023,
           "built_from": "Act 24 calibrated base (chain/forecast_sb3125_enhanced.py --cd 2), MID alpha and premium"}
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"below $50k: donors {low['rate']:.3f} ({low['paying']} of {low['families']} families, se {low['se']:.3f})")
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
