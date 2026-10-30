"""
ARPA 2021 Expanded Child Tax Credit (counterfactual / what-if module).

Computes the federal CTC under the American Rescue Plan Act (ARPA) 2021
expansion (IRC §24(i), one-year-only, in effect for TY 2021). Used as a
counterfactual scenario for the poverty-impact module: what would the
CTC look like if ARPA had been extended into the current tax year?

ARPA 2021 design (vs. TCJA baseline):
  * Max credit per child: $3,600 (under 6) / $3,000 (age 6-17)
    — TCJA baseline is $2,000/child.
  * Age limit raised from under-17 to under-18.
  * Fully refundable (no $1,500/$1,600/$1,700 ACTC cap, no $2,500
    earned-income floor).
  * Two-tier phase-out:
      - The "bonus" amount (max minus $2,000 base) phases out at
        5%/dollar above $75k single / $112.5k HoH / $150k MFJ, down
        to $0 (cannot reduce the $2,000 base).
      - The $2,000/child base then phases out per the standard TCJA
        rules at $200k single / $400k MFJ.

LIMITATIONS (intentional v1 approximations):

1. **Dependent ages**: the upstream pipeline synthesizes every dependent
   as age 10 in :func:`tax_modeler.pipeline.enrich_for_credits` (the
   PUMS person→tax-unit map preserves the dependent count but loses
   age detail). For the ARPA tier split we therefore use a Hawaii-wide
   under-6 fraction (33.4%, derived from 2022 ACS under-18
   population) and assign the **expected** per-child max:

        expected_max_per_child = 0.334 × $3,600 + 0.666 × $3,000 ≈ $3,200

   Precision better than this requires re-imputing dependent ages from
   PUMS persons; see ``docs/archive/CBO_COMPONENT_AGING_SCOPE.md`` for that scope.

2. **Phase-out scope**: applied to filing-status-derived MAGI proxy
   (``total_cash_income`` from ``enrich_for_credits``). Modified AGI
   under ARPA includes the same components as standard CTC MAGI
   (essentially AGI for non-international filers), so this is fine for
   the Hawaii population.

3. **Age-18 children**: ARPA raised the age limit to under-18 (so 17
   year-olds qualified), adding ~6% to the qualifying-child population
   on average. With age-uniform synthesized dependents at age 10, we
   don't capture this directly — instead the population scales up via
   the multiplier ``_ARPA_QUALIFYING_AGE_INFLATION = 1.06``. Same
   limitation as (1): tighter with re-imputed ages.

Reference: IRS Publication 972 (2021), ARPA §9611 (Pub. L. 117-2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Hawaii under-6 share of population under 18 (2022 ACS B01001).
# 0-5: ~99k; 6-17: ~197k; share under 6 = 99/(99+197) ≈ 0.334.
_HI_UNDER_6_SHARE = 0.334

# Per-child maximums under ARPA (IRC §24(i)(2)(A)).
_ARPA_MAX_UNDER_6 = 3_600.0
_ARPA_MAX_6_TO_17 = 3_000.0

# Expected per-child maximum (Hawaii age blend).
_ARPA_EXPECTED_MAX_PER_CHILD = (
    _HI_UNDER_6_SHARE * _ARPA_MAX_UNDER_6
    + (1.0 - _HI_UNDER_6_SHARE) * _ARPA_MAX_6_TO_17
)

# Qualifying-child population multiplier: ARPA raised the age limit
# from under-17 to under-18, adding the 17-year-olds (~6% of 0-17
# population in Hawaii per ACS 2022).
_ARPA_QUALIFYING_AGE_INFLATION = 1.06

# ARPA "bonus" phase-out thresholds (statutory, §24(i)(4)(B)).
_ARPA_BONUS_PO_SINGLE = 75_000.0
_ARPA_BONUS_PO_HOH = 112_500.0
_ARPA_BONUS_PO_JOINT = 150_000.0

# TCJA base-credit phase-out thresholds (preserved at $2,000/child floor).
_TCJA_PO_SINGLE = 200_000.0
_TCJA_PO_JOINT = 400_000.0

_PHASEOUT_RATE = 0.05  # $50 per $1,000 excess (statutory).
_TCJA_BASE_PER_CHILD = 2_000.0


def _phaseout_threshold(filing_status: pd.Series, *, single: float, hoh: float, joint: float) -> np.ndarray:
    """Vectorized phase-out threshold lookup by filing status."""
    fs = filing_status.astype(str).str.lower().to_numpy()
    out = np.where(fs == "married_filing_jointly", joint,
                   np.where(fs == "head_of_household", hoh, single))
    return out.astype(float)


def arpa_ctc_for_tax_units(
    units: pd.DataFrame,
    *,
    qualifying_children_col: str = "num_qualifying_children",
    income_col: str = "total_cash_income",
    filing_status_col: str = "filing_status",
    out_total_col: str = "arpa_ctc_total",
    out_refundable_col: str = "arpa_ctc_refundable",
) -> pd.DataFrame:
    """Compute ARPA 2021 expanded CTC per tax unit.

    Returns a *copy* of ``units`` with two new columns:
      * ``arpa_ctc_total`` — total ARPA CTC after both phase-outs
        (fully refundable, so total == refundable in ARPA design).
      * ``arpa_ctc_refundable`` — equal to total under ARPA.

    Inputs expected on ``units``:
      * ``num_qualifying_children`` (int): CTC-qualifying-child count
        from :func:`tax_modeler.pipeline.enrich_for_credits`.
      * ``total_cash_income`` (float): MAGI proxy.
      * ``filing_status`` (str): one of "single", "head_of_household",
        "married_filing_jointly", "married_filing_separately".
    """
    for col in (qualifying_children_col, income_col, filing_status_col):
        if col not in units.columns:
            raise KeyError(
                f"arpa_ctc_for_tax_units requires column {col!r}; "
                f"present: {sorted(units.columns)[:20]}..."
            )

    df = units.copy()
    n_children = (
        df[qualifying_children_col].fillna(0).clip(lower=0).astype(float)
        * _ARPA_QUALIFYING_AGE_INFLATION
    )
    income = df[income_col].fillna(0).astype(float).to_numpy()

    # Base credit (TCJA $2,000/child) — phased out at $200k single / $400k MFJ.
    base_credit = n_children.to_numpy() * _TCJA_BASE_PER_CHILD
    tcja_threshold = _phaseout_threshold(
        df[filing_status_col], single=_TCJA_PO_SINGLE, hoh=_TCJA_PO_SINGLE, joint=_TCJA_PO_JOINT
    )
    base_excess = np.maximum(0.0, income - tcja_threshold)
    base_after_po = np.maximum(0.0, base_credit - _PHASEOUT_RATE * base_excess)

    # Bonus = expected_max_per_child - $2,000, phased out at $75k/$112.5k/$150k.
    bonus_per_child = _ARPA_EXPECTED_MAX_PER_CHILD - _TCJA_BASE_PER_CHILD
    bonus_credit = n_children.to_numpy() * bonus_per_child
    arpa_threshold = _phaseout_threshold(
        df[filing_status_col], single=_ARPA_BONUS_PO_SINGLE,
        hoh=_ARPA_BONUS_PO_HOH, joint=_ARPA_BONUS_PO_JOINT,
    )
    bonus_excess = np.maximum(0.0, income - arpa_threshold)
    bonus_after_po = np.maximum(0.0, bonus_credit - _PHASEOUT_RATE * bonus_excess)

    total = base_after_po + bonus_after_po
    df[out_total_col] = total
    df[out_refundable_col] = total  # ARPA is fully refundable.
    return df


__all__ = ["arpa_ctc_for_tax_units"]
