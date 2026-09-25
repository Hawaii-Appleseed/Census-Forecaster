"""Hawaii Refundable Food/Excise Tax Credit (HRS §235-55.85).

The Food/Excise Tax Credit offsets the regressive burden of the Hawaii
General Excise Tax (GET) on food and basic goods. It is refundable and is
claimed per qualified exemption, at a flat amount set by an AGI table.

Statutory schedules (§235-55.85(b); AGI is federal AGI, §235-55.85(g)):

* **Act 163, SLH 2023** — taxable years 2023 through 2027. Act 163 is
  repealed on December 31, 2027 and subsection (b) is reenacted as it read
  before (L 2023, c 163, §5), so the earlier table returns for 2028 onward.
* **Prior law** (L 2015, c 223) — taxable years before 2023 and from 2028.

Computation::

    credit = amount(AGI band, filing-status table) × qualified exemptions

Qualified exemptions = filer + spouse (joint) + dependents. The statute's
further limits (no extra exemption for age 65+ or disability; presence in
Hawaii more than nine months) are not modeled.

This supersedes three approximations that matched neither schedule: a linear
phase-out ($110 per exemption fading out over $30K-$50K single / $50K-$70K
joint) here and in adjustments/hawaii_credits.py, and a flat $110 per
exemption below $20K / $30K in liability/hawaii.py. Both of those now call
:func:`food_excise_credit`.

Reform DSL (``Reform.benefit_overrides["hi_food_excise"]``):

  * ``single`` / ``joint``    replacement tables: tuples of (AGI ceiling, amount)
  * ``amount_pct``            multiplier on every amount (default 1.0)
  * ``income_threshold_factor`` multiplier on every AGI ceiling (default 1.0)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

Schedule = tuple[tuple[float, float], ...]   # ((AGI ceiling, credit per exemption), ...)

# §235-55.85(b) as amended by Act 163 (2023): "Under $15,000 ... $220" etc.
ACT163_SINGLE: Schedule = (
    (15_000, 220), (20_000, 200), (25_000, 170), (30_000, 140), (40_000, 110),
)
ACT163_JOINT: Schedule = ACT163_SINGLE + ((50_000, 90), (60_000, 70))

# §235-55.85(b) before Act 163 (L 2015, c 223), reenacted from TY2028.
PRIOR_SINGLE: Schedule = (
    (5_000, 110), (10_000, 100), (15_000, 85), (20_000, 70), (30_000, 55),
)
PRIOR_JOINT: Schedule = PRIOR_SINGLE + ((40_000, 45), (50_000, 35))

ACT163_FIRST_YEAR = 2023
ACT163_LAST_YEAR = 2027           # repealed December 31, 2027

# The "joint" table covers heads of household, surviving spouses, spouses
# filing separately and married couples filing jointly.
_JOINT_TABLE_STATUSES = frozenset({
    "married_filing_jointly", "head_of_household", "married_filing_separately",
    "qualifying_widow", "qualifying_surviving_spouse",
})


@dataclass(frozen=True)
class HawaiiFoodExciseParameters:
    """Food/excise credit schedules. Defaults are the Act 163 (TY2023-2027) law."""

    single: Schedule = ACT163_SINGLE
    joint: Schedule = ACT163_JOINT
    amount_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_food_excise_parameters(tax_year: Optional[int] = None) -> HawaiiFoodExciseParameters:
    """Year-aware parameters: Act 163 for TY2023-2027, prior law otherwise.

    With ``tax_year`` omitted, returns the Act 163 schedule (law in effect
    when this was written, 2026).
    """
    if tax_year is None or ACT163_FIRST_YEAR <= tax_year <= ACT163_LAST_YEAR:
        return HawaiiFoodExciseParameters()
    return HawaiiFoodExciseParameters(single=PRIOR_SINGLE, joint=PRIOR_JOINT)


def with_food_excise_overrides(
    base: HawaiiFoodExciseParameters, overrides: Optional[Mapping[str, object]]
) -> HawaiiFoodExciseParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown HI food/excise override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def _per_exemption(agi: np.ndarray, schedule: Schedule, factor: float) -> np.ndarray:
    """Amount per exemption from a bracket table; 0 at or above the last ceiling."""
    ceilings = np.array([c for c, _ in schedule], dtype=float) * factor
    amounts = np.append(np.array([a for _, a in schedule], dtype=float), 0.0)
    return amounts[np.searchsorted(ceilings, agi, side="right")]


def food_excise_credit(
    agi: float,
    filing_status: str,
    n_exemptions: int,
    tax_year: Optional[int] = None,
    params: Optional[HawaiiFoodExciseParameters] = None,
) -> float:
    """Scalar §235-55.85 credit for one tax unit (the per-filer liability and
    legacy credit paths). ``n_exemptions`` = filer + spouse + dependents."""
    p = params or hawaii_food_excise_parameters(tax_year)
    table = p.joint if filing_status in _JOINT_TABLE_STATUSES else p.single
    per_ex = _per_exemption(np.array([max(float(agi), 0.0)]), table, p.income_threshold_factor)[0]
    return float(per_ex * max(int(n_exemptions), 0) * p.amount_pct)


def compute_hi_food_excise_for_units(
    units: pd.DataFrame,
    *,
    tax_year: Optional[int] = None,
    params: Optional[HawaiiFoodExciseParameters] = None,
    overrides: Optional[Mapping[str, object]] = None,
    out_col: str = "hi_food_excise_amount",
) -> pd.DataFrame:
    """Compute the HI Food/Excise Tax Credit per tax unit.

    ``tax_year`` selects the statutory schedule when ``params`` is not given.
    """
    p = with_food_excise_overrides(
        params or hawaii_food_excise_parameters(tax_year), overrides
    )
    df = units.copy()

    agi = df["income"].fillna(0).astype(float).clip(lower=0).to_numpy()
    status = df["filing_status"].to_numpy()
    is_joint_table = np.isin(status, list(_JOINT_TABLE_STATUSES))
    n_exemptions = (
        1
        + (status == "married_filing_jointly").astype(int)
        + df["num_dependents"].fillna(0).astype(int).to_numpy()
    )

    per_ex = np.where(
        is_joint_table,
        _per_exemption(agi, p.joint, p.income_threshold_factor),
        _per_exemption(agi, p.single, p.income_threshold_factor),
    )
    df[out_col] = per_ex * n_exemptions * p.amount_pct
    return df
