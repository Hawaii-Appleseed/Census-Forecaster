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

# Money income aged to the target year, written by
# ``project_tax_units_forward``. ``total_cash_income`` stays in base-year
# dollars on projected frames (quintile binning ranks on it), so the SPM
# resource sum reads this column whenever a frame carries it.
AGED_MONEY_INCOME_COL = "spm_money_income"


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


@dataclass(frozen=True)
class SPMColumns:
    """The unit-frame columns :func:`compute_spm_resources` reads. A column
    that is ``None`` or absent from the frame counts as zero. Override one
    with ``SPMColumns(eitc="my_eitc")`` or ``dataclasses.replace``.

    ``money_income`` None picks ``spm_money_income`` when the frame has it
    (a projected frame), else ``total_cash_income``; ``federal_tax`` and
    ``payroll_tax`` absent fall back to the estimates described there."""

    money_income: str | None = None
    state_tax: str = "hi_tax_liability"
    eitc: str = "eitc_amount"
    refundable_ctc: str = "ctc_refundable"
    federal_tax: str | None = "federal_tax_liability"
    payroll_tax: str | None = None
    snap: str | None = "snap_amount"
    ssi: str | None = "ssi_amount"
    ssi_hi: str | None = "ssi_hi_amount"
    aca_ptc: str | None = "aca_ptc_amount"
    wic: str | None = "wic_amount"
    housing_subsidy: str | None = "housing_subsidy_amount"
    liheap: str | None = "liheap_amount"
    childcare_subsidy: str | None = "childcare_amount"
    school_lunch: str | None = "school_lunch_amount"
    hi_eitc: str | None = "hi_eitc_amount"
    hi_food_excise: str | None = "hi_food_excise_amount"
    hi_renters: str | None = "hi_renters_amount"
    moop: str | None = "moop_amount"
    childcare_expense: str | None = "childcare_expense_amount"
    work_expense: str | None = "work_expense_amount"
    rxkids: str | None = "rxkids_amount"
    filing_status: str = "filing_status"
    earned_income: str = "earned_income"


def compute_spm_resources(
    units: pd.DataFrame,
    *,
    columns: SPMColumns = SPMColumns(),
    federal_tax_fallback: bool = True,
    tax_year: int | None = None,
    extrapolate: bool = False,
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

    ``money_income_col`` defaults to ``spm_money_income`` when the frame
    carries it (a projected frame: money income aged to the target year by
    :func:`~tax_modeler.projection.tax_unit_projector.project_tax_units_forward`)
    and to ``total_cash_income`` otherwise. On a projected frame
    ``total_cash_income`` is still in base-year dollars while the credits and
    taxes are target-year amounts on aged incomes, so mixing the two
    understated resources.

    ``extrapolate`` lets the federal-tax fallback score a ``tax_year`` past
    the last published Rev. Proc. (CPI-extrapolated parameters) instead of
    dropping to the flat-rate estimate. Off by default, like the threshold
    extrapolation in :func:`~tax_modeler.poverty.impact.compute_poverty_impact`,
    which passes the same flag here.

    Returns
    -------
    (units_out, meta):
        ``units_out`` is a copy with the new ``spm_resources`` column
        and a ``spm_components`` column listing the per-unit dollar
        amounts that fed into the calculation (for audit). ``meta`` is
        a :class:`SPMResourceMeta` with the provenance.
    """
    money_income_col = columns.money_income
    if money_income_col is None:
        money_income_col = (
            AGED_MONEY_INCOME_COL if AGED_MONEY_INCOME_COL in units.columns
            else "total_cash_income"
        )
    if money_income_col not in units.columns:
        raise DataValidationError(
            f"compute_spm_resources requires {money_income_col!r}; "
            "run enrich_for_credits() first."
        )

    df = units.copy()
    n = len(df)

    def _col(name: str | None) -> np.ndarray:
        if name is None or name not in df.columns:
            return np.zeros(n)
        return df[name].fillna(0.0).to_numpy(dtype=float)

    used: list[str] = []
    zeroed: list[str] = []

    money_income = _col(money_income_col); used.append(money_income_col)
    eitc = _col(columns.eitc)
    if columns.eitc in df.columns:
        used.append(columns.eitc)
    else:
        zeroed.append(columns.eitc)
    rctc = _col(columns.refundable_ctc)
    if columns.refundable_ctc in df.columns:
        used.append(columns.refundable_ctc)
    else:
        zeroed.append(columns.refundable_ctc)
    state_tax = _col(columns.state_tax)
    if columns.state_tax in df.columns:
        used.append(columns.state_tax)
    else:
        zeroed.append(columns.state_tax)

    # F3 de-duplication: hi_tax_liability is NET of a simplified low-income
    # food/excise credit (column ``hi_low_income_credit``, HRS §235-55.85). If
    # the caller ALSO supplies the dedicated graduated model in
    # ``columns.hi_food_excise``, the same statutory credit would be counted twice —
    # once implicitly via the reduced state_tax, once explicitly via
    # +hi_food_excise. Gross state_tax back up by the embedded credit so the
    # credit is counted exactly once (through the dedicated hi_food_excise line,
    # the better model). Callers that don't model hi_food_excise (e.g. the
    # poverty_impact_report path) are unaffected — the credit stays counted once
    # via the netted liability.
    if columns.hi_food_excise and columns.hi_food_excise in df.columns:
        if "hi_low_income_credit" in df.columns:
            embedded_credit = _col("hi_low_income_credit")
            state_tax = state_tax + embedded_credit
            used.append("hi_low_income_credit(grossed-up:F3)")
        else:
            import logging
            logging.getLogger(__name__).warning(
                "compute_spm_resources: columns.hi_food_excise=%r present but no "
                "'hi_low_income_credit' column to un-net from %r — the §235-55.85 "
                "food/excise credit may be double-counted in SPM resources.",
                columns.hi_food_excise, columns.state_tax,
            )

    # Federal tax: prefer column → real calculator → flat-rate fallback → zero
    if columns.federal_tax and columns.federal_tax in df.columns:
        federal_tax = _col(columns.federal_tax)
        used.append(columns.federal_tax)
        federal_source = "column"
    elif (
        federal_tax_fallback
        and tax_year is not None
        and columns.filing_status in df.columns
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
                filing_status_col=columns.filing_status,
                extrapolate=extrapolate,
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
    if columns.payroll_tax and columns.payroll_tax in df.columns:
        payroll_tax = _col(columns.payroll_tax)
        used.append(columns.payroll_tax)
        payroll_source = "column"
    elif columns.earned_income in df.columns:
        payroll_tax = _col(columns.earned_income) * _EMPLOYEE_FICA_RATE
        used.append(columns.earned_income)
        payroll_source = "computed"
    else:
        payroll_tax = np.zeros(n)
        payroll_source = "zero"
        zeroed.append("payroll_tax")

    snap = _col(columns.snap)
    if columns.snap and columns.snap in df.columns:
        used.append(columns.snap)
    else:
        zeroed.append("snap")
    ssi = _col(columns.ssi)
    if columns.ssi and columns.ssi in df.columns:
        used.append(columns.ssi)
    else:
        zeroed.append("ssi")
    ssi_hi = _col(columns.ssi_hi)
    if columns.ssi_hi and columns.ssi_hi in df.columns:
        used.append(columns.ssi_hi)
    else:
        zeroed.append("ssi_hi_supplement")
    aca_ptc = _col(columns.aca_ptc)
    if columns.aca_ptc and columns.aca_ptc in df.columns:
        used.append(columns.aca_ptc)
    else:
        zeroed.append("aca_ptc")
    wic = _col(columns.wic)
    if columns.wic and columns.wic in df.columns:
        used.append(columns.wic)
    else:
        zeroed.append("wic")
    housing = _col(columns.housing_subsidy)
    if columns.housing_subsidy and columns.housing_subsidy in df.columns:
        used.append(columns.housing_subsidy)
    else:
        zeroed.append("housing_subsidy")
    liheap = _col(columns.liheap)
    if columns.liheap and columns.liheap in df.columns:
        used.append(columns.liheap)
    else:
        zeroed.append("liheap")
    childcare_subsidy = _col(columns.childcare_subsidy)
    if columns.childcare_subsidy and columns.childcare_subsidy in df.columns:
        used.append(columns.childcare_subsidy)
    else:
        zeroed.append("childcare_subsidy")
    school_lunch = _col(columns.school_lunch)
    if columns.school_lunch and columns.school_lunch in df.columns:
        used.append(columns.school_lunch)
    else:
        zeroed.append("school_lunch")
    hi_eitc = _col(columns.hi_eitc)
    if columns.hi_eitc and columns.hi_eitc in df.columns:
        used.append(columns.hi_eitc)
    else:
        zeroed.append("hi_eitc")
    hi_food_excise = _col(columns.hi_food_excise)
    if columns.hi_food_excise and columns.hi_food_excise in df.columns:
        used.append(columns.hi_food_excise)
    else:
        zeroed.append("hi_food_excise")
    hi_renters = _col(columns.hi_renters)
    if columns.hi_renters and columns.hi_renters in df.columns:
        used.append(columns.hi_renters)
    else:
        zeroed.append("hi_renters")

    moop = _col(columns.moop)
    childcare = _col(columns.childcare_expense)
    work_exp = _col(columns.work_expense)
    for label, name in [
        ("moop", columns.moop),
        ("childcare_expense", columns.childcare_expense),
        ("work_expense", columns.work_expense),
    ]:
        if name and name in df.columns:
            used.append(name)
        else:
            zeroed.append(label)

    # Hypothetical program: RxKids Hawaiʻi cash prescription. Charitable
    # disbursement (non-taxable, doesn't interact with AGI / EITC / CTC
    # phase-outs) — counted as SPM resources only.
    rxkids = _col(columns.rxkids)
    if columns.rxkids and columns.rxkids in df.columns:
        used.append(columns.rxkids)
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
    if money_income_col == AGED_MONEY_INCOME_COL:
        notes.append(
            f"money income from {AGED_MONEY_INCOME_COL!r}: total_cash_income "
            "aged to the projection year with the tax inputs."
        )
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
