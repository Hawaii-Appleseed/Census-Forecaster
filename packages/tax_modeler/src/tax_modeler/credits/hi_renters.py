"""Hawaii Low-Income Household Renters Credit (HRS §235-55.7).

A small refundable credit for renting low-income filers. The existing
:meth:`HawaiiTaxCredits.low_income_renters_credit` uses random sampling
(``np.random.random() < 0.30``) to imitate the share of low-income
filers who actually claim. That non-determinism is incompatible with
the Reform DSL's "compare baseline vs counterfactual" semantics — the
RNG state would scramble per call.

This module re-implements the credit deterministically:

  * Income test (AGI by filing status) — same as the existing module
  * Renter test: when the frame carries ``tenure`` (``pipeline.attach_tenure``,
    PUMS ``TEN``), only ``tenure == "renter"`` units can claim. Until 2026-11
    the poverty pipeline screened on income alone, so homeowners got the
    credit (232K eligible units against 97K renters-only, TY2022).
  * Take-up is a *uniform multiplier* on the credit amount rather than a
    per-unit Bernoulli coin flip (every eligible unit gets ``takeup`` x the
    full credit). Aggregate dollars are identical and the Reform DSL stays
    deterministic; per-unit values are smoothed.

Empirical anchor for ``takeup_pct``
-----------------------------------

``takeup_pct=None`` (the default) fits the multiplier so renters-only model
dollars equal DOTAX's claimed dollars (``calibration.dotax_renters_claims``,
"Tax Credits Claimed by Hawaii Taxpayers", Table A-1) for ``anchor_year``
(default: the frame's tax year capped at the latest published year, TY2023;
after that the claimed dollars are held flat). The fit uses the baseline
schedule, never the reform overrides, so a reform's ``amount_pct`` and
``income_threshold_factor`` still move dollars. The fit must land inside
``TAKEUP_GUARD`` (0.05-0.40) or it raises; TY2022 and TY2023 fit at about
0.15 (``docs/data_source_tests/renters-credit-anchor.md``). A frame with no
``tenure`` or weight column cannot be fitted and uses ``FALLBACK_TAKEUP``.
Claims are tax-return filings, so the factor is take-up and filing combined.

NOT verified against the statute: the $50/$100/$150 AGI tiers and the AGI
caps below are the module's long-standing schedule. HRS §235-55.7 could not
be fetched when the anchor was built (capitol.hawaii.gov refused scripted
requests), so the statute's per-exemption amount, the age-65 doubling of
exemptions and any minimum-rent test are NOT implemented here. Observed
claims average $99 against the model's $114 per unit. The fit absorbs a
schedule error in aggregate, but check the schedule against the statute
before relying on per-unit amounts.

Reform DSL (``Reform.benefit_overrides["hi_renters"]``):

  * ``takeup_pct``           share of eligible renters receiving
                             (default None: fitted to DOTAX dollars)
  * ``anchor_year``          DOTAX year the fit targets
  * ``renters_only``         require ``tenure == "renter"`` (default True)
  * ``amount_pct``           multiplier on final credit (default 1.0)
  * ``income_threshold_factor`` multiplier on AGI ceilings (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_renters_claims
from tax_modeler.errors import ConfigError

#: The fitted take-up must land here or the fit raises (TY2022-23 fit at about 0.15).
TAKEUP_GUARD = (0.05, 0.40)
#: Used only when a frame cannot be fitted (no tenure or weight column): the TY2022-23 fit.
FALLBACK_TAKEUP = 0.15

_DEFAULT_INCOME_CAP = {
    "single": 30_000,
    "married_filing_jointly": 40_000,
    "head_of_household": 35_000,
    "married_filing_separately": 20_000,
}


@dataclass(frozen=True)
class HawaiiRentersCreditParameters:
    income_cap_by_status: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_INCOME_CAP)
    )
    # Tiered credit by AGI (matches existing hawaii_credits.py)
    credit_under_15k: float = 150.0
    credit_15k_to_25k: float = 100.0
    credit_above_25k: float = 50.0
    takeup_pct: float | None = None  # None: fitted to DOTAX dollars
    anchor_year: int | None = None  # None: the frame's tax year, capped at the latest DOTAX year
    renters_only: bool = True
    amount_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_renters_credit_parameters() -> HawaiiRentersCreditParameters:
    return HawaiiRentersCreditParameters()


def with_renters_overrides(
    base: HawaiiRentersCreditParameters, overrides: Mapping[str, object] | None
) -> HawaiiRentersCreditParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown HI renters override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def _weights(df: pd.DataFrame) -> np.ndarray | None:
    for c in ("weight", "WGTP"):
        if c in df.columns:
            return df[c].to_numpy(float)
    return None


def _full_credit(df: pd.DataFrame, p: HawaiiRentersCreditParameters) -> np.ndarray:
    """Credit per unit at 100% take-up: the income test and tiers, then the renter test."""
    income = df["income"].fillna(0).astype(float).to_numpy()
    income_caps = df["filing_status"].map(p.income_cap_by_status).fillna(
        p.income_cap_by_status.get("single", 30_000)
    ).to_numpy() * p.income_threshold_factor
    tier_credit = np.where(
        income < 15_000, p.credit_under_15k,
        np.where(income < 25_000, p.credit_15k_to_25k, p.credit_above_25k),
    )
    credit = np.where(income < income_caps, tier_credit, 0.0)
    if p.renters_only and "tenure" in df.columns:
        credit = np.where((df["tenure"] == "renter").to_numpy(), credit, 0.0)
    return credit


def fit_renters_takeup(model_dollars: float, year: int) -> float:
    """Take-up that makes renters-only model dollars (at 100% take-up) equal DOTAX's claimed dollars.

    ``year`` is capped at the latest published tax year. Raises ``ConfigError`` outside ``TAKEUP_GUARD``.
    """
    if model_dollars <= 0:
        raise ConfigError("no renters-only credit dollars to fit the take-up to")
    target = dotax_renters_claims.dollars_M(min(year, dotax_renters_claims.latest_year())) * 1e6
    t = target / model_dollars
    lo, hi = TAKEUP_GUARD
    if not lo <= t <= hi:
        raise ConfigError(
            f"fitted HI renters take-up {t:.3f} is outside the guard {TAKEUP_GUARD}; "
            "the eligible population or schedule has moved, check before trusting the fit"
        )
    return t


def compute_hi_renters_for_units(
    units: pd.DataFrame,
    *,
    params: HawaiiRentersCreditParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "hi_renters_amount",
    tax_year: int | None = None,
) -> pd.DataFrame:
    """Compute the HI Low-Income Household Renters Credit per tax unit.

    ``tax_year`` picks the DOTAX year the take-up is fitted to when ``takeup_pct`` is None and
    ``anchor_year`` is unset; with neither, the latest published year.
    """
    p = with_renters_overrides(
        params or hawaii_renters_credit_parameters(), overrides
    )
    df = units.copy()
    credit = _full_credit(df, p)

    takeup = p.takeup_pct
    if takeup is None:
        w = _weights(df)
        if w is None or "tenure" not in df.columns:
            takeup = FALLBACK_TAKEUP
        else:
            # Fit on the baseline schedule so reform overrides still move dollars.
            base = replace(p, income_threshold_factor=1.0, amount_pct=1.0, takeup_pct=None, renters_only=True)
            year = p.anchor_year or tax_year or dotax_renters_claims.latest_year()
            takeup = fit_renters_takeup(float((w * _full_credit(df, base)).sum()), year)
    df[out_col] = credit * takeup * p.amount_pct
    return df
