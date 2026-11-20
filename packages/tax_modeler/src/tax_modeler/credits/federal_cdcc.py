"""Federal credit for child and dependent care expenses (IRC §21), tax year 2023, for validation only.

Nothing in the revenue or poverty models uses this. It exists so the care-credit expense model (CPS ASEC
donor spending, ``hi_cdcc.expected_capped_expense``) can be checked against the IRS's own counts and dollars
by AGI class (Table 2 for Hawaiʻi, "Child and dependent care credit"): the same spending, the federal
rules.

TY2023 rules (the American Rescue Plan's one-year enhancement ended with TY2021):

* expenses counted: up to $3,000 for one qualifying individual and $6,000 for two or more (§21(c));
* percentage: 35% at AGI of $15,000 or less, one point less for each $2,000 (or part) above $15,000, to
  20% above $43,000 (§21(a)(2));
* limited to the earnings of the lower-earning spouse (§21(d));
* **non-refundable**: no more than the federal income tax before credits (§26). Below about $50,000 of AGI
  a family with children owes little or no federal tax once the standard deduction and the child tax credit
  are taken, so the credit is mostly unusable there and the IRS counts few returns; classes under $50,000
  are not comparable.

Qualifying individuals are children under 13; married filing separately is not eligible (§21(e)(2)).
Dependent care benefits excluded from income (§129) are not modeled.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tax_modeler.units.earnings import lower_earner_income

from .hi_cdcc import default_donors, pay_probability

CAPS = (3_000.0, 6_000.0)


def applicable_percentage(agi) -> np.ndarray:
    """35% to $15,000 of AGI, minus one point per $2,000 or part above it, floor 20%."""
    a = np.asarray(agi, dtype=float)
    steps = np.ceil(np.maximum(a - 15_000.0, 0.0) / 2_000.0)
    return np.maximum(0.35 - 0.01 * steps, 0.20)


def federal_cdcc_expected(units: pd.DataFrame, donors=None, caps=CAPS) -> pd.DataFrame:
    """Expected federal credit per unit and the probability it is positive, over the donors' spending.

    Per donor outcome the credit is the percentage times min(spending, limit, lower earner's earnings), no more
    than the unit's federal tax before credits (``federal_tax_before_credits``); the expectation weights the
    outcomes by the donor shares (those who pay nothing get nothing). Needs ``num_children_under_13`` (NaN = 0),
    ``federal_agi``, ``federal_tax_before_credits``, ``filing_status`` and the filer earnings columns.
    """
    dist = donors if donors is not None else default_donors()
    n13 = units["num_children_under_13"].fillna(0).to_numpy(float)
    agi = units["federal_agi"].fillna(0).to_numpy(float)
    tax = units["federal_tax_before_credits"].fillna(0).to_numpy(float)
    earn = np.maximum(lower_earner_income(units), 0.0)
    ok = (n13 > 0) & (units["filing_status"].to_numpy() != "married_filing_separately")
    pct = applicable_percentage(agi)
    pay = pay_probability(agi)
    cap = np.where(n13 >= 2, caps[1], caps[0])
    credit = np.zeros(len(units))
    prob = np.zeros(len(units))
    for b, (x, p) in dist.items():
        idx = ok & ((n13 >= 2) if b == 2 else (n13 == 1))
        for xj, pj in zip(x, p, strict=True):
            c = np.minimum(pct[idx] * np.minimum(np.minimum(xj, cap[idx]), earn[idx]), tax[idx])
            credit[idx] += pj * pay[idx] * c
            prob[idx] += pj * pay[idx] * (c > 0)
    return pd.DataFrame({"federal_cdcc": credit, "federal_cdcc_claim_prob": prob}, index=units.index)
