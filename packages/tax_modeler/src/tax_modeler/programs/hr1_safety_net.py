"""Who the 2025 reconciliation law (H.R.1, Public Law 119-21) takes SNAP and Medicaid from in Hawaii.

Two populations, each read where it is recorded best:

* **SNAP**: Hawaii's rows of the FY2024 SNAP Quality Control file (``data/snap_qc/``, written by
  ``scripts/fetch_snap_qc_hawaii.py``). They are certified SNAP units with each member's age, disability, work
  registration, time-limit status, hours and countable income, and the unit's deductions and benefit, so the law's
  tests are applied to the facts a caseworker applies them to. :func:`classify_snap_adults` sorts adults into the
  groups section 10102 newly puts under the three-month time limit; :func:`snap_loss_if_removed` recomputes the
  unit's benefit without the member.
* **Medicaid**: ACS PUMS persons who report Medicaid. :func:`classify_medicaid_adults` picks out the expansion
  adults section 71119 covers and sorts them into exempt, already meeting the test, and neither. The survey
  undercounts Medicaid, so the analysis script scales the group to Med-QUEST's enrollment count.

Neither source says who will fail to keep benefits they qualify for. That is the :class:`LossRates` assumption,
given as LOW / MID / HIGH; ``HR1_SNAP_MEDICAID_METHODOLOGY.md`` has the evidence behind each rate.

Nothing here is on the published chain: ``analyses/forecast_hr1_snap_medicaid.py`` is the only caller.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError, DataValidationError

_QC_DIR = Path(__file__).resolve().parents[1] / "data" / "snap_qc"

# --------------------------------------------------------------------------------------------------------------
# SNAP
# --------------------------------------------------------------------------------------------------------------

# Hawaii maximum monthly allotments, household of 1..8 (USDA FNS cost-of-living memos). FY2024 is the level in the
# QC file (its BENMAX column); FY2027 (October 2026 to September 2027) is the level benefits are restated at.
HI_MAX_ALLOTMENT: dict[int, tuple[int, ...]] = {
    2024: (527, 967, 1385, 1759, 2088, 2506, 2770, 3166),
    2027: (496, 910, 1307, 1655, 1969, 2367, 2615, 2979),
}
HI_MAX_ALLOTMENT_EXTRA_PERSON = {2024: 396, 2027: 364}
# Hawaii standard deduction by unit size, FY2024 (sizes 1-4, 5, 6+), as recorded in the QC file.
HI_STANDARD_DEDUCTION_2024 = {1: 279, 2: 279, 3: 279, 4: 279, 5: 280, 6: 321}
HI_MIN_BENEFIT_2024 = 42          # units of one or two people
# Monthly poverty guideline SNAP used for Hawaii in FY2024 (the 100% net income standard).
HI_POVERTY_LINE_2024 = (1398, 493)  # one person, each additional person

# Work registration codes (WRKREG) that also exempt a person from the time limit, 7 CFR 273.24(c)(5): unfit for
# work, caring for a child under 6 or an incapacitated person, other federal exemption (students among them),
# complying with TANF work rules, on unemployment compensation, in drug or alcohol treatment.
_WRKREG_EXEMPT = (2, 3, 5, 6, 7, 8)
_WRKREG_WORKING_30H = 4
# Hours bands (EMPSTB) at or above the 80 hours a month the time limit asks for: 20-29, 30-39, 40+ a week.
_EMPSTB_20H_PLUS = (3, 4, 5)
# Employment and training codes (EMPRG) that count toward the 80 hours: workfare, work experience, education and
# training, other components. Job search alone (2, 3) does not.
_EMPRG_QUALIFYING = (1, 4, 5, 6, 7, 8, 9)
# Time-limit status codes (ABWDST) for the exceptions the 2023 Fiscal Responsibility Act added and H.R.1 removed.
_ABWDST_REMOVED_EXCEPTIONS = {6: "veteran", 7: "homeless", 8: "former_foster_youth"}
# Citizenship code (CTZN) for refugees, asylees and people with deportation withheld, whom section 10108 removes.
_CTZN_REFUGEE_ASYLEE = 4

SNAP_GROUPS = ("age_55_64", "child_14_17", "removed_exception", "already_subject", "not_subject")
SNAP_STATUSES = ("exempt", "meets", "at_risk")


def load_hawaii_qc(directory: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The tracked Hawaii QC extract: ``(units, persons)``."""
    d = Path(directory) if directory else _QC_DIR
    paths = [d / "hawaii_qc_fy2024_units.csv", d / "hawaii_qc_fy2024_persons.csv"]
    for p in paths:
        if not p.exists():
            raise DataValidationError(f"{p} is missing; run scripts/fetch_snap_qc_hawaii.py")
    return pd.read_csv(paths[0]), pd.read_csv(paths[1])


def max_allotment(size: int, year: int = 2024) -> int:
    if year not in HI_MAX_ALLOTMENT:
        raise ConfigError(f"no Hawaii allotment table for FY{year}", available=sorted(HI_MAX_ALLOTMENT))
    if size <= 0:
        return 0
    table = HI_MAX_ALLOTMENT[year]
    if size <= len(table):
        return table[size - 1]
    return table[-1] + (size - len(table)) * HI_MAX_ALLOTMENT_EXTRA_PERSON[year]


def poverty_line_2024(size: int) -> float:
    return HI_POVERTY_LINE_2024[0] + HI_POVERTY_LINE_2024[1] * (max(size, 1) - 1)


def snap_benefit_2024(
    *, size: int, gross: float, earned: float, medical: float = 0.0, dependent_care: float = 0.0,
    child_support: float = 0.0, shelter_expense: float = 0.0, shelter_cap: float = 905.0,
    elderly_or_disabled: bool = False,
) -> float:
    """Monthly benefit under the FY2024 Hawaii rules, from a unit's countable income and expenses.

    It reproduces the benefit the QC file records for 570 of Hawaii's 571 units (the other is a minimum-benefit
    case over the net income limit). Eligibility tests are not applied: every unit it is called on is certified,
    and a unit that loses a member keeps its categorical eligibility.
    """
    if size <= 0:
        return 0.0
    std = HI_STANDARD_DEDUCTION_2024[min(size, 6)]
    before_shelter = max(0.0, gross - std - 0.20 * earned - medical - dependent_care - child_support)
    shelter = max(0.0, shelter_expense - 0.5 * before_shelter)
    if not elderly_or_disabled:
        shelter = min(shelter, shelter_cap)
    net = max(0.0, before_shelter - shelter)
    benefit = max(0.0, max_allotment(size, 2024) - np.ceil(0.30 * net))
    if size <= 2:
        benefit = max(benefit, HI_MIN_BENEFIT_2024)
    return float(benefit)


def classify_snap_adults(units: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    """Eligible members aged 18 to 64, each with the group H.R.1 puts them in and their status under it.

    ``group`` (first that applies):

    * ``age_55_64``: 55 to 64 with no child under 14 in the unit. The time limit stopped at 54.
    * ``child_14_17``: 18 to 54 and the youngest child in the unit is 14 to 17. Any child under 18 used to exempt
      every adult in the unit.
    * ``removed_exception``: 18 to 54, no child, recorded as excepted as a veteran, homeless or a former foster
      youth. Section 10102 repeals those exceptions.
    * ``already_subject``: 18 to 54, no child. Under the time limit before H.R.1 (Hawaii's waiver ended June 30,
      2024), so not counted as an effect of the law.
    * ``not_subject``: a child under 14 in the unit.

    ``status``: ``exempt`` (a disability, or a work-registration exemption that carries over to the time limit),
    ``meets`` (20 or more hours a week, or a qualifying work program), ``at_risk`` (neither).
    """
    for col in ("unit_id", "FSAFIL", "AGE", "DIS64", "WRKREG", "ABWDST", "EMPSTB", "EMPRG"):
        if col not in persons.columns:
            raise DataValidationError(f"persons frame needs column {col!r}")
    members = persons[persons["FSAFIL"] == 1].copy()
    kids = members[members["AGE"] < 18].groupby("unit_id")["AGE"].min().rename("youngest_child")
    adults = members[(members["AGE"] >= 18) & (members["AGE"] <= 64)].join(kids, on="unit_id")
    youngest = adults["youngest_child"].fillna(99)
    age = adults["AGE"]

    group = np.select(
        [
            youngest < 14,
            (age >= 55),
            (youngest < 18),
            adults["ABWDST"].isin(list(_ABWDST_REMOVED_EXCEPTIONS)),
        ],
        ["not_subject", "age_55_64", "child_14_17", "removed_exception"],
        default="already_subject",
    )
    exempt = (adults["DIS64"] == 1) | adults["WRKREG"].isin(_WRKREG_EXEMPT)
    meets = (
        adults["EMPSTB"].isin(_EMPSTB_20H_PLUS)
        | (adults["WRKREG"] == _WRKREG_WORKING_30H)
        | adults["EMPRG"].isin(_EMPRG_QUALIFYING)
    )
    adults["group"] = group
    adults["status"] = np.select([exempt, meets], ["exempt", "meets"], default="at_risk")
    adults["newly_subject"] = adults["group"].isin(("age_55_64", "child_14_17", "removed_exception"))
    return adults.join(units.set_index("unit_id")[["FYWGT", "FSUSIZE", "FSBEN"]], on="unit_id")


def snap_loss_if_removed(units: pd.DataFrame, adults: pd.DataFrame) -> pd.Series:
    """Monthly benefit each adult's unit loses if that adult alone becomes ineligible (FY2024 dollars).

    A one-person unit loses its whole benefit. A larger unit is recomputed one person smaller, counting all but a
    pro rata share of the ineligible member's income, as 7 CFR 273.11(c)(2) does for a member past the time limit:
    their income is divided evenly among the members, them included, and their own share is left out. Shelter and
    other expenses are left as recorded.
    """
    u = units.set_index("unit_id")
    out = np.zeros(len(adults))
    for k, (unit_id, earned, unearned) in enumerate(
        zip(adults["unit_id"], adults["earned"], adults["unearned"], strict=True)
    ):
        row = u.loc[unit_id]
        n = int(row["FSUSIZE"])
        if n <= 1:
            out[k] = row["FSBEN"]
            continue
        new = snap_benefit_2024(
            size=n - 1,
            gross=row["FSGRINC"] - (earned + unearned) / n,
            earned=row["FSEARN"] - earned / n,
            medical=row["FSMEDDED"], dependent_care=row["FSDEPDED"], child_support=row["FSCSDED"],
            shelter_expense=row["FSSLTEXP"], shelter_cap=row["SHELCAP"],
            elderly_or_disabled=bool(row["FSELDER"] == 1 or row["FSDIS"] == 1),
        )
        out[k] = max(0.0, row["FSBEN"] - new)
    return pd.Series(out, index=adults.index, name="loss_if_removed")


def refugee_asylee_members(units: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    """Eligible members recorded as refugees, asylees or with deportation withheld (section 10108 removes them)."""
    m = persons[(persons["FSAFIL"] == 1) & (persons["CTZN"] == _CTZN_REFUGEE_ASYLEE)]
    return m.join(units.set_index("unit_id")[["FYWGT", "FSUSIZE", "FSBEN"]], on="unit_id")


def snap_state_share(payment_error_rate: float) -> float:
    """State's share of benefit costs from fiscal year 2028 (section 10105), by payment error rate in percent."""
    if payment_error_rate < 6.0:
        return 0.0
    if payment_error_rate < 8.0:
        return 0.05
    if payment_error_rate < 10.0:
        return 0.10
    return 0.15


# --------------------------------------------------------------------------------------------------------------
# Medicaid
# --------------------------------------------------------------------------------------------------------------

MEDICAID_MONTHLY_INCOME_TEST = 580.0      # 80 hours at the $7.25 federal minimum wage
MEDICAID_STATUSES = ("exempt", "meets", "at_risk")
# ACS relationship codes (RELSHIPP) for the householder, a spouse or an unmarried partner.
_REL_HEAD_OR_PARTNER = (20, 21, 22, 23, 24)
_ESR_EMPLOYED = (1, 2, 4, 5)


def classify_medicaid_adults(persons: pd.DataFrame, *, look_back_weeks: int = 26) -> pd.DataFrame:
    """ACS persons on Medicaid aged 19 to 64, with the eligibility group and status under section 71119.

    ``persons`` is a PUMS person file with the household's persons all present (the children's ages are read off
    it). Adds:

    * ``mq_group``: ``aged_blind_disabled`` (Medicare or SSI), otherwise ``non_disabled_adult``: the expansion
      group and the parent and caretaker group together. :func:`split_expansion` tells the two apart.
    * ``is_parent``: lives with their own child under 19.
    * ``status`` (non-disabled adults): ``exempt`` (caring for a child 13 or under, a disability, gave birth in
      the last year, American Indian or Alaska Native), ``meets`` (see below), ``at_risk``.
    * ``p_meets``: the probability an adult has a qualifying month in a ``look_back_weeks`` window. Hawaii asks
      for one month, in the six before renewal, with $580 of income, 80 hours of work or half-time school. An
      adult employed at the interview or enrolled in school has 1. One who worked ``w`` weeks in the last 52 at
      $580 a month or more has ``min(1, (w + look_back_weeks) / 52)``, the chance a window of that length touches
      one spell of work of that length. ``status`` is ``meets`` at 0.5 or more.

    A parent is the householder, their spouse or partner in a household with a child of the householder, or
    anyone in a subfamily with a child. PUMS has no pointer from a child to a parent.
    """
    need = ("SERIALNO", "AGEP", "HINS3", "HINS4", "SSIP", "DIS", "ESR", "WKWN", "WAGP", "SEMP", "SCH", "POVPIP",
            "RELSHIPP", "PWGTP")
    missing = [c for c in need if c not in persons.columns]
    if missing:
        raise DataValidationError(f"persons frame is missing PUMS columns {missing}")
    p = persons.copy()
    is_child = p["AGEP"] < 19
    # Own children of the householder (biological, adopted, step) and children in subfamilies.
    own_child = is_child & p["RELSHIPP"].isin((25, 26, 27))
    sub = p["SFN"] if "SFN" in p.columns else pd.Series(np.nan, index=p.index)
    sub_child = is_child & sub.notna() & ~own_child
    youngest_own = p[own_child].groupby("SERIALNO")["AGEP"].min()
    youngest_sub = p[sub_child].groupby(["SERIALNO", sub[sub_child]])["AGEP"].min()

    adults = p[(p["AGEP"] >= 19) & (p["AGEP"] <= 64) & (p["HINS4"] == 1)].copy()
    child_age = pd.Series(np.nan, index=adults.index)
    head = adults["RELSHIPP"].isin(_REL_HEAD_OR_PARTNER)
    child_age[head] = adults.loc[head, "SERIALNO"].map(youngest_own)
    in_sub = sub.reindex(adults.index).notna() & ~head
    if in_sub.any():
        keys = list(zip(adults.loc[in_sub, "SERIALNO"], sub.reindex(adults.index)[in_sub], strict=True))
        child_age[in_sub] = [youngest_sub.get(k, np.nan) for k in keys]
    adults["youngest_child"] = child_age

    medicare_or_ssi = (adults["HINS3"] == 1) | (adults["SSIP"].fillna(0) > 0)
    adults["is_parent"] = adults["youngest_child"].notna()
    adults["mq_group"] = np.where(medicare_or_ssi, "aged_blind_disabled", "non_disabled_adult")

    weeks = adults["WKWN"].fillna(0).clip(0, 52)
    earnings = adults["WAGP"].fillna(0) + adults["SEMP"].fillna(0).clip(lower=0)
    if "ADJINC" in adults.columns:
        earnings = earnings * adults["ADJINC"] / 1e6
    monthly_when_working = np.where(weeks > 0, earnings / np.maximum(weeks / 4.33, 1.0), 0.0)
    worked = (weeks > 0) & (monthly_when_working >= MEDICAID_MONTHLY_INCOME_TEST)
    p_window = np.minimum(1.0, (weeks + look_back_weeks) / 52.0)
    employed_now = adults["ESR"].isin(_ESR_EMPLOYED)
    student = adults["SCH"].isin((2, 3))
    adults["p_meets"] = np.where(employed_now | student, 1.0, np.where(worked, p_window, 0.0))

    aian = adults["RAC1P"].isin((3, 4, 5)) if "RAC1P" in adults.columns else False
    gave_birth = (adults["FER"] == 1) if "FER" in adults.columns else False
    exempt = (adults["youngest_child"].fillna(99) <= 13) | (adults["DIS"] == 1) | aian | gave_birth
    status = np.select([exempt, adults["p_meets"] >= 0.5], ["exempt", "meets"], default="at_risk")
    adults["status"] = np.where(adults["mq_group"] == "non_disabled_adult", status, "not_subject")
    return adults


def split_expansion(
    adults: pd.DataFrame, *, expansion_target: float, parent_caretaker_target: float, weight_col: str = "PWGTP",
) -> pd.DataFrame:
    """Scale the survey's non-disabled Medicaid adults to Med-QUEST's counts and mark the expansion group.

    The ACS finds fewer Medicaid adults than Med-QUEST enrolls, and annual survey income cannot place a parent
    on either side of the parent and caretaker limit the way a monthly determination does. So the two groups are
    set from the enrollment counts: every non-disabled adult's weight is scaled by one factor so the pool equals
    the two groups together, then parents are assigned to the parent and caretaker group from the lowest family
    income up until that group's count is met. Everyone else is an expansion adult.

    Adds ``mq_weight`` (the scaled weight, 0 for the aged, blind and disabled) and ``expansion_share`` (0 to 1;
    the parent at the boundary is split).
    """
    out = adults.copy()
    pool = (out["mq_group"] == "non_disabled_adult").to_numpy()
    w = out[weight_col].to_numpy(dtype=float)
    if w[pool].sum() <= 0:
        raise DataValidationError("no non-disabled Medicaid adults to scale")
    scaled = np.where(pool, w * (expansion_target + parent_caretaker_target) / w[pool].sum(), 0.0)
    share = pool.astype(float)
    parents = np.flatnonzero(pool & out["is_parent"].to_numpy())
    order = parents[np.argsort(out["POVPIP"].fillna(501).to_numpy()[parents], kind="stable")]
    before = np.concatenate([[0.0], np.cumsum(scaled[order])[:-1]])
    in_parent_group = np.clip((parent_caretaker_target - before) / np.maximum(scaled[order], 1e-12), 0.0, 1.0)
    share[order] = 1.0 - in_parent_group
    out["mq_weight"] = scaled
    out["expansion_share"] = share
    return out


# --------------------------------------------------------------------------------------------------------------
# Who loses what they are subject to
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LossRates:
    """Share of each status that ends up without the benefit. The assumption the estimate turns on."""

    name: str
    at_risk: float
    meets: float
    exempt: float
    # Medicaid only: share of the adults who stay enrolled that the move from yearly to six-month renewals drops.
    six_month_renewal: float = 0.0

    def rate(self, status: pd.Series) -> np.ndarray:
        table: Mapping[str, float] = {"at_risk": self.at_risk, "meets": self.meets, "exempt": self.exempt}
        return status.map(table).fillna(0.0).to_numpy()


SNAP_LOSS_RATES = {
    "LOW": LossRates("LOW", at_risk=0.50, meets=0.00, exempt=0.00),
    "MID": LossRates("MID", at_risk=0.70, meets=0.05, exempt=0.02),
    "HIGH": LossRates("HIGH", at_risk=0.90, meets=0.10, exempt=0.05),
}
MEDICAID_LOSS_RATES = {
    "LOW": LossRates("LOW", at_risk=0.60, meets=0.02, exempt=0.02, six_month_renewal=0.03),
    "MID": LossRates("MID", at_risk=0.80, meets=0.09, exempt=0.09, six_month_renewal=0.05),
    "HIGH": LossRates("HIGH", at_risk=0.95, meets=0.15, exempt=0.15, six_month_renewal=0.08),
}


@dataclass(frozen=True)
class Hr1Parameters:
    """Administrative figures the estimate is scaled to, each with its source in the methodology note."""

    # SNAP: benefits issued, annualized from July-September 2026 (Hawaii DHS monthly participation file).
    snap_annual_benefits_2027: float = 607.4e6
    snap_admin_cost_shift: float = 14.8e6          # DHS, January 2026 budget briefing
    snap_error_rate_fy2025: float = 10.92          # USDA FNS, June 2026
    # Medicaid: expansion adults enrolled, September 2026 (Med-QUEST enrollment report, November 6, 2026).
    medicaid_expansion_adults: float = 129_577
    medicaid_parent_caretakers: float = 50_006
    medicaid_cost_per_expansion_adult: float = 4_853.10   # KFF from T-MSIS, 2024, full-benefit enrollee
    medicaid_expansion_federal_share: float = 0.90
    snap_rates: Mapping[str, LossRates] = field(default_factory=lambda: dict(SNAP_LOSS_RATES))
    medicaid_rates: Mapping[str, LossRates] = field(default_factory=lambda: dict(MEDICAID_LOSS_RATES))
