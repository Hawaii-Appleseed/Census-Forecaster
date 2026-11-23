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

* **SNAP on the ACS population**, for poverty and geography: the QC file has no county and no household around
  the SNAP unit, so the SPM run in the analysis script needs SNAP on ACS households. :func:`acs_snap_baseline`
  builds it (FY2027 Hawaiʻi rules, households that report SNAP, scaled to the DHS caseload and issuance),
  :func:`classify_acs_snap_adults` applies the same groups and statuses to ACS adults, and
  :func:`calibrate_to_qc` sets each adult's probability of losing SNAP so that, cell by cell, the ACS reproduces
  who the QC file says loses it.

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
# SNAP on the ACS population (poverty and geography)
# --------------------------------------------------------------------------------------------------------------

# Hawaiʻi FY2027 (October 2026 to September 2027), USDA FNS cost-of-living memo of August 21, 2026. Hawaiʻi uses
# broad-based categorical eligibility: a gross limit of 200 percent of poverty and, since February 2025, no net
# income test (Hawaiʻi DHS).
HI_GROSS_LIMIT_200PCT_FY2027 = (3060, 4150, 5238, 6326, 7416, 8506, 9596, 10686)
HI_GROSS_LIMIT_EXTRA_PERSON_FY2027 = 1090
HI_STANDARD_DEDUCTION_FY2027 = {1: 306, 2: 306, 3: 306, 4: 306, 5: 309, 6: 354}
# ACS codes: employed at work or with a job (ESR); enrolled in public or private school (SCH); served on active
# duty in the past, not now (MIL).
_ACS_EMPLOYED = (1, 2, 4, 5)
_ACS_IN_SCHOOL = (2, 3)
_ACS_VETERAN = 2
NEWLY_SUBJECT_GROUPS = ("age_55_64", "child_14_17", "removed_exception")


def snap_parameters_fy2027():
    """:class:`tax_modeler.benefits.snap.SnapParameters` for Hawaiʻi in FY2027 under categorical eligibility.

    The module has no shelter or dependent-care deduction, so its benefits run low for households with high
    housing costs; :func:`acs_snap_baseline` scales the total to the issuance DHS reports.
    """
    from tax_modeler.benefits.snap import SnapParameters

    return SnapParameters(
        max_benefit_by_size=dict(enumerate(HI_MAX_ALLOTMENT[2027], start=1)),
        max_benefit_increment_per_extra_person=HI_MAX_ALLOTMENT_EXTRA_PERSON[2027],
        gross_income_limit_by_size=dict(enumerate(HI_GROSS_LIMIT_200PCT_FY2027, start=1)),
        gross_income_increment_per_extra_person=HI_GROSS_LIMIT_EXTRA_PERSON_FY2027,
        standard_deduction_by_size=dict(HI_STANDARD_DEDUCTION_FY2027),
        net_income_limit_factor=1e6,          # no net income test
    )


@dataclass(frozen=True)
class AcsSnapBaseline:
    """SNAP on ACS tax units (``units["snap_amount"]``, annual), and what it took to get there."""

    units: pd.DataFrame
    # one row per household: hh_id, snap (monthly, before scaling), income, earned, WGTP, FS, n_persons, recipient
    households: pd.DataFrame
    dollar_scale: float
    reported: float               # weighted households that report SNAP (all kept)
    reported_ineligible: float    # of which fail the FY2027 gross test on annual income (given the size average)
    imputed: float                # eligible non-reporters drawn to reach the caseload
    draw_rate: float              # the probability each eligible non-reporter was drawn with


def _hash_uniform(keys: pd.Index, salt: str) -> np.ndarray:
    """A uniform number per key, the same on every run (the same idea as assign_geography's district draw)."""
    import hashlib

    return np.array([int(hashlib.md5(f"{salt}_{k}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF for k in keys])


def acs_snap_baseline(
    units: pd.DataFrame, households: pd.DataFrame, persons: pd.DataFrame, *,
    target_households: float, target_dollars: float, fallback_benefit_by_size: Mapping[int, float],
    params=None,
) -> AcsSnapBaseline:
    """SNAP on the ACS population: who gets it from the survey, how much from the rules, the level from DHS.

    Every household that reports SNAP (``FS`` = 1) is a recipient. Its benefit is computed by
    :func:`tax_modeler.benefits.snap.compute_snap_for_units` under :func:`snap_parameters_fy2027`; a reporting
    household that fails the gross test on its annual survey income (income from members outside the SNAP unit,
    or months before or after receiving it) gets ``fallback_benefit_by_size[min(n, 6)]`` a month instead. The
    survey misses about a third of recipients, so eligible households that do not report SNAP are each drawn,
    with one probability, until the weighted count (``WGTP``) reaches ``target_households``; the draw is a hash
    of the household id, so it is the same on every run. Every benefit is then scaled by one factor so the
    annual total equals ``target_dollars``. ``households`` needs ``SERIALNO``, ``WGTP`` and ``FS``; ``persons``
    ``SERIALNO``.
    """
    from tax_modeler.benefits.snap import compute_snap_for_units

    for col in ("SERIALNO", "WGTP", "FS"):
        if col not in households.columns:
            raise DataValidationError(f"households frame needs column {col!r}")
    prm = params or snap_parameters_fy2027()
    u = compute_snap_for_units(units, params=prm, months=1)
    earned_col = "earned_income" if "earned_income" in u.columns else "income"
    hh = u.assign(_earned=u[earned_col]).groupby(u["hh_id"].astype(str)).agg(
        snap=("snap_amount", "sum"), income=("income", "sum"), earned=("_earned", "sum"))
    h = households.assign(hh_id=households["SERIALNO"].astype(str)).set_index("hh_id")[["WGTP", "FS"]]
    hh = hh.join(h, how="left")
    sizes = persons.groupby(persons["SERIALNO"].astype(str)).size()
    hh["n_persons"] = sizes.reindex(hh.index).fillna(0).astype(int)
    eligible = hh["snap"] > 0
    reports = hh["FS"] == 1
    fallback = hh["n_persons"].clip(1, 6).map(lambda n: float(fallback_benefit_by_size[int(n)]))
    formula = hh["snap"].copy()
    hh.loc[reports & ~eligible, "snap"] = fallback[reports & ~eligible]
    hh["recipient"] = reports.copy()
    reported = float(hh.loc[reports, "WGTP"].sum())
    pool = eligible & ~reports
    pool_w = float(hh.loc[pool, "WGTP"].sum())
    rate = min(1.0, max(0.0, (target_households - reported) / pool_w)) if pool_w > 0 else 0.0
    drawn = pool & (_hash_uniform(hh.index, "snap_takeup") < rate)
    hh.loc[drawn, "recipient"] = True
    imputed = float(hh.loc[drawn, "WGTP"].sum())
    monthly = float((hh["WGTP"] * hh["snap"] * hh["recipient"]).sum())
    if monthly <= 0:
        raise DataValidationError("no SNAP recipients on the ACS population")
    scale = target_dollars / (12.0 * monthly)
    # Spread each recipient household's benefit over its tax units by the module's member shares, or evenly
    # where the formula gave the household nothing.
    hid = u["hh_id"].astype(str)
    unit_share = np.where(hid.map(formula).to_numpy() > 0,
                          u["snap_amount"].to_numpy() / hid.map(formula).replace(0, np.nan).to_numpy(),
                          1.0 / hid.map(hid.value_counts()).to_numpy())
    unit_share = np.nan_to_num(unit_share)
    keep = hid.map(hh["recipient"]).fillna(False).to_numpy(dtype=bool)
    u["snap_amount"] = np.where(keep, hid.map(hh["snap"]).to_numpy() * unit_share * 12.0 * scale, 0.0)
    return AcsSnapBaseline(
        units=u, households=hh.rename_axis("hh_id").reset_index(), dollar_scale=scale, reported=reported,
        reported_ineligible=float(hh.loc[reports & ~eligible, "WGTP"].sum()), imputed=imputed, draw_rate=rate,
    )


def classify_acs_snap_adults(persons: pd.DataFrame, recipient_households) -> pd.DataFrame:
    """ACS adults 18 to 64 in SNAP households, with the groups and statuses of :func:`classify_snap_adults`.

    The ACS cannot see a homeless or former-foster-youth exception, so ``removed_exception`` holds veterans only.
    Status: ``exempt`` (an ACS disability, SSI, or in school), ``meets`` (employed and usually working 20 or more
    hours a week), ``at_risk``. Children are the household's: the ACS does not say which members share a SNAP
    unit.
    """
    need = ("SERIALNO", "AGEP", "DIS", "SSIP", "SCH", "ESR", "WKHP", "MIL", "PWGTP")
    missing = [c for c in need if c not in persons.columns]
    if missing:
        raise DataValidationError(f"persons frame is missing PUMS columns {missing}")
    p = persons.assign(SERIALNO=persons["SERIALNO"].astype(str))
    p = p[p["SERIALNO"].isin({str(x) for x in recipient_households})]
    youngest = p[p["AGEP"] < 18].groupby("SERIALNO")["AGEP"].min()
    a = p[(p["AGEP"] >= 18) & (p["AGEP"] <= 64)].copy()
    a["youngest_child"] = a["SERIALNO"].map(youngest)
    y = a["youngest_child"].fillna(99)
    a["group"] = np.select(
        [y < 14, a["AGEP"] >= 55, y < 18, a["MIL"] == _ACS_VETERAN],
        ["not_subject", "age_55_64", "child_14_17", "removed_exception"], default="already_subject")
    exempt = (a["DIS"] == 1) | (a["SSIP"].fillna(0) > 0) | a["SCH"].isin(_ACS_IN_SCHOOL)
    meets = a["ESR"].isin(_ACS_EMPLOYED) & (a["WKHP"].fillna(0) >= 20)
    a["status"] = np.select([exempt, meets], ["exempt", "meets"], default="at_risk")
    a["newly_subject"] = a["group"].isin(NEWLY_SUBJECT_GROUPS)
    return a


def calibrate_to_qc(adults: pd.DataFrame, qc_cells: pd.DataFrame, rates: LossRates,
                    weight_col: str = "PWGTP") -> tuple[np.ndarray, pd.DataFrame]:
    """Each newly subject ACS adult's probability of losing SNAP, set so the ACS matches the QC file by cell.

    ``qc_cells`` has ``group``, ``status`` and ``persons`` (weighted QC adults, from :func:`classify_snap_adults`).
    The target for a (group, status) cell is its QC count times the status's loss rate; the cell's ACS adults
    each get ``target / weighted ACS adults``, but no more than 1. A cell the ACS sample lacks, or one held to 1,
    passes its shortfall to the group's other cells in proportion to their targets. Returns the probabilities
    (0 for adults not newly subject) and a table of target and achieved expected losers by cell.
    """
    w = adults[weight_col].to_numpy(dtype=float)
    new = adults["newly_subject"].to_numpy(dtype=bool)
    prob = np.zeros(len(adults))
    rows = []
    cells = qc_cells[qc_cells["group"].isin(NEWLY_SUBJECT_GROUPS)]
    for group, q in cells.groupby("group"):
        statuses = list(q["status"])
        target = {st: float(n) * float(rates.rate(pd.Series([st]))[0])
                  for st, n in zip(statuses, q["persons"], strict=True)}
        masks = {st: new & (adults["group"] == group).to_numpy() & (adults["status"] == st).to_numpy()
                 for st in statuses}
        have = {st: float(w[m].sum()) for st, m in masks.items()}
        goal = dict(target)
        for _ in range(10):
            short = sum(g - min(g, have[st]) for st, g in goal.items())
            room = {st: g for st, g in goal.items() if have[st] > g and g > 0}
            if short <= 1e-9 or not room:
                break
            total = sum(room.values())
            goal = {st: min(g, have[st]) + (short * room[st] / total if st in room else 0.0)
                    for st, g in goal.items()}
        for st, m in masks.items():
            pr = min(1.0, goal[st] / have[st]) if have[st] > 0 else 0.0
            prob[m] = pr
            rows.append({"group": group, "status": st,
                         "qc_adults": float(q.loc[q["status"] == st, "persons"].sum()),
                         "qc_expected_losers": target[st], "acs_adults": have[st], "acs_sample": int(m.sum()),
                         "probability": pr, "acs_expected_losers": pr * have[st]})
    return prob, pd.DataFrame(rows)


def acs_snap_loss(adults: pd.DataFrame, baseline: AcsSnapBaseline, persons: pd.DataFrame,
                  params=None) -> np.ndarray:
    """Annual SNAP each ACS adult's household loses if that adult alone becomes ineligible.

    The household is recomputed one person smaller with all but ``1/n`` of the adult's income still counted
    (7 CFR 273.11(c)(2)), and the change scaled like the baseline. The adult's income is their share of the
    household's personal income (``PINCP``) applied to the household's aged income. A household on the size
    average (it reports SNAP but fails the gross test on annual income) loses ``1/n`` of it.
    """
    from tax_modeler.benefits.snap import _compute_household_snap

    prm = params or snap_parameters_fy2027()
    hh = baseline.households.set_index("hh_id")
    inc = persons["PINCP"].fillna(0).clip(lower=0)
    hh_pin = inc.groupby(persons["SERIALNO"].astype(str)).sum()
    loss = np.zeros(len(adults))
    serials = adults["SERIALNO"].astype(str).to_numpy()
    own_inc = adults["PINCP"].fillna(0).clip(lower=0).to_numpy(dtype=float)
    for k, (serial, own) in enumerate(zip(serials, own_inc, strict=True)):
        row = hh.loc[serial]
        if not bool(row["recipient"]):
            continue
        n = max(int(row["n_persons"]), 1)
        gross, earned = float(row["income"]) / 12.0, float(row["earned"]) / 12.0
        before = _compute_household_snap(gross, earned, n, prm)
        if before <= 0:          # on the size average: lose a member's share of it
            loss[k] = float(row["snap"]) / n * 12.0 * baseline.dollar_scale
            continue
        if n == 1:
            after = 0.0
        else:
            share = own / hh_pin[serial] if hh_pin.get(serial, 0) > 0 else 0.0
            after = _compute_household_snap(gross * (1 - share / n), max(earned * (1 - share / n), 0.0), n - 1, prm)
        loss[k] = max(before - after, 0.0) * 12.0 * baseline.dollar_scale
    return loss


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
    # Households and persons a month, the same three months (the SPM run's SNAP is scaled to these).
    snap_households_2027: float = 77_985
    snap_persons_2027: float = 157_850
    snap_admin_cost_shift: float = 14.8e6          # DHS, January 2026 budget briefing
    snap_error_rate_fy2025: float = 10.92          # USDA FNS, June 2026
    # Medicaid: expansion adults enrolled, September 2026 (Med-QUEST enrollment report, November 6, 2026).
    medicaid_expansion_adults: float = 129_577
    medicaid_parent_caretakers: float = 50_006
    medicaid_cost_per_expansion_adult: float = 4_853.10   # KFF from T-MSIS, 2024, full-benefit enrollee
    medicaid_expansion_federal_share: float = 0.90
    snap_rates: Mapping[str, LossRates] = field(default_factory=lambda: dict(SNAP_LOSS_RATES))
    medicaid_rates: Mapping[str, LossRates] = field(default_factory=lambda: dict(MEDICAID_LOSS_RATES))
