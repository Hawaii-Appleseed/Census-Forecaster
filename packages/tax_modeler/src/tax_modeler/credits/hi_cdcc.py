"""Hawaii credit for household and dependent care expenses (HRS §235-55.6).

The credit is a percentage of employment-related expenses, which are capped by
the number of qualifying individuals and by the lower earner's earned income. It
is refundable (excess over tax is paid out; "no refunds or payment ... for
amounts less than $1") and has no phase-out: the percentage falls with adjusted
gross income and stops at 15 per cent.

Statutory parameters (§235-55.6, as of the Department's current text):

* **Applicable percentage** (subsection (a)(2)), unchanged since at least 2016
  (L 2016, c 235): 25% up to $25,000 of AGI, then one point lower for each
  further $5,000 (24% to $30,000 ... 20% to $50,000), and 15% above $50,000.
* **Dollar limit** (subsection (c)), the only parameter Act 163 touched:

  - Act 163, SLH 2023, §1: $10,000 for one qualifying individual and $20,000 for
    two or more, for taxable years beginning after December 31, 2022. Subsection
    (c) is "[Repeal and reenactment on December 31, 2027. L 2023, c 163, §5.]",
    so the limit reverts for taxable years beginning after that date.
  - Before Act 163 (and from TY2028): $2,400 and $4,800. These are the
    bracketed text the 2023 and 2026 bills strike (HB 1049 SD2 (2023); HB 2306
    (2026)).

The reverted limits are what the repeal-and-reenactment clause restores; the
reenacted text itself is not printed in the current statute. The 2026 session's
HB 2306 would have rewritten the percentage schedule (50% falling by one point per
$3,000 above $150,000, floor 15%); it was not enacted, and Act 24 (SB 3125 CD2)
does not touch §235-55.6.

Not modeled here (the calculator takes expenses as an input; what is left out of
the population side is listed in ``chain/forecast_working_family_credits.py``):

* the reduction of the limit by amounts excluded under IRC §129 (employer
  dependent care assistance), which is ``dcap_exclusion`` and defaults to zero;
* the statute's other conditions: qualifying individuals who are disabled
  dependents or a disabled spouse, student-spouse deemed income (subsection
  (d)(2)), payments to related individuals, and the service provider's
  identifying information (subsection (e)).

AGI is federal adjusted gross income, as on the N-11.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np
import pandas as pd

Caps = tuple[float, float]                   # (one qualifying individual, two or more)

#: §235-55.6(c) as amended by Act 163 (L 2023, c 163, §1): taxable years 2023-2027.
ACT163_CAPS: Caps = (10_000.0, 20_000.0)
#: §235-55.6(c) before Act 163 and, once it is repealed, from TY2028.
PRIOR_CAPS: Caps = (2_400.0, 4_800.0)

#: §235-55.6(a)(2): (AGI ceiling, applicable percentage); "not over" each ceiling.
PERCENT_SCHEDULE: tuple[tuple[float, float], ...] = (
    (25_000.0, 0.25), (30_000.0, 0.24), (35_000.0, 0.23),
    (40_000.0, 0.22), (45_000.0, 0.21), (50_000.0, 0.20),
)
PERCENT_ABOVE = 0.15

#: Pooled Hawaii CPS ASEC donor families (scripts/pool_cps_care_donors.py) and the probability that a family with a
#: child under 13 pays for care, by AGI class (scripts/build_care_pay_rates.py).
_DATA = Path(__file__).resolve().parents[1] / "data" / "cps_donor"
DONOR_FAMILIES_PATH = _DATA / "cps_asec_hawaii_care_families.parquet"
PAY_RATES_PATH = _DATA / "care_pay_rates.json"

ACT163_FIRST_YEAR = 2023
ACT163_LAST_YEAR = 2027            # (c) repealed and reenacted on December 31, 2027


def caps_for_year(tax_year: int | None) -> Caps:
    """The dollar limits in force: Act 163's for TY2023-2027, the earlier ones otherwise.

    With ``tax_year`` omitted, Act 163's (the law when this was written, 2026).
    """
    if tax_year is None or ACT163_FIRST_YEAR <= tax_year <= ACT163_LAST_YEAR:
        return ACT163_CAPS
    return PRIOR_CAPS


def applicable_percentage(agi) -> np.ndarray:
    """§235-55.6(a)(2): 25% to $25,000, one point lower per further $5,000, 15% above $50,000."""
    a = np.asarray(agi, dtype=float)
    ceilings = np.array([c for c, _ in PERCENT_SCHEDULE])
    pcts = np.array([p for _, p in PERCENT_SCHEDULE] + [PERCENT_ABOVE])
    return pcts[np.searchsorted(ceilings, a, side="left")]


def expense_limit(n_qualifying, caps: Caps, *, dcap_exclusion=0.0) -> np.ndarray:
    """§235-55.6(c): the cap on expenses taken into account, less any §129 exclusion."""
    n = np.asarray(n_qualifying)
    cap = np.where(n >= 2, caps[1], caps[0])
    return np.where(n >= 1, np.maximum(cap - np.asarray(dcap_exclusion, dtype=float), 0.0), 0.0)


def cdcc_credit(agi, n_qualifying, expenses, earned_limit, caps: Caps, *, dcap_exclusion=0.0) -> np.ndarray:
    """The credit per tax unit for given expenses.

    ``expenses``: employment-related expenses paid. ``earned_limit``: subsection (d)(1)'s
    earned income limit (the filer's earned income if unmarried; the lesser of the two
    spouses' if married). Credit = applicable percentage × min(expenses, dollar limit,
    earned income limit); refundable, so no tax liability enters; under $1 is not paid.
    """
    base = np.minimum(np.minimum(np.asarray(expenses, dtype=float),
                                 expense_limit(n_qualifying, caps, dcap_exclusion=dcap_exclusion)),
                      np.maximum(np.asarray(earned_limit, dtype=float), 0.0))
    credit = applicable_percentage(agi) * np.maximum(base, 0.0)
    return np.where(credit < 1.0, 0.0, credit)


def count_children_under_13(dependents_details) -> int:
    """Dependents under 13 in a unit's ``dependents_details`` (the qualifying individuals
    of §235-55.6(b)(1)(A) this model can see)."""
    n = 0
    for d in dependents_details if isinstance(dependents_details, (list, tuple)) else ():
        try:
            if float(d.get("age", 99)) < 13:
                n += 1
        except (TypeError, ValueError, AttributeError):
            continue
    return n


def earned_income_limit(units: pd.DataFrame) -> np.ndarray:
    """Subsection (d)(1) on the tax-unit frame.

    Unmarried: the filer's earned income. Married filing jointly: the lesser spouse's
    earned income. The frame carries each spouse's wages and self-employment income
    as built from PUMS; those are in base-year dollars, so the lesser spouse's *share*
    of the pair's base earnings is applied to the unit's ``earned_income`` (which is
    aged to the tax year), keeping the aged total.
    """
    from tax_modeler.units.earnings import lower_earner_income

    return lower_earner_income(units)


@functools.cache
def pay_rates() -> dict:
    """``care_pay_rates.json``: ``edges`` (AGI, ascending) and ``rates`` (one more than edges): the probability that a
    family with a child under 13 pays for care, by AGI class (see :func:`pay_probability`)."""
    return json.loads(PAY_RATES_PATH.read_text(encoding="utf-8"))


def pay_probability(agi) -> np.ndarray:
    """Probability that a family with a child under 13 pays for work-related care, by AGI.

    Below $50,000 the pooled CPS ASEC Hawaii donors' rate for families with household income under $50,000. From
    $50,000 up, the rate that makes the model's federal credit claims match IRS SOI Table 2's child and dependent
    care credit returns for Hawaii in each class (scaled to the resident universe as the family margins are,
    ``scripts/build_care_pay_rates.py``); the same rate is used for the Hawaii credit, which has the same payers. Applied
    to the Hawaii AGI in the state credit and to federal AGI in the federal check (the two differ by under 2% on average).
    """
    r = pay_rates()
    a = np.nan_to_num(np.asarray(agi, dtype=float), nan=0.0)
    return np.asarray(r["rates"], dtype=float)[np.searchsorted(np.asarray(r["edges"], dtype=float), a, side="right")]


def donor_expense_distribution(donor_path=None) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Pooled CPS ASEC Hawaii donors' work-related childcare expense, by number of children under 13, conditional on paying.

    Returns ``{1: (x, p), 2: (x, p)}`` (2 meaning two or more): the dollar amounts ``x`` (calendar-2023 dollars) of
    the donor families with a child under 13 that paid anything, and ``p`` their weights as shares of the paying
    families in the bin, so ``p.sum()`` is one. Whether a family pays is a separate question
    (:func:`pay_probability`, by AGI); this is how much it spends if it does. Donors are the households with a
    child under 13 in ASEC 2022-2025 (calendar 2021-2024; ``scripts/pool_cps_care_donors.py``), each year weighted
    equally. The Hawaii sample is small, so the bins are one child and two or more, the cap's own break, and
    income is not a bin: the earned-income limit and the credit's percentage carry the income gradient.
    """
    fam = pd.read_parquet(donor_path or DONOR_FAMILIES_PATH)
    out = {}
    for b, sel in ((1, fam["n13"] == 1), (2, fam["n13"] >= 2)):
        d = fam[sel & (fam["expense"] > 0)]
        out[b] = (d["expense"].to_numpy(dtype=float), d["weight"].to_numpy(dtype=float) / d["weight"].sum())
    return out


@functools.cache
def default_donors() -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """:func:`donor_expense_distribution` from the pooled file in the package, read once."""
    return donor_expense_distribution()


def conditional_capped_expense(n_children, earned_limit, caps: Caps, donors=None) -> np.ndarray:
    """Expected employment-related expenses the credit counts for a family that pays, per unit: the donor families'
    mean of ``min(spending, dollar limit, earned income limit)`` over those that paid.

    ``n_children``: children under 13 (qualifying individuals); 1 uses the one-child donors, 2 or more
    the two-or-more donors, 0 gives 0. ``earned_limit``: subsection (d)(1)'s limit (the lower earner's
    earnings; ``np.inf`` for none). The donors' amounts are summed in file order, which the simulator kernel repeats.
    """
    dist = donors if donors is not None else default_donors()
    n = np.asarray(n_children)
    earn = np.broadcast_to(np.maximum(np.asarray(earned_limit, dtype=float), 0.0), n.shape)
    cap = np.where(n >= 2, caps[1], caps[0]).astype(float)
    out = np.zeros(n.shape, dtype=float)
    for b, (x, p) in dist.items():
        idx = (n >= 2) if b == 2 else (n == 1)
        if not idx.any():
            continue
        for xj, pj in zip(x, p, strict=True):
            out[idx] += pj * np.minimum(np.minimum(xj, cap[idx]), earn[idx])
    return out


def expected_capped_expense(n_children, earned_limit, caps: Caps, donors=None, agi=None) -> np.ndarray:
    """Expected expenses the credit counts, per unit: :func:`pay_probability` at ``agi`` times
    :func:`conditional_capped_expense`. The credit is then ``applicable_percentage(agi)`` times this: the scoring path's
    care credit for current law and for both bills, which differ in ``caps`` and percentage schedule only. The
    statute's $1 floor applies to a single claim and is ignored in the expectation (a sub-dollar effect).
    ``agi=None`` (no income known) uses the rate for families under $50,000.
    """
    n = np.asarray(n_children)
    pay = pay_probability(np.zeros(n.shape) if agi is None else np.broadcast_to(np.asarray(agi, dtype=float), n.shape))
    return pay * conditional_capped_expense(n_children, earned_limit, caps, donors)


def compute_hi_cdcc_expected(units: pd.DataFrame, *, expanded: Caps = ACT163_CAPS,
                             reverted: Caps | None = PRIOR_CAPS, donors=None) -> pd.DataFrame:
    """Expected §235-55.6 credit per tax unit under two laws, before take-up.

    A tax unit's expenses are not known, so its credit is the mean of :func:`cdcc_credit`
    over the donor families' expense distribution (:func:`donor_expense_distribution`)
    for its number of children under 13: the dollar limit then binds on the families
    that pay a lot, which a cell mean cannot show. A cell mean (what the SPM childcare
    subtraction uses) puts nearly everyone under the old $2,400 limit, so the expansion
    would be worth nothing.

    Eligibility: a child under 13 among ``dependents_details``; earned income of the
    filer (both spouses, if married filing jointly: the lesser spouse's, subsection
    (d)(1)) above zero. Married filing separately is not eligible (subsection (e)(2))
    and gets zero.

    Columns: ``cdcc_expanded`` and ``cdcc_reverted`` (expected dollars), ``..._claim_prob``
    (the probability the credit is positive), and ``cdcc_loss_prob`` (the probability the
    expanded credit is larger than the reverted one).
    """
    dist = donors if donors is not None else default_donors()
    n13 = units["dependents_details"].apply(count_children_under_13).to_numpy()
    agi = (units["federal_agi"] if "federal_agi" in units.columns else units["income"]).fillna(0).to_numpy(dtype=float)
    earn = earned_income_limit(units)
    status = units["filing_status"].astype(str).to_numpy()
    eligible = (n13 > 0) & (earn > 0) & (status != "married_filing_separately")
    pay = pay_probability(agi)
    laws = {"cdcc_expanded": expanded, "cdcc_reverted": reverted if reverted is not None else expanded}
    n = len(units)
    out = {k: np.zeros(n) for k in laws}
    claim = {k: np.zeros(n) for k in laws}
    loss = np.zeros(n)
    for b, (x, p) in dist.items():
        idx = np.where(eligible & ((n13 >= 2) if b == 2 else (n13 == 1)))[0]
        if not len(idx):
            continue
        for xj, pj in zip(x, p, strict=True):
            cr = {k: cdcc_credit(agi[idx], n13[idx], np.full(len(idx), xj), earn[idx], caps)
                  for k, caps in laws.items()}
            for k in laws:
                out[k][idx] += pj * pay[idx] * cr[k]
                claim[k][idx] += pj * pay[idx] * (cr[k] > 0)
            loss[idx] += pj * pay[idx] * (cr["cdcc_expanded"] > cr["cdcc_reverted"])
    res = pd.DataFrame({**out, **{f"{k}_claim_prob": v for k, v in claim.items()}, "cdcc_loss_prob": loss},
                       index=units.index)
    return res


def compute_hi_cdcc_for_units(units: pd.DataFrame, *, tax_year: int | None = None,
                              caps: Caps | None = None, donors=None,
                              out_col: str = "hi_cdcc_amount") -> pd.DataFrame:
    """``units`` with the expected §235-55.6 credit (before take-up) under the law of ``tax_year``."""
    c = caps if caps is not None else caps_for_year(tax_year)
    r = compute_hi_cdcc_expected(units, expanded=c, reverted=None, donors=donors)
    out = units.copy()
    out[out_col] = r["cdcc_expanded"].to_numpy()
    return out


__all__ = [
    "ACT163_CAPS", "PRIOR_CAPS", "PERCENT_SCHEDULE", "caps_for_year", "applicable_percentage",
    "expense_limit", "cdcc_credit", "count_children_under_13", "earned_income_limit",
    "donor_expense_distribution", "default_donors", "pay_probability", "pay_rates", "conditional_capped_expense",
    "expected_capped_expense",
    "compute_hi_cdcc_expected", "compute_hi_cdcc_for_units",
]
