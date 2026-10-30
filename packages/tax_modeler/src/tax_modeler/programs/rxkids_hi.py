"""RxKids Hawaiʻi — modeled prenatal + infant cash prescription program.

Hawaii-equivalent of the RxKids program operating in Flint MI and 35+
other Michigan communities (administered by Michigan State University +
GiveDirectly, launched Jan 2024). RxKids provides unrestricted cash
"prescribed" during pregnancy and the postnatal period, structured as
charitable disbursement (not taxable income, no SNAP/Medicaid offset).

This module evaluates a Hawaii-targeted variant adapted to local cost
of living and Hawaii's existing Medicaid (QUEST Integration) eligibility
framework. The default parameters model the **statutory** eligibility:
a unit qualifies if it satisfies EITHER of two clauses (see "Eligibility"
below). This is a substantially broader gate than a Medicaid-only design
and sits between the Medicaid-targeted (~$8-15M) and fully-universal
(~$110M) cost poles described in RXKIDS_METHODOLOGY.md.

Program scope
-------------

Eligibility (statutory — clause 1 OR clause 2)
    * **Clause 1 — Medicaid.** Any unit whose member qualifies for
      benefits under the State's Medicaid (Med-QUEST) program. Reuses the
      ``medicaid_receives`` boolean from
      ``tax_modeler.benefits.compute_medicaid_for_units``; the caller
      pre-attaches that column. For the prenatal arm, the pregnant-women
      pathway (``pregnant_fpl_cap``, 196% FPL) is also honored. (If the
      flag is absent the module falls back to clause 2 only and warns.)
    * **Clause 2 — 300% FPL.** Family income ≤ 300% FPL for a family of
      applicable size *including the expected unborn child*. The income
      test runs at family size; a birth's family includes the child either
      way (the unborn child pre-birth equals the newborn dependent in the
      cross-section), so one family-size test serves both arms.

Both arms are driven by the same **birth events** — ``num_dependents ×
child_under_age_share`` (the annual birth-rate per dependent). A first
birth appears as the newborn dependent in the PUMS cross-section, so this
captures first AND repeat births across ALL filing statuses (married
couples included), not just a single/HoH-no-dependent proxy. Each eligible
birth draws one prenatal and one postnatal payment.

Payment structure (defaults below; override for alternative designs)
    * Prenatal: ``prenatal_monthly`` × ``prenatal_months``
      (default $1,500 × 1 = $1,500 per pregnancy)
    * Postnatal: ``postnatal_monthly_per_child`` × ``n_children`` ×
      ``postnatal_months`` (default $500/mo × eligible kids × 6 = $3,000)
    * Take-up: ``takeup_rate`` (default 0.90; Flint observed 0.98 under
      universal design + hospital partnership — Hawaii models 0.90 in
      absence of analogous infrastructure)

Tax treatment
    * ``is_taxable = False`` (default). Flint program is structured as
      charitable disbursement (GiveDirectly), so payments do not flow
      through AGI / EITC / CTC interactions. Modeled by adding the
      ``rxkids_amount`` column to SPM resources only — NOT to
      ``total_cash_income``. See ``compute_spm_resources`` wiring.

Outputs
    * ``rxkids_amount`` — combined expected annual benefit per unit.
    * ``rxkids_prenatal_amount`` / ``rxkids_postnatal_amount`` — the two
      arm subtotals, for splitting program outlay in the cost report.

Universal variant (advocacy framing)
    Override the defaults to model the Flint universal program:
        params = RxKidsHIParams(
            prenatal_monthly=2000.0,    # one-time $2,000 (use
                                        # prenatal_months=1 to model
                                        # as one-shot)
            prenatal_months=1,
            postnatal_monthly_per_child=500.0,
            income_fpl_cap=10.0,        # effectively universal
            takeup_rate=0.95,
        )

Caveats
-------
* Birth driver: prefer an OBSERVED per-unit birth count (age-0 dependents
  from PUMS) supplied via ``birth_count_col``. The library default, when no
  such column is given, is the proxy ``num_dependents × child_under_age_share``
  (0.066 = Hawaii annual births ÷ dependents 0-17) — probabilistic, with
  reliable population totals but per-unit expectations, not literal payments.
  The proxy multiplies an all-dependents count by a children-only-calibrated
  rate and overstates births; see ``compute_rxkids_for_units`` and
  ``RXKIDS_METHODOLOGY.md`` §3.
* The Flint RxKids program is universal (no income/Medicaid test). The
  default parameters here model a Medicaid-eligibility-gated variant
  for cost reasons. Override ``income_fpl_cap`` to a high value to
  model the universal variant.
* Modeled as non-taxable charitable cash. If the Hawaii legislature
  structured a similar program differently (e.g. as a refundable tax
  credit), the resource-accounting treatment would need to change.

Reform DSL hooks (``Reform.benefit_overrides["rxkids"]``)
    * ``prenatal_monthly``                $-per-payment override (one-time)
    * ``prenatal_months``                 number of prenatal payments (1 = one-time)
    * ``postnatal_monthly_per_child``     $-per-month-per-child override
    * ``postnatal_months``                number of postnatal monthly payments
    * ``income_fpl_cap``                  FPL multiplier override (10.0 = universal)
    * ``takeup_rate``                     take-up rate override

See ``RXKIDS_METHODOLOGY.md`` at the repo root for full sourcing of
parameter values and the Hawaii calibration.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ..benefits._fpl import fpl_year_for, hawaii_fpl

LOG = logging.getLogger(__name__)


# Annual qualifying-birth rate per dependent (FLOW, not a point-in-time
# stock). The postnatal payment is the full per-birth entitlement
# (postnatal_monthly_per_child × postnatal_months, e.g. $500 × 6 = $3,000),
# so the correct annual-cost basis is the full annual birth cohort, not the
# <6-month snapshot. Using a half-year stock here would understate the
# postnatal arm by ~2× (see RXKIDS_METHODOLOGY.md §3).
#   Hawaii annual births 15,535 (CDC NVSR 73-02)
#   ÷ ACS PUMS Hawaii dependents 0-17 ≈ 233,000 (5-yr sample)
#   ≈ 0.066.
# Interpretation: n_dep × this rate ≈ the family's expected qualifying
# births this year; summed over the population it recovers total births.
_DEFAULT_CHILD_UNDER_AGE_SHARE = 0.066


@dataclass(frozen=True)
class RxKidsHIParams:
    """Parameters for the Hawaii RxKids equivalent.

    Defaults model the statutory eligibility (Medicaid OR ≤300% FPL incl.
    unborn child) with this payment design:
      * Prenatal: one-time $1,500 payment per pregnancy.
      * Postnatal: $500/month for 6 months beginning at birth ($3,000 total
        per child).

    Eligibility is set by ``income_fpl_cap`` (default 3.00 = 300% FPL) plus
    the Medicaid clause evaluated in ``compute_rxkids_for_units``. Set
    ``income_fpl_cap=1.38`` for a Medicaid-adult-expansion-only income test,
    or a high value (e.g. 10.0) for an effectively-universal variant.
    """

    prenatal_monthly: float = 1500.0
    """Dollar payment per prenatal month, per pregnant filer."""

    postnatal_monthly_per_child: float = 500.0
    """Dollar payment per month, per child under ``postnatal_age_cutoff``."""

    prenatal_months: int = 1
    """Number of months of prenatal payments per pregnancy (1 = one-time)."""

    postnatal_months: int = 6
    """Number of months of postnatal payments per eligible child."""

    postnatal_age_cutoff: int = 1
    """Children under this age (years) receive postnatal payments.

    Default 1 = infants in their first year of life, approximating the
    0–6-month window. PUMS does not carry sub-year ages, so age=0 is
    the closest proxy.
    """

    income_fpl_cap: float = 3.00
    """Income / FPL ratio at or below which a unit qualifies (clause 2).

    Default 3.00 = the statutory 300% FPL income test. A unit also
    qualifies via clause 1 (Medicaid receipt) regardless of this cap;
    see ``compute_rxkids_for_units``. Set higher (e.g. 10.0) to model an
    effectively-universal income variant, or to 1.38 for a Medicaid-
    adult-expansion-only income test.
    """

    pregnant_fpl_cap: float = 1.96
    """Medicaid pregnant-women pathway FPL cap (clause 1, prenatal arm).

    Hawaii Med-QUEST covers pregnant women up to 196% FPL. For the prenatal
    arm, a family at or below this ratio qualifies via the Medicaid
    pregnancy pathway even if the general ``medicaid_receives`` flag (which
    is built from the adult/child pathways) does not fire. At the default
    ``income_fpl_cap=3.00`` this is subsumed by the income test; it only
    binds when the income cap is set below 196% FPL.
    """

    takeup_rate: float = 0.90
    """Postnatal (newborn) take-up — the fraction of eligible newborns claimed.

    Default 0.90 — below Flint's observed 0.98 (universal design + hospital
    partnership) to reflect weaker year-1 infrastructure in Hawaii, but
    above a deeply conservative floor. Set 0.98 for the Flint-observed rate.
    Also the prenatal rate unless ``prenatal_takeup_rate`` is set.
    """

    prenatal_takeup_rate: float | None = None
    """Prenatal-arm take-up; falls back to ``takeup_rate`` when ``None``.

    Mothers enroll prenatally at a lower rate than newborns are enrolled
    (Flint: ~90% prenatal vs ~98% of newborns), because some are reached only
    after birth. ``None`` (the library default) keeps both arms uniform; the
    forecast sets this to ``takeup_rate × a Flint-anchored ratio`` so the
    prenatal arm runs a few points below the postnatal/newborn rate.
    """

    is_taxable: bool = False
    """If False, payments are added to SPM resources but not money income.

    Matches the Flint design (GiveDirectly charitable disbursement, not
    IRS-reported). A True value would route payments through AGI and
    therefore interact with EITC/CTC phase-outs — currently not modeled.
    """

    child_under_age_share: float = _DEFAULT_CHILD_UNDER_AGE_SHARE
    """Annual qualifying-birth rate per dependent (a FLOW, default 0.066).

    Converts ``num_dependents`` into the family's expected number of
    qualifying births this year — the common driver of BOTH arms (each
    birth draws a prenatal and a postnatal payment), since PUMS does not
    carry individual child ages on the tax-unit frame. Because the
    payments are full per-birth entitlements, this must be the full annual
    birth cohort per dependent (≈ births ÷ dependents 0-17), NOT a
    <6-month stock share. Sensitivity: linear (scales total cost).
    """


def hawaii_rxkids_parameters() -> RxKidsHIParams:
    """Default RxKids Hawaiʻi parameters (statutory eligibility variant)."""
    return RxKidsHIParams()


def with_rxkids_overrides(
    base: RxKidsHIParams, overrides: Mapping[str, object] | None
) -> RxKidsHIParams:
    """Apply Reform-DSL-style overrides to a parameter set."""
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown RxKids override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_rxkids_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: RxKidsHIParams | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "rxkids_amount",
    medicaid_col: str = "medicaid_receives",
    family_income_col: str | None = None,
    family_size_col: str | None = None,
    birth_count_col: str | None = None,
) -> pd.DataFrame:
    """Compute the annual RxKids Hawaiʻi benefit amount per tax unit.

    Adds ``rxkids_amount`` (= prenatal + postnatal, take-up-adjusted) plus
    the two arm subtotals ``rxkids_prenatal_amount`` and
    ``rxkids_postnatal_amount`` (derived from ``out_col``'s stem).

    Statutory eligibility (clause 1 OR clause 2)
    --------------------------------------------
    * **Clause 1 — Medicaid**: ``medicaid_col`` (default
      ``"medicaid_receives"``) is True; the prenatal arm additionally
      honors the Medicaid pregnant-women pathway (``pregnant_fpl_cap``,
      196% FPL). The caller must pre-attach ``medicaid_col`` via
      ``tax_modeler.benefits.compute_medicaid_for_units``; if it is missing
      the function falls back to clause 2 only and logs a warning.
    * **Clause 2 — 300% FPL**: ``income / FPL(family_size) <=
      income_fpl_cap`` (default 3.00), tested at family size. A birth's
      family includes the child either way, so one family-size test serves
      both arms. ``tax_year`` selects the FPL table.

    Birth events (the common driver of both arms)
    ---------------------------------------------
    By default, ``birth_events = num_dependents × child_under_age_share`` —
    a *proxy* that imputes the family's expected qualifying births this year
    from its dependent count. If ``birth_count_col`` is supplied and present
    on the frame, that column is used directly as the per-unit birth count
    instead (the **observed** basis — e.g. a count of age-0 dependents from
    PUMS, optionally calibrated to a vital-statistics total). The proxy
    multiplies an all-dependents count (which includes older children,
    students, and adult dependents) by a flat rate calibrated against a
    children-only denominator, so it overstates total births materially;
    prefer ``birth_count_col`` whenever observed infant counts are available.

    Arms (both driven by ``birth_events``)
    --------------------------------------
    * **Postnatal**: ``birth_events > 0`` AND (clause 1 OR clause 2).
      Amount = ``birth_events × postnatal_monthly_per_child ×
      postnatal_months × takeup_rate``.
    * **Prenatal**: ``birth_events > 0`` AND (clause 1 incl. pregnancy
      pathway OR clause 2). Amount = ``birth_events × prenatal_monthly ×
      prenatal_months × takeup_rate``. Captures first and repeat births
      across all filing statuses (each birth's newborn appears as a
      dependent in the cross-section).

    Notes
    -----
    * ``is_taxable=False`` is the modeling default — the amount returned
      here must be added to SPM resources, not money_income. The wiring
      in ``compute_spm_resources`` already supports a ``rxkids_col``
      parameter for this purpose.
    * Take-up imputation here is the *combined* take-up rate (eligibility
      × claim). Unlike SNAP, RxKids has no separate IRS-anchored take-up
      target (the program is hypothetical for Hawaii). To explore
      sensitivity, override ``takeup_rate``.
    """
    p = with_rxkids_overrides(params or hawaii_rxkids_parameters(), overrides)
    if not 0.0 <= p.takeup_rate <= 1.0:
        raise ConfigError(
            f"takeup_rate must be in [0.0, 1.0], got {p.takeup_rate}"
        )
    if p.prenatal_takeup_rate is not None and not 0.0 <= p.prenatal_takeup_rate <= 1.0:
        raise ConfigError(
            f"prenatal_takeup_rate must be in [0.0, 1.0], got {p.prenatal_takeup_rate}"
        )
    if p.postnatal_age_cutoff < 0:
        raise ConfigError(
            f"postnatal_age_cutoff must be >= 0, got {p.postnatal_age_cutoff}"
        )

    # Arm-specific take-up: postnatal (newborn) is takeup_rate; prenatal falls
    # back to it unless set lower (mothers enroll prenatally at a lower rate).
    post_takeup = p.takeup_rate
    pre_takeup = p.prenatal_takeup_rate if p.prenatal_takeup_rate is not None else p.takeup_rate

    df = units.copy()

    filing_status = df["filing_status"].astype(str).to_numpy()
    n_dep = df["num_dependents"].fillna(0).clip(lower=0).astype(int).to_numpy()
    is_joint = (filing_status == "married_filing_jointly")
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()

    # Income/size basis for the FPL test. The statute uses *family* income for
    # a *family* of applicable size. If the caller supplies family-grain columns
    # (income and persons summed across the tax units that share a household /
    # SPM unit), use those — testing per tax unit at tax-unit size would split a
    # household's income across small units and overstate eligibility. Falls
    # back to tax-unit grain when the columns are absent.
    if (
        family_income_col is not None and family_size_col is not None
        and family_income_col in df.columns and family_size_col in df.columns
    ):
        test_income = df[family_income_col].fillna(0).astype(float).to_numpy()
        base_size = df[family_size_col].fillna(0).clip(lower=1).astype(int).to_numpy()
    else:
        test_income = income
        base_size = hh_size

    # ---- Clause 1: Medicaid receipt ----
    # The statute makes anyone who "qualifies for benefits under the
    # State's Medicaid program" eligible, independent of the 300% FPL
    # income test. We reuse the medicaid_receives boolean produced by
    # tax_modeler.benefits.compute_medicaid_for_units; the caller must
    # pre-attach it (keeps this hypothetical-program module decoupled
    # from the benefits assembly). If it is absent we fall back to the
    # income test alone and warn.
    if medicaid_col in df.columns:
        medicaid_eligible = df[medicaid_col].fillna(False).to_numpy(dtype=bool)
    else:
        LOG.warning(
            "compute_rxkids_for_units: %r column not found; applying the "
            "300%% FPL income test only (clause 2). Run "
            "compute_medicaid_for_units(units) first to honor the Medicaid "
            "eligibility clause.",
            medicaid_col,
        )
        medicaid_eligible = np.zeros(len(df), dtype=bool)

    # ---- Clause 2: 300% FPL income test (at family size) ----
    # The run's tax_year selects the FPL table so forward-projected incomes
    # are tested against same-year thresholds. A birth's family size includes
    # the child either way ("the expected unborn child" pre-birth equals the
    # newborn dependent post-birth), so a single family-size test serves both
    # arms.
    fpl_yr = fpl_year_for(tax_year)
    fpl_base = np.array(
        [hawaii_fpl(fpl_yr, household_size=int(s)) for s in base_size]
    )
    fpl_ratio = np.where(fpl_base > 0, test_income / fpl_base, np.inf)
    income_eligible = fpl_ratio <= p.income_fpl_cap
    # Clause 1 pregnancy pathway: Med-QUEST covers pregnant women to 196% FPL.
    # Lets the prenatal arm qualify via Medicaid pregnancy coverage even when
    # the income cap is set below that (subsumed at the 300% default).
    preg_medicaid_eligible = fpl_ratio <= p.pregnant_fpl_cap

    # ---- Birth events: the common driver of both arms ----
    # Preferred basis: an OBSERVED per-unit birth count supplied via
    # ``birth_count_col`` (e.g. age-0 dependents from PUMS, calibrated to a
    # vital-statistics total). Fallback proxy: n_dep × the annual
    # birth-rate-per-dependent. The proxy multiplies an all-dependents count
    # by a rate calibrated against a children-only denominator, so it
    # overstates total births — use the observed column when available.
    # Either way a birth shows up as the newborn dependent in the
    # cross-section, capturing first AND repeat births across ALL filing
    # statuses (married couples included).
    if birth_count_col is not None and birth_count_col in df.columns:
        birth_events = (
            df[birth_count_col].fillna(0).clip(lower=0).astype(float).to_numpy()
        )
    else:
        birth_events = n_dep.astype(float) * p.child_under_age_share
    has_birth = birth_events > 0

    # Postnatal: eligible via Medicaid (adult/child pathways) OR 300% FPL.
    postnatal_eligible = (medicaid_eligible | income_eligible) & has_birth
    postnatal_amount = np.where(
        postnatal_eligible,
        birth_events * p.postnatal_monthly_per_child * p.postnatal_months
        * post_takeup,
        0.0,
    )

    # Prenatal: same births, paid during pregnancy. Eligible via Medicaid
    # (incl. the 196% pregnancy pathway) OR 300% FPL.
    prenatal_eligible = (
        (medicaid_eligible | preg_medicaid_eligible | income_eligible) & has_birth
    )
    prenatal_amount = np.where(
        prenatal_eligible,
        birth_events * p.prenatal_monthly * p.prenatal_months * pre_takeup,
        0.0,
    )

    # Emit prenatal / postnatal subtotals alongside the combined amount so
    # the fiscal-cost report can split program outlay by arm.
    stem = out_col[:-len("_amount")] if out_col.endswith("_amount") else out_col
    df[f"{stem}_prenatal_amount"] = prenatal_amount
    df[f"{stem}_postnatal_amount"] = postnatal_amount
    df[out_col] = prenatal_amount + postnatal_amount
    return df


__all__ = [
    "RxKidsHIParams",
    "compute_rxkids_for_units",
    "hawaii_rxkids_parameters",
    "with_rxkids_overrides",
]
