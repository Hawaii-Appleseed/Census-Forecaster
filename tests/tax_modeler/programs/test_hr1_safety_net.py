"""H.R.1 SNAP and Medicaid work rules: who is newly subject, who is exempt, and what a unit loses."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.errors import DataValidationError
from tax_modeler.programs import hr1_safety_net as hr1

# --------------------------------------------------------------------------------------------------------------
# SNAP
# --------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qc():
    return hr1.load_hawaii_qc()


def test_extract_matches_the_published_hawaii_totals(qc):
    units, persons = qc
    assert len(units) == 571
    assert units["FYWGT"].sum() == pytest.approx(83_109, abs=1)
    assert (units["FYWGT"] * units["FSUSIZE"]).sum() == pytest.approx(152_269, abs=1)
    # Hawaii DHS issued $694.9M in state fiscal year 2025; the QC year overlaps it by three months.
    assert (units["FYWGT"] * units["FSBEN"]).sum() * 12 == pytest.approx(695.1e6, rel=1e-3)
    eligible = persons[persons["FSAFIL"] == 1].groupby("unit_id").size()
    assert (eligible.reindex(units["unit_id"]).to_numpy() == units["FSUSIZE"].to_numpy()).all()


def test_benefit_formula_reproduces_the_recorded_benefit(qc):
    units, _ = qc
    computed = np.array([
        hr1.snap_benefit_2024(
            size=int(r.FSUSIZE), gross=r.FSGRINC, earned=r.FSEARN, medical=r.FSMEDDED,
            dependent_care=r.FSDEPDED, child_support=r.FSCSDED, shelter_expense=r.FSSLTEXP,
            shelter_cap=r.SHELCAP, elderly_or_disabled=bool(r.FSELDER == 1 or r.FSDIS == 1),
        )
        for r in units.itertuples()
    ])
    # The earned-income deduction is recorded to the dollar, hence the tolerance.
    close = np.abs(computed - units["FSBEN"].to_numpy()) <= 2
    assert close.sum() >= len(units) - 3


def test_allotment_tables_match_the_file_and_extend_past_eight():
    assert hr1.max_allotment(1, 2024) == 527
    assert hr1.max_allotment(4, 2027) == 1655
    assert hr1.max_allotment(10, 2024) == 3958
    assert hr1.max_allotment(0, 2024) == 0
    assert hr1.poverty_line_2024(3) == 1398 + 2 * 493


def _unit(unit_id, size, ben=500, gross=0.0, earned=0.0):
    return {"unit_id": unit_id, "FYWGT": 100.0, "FSUSIZE": size, "FSBEN": ben, "FSGRINC": gross,
            "FSEARN": earned, "FSMEDDED": 0.0, "FSDEPDED": 0.0, "FSCSDED": 0.0, "FSSLTEXP": 0.0,
            "SHELCAP": 905, "FSELDER": 0, "FSDIS": 0}


def _person(unit_id, age, *, dis=0, wrkreg=1, abwdst=9, empstb=1, emprg=0, afil=1, earned=0.0, unearned=0.0):
    return {"unit_id": unit_id, "FSAFIL": afil, "AGE": age, "DIS64": dis, "WRKREG": wrkreg, "ABWDST": abwdst,
            "EMPSTB": empstb, "EMPRG": emprg, "CTZN": 1, "earned": earned, "unearned": unearned}


def test_snap_groups_follow_section_10102():
    units = pd.DataFrame([_unit(i, 2) for i in range(1, 8)])
    persons = pd.DataFrame([
        _person(1, 58),                              # 55-64, no child: newly subject
        _person(2, 40), _person(2, 15),              # youngest child 14-17: newly subject
        _person(3, 40), _person(3, 13),              # child under 14: not subject
        _person(4, 40),                              # was already under the time limit
        _person(5, 40, abwdst=6),                    # veteran exception, repealed
        _person(6, 60), _person(6, 12),              # 60 with a child under 14: not subject
        _person(7, 66), _person(7, 17, afil=3),      # 65 is past the limit; ineligible members are ignored
    ])
    adults = hr1.classify_snap_adults(units, persons).set_index("unit_id")
    assert adults.loc[1, "group"] == "age_55_64"
    assert adults.loc[2, "group"] == "child_14_17"
    assert adults.loc[3, "group"] == "not_subject"
    assert adults.loc[4, "group"] == "already_subject"
    assert adults.loc[5, "group"] == "removed_exception"
    assert adults.loc[6, "group"] == "not_subject"
    assert 7 not in adults.index
    assert adults["newly_subject"].to_dict() == {1: True, 2: True, 3: False, 4: False, 5: True, 6: False}


@pytest.mark.parametrize(
    "kwargs, status",
    [
        ({}, "at_risk"),
        ({"dis": 1}, "exempt"),
        ({"wrkreg": 2}, "exempt"),            # unfit for work
        ({"wrkreg": 7}, "exempt"),            # unemployment compensation
        ({"wrkreg": 0}, "at_risk"),           # 60+ is exempt from registering, not from the time limit
        ({"empstb": 2}, "at_risk"),           # 1-19 hours a week
        ({"empstb": 3}, "meets"),             # 20-29 hours
        ({"wrkreg": 4}, "meets"),             # 30 hours' equivalent
        ({"emprg": 4}, "meets"),              # workfare
        ({"emprg": 2}, "at_risk"),            # job search alone does not count
        ({"dis": 1, "empstb": 5}, "exempt"),
    ],
)
def test_snap_status(kwargs, status):
    adults = hr1.classify_snap_adults(pd.DataFrame([_unit(1, 1)]), pd.DataFrame([_person(1, 61, **kwargs)]))
    assert adults["status"].iloc[0] == status


def test_single_person_unit_loses_its_whole_benefit():
    units = pd.DataFrame([_unit(1, 1, ben=527)])
    adults = hr1.classify_snap_adults(units, pd.DataFrame([_person(1, 60)]))
    assert hr1.snap_loss_if_removed(units, adults).iloc[0] == 527


def test_larger_unit_keeps_a_smaller_benefit_and_a_prorated_share_of_income():
    # Two people, no income: the benefit falls from the two-person to the one-person maximum.
    units = pd.DataFrame([_unit(1, 2, ben=967), _unit(2, 2, ben=hr1.snap_benefit_2024(size=2, gross=1000, earned=0),
                                                       gross=1000.0)])
    persons = pd.DataFrame([_person(1, 60), _person(1, 70), _person(2, 60, unearned=1000.0), _person(2, 70)])
    adults = hr1.classify_snap_adults(units, persons)
    loss = hr1.snap_loss_if_removed(units, adults).to_numpy()
    assert loss[0] == 967 - 527
    # Half of the removed member's $1,000 still counts against the remaining member.
    assert loss[1] == units["FSBEN"].iloc[1] - hr1.snap_benefit_2024(size=1, gross=500, earned=0)
    assert loss[1] > 0


def test_missing_column_is_an_error():
    with pytest.raises(DataValidationError):
        hr1.classify_snap_adults(pd.DataFrame([_unit(1, 1)]), pd.DataFrame([{"unit_id": 1, "AGE": 30}]))


@pytest.mark.parametrize("rate, share", [(5.99, 0.0), (6.0, 0.05), (6.68, 0.05), (8.0, 0.10), (10.0, 0.15),
                                         (10.92, 0.15), (21.78, 0.15)])
def test_state_share_schedule(rate, share):
    assert hr1.snap_state_share(rate) == share


# --------------------------------------------------------------------------------------------------------------
# Medicaid
# --------------------------------------------------------------------------------------------------------------


def _acs(serial, age, rel=20, **kw):
    base = {"SERIALNO": serial, "AGEP": age, "RELSHIPP": rel, "HINS3": 2, "HINS4": 1, "SSIP": 0, "DIS": 2,
            "ESR": 6, "WKWN": np.nan, "WAGP": 0, "SEMP": 0, "SCH": 1, "POVPIP": 80, "PWGTP": 10, "SFN": np.nan,
            "RAC1P": 1, "FER": 2, "ADJINC": 1_000_000}
    return {**base, **kw}


def test_medicaid_groups_and_status():
    persons = pd.DataFrame([
        _acs("a", 40),                                          # out of the labor force, no exemption
        _acs("b", 40, ESR=1),                                   # employed now
        _acs("c", 40, WKWN=10, WAGP=6_000),                     # ten weeks at about $2,600 a month
        _acs("d", 40, WKWN=30, WAGP=20_000),                    # thirty weeks
        _acs("e", 40, DIS=1),                                   # disability
        _acs("f", 40), _acs("f", 13, rel=25, HINS4=1),          # parent of a child 13 or under
        _acs("g", 40), _acs("g", 16, rel=25),                   # parent of a 16-year-old: not exempt
        _acs("h", 40, HINS3=1),                                 # Medicare
        _acs("i", 40, SSIP=9_000),                              # SSI
        _acs("j", 40, SCH=2),                                   # student
        _acs("k", 40, HINS4=2),                                 # not on Medicaid
        _acs("l", 66),                                          # past 64
        _acs("m", 30, rel=25, SFN=1), _acs("m", 2, rel=30, SFN=1), _acs("m", 60),  # subfamily parent
    ])
    adults = hr1.classify_medicaid_adults(persons)
    by = adults.set_index(["SERIALNO", "AGEP"])
    assert set(adults["SERIALNO"]) == {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "m"}
    assert by.loc[("a", 40), "status"] == "at_risk"
    assert by.loc[("b", 40), "status"] == "meets"
    assert by.loc[("c", 40), "p_meets"] == pytest.approx(36 / 52)
    assert by.loc[("d", 40), "p_meets"] == 1.0
    assert by.loc[("e", 40), "status"] == "exempt"
    assert by.loc[("f", 40), "status"] == "exempt"
    assert by.loc[("g", 40), "status"] == "at_risk" and by.loc[("g", 40), "is_parent"]
    assert by.loc[("h", 40), "mq_group"] == "aged_blind_disabled"
    assert by.loc[("i", 40), "status"] == "not_subject"
    assert by.loc[("j", 40), "status"] == "meets"
    assert by.loc[("m", 30), "status"] == "exempt" and by.loc[("m", 30), "is_parent"]
    assert not by.loc[("m", 60), "is_parent"]


def test_low_pay_does_not_meet_the_income_test():
    persons = pd.DataFrame([_acs("a", 40, WKWN=40, WAGP=2_000)])   # about $217 a month while working
    assert hr1.classify_medicaid_adults(persons)["p_meets"].iloc[0] == 0.0


def test_split_expansion_hits_both_enrollment_counts():
    persons = pd.DataFrame(
        [_acs(f"p{i}", 35, POVPIP=10 * i) for i in range(10)]
        + [_acs(f"p{i}", 5, rel=25, HINS4=2) for i in range(10)]
        + [_acs(f"s{i}", 35) for i in range(20)]
        + [_acs("abd", 50, HINS3=1)]
    )
    adults = hr1.split_expansion(hr1.classify_medicaid_adults(persons), expansion_target=5_000,
                                 parent_caretaker_target=1_250)
    assert adults["mq_weight"].sum() == pytest.approx(6_250)
    expansion = (adults["mq_weight"] * adults["expansion_share"]).sum()
    assert expansion == pytest.approx(5_000)
    parents = adults[adults["is_parent"]].sort_values("POVPIP")
    # 30 adults share 6,250, so 6 parents fill the parent group: the six with the lowest income.
    assert parents["expansion_share"].to_numpy() == pytest.approx([0.0] * 6 + [1.0] * 4)
    assert adults.loc[adults["mq_group"] == "aged_blind_disabled", "mq_weight"].iloc[0] == 0.0


def test_loss_rates_are_ordered_and_map_statuses():
    for table in (hr1.SNAP_LOSS_RATES, hr1.MEDICAID_LOSS_RATES):
        low, mid, high = table["LOW"], table["MID"], table["HIGH"]
        assert low.at_risk < mid.at_risk < high.at_risk <= 1
        assert low.meets <= mid.meets <= high.meets < low.at_risk
    rates = hr1.SNAP_LOSS_RATES["MID"].rate(pd.Series(["at_risk", "meets", "exempt", "not_subject"]))
    assert rates.tolist() == [0.70, 0.05, 0.02, 0.0]
