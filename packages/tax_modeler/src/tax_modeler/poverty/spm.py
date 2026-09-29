"""SPM resource calculation.

Computes Supplemental Poverty Measure resources at tax-unit granularity
using the components ``tax_modeler`` already produces. Components not
yet modeled (SNAP, housing, MOOP, childcare expenses) default to zero
and are flagged in the returned ``meta`` so downstream consumers can
warn users their poverty rates are biased upward (resources understated).

The function is a pure transformation of an input DataFrame — it adds
columns and returns a new frame. No side effects on the input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError


# Employee-side FICA + Medicare rate (employer half is *not* counted in
# SPM resources because it's already excluded from money income).
_EMPLOYEE_FICA_RATE = 0.0765
# Last-resort federal-tax effective rate when no explicit federal column
# is supplied AND the real ``compute_federal_income_tax_for_units``
# calculator can't be invoked (no ``tax_year`` passed, or missing
# ``filing_status`` column). The real calculator (TY 2022–2025 brackets
# + standard deductions, IRS Rev. Procs.) is the preferred path; this
# 10% flat fallback overstates federal tax for SPM-eligible filers by
# $0–$2,000+ per return. Documented in :class:`SPMResourceMeta` so
# callers know which path fired.
_FEDERAL_FALLBACK_EFFECTIVE_RATE = 0.10


@dataclass(frozen=True)
class SPMResourceMeta:
    """Audit trail for an SPM-resources computation.

    Records which components were sourced from real columns vs. defaulted
    to zero, so downstream poverty numbers can be qualified honestly.
    """

    used_columns: tuple[str, ...]
    zeroed_components: tuple[str, ...]
    federal_tax_source: str  # "column" | "fallback_rate" | "zero"
    payroll_tax_source: str  # "column" | "computed" | "zero"
    notes: tuple[str, ...] = field(default_factory=tuple)


def compute_spm_resources(
    units: pd.DataFrame,
    *,
    money_income_col: str = "total_cash_income",
    state_tax_col: str = "hi_tax_liability",
    eitc_col: str = "eitc_amount",
    refundable_ctc_col: str = "ctc_refundable",
    federal_tax_col: Optional[str] = "federal_tax_liability",
    payroll_tax_col: Optional[str] = None,
    snap_col: Optional[str] = "snap_amount",
    ssi_col: Optional[str] = "ssi_amount",
    ssi_hi_col: Optional[str] = "ssi_hi_amount",
    aca_ptc_col: Optional[str] = "aca_ptc_amount",
    wic_col: Optional[str] = "wic_amount",
    housing_subsidy_col: Optional[str] = "housing_subsidy_amount",
    liheap_col: Optional[str] = "liheap_amount",
    childcare_subsidy_col: Optional[str] = "childcare_amount",
    school_lunch_col: Optional[str] = "school_lunch_amount",
    hi_eitc_col: Optional[str] = "hi_eitc_amount",
    hi_food_excise_col: Optional[str] = "hi_food_excise_amount",
    hi_renters_col: Optional[str] = "hi_renters_amount",
    moop_col: Optional[str] = "moop_amount",
    childcare_expense_col: Optional[str] = "childcare_expense_amount",
    work_expense_col: Optional[str] = "work_expense_amount",
    rxkids_col: Optional[str] = "rxkids_amount",
    # behavior knobs
    federal_tax_fallback: bool = True,
    tax_year: Optional[int] = None,
    filing_status_col: str = "filing_status",
    earned_income_col: str = "earned_income",
    out_col: str = "spm_resources",
) -> tuple[pd.DataFrame, SPMResourceMeta]:
    """Add an ``spm_resources`` column to ``units``.

    Resource formula (all weighted-summed at unit level):

        money_income
        + EITC + refundable_CTC
        + SNAP + housing_subsidy + LIHEAP
        - state_tax
        - federal_tax (or fallback estimate)
        - payroll_tax (employee FICA on earned_income)
        - MOOP - childcare_expense - work_expense

    Missing columns default to zero. Federal tax has an opt-in fallback
    estimate if the column isn't supplied (since ``tax_modeler`` is
    Hawaii-focused and doesn't compute federal liability natively).

    Returns
    -------
    (units_out, meta):
        ``units_out`` is a copy with the new ``spm_resources`` column
        and a ``spm_components`` column listing the per-unit dollar
        amounts that fed into the calculation (for audit). ``meta`` is
        a :class:`SPMResourceMeta` with the provenance.
    """
    if money_income_col not in units.columns:
        raise DataValidationError(
            f"compute_spm_resources requires {money_income_col!r}; "
            "run enrich_for_credits() first."
        )

    df = units.copy()
    n = len(df)

    def _col(name: Optional[str]) -> np.ndarray:
        if name is None or name not in df.columns:
            return np.zeros(n)
        return df[name].fillna(0.0).to_numpy(dtype=float)

    used: list[str] = []
    zeroed: list[str] = []

    money_income = _col(money_income_col); used.append(money_income_col)
    eitc = _col(eitc_col)
    if eitc_col in df.columns:
        used.append(eitc_col)
    else:
        zeroed.append(eitc_col)
    rctc = _col(refundable_ctc_col)
    if refundable_ctc_col in df.columns:
        used.append(refundable_ctc_col)
    else:
        zeroed.append(refundable_ctc_col)
    state_tax = _col(state_tax_col)
    if state_tax_col in df.columns:
        used.append(state_tax_col)
    else:
        zeroed.append(state_tax_col)

    # F3 de-duplication: hi_tax_liability is NET of a simplified low-income
    # food/excise credit (column ``hi_low_income_credit``, HRS §235-55.85). If
    # the caller ALSO supplies the dedicated graduated model in
    # ``hi_food_excise_col``, the same statutory credit would be counted twice —
    # once implicitly via the reduced state_tax, once explicitly via
    # +hi_food_excise. Gross state_tax back up by the embedded credit so the
    # credit is counted exactly once (through the dedicated hi_food_excise line,
    # the better model). Callers that don't model hi_food_excise (e.g. the
    # poverty_impact_report path) are unaffected — the credit stays counted once
    # via the netted liability.
    if hi_food_excise_col and hi_food_excise_col in df.columns:
        if "hi_low_income_credit" in df.columns:
            embedded_credit = _col("hi_low_income_credit")
            state_tax = state_tax + embedded_credit
            used.append("hi_low_income_credit(grossed-up:F3)")
        else:
            import logging
            logging.getLogger(__name__).warning(
                "compute_spm_resources: hi_food_excise_col=%r present but no "
                "'hi_low_income_credit' column to un-net from %r — the §235-55.85 "
                "food/excise credit may be double-counted in SPM resources.",
                hi_food_excise_col, state_tax_col,
            )

    # Federal tax: prefer column → real calculator → flat-rate fallback → zero
    if federal_tax_col and federal_tax_col in df.columns:
        federal_tax = _col(federal_tax_col)
        used.append(federal_tax_col)
        federal_source = "column"
    elif (
        federal_tax_fallback
        and tax_year is not None
        and filing_status_col in df.columns
    ):
        # Real federal-tax calculator: TY 2022–2025 brackets + standard
        # deductions per IRS Rev. Procs. Removes the ~4–6pp upward bias
        # the 10% flat fallback introduced on Hawaii SPM rates.
        from tax_modeler.liability.federal import (
            compute_federal_income_tax_for_units,
        )
        try:
            df_fed = compute_federal_income_tax_for_units(
                df,
                tax_year=tax_year,
                # Federal AGI when the frame has it: taxing SSI, public
                # assistance and full Social Security overstated tax.
                income_col="federal_agi" if "federal_agi" in df.columns else money_income_col,
                filing_status_col=filing_status_col,
            )
            federal_tax = df_fed["federal_tax_liability"].fillna(0).to_numpy(
                dtype=float,
            )
            federal_source = "computed"
            used.append(f"computed_federal_tax_ty{tax_year}")
        except (KeyError, ValueError) as e:
            # Calculator can't run on this frame (unsupported tax_year,
            # missing supporting column). Fall back to flat 10%.
            import logging
            logging.getLogger(__name__).warning(
                "compute_spm_resources: real federal-tax calculator failed "
                "(%s); using flat-%.0f%% fallback. SPM rates will be biased "
                "upward by ~4-6pp.",
                e, _FEDERAL_FALLBACK_EFFECTIVE_RATE * 100,
            )
            federal_tax = np.maximum(money_income, 0.0) * _FEDERAL_FALLBACK_EFFECTIVE_RATE
            federal_source = "fallback_rate"
    elif federal_tax_fallback:
        # Crude proxy on positive money income only — last-resort path
        # when tax_year wasn't passed (legacy direct callers). Logs a
        # warning so the caller knows to upgrade.
        import logging
        logging.getLogger(__name__).warning(
            "compute_spm_resources: no federal_tax column and no tax_year "
            "supplied; using flat-%.0f%% fallback. Pass tax_year= to invoke "
            "the real calculator instead. SPM rates will be biased upward "
            "by ~4-6pp.",
            _FEDERAL_FALLBACK_EFFECTIVE_RATE * 100,
        )
        federal_tax = np.maximum(money_income, 0.0) * _FEDERAL_FALLBACK_EFFECTIVE_RATE
        federal_source = "fallback_rate"
    else:
        federal_tax = np.zeros(n)
        federal_source = "zero"
        zeroed.append("federal_tax")

    # Payroll tax: prefer column, else compute on earned_income
    if payroll_tax_col and payroll_tax_col in df.columns:
        payroll_tax = _col(payroll_tax_col)
        used.append(payroll_tax_col)
        payroll_source = "column"
    elif earned_income_col in df.columns:
        payroll_tax = _col(earned_income_col) * _EMPLOYEE_FICA_RATE
        used.append(earned_income_col)
        payroll_source = "computed"
    else:
        payroll_tax = np.zeros(n)
        payroll_source = "zero"
        zeroed.append("payroll_tax")

    snap = _col(snap_col)
    if snap_col and snap_col in df.columns:
        used.append(snap_col)
    else:
        zeroed.append("snap")
    ssi = _col(ssi_col)
    if ssi_col and ssi_col in df.columns:
        used.append(ssi_col)
    else:
        zeroed.append("ssi")
    ssi_hi = _col(ssi_hi_col)
    if ssi_hi_col and ssi_hi_col in df.columns:
        used.append(ssi_hi_col)
    else:
        zeroed.append("ssi_hi_supplement")
    aca_ptc = _col(aca_ptc_col)
    if aca_ptc_col and aca_ptc_col in df.columns:
        used.append(aca_ptc_col)
    else:
        zeroed.append("aca_ptc")
    wic = _col(wic_col)
    if wic_col and wic_col in df.columns:
        used.append(wic_col)
    else:
        zeroed.append("wic")
    housing = _col(housing_subsidy_col)
    if housing_subsidy_col and housing_subsidy_col in df.columns:
        used.append(housing_subsidy_col)
    else:
        zeroed.append("housing_subsidy")
    liheap = _col(liheap_col)
    if liheap_col and liheap_col in df.columns:
        used.append(liheap_col)
    else:
        zeroed.append("liheap")
    childcare_subsidy = _col(childcare_subsidy_col)
    if childcare_subsidy_col and childcare_subsidy_col in df.columns:
        used.append(childcare_subsidy_col)
    else:
        zeroed.append("childcare_subsidy")
    school_lunch = _col(school_lunch_col)
    if school_lunch_col and school_lunch_col in df.columns:
        used.append(school_lunch_col)
    else:
        zeroed.append("school_lunch")
    hi_eitc = _col(hi_eitc_col)
    if hi_eitc_col and hi_eitc_col in df.columns:
        used.append(hi_eitc_col)
    else:
        zeroed.append("hi_eitc")
    hi_food_excise = _col(hi_food_excise_col)
    if hi_food_excise_col and hi_food_excise_col in df.columns:
        used.append(hi_food_excise_col)
    else:
        zeroed.append("hi_food_excise")
    hi_renters = _col(hi_renters_col)
    if hi_renters_col and hi_renters_col in df.columns:
        used.append(hi_renters_col)
    else:
        zeroed.append("hi_renters")

    moop = _col(moop_col)
    childcare = _col(childcare_expense_col)
    work_exp = _col(work_expense_col)
    for label, name in [
        ("moop", moop_col),
        ("childcare_expense", childcare_expense_col),
        ("work_expense", work_expense_col),
    ]:
        if name and name in df.columns:
            used.append(name)
        else:
            zeroed.append(label)

    # Hypothetical program: RxKids Hawaiʻi cash prescription. Charitable
    # disbursement (non-taxable, doesn't interact with AGI / EITC / CTC
    # phase-outs) — counted as SPM resources only.
    rxkids = _col(rxkids_col)
    if rxkids_col and rxkids_col in df.columns:
        used.append(rxkids_col)
    else:
        zeroed.append("rxkids")

    # Per Census P60-280: Medicaid is excluded from SPM resources (it
    # offsets MOOP indirectly rather than counting as cash). We compute
    # it elsewhere and report it separately, but do NOT add it here.
    # Hawaii state-level refundable credits (hi_eitc, hi_food_excise,
    # hi_renters) ARE included — they are paid out as refunds and
    # function as cash transfers in the SPM accounting.
    resources = (
        money_income
        + eitc + rctc
        + snap + ssi + ssi_hi
        + aca_ptc + wic
        + housing + liheap + childcare_subsidy
        + school_lunch
        + hi_eitc + hi_food_excise + hi_renters
        + rxkids
        - state_tax
        - federal_tax
        - payroll_tax
        - moop - childcare - work_exp
    )
    df[out_col] = resources

    notes: list[str] = []
    if federal_source == "fallback_rate":
        notes.append(
            f"federal_tax estimated as {_FEDERAL_FALLBACK_EFFECTIVE_RATE:.0%} of "
            "positive money income (no federal_tax_liability column and "
            "tax_year not passed; the real calculator at "
            "tax_modeler.liability.federal.compute_federal_income_tax_for_units "
            "is preferred). SPM rates biased upward by ~4-6pp."
        )
    elif federal_source == "computed":
        notes.append(
            f"federal_tax computed via tax_modeler.liability.federal for "
            f"TY {tax_year} (IRS Rev. Procs. brackets + standard deduction)."
        )
    if "snap" in zeroed:
        notes.append("SNAP not yet modeled (Phase 3); SPM resources understated for low-income units")
    if "moop" in zeroed:
        notes.append("MOOP not imputed; SPM resources overstated for high-medical-cost units")

    meta = SPMResourceMeta(
        used_columns=tuple(sorted(set(used))),
        zeroed_components=tuple(sorted(set(zeroed))),
        federal_tax_source=federal_source,
        payroll_tax_source=payroll_source,
        notes=tuple(notes),
    )
    return df, meta
