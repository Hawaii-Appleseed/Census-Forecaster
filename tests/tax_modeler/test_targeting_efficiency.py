"""analyses/forecast_targeting_efficiency.py: the scoring, on hand-built SPM units."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "analyses"))

import forecast_targeting_efficiency as te  # noqa: E402


def _population(**eligible) -> te.Population:
    # four units: deep poverty, just under the line, just over it, well off
    weights = np.array([[10.0, 20.0, 30.0, 40.0],
                        [12.0, 18.0, 30.0, 40.0],
                        [8.0, 22.0, 30.0, 40.0]])
    persons = np.array([1.0, 3.0, 2.0, 4.0])
    return te.Population(
        weights=weights, persons=persons, children=np.array([0.0, 2.0, 0.0, 2.0]),
        resources=np.array([4_000.0, 28_000.0, 21_000.0, 90_000.0]),
        threshold=np.array([10_000.0, 30_000.0, 20_000.0, 40_000.0]),
        eligible={"everyone": persons, **eligible},
    )


def test_score_counts_cost_people_lifted_and_gap():
    pop = _population()
    out = te.score(pop, 1_000.0 * pop.persons)      # $1,000 a person: only the second unit crosses

    assert out["cost_$M"] == pytest.approx((10 * 1 + 20 * 3 + 30 * 2 + 40 * 4) * 1_000 / 1e6)
    assert out["persons_lifted"] == pytest.approx(20 * 3)
    assert out["children_lifted"] == pytest.approx(20 * 2)
    assert out["persons_lifted_from_deep_poverty"] == pytest.approx(10 * 1)   # 4,000 -> 5,000 = half the line
    # the first unit's $1,000 all fills its gap; the second needed $2,000 of its $3,000
    assert out["poverty_gap_closed_$M"] == pytest.approx((10 * 1_000 + 20 * 2_000) / 1e6)
    assert out["share_of_dollars_closing_gap"] == pytest.approx(50_000 / 290_000)
    assert out["share_of_dollars_to_poor_units"] == pytest.approx(70_000 / 290_000)
    assert out["cost_per_person_lifted_$"] == pytest.approx(290_000 / 60)
    assert out["persons_lifted_per_$M"] == pytest.approx(60 / 0.29)


def test_score_standard_error_is_sdr_over_the_replicates():
    pop = _population()
    out = te.score(pop, 1_000.0 * pop.persons)
    lifted = np.array([20 * 3, 18 * 3, 22 * 3], dtype=float)
    assert out["persons_lifted_se"] == pytest.approx(np.sqrt(4 / 2 * ((lifted[1:] - lifted[0]) ** 2).sum()))


def test_a_payment_to_nobody_scores_zero_without_dividing_by_zero():
    out = te.score(_population(), np.zeros(4))
    assert out["cost_$M"] == 0 and out["persons_lifted_per_$M"] == 0
    assert np.isnan(out["cost_per_person_lifted_$"])


def test_same_budget_spends_the_budget_on_every_group():
    pop = _population(**{k: np.array([1.0, 2.0, 0.0, 2.0]) for k, _ in te.GROUPS if k != "everyone"})
    table = te.same_budget(pop, budgets_m=(1.0, 2.0), income_tests=(None, 1.5))
    assert np.allclose(table["cost_$M"], table["budget_$M"])
    tested = table[table["income_test"] != "none"]
    # under 150% of the line leaves out the fourth unit only
    assert np.allclose(tested.loc[tested["group"] == "everyone", "eligible_persons"], 10 * 1 + 20 * 3 + 30 * 2)
    assert set(table["rank_by_gap_closed"]) == set(range(1, len(table) // 2 + 1))


def test_nearest_the_line_funds_the_cheapest_people_first_and_stays_in_budget():
    pop = _population()
    # per person the second unit's gap is 667, the first's 6,000; its whole gap costs 20 * 2,000
    pay = te.nearest_the_line(pop, 45_000.0)
    assert pay[1] == pytest.approx(2_000.01) and pay[0] == 0 and pay[2] == 0 and pay[3] == 0
    assert te.score(pop, pay)["persons_lifted"] == pytest.approx(60)
    everyone = te.nearest_the_line(pop, 1e9)
    assert te.score(pop, everyone)["poverty_rate_after"] == 0


def test_eligible_counts_reads_ages_from_persons_and_tenure_from_the_frame():
    frame = pd.DataFrame({"spm_unit_id": ["a", "b", "c"], "n_persons": [3, 2, 1], "n_children": [2, 0, 0],
                          "tenure": ["renter", "owner_with_mortgage", "renter"],
                          "n_adults": [1, 2, 1], "snap_amount": [1_200.0, 0.0, 0.0],
                          "eitc_amount": [3_000.0, 0.0, np.nan]})
    persons = pd.DataFrame({"spm_unit_id": ["a", "a", "a", "b", "b", "c", None],
                            "SERIALNO": ["1", "1", "1", "2", "2", "3", "4"],
                            "AGEP": [30, 4, 9, 70, 40, 25, 50]})
    got = te.eligible_counts(frame, persons)

    assert got["children_under_6"].tolist() == [1, 0, 0]
    assert got["children_6_17"].tolist() == [1, 0, 0]
    assert got["single_parent_children"].tolist() == [2, 0, 0]
    assert got["adults_65_plus"].tolist() == [0, 1, 0]
    assert got["adults_no_children"].tolist() == [0, 1, 1]
    assert got["renters"].tolist() == [3, 0, 1]
    assert got["snap"].tolist() == [3, 0, 0]
    assert got["eitc"].tolist() == [3, 0, 0]
    assert set(got) == {k for k, _ in te.GROUPS}

