"""Hawaii Low-Income Household Renters Credit (HRS §235-55.7).

A small refundable credit for renting low-income filers. The existing
:meth:`HawaiiTaxCredits.low_income_renters_credit` uses random sampling
(``np.random.random() < 0.30``) to imitate the share of low-income
filers who actually claim. That non-determinism is incompatible with
the Reform DSL's "compare baseline vs counterfactual" semantics — the
RNG state would scramble per call.

Current law (HRS §235-55.7(c); L Sp 1977 1st, c 15, last amended L 1990, c 98; read
2026-11-25 at capitol.hawaii.gov/hrscurrent/Vol04_Ch0201-0257/HRS0235/HRS_0235-0055_0007.htm):
a taxpayer with adjusted gross income **under $30,000** who paid **more than $1,000 in rent**
in the year may claim **$50 times the number of qualified exemptions**; each taxpayer 65 or
over may claim double. Refundable. Qualified exemptions (a)(2) are the chapter 235
exemptions for persons who lived in the State more than nine months, with no multiple for
vision, hearing or other disability. The claimant must not be claimable as a dependent and
the property must not be wholly or partly exempt from real property tax (b); rent is split
pro rata between qualifying occupants (d); spouses filing separately get what a joint
return would. Until 2026-11 this module used $150/$100/$50 AGI tiers and status-specific
caps ($30K/$40K/$35K/$20K), none of which is in the statute.

This module implements it deterministically:

  * AGI test: ``income`` under ``income_limit`` ($30,000) for every filing status.
  * Amount: ``per_exemption_amount`` ($50) x exemptions, from
    ``liability.hawaii.exemption_counts`` (filer, spouse on a joint return, each dependent;
    a ``num_exemptions`` column wins), the count the Hawaii tax computation uses, without
    disability multiples. Age 65+ is read per person: the filer, and the spouse on a joint
    return, each count as two exemptions when ``primary_agep`` / ``secondary_agep`` is 65 or
    over (the per-person reading of "each taxpayer sixty-five years of age or over may claim
    double", the way the form instructions count it). Dependents' ages are not on the
    frame, so a 65+ dependent is not doubled; a frame with no age columns gets no doubling.
  * Renter test: when the frame carries ``tenure`` (``pipeline.attach_tenure``, PUMS
    ``TEN``), only ``tenure == "renter"`` units qualify. Until 2026-11 the poverty
    pipeline screened on income alone, so homeowners got the credit (232K eligible units
    against 97K renters-only, TY2022).
  * Rent test: when the frame carries ``annual_rent`` (``pipeline.attach_rent``, PUMS ``RNTP``
    x 12, 0 for no cash rent), it must exceed ``min_annual_rent`` ($1,000). The whole
    household rent is credited to each unit in it; the pro-rata share in (d) is not split.
  * Not modelled (no field on the frame): the claimed-as-a-dependent exclusion in (b), the
    real-property-tax-exempt-property exclusion, the nine-month residence test, and the
    no-income resident clause. Dependents of another unit are not flagged on the frame.
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
0.20 (0.207 for TY2022, 0.194 for TY2023) (``docs/data_source_tests/renters-credit-anchor.md``). A frame with no
``tenure`` or weight column cannot be fitted and uses ``FALLBACK_TAKEUP``.
Claims are tax-return filings, so the factor is take-up and filing combined.

Reform DSL (``Reform.benefit_overrides["hi_renters"]``):

  * ``takeup_pct``           share of eligible renters receiving
                             (default None: fitted to DOTAX dollars)
  * ``anchor_year``          DOTAX year the fit targets
  * ``renters_only``         require ``tenure == "renter"`` (default True)
  * ``per_exemption_amount`` dollars per qualified exemption (default 50)
  * ``income_limit``         AGI ceiling (default 30,000)
  * ``min_annual_rent``      rent that must be exceeded (default 1,000)
  * ``amount_pct``           multiplier on final credit (default 1.0)
  * ``income_threshold_factor`` multiplier on the AGI ceiling (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_renters_claims
from tax_modeler.errors import ConfigError

#: The fitted take-up must land here or the fit raises (TY2022-23 fit at about 0.20).
TAKEUP_GUARD = (0.05, 0.40)
#: Used only when a frame cannot be fitted (no tenure or weight column): about the TY2022-23 fit.
FALLBACK_TAKEUP = 0.20

ELDERLY_AGE = 65


@dataclass(frozen=True)
class HawaiiRentersCreditParameters:
    per_exemption_amount: float = 50.0  # HRS 235-55.7(c)
    income_limit: float = 30_000.0  # AGI must be below this, every filing status
    min_annual_rent: float = 1_000.0  # rent paid must exceed this
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


def _exemptions(df: pd.DataFrame) -> np.ndarray:
    """Qualified exemptions: the Hawaii count, plus one more for each filer (and joint spouse) 65 or over."""
    from tax_modeler.liability.hawaii import exemption_counts

    n = np.nan_to_num(exemption_counts(df), nan=1.0)
    joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    if "primary_agep" in df.columns:
        n = n + (pd.to_numeric(df["primary_agep"], errors="coerce").fillna(0).to_numpy() >= ELDERLY_AGE)
    if "secondary_agep" in df.columns:
        n = n + (joint & (pd.to_numeric(df["secondary_agep"], errors="coerce").fillna(0).to_numpy() >= ELDERLY_AGE))
    return n


def _full_credit(df: pd.DataFrame, p: HawaiiRentersCreditParameters) -> np.ndarray:
    """Credit per unit at 100% take-up: AGI test, $ per exemption, then the renter and rent tests."""
    income = df["income"].fillna(0).astype(float).to_numpy()
    eligible = income < p.income_limit * p.income_threshold_factor
    if p.renters_only and "tenure" in df.columns:
        eligible &= (df["tenure"] == "renter").to_numpy()
    if p.renters_only and "annual_rent" in df.columns:
        eligible &= df["annual_rent"].fillna(0).to_numpy(float) > p.min_annual_rent
    return np.where(eligible, p.per_exemption_amount * _exemptions(df), 0.0)


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
