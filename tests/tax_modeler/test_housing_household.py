"""``benefits.housing``: one value per renter household, capped at the housing part of its SPM line, and take-up as
a probability the poverty draws draw from (November 25, 2026)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.benefits.housing import compute_housing_for_units, housing_takeup
from tax_modeler.poverty.thresholds import renter_housing_portion


def _units():
    # household A: a single renter with no income and a relative filing alone, also with none;
    # household B: a renter couple with two children and $20,000; household C: owners with no income.
    return pd.DataFrame({
        "hh_id": ["A", "A", "B", "C"],
        "filing_status": ["single", "single", "married_filing_jointly", "single"],
        "num_dependents": [0, 0, 2, 0],
        "income": [0.0, 0.0, 20_000.0, 0.0],
        "tenure": ["renter", "renter", "renter", "owner_no_mortgage"],
        "county": ["Honolulu"] * 4,
        "weight": [100.0, 100.0, 50.0, 80.0],
    })


def test_one_value_per_household_capped_at_the_lines_housing_part():
    out = compute_housing_for_units(_units(), tax_year=2025)
    v = out["housing_subsidy_amount"].to_numpy()
    cap_a = renter_housing_portion(2025, n_adults=2, n_children=0, area="honolulu")
    assert v[0] == pytest.approx(cap_a)          # household A: both members' line, no income, under the $30,000 FMR
    assert v[0] < 30_000
    assert v[1] == 0.0                            # the relative's tax unit carries nothing: one subsidy per household
    cap_b = renter_housing_portion(2025, n_adults=2, n_children=2, area="honolulu")
    assert v[2] == pytest.approx(min(30_000.0, cap_b) - 0.30 * 20_000)
    assert v[3] == 0.0                            # owners are not eligible
    assert out["housing_eligible"].tolist() == [True, False, True, False]


def test_takeup_is_one_probability_that_meets_the_caseload():
    out = housing_takeup(compute_housing_for_units(_units(), tax_year=2025), target_households=75.0)
    p = out["housing_takeup_prob"].to_numpy()
    assert p[0] == pytest.approx(0.5) and p[2] == pytest.approx(0.5)      # 75 of the 150 eligible households' weight
    assert p[1] == 0.0 and p[3] == 0.0
    np.testing.assert_allclose(out["housing_subsidy_amount"], out["housing_value"] * p)


def test_housing_draws_average_to_the_expected_value_and_split_the_household():
    """A household whose expected subsidy puts it just over the line is poor in about the draws it does not take
    up, instead of never."""
    from tax_modeler.poverty.draws import DrawSpec, poverty_draws

    df = pd.DataFrame({"spm_unit_id": ["a"], "spm_resources": [10_000.0 + 0.4 * 15_000.0],
                       "housing_value": [15_000.0], "housing_takeup_prob": [0.4]})
    res = poverty_draws(DrawSpec(blocks=(), n_draws=4_000), df, np.array([12_000.0]), {"persons": np.ones(1)})
    assert res.poor_prob["baseline"][0] == pytest.approx(0.6, abs=0.03)    # poor whenever it does not take up
