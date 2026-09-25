"""Hawaii State Earned Income Tax Credit (HRS §235-55.75).

Hawaii enacted a state EITC effective TY 2018 (Act 107, 2017) at 20% of
the federal EITC, **non-refundable**. Act 114 (2022, HB 2510) made it
**refundable** and permanent from TY 2023, and Act 163 (2023) raised the rate
to **40% of federal EITC** for tax years beginning after December 31, 2022.

Act 163 is repealed on December 31, 2027 and §235-55.75(a) is reenacted as
it read before (L 2023, c 163, §5): **refundable, 20% of federal**, from
TY 2028. The 2026 session did not extend it (HB 2306's extension was not
enacted; its companion SB 3125 became Act 24 without it).

Calculation::

    hi_eitc = max(0, federal_eitc) × rate_of_federal

When ``refundable=True`` the credit is paid out in full (added to SPM
resources). When ``refundable=False`` (TY 2018-2022 vintage) it is
limited to remaining state tax liability after other non-refundable
credits.

Reform DSL (``Reform.benefit_overrides["hi_eitc"]``):

  * ``rate_of_federal``    fraction of federal EITC (0.40 for TY 2023-2027, 0.20 otherwise)
  * ``refundable``         True/False (True from TY 2023)
  * ``amount_pct``         multiplier on final credit (default 1.0)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError


@dataclass(frozen=True)
class HawaiiEitcParameters:
    """HI EITC parameters. Defaults reflect Act 163 (TY 2023-2027) law."""

    rate_of_federal: float = 0.40
    refundable: bool = True
    amount_pct: float = 1.0


def hawaii_eitc_parameters(tax_year: Optional[int] = None) -> HawaiiEitcParameters:
    """Year-aware HI EITC parameters.

    * TY 2018-2022: 20% of federal, non-refundable (Act 107, 2017).
    * TY 2023-2027: 40% of federal, refundable (Act 114, 2022; Act 163, 2023).
    * TY 2028 on:   20% of federal, refundable (Act 163 repealed 12/31/2027;
      the Act 114 text is reenacted).

    When ``tax_year`` is omitted, returns the Act 163 parameters (law in
    effect when this was written, 2026). Callers scoring other years must pass
    the year so neither backtests nor post-2027 projections misstate the credit.
    """
    if tax_year is None or 2023 <= tax_year <= 2027:
        return HawaiiEitcParameters(rate_of_federal=0.40, refundable=True)
    if tax_year >= 2028:
        return HawaiiEitcParameters(rate_of_federal=0.20, refundable=True)
    return HawaiiEitcParameters(rate_of_federal=0.20, refundable=False)


def with_hi_eitc_overrides(
    base: HawaiiEitcParameters, overrides: Optional[Mapping[str, object]]
) -> HawaiiEitcParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown HI EITC override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_hi_eitc_for_units(
    units: pd.DataFrame,
    *,
    tax_year: Optional[int] = None,
    params: Optional[HawaiiEitcParameters] = None,
    overrides: Optional[Mapping[str, object]] = None,
    federal_eitc_col: str = "eitc_amount",
    tax_before_credits_col: str = "hi_tax_before_credits",
    out_col: str = "hi_eitc_amount",
) -> pd.DataFrame:
    """Compute the Hawaii state EITC per tax unit.

    Adds ``out_col`` to a copy of ``units``. If the federal EITC column
    is missing (e.g. baseline didn't run ``compute_base_tax``), the
    output is zero everywhere.

    For non-refundable mode, the credit is capped at remaining state tax
    liability (``hi_tax_before_credits`` minus other already-applied
    non-refundable credits). When that column is missing the function
    falls back to the full credit — slightly overstates the
    non-refundable case for taxpayers whose tax is below the credit.
    """
    p = with_hi_eitc_overrides(
        params or hawaii_eitc_parameters(tax_year), overrides
    )
    df = units.copy()

    if federal_eitc_col not in df.columns:
        df[out_col] = 0.0
        return df

    fed = df[federal_eitc_col].fillna(0).astype(float).to_numpy()
    raw = np.maximum(fed, 0.0) * p.rate_of_federal * p.amount_pct

    if p.refundable or tax_before_credits_col not in df.columns:
        df[out_col] = raw
    else:
        tax_cap = df[tax_before_credits_col].fillna(0).astype(float).to_numpy()
        df[out_col] = np.minimum(raw, np.maximum(tax_cap, 0.0))

    return df
