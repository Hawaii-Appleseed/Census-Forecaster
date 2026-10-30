"""Hawaii state supplement to federal SSI.

Hawaii is one of ~20 states that supplements the federal SSI benefit.
The HI supplement is a flat monthly amount added to every federal SSI
recipient's payment. As of 2024 the supplement is approximately $5/month
for living-independently individuals (DHS source: Hawaii Department of
Human Services, Med-QUEST eligibility manual). The amount is small but
the population covered is the same as federal SSI.

Phase 3 implementation: assumes any unit receiving federal SSI receives
the state supplement (true in practice — the supplement is mandatory for
every federal SSI recipient resident in Hawaii). Reform DSL can adjust
the amount to model state-budget changes.

Reform DSL knobs (``Reform.benefit_overrides["ssi_hi_supplement"]``):

  * ``monthly_amount`` flat HI state supplement (default $5/month)
  * ``coverage_pct``   share of federal-SSI recipients receiving the supplement (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

_HI_SSI_SUPPLEMENT_MONTHLY_2024 = 5.0


@dataclass(frozen=True)
class HawaiiSsiSupplementParameters:
    monthly_amount: float = _HI_SSI_SUPPLEMENT_MONTHLY_2024
    coverage_pct: float = 1.0


def hawaii_ssi_supplement_parameters() -> HawaiiSsiSupplementParameters:
    return HawaiiSsiSupplementParameters()


def with_hi_supp_overrides(
    base: HawaiiSsiSupplementParameters, overrides: Mapping[str, object] | None
) -> HawaiiSsiSupplementParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown SSI HI supplement keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_ssi_hi_supplement_for_units(
    units: pd.DataFrame,
    *,
    params: HawaiiSsiSupplementParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    federal_ssi_col: str = "ssi_amount",
    out_col: str = "ssi_hi_amount",
) -> pd.DataFrame:
    """Add the Hawaii state-supplement annual amount to ``units``.

    Requires the federal SSI column to already exist (units that get the
    federal benefit also get the supplement). If ``federal_ssi_col`` is
    absent or all zero, the output column is zero everywhere.
    """
    p = with_hi_supp_overrides(
        params or hawaii_ssi_supplement_parameters(), overrides
    )
    df = units.copy()
    if federal_ssi_col not in df.columns:
        df[out_col] = 0.0
        return df

    fed = df[federal_ssi_col].fillna(0).astype(float).to_numpy()
    receives_federal = fed > 0
    annual_supplement = p.monthly_amount * 12 * p.coverage_pct
    df[out_col] = np.where(receives_federal, annual_supplement, 0.0)
    return df
