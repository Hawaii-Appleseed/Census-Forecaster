"""Take-up propensity (:mod:`tax_modeler.calibration.takeup_propensity`): shape from a survey or administrative
margins, level from an administrative total, and the known-answer test that says whether a survey can be used."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration.takeup_propensity import (
    PropensityModel,
    Term,
    claim_probabilities,
    design_matrix,
    fit_from_margins,
    fit_from_survey,
    known_answer_test,
    pseudo_weights,
)
from tax_modeler.errors import ConfigError, DataValidationError

LOW = Term("low_income", "income", lo=None, hi=20_000)
KIDS = Term("has_kids", "kids", lo=1, hi=None)
RENT = Term("renter", "tenure", kind="isin", values=("rent",))
TERMS = (LOW, KIDS)


def _population(n=40_000, seed=3):
    rng = np.random.default_rng(seed)
    income = rng.lognormal(10.3, 0.8, n)
    kids = rng.integers(0, 4, n)
    tenure = np.where(rng.random(n) < 0.35 + 0.3 * (income < 20_000), "rent", "own")
    # SNAP receipt depends on income, kids and tenure: tenure is the part the terms below do not carry.
    snap = rng.random(n) < 1 / (1 + np.exp(-(-2.2 + 1.8 * (income < 20_000) + 0.5 * (kids > 0) + 0.6 * (tenure == "rent"))))
    # Claiming depends on income and kids.
    claim = rng.random(n) < 1 / (1 + np.exp(-(1.2 - 1.0 * (income < 20_000) + 0.7 * (kids > 0))))
    return pd.DataFrame({"income": income, "kids": kids, "tenure": tenure, "snap": snap.astype(float),
                         "claimed": claim.astype(float), "weight": rng.uniform(20, 60, n),
                         "credit": rng.uniform(100, 900, n)})


def test_terms_evaluate_bands_sets_and_clipped_linear():
    f = pd.DataFrame({"x": [5.0, 10.0, 20.0, np.nan], "s": ["a", "b", "c", "a"]})
    np.testing.assert_array_equal(Term("b", "x", lo=10, hi=20).evaluate(f), [0, 1, 0, 0])
    np.testing.assert_array_equal(Term("s", "s", kind="isin", values=("a", "c")).evaluate(f), [1, 0, 1, 1])
    np.testing.assert_allclose(Term("l", "x", kind="linear", lo=0, hi=10, center=5, scale=5).evaluate(f), [0, 1, 1, 0])
    assert design_matrix(f, ()).shape == (4, 0)
    with pytest.raises(DataValidationError):
        Term("m", "missing").evaluate(f)
    with pytest.raises(ConfigError):
        PropensityModel(terms=(LOW,), coefficients={})


def test_no_terms_is_the_uniform_factor_exactly():
    pop = _population(2_000)
    a = (pop["weight"] * pop["credit"]).to_numpy().copy()
    a[:300] = 0.0                                                 # not eligible
    target = 0.74 * a.sum()
    cp = claim_probabilities(PropensityModel(), pop, weighted_amount=a, target=target)
    assert cp.scale == 1.0 and cp.uniform_factor == target / a.sum()
    assert (cp.prob[:300] == 0).all() and (cp.prob[300:] == target / a.sum()).all()


def test_the_level_meets_the_target_and_the_shape_follows_the_coefficients():
    pop = _population(5_000)
    a = (pop["weight"] * pop["credit"]).to_numpy()
    model = PropensityModel(terms=TERMS, coefficients={"low_income": -1.0, "has_kids": 0.7})
    cp = claim_probabilities(model, pop, weighted_amount=a, target=0.74 * a.sum())
    np.testing.assert_allclose((a * cp.prob).sum(), 0.74 * a.sum(), rtol=1e-10)
    assert ((cp.prob > 0) & (cp.prob < 1)).all()
    low, kids = (pop["income"] < 20_000).to_numpy(), (pop["kids"] > 0).to_numpy()
    cell = lambda m: cp.prob[m][0]                                # noqa: E731 - one value per cell
    odds = lambda p: p / (1 - p)                                  # noqa: E731
    np.testing.assert_allclose(np.log(odds(cell(low & kids)) / odds(cell(~low & kids))), -1.0, atol=1e-9)
    np.testing.assert_allclose(np.log(odds(cell(low & kids)) / odds(cell(low & ~kids))), 0.7, atol=1e-9)


def test_a_target_above_everyone_claiming_scales_the_dollars():
    pop = _population(1_000)
    a = (pop["weight"] * pop["credit"]).to_numpy()
    model = PropensityModel(terms=TERMS, coefficients={"low_income": -1.0, "has_kids": 0.7})
    cp = claim_probabilities(model, pop, weighted_amount=a, target=1.05 * a.sum())
    assert (cp.prob == 1).all()
    np.testing.assert_allclose(cp.scale, 1.05)
    np.testing.assert_allclose((a * cp.expected_factor).sum(), 1.05 * a.sum())
    with pytest.raises(DataValidationError):
        claim_probabilities(model, pop, weighted_amount=np.zeros(len(pop)), target=1.0)


def test_a_random_sample_survey_recovers_the_coefficients_and_round_trips(tmp_path):
    pop = _population()
    survey = pop.sample(6_000, random_state=1)
    model = fit_from_survey(survey, outcome="claimed", terms=TERMS, ridge=0.0)
    assert abs(model.coefficients["low_income"] - (-1.0)) < 3 * model.standard_errors["low_income"] + 0.05
    assert abs(model.coefficients["has_kids"] - 0.7) < 3 * model.standard_errors["has_kids"] + 0.05
    assert abs(model.intercept - 1.2) < 0.2
    path = tmp_path / "model.json"
    model.to_json(path)
    back = PropensityModel.from_json(path)
    assert back.terms == model.terms and dict(back.coefficients) == dict(model.coefficients)
    np.testing.assert_array_equal(back.predict(pop), model.predict(pop))


def test_ridge_shrinks_toward_uniform_and_too_few_respondents_are_refused():
    pop = _population()
    survey = pop.sample(400, random_state=2)
    free = fit_from_survey(survey, outcome="claimed", terms=TERMS, ridge=0.0)
    tight = fit_from_survey(survey, outcome="claimed", terms=TERMS, ridge=500.0)
    assert abs(tight.coefficients["low_income"]) < 0.25 * abs(free.coefficients["low_income"])
    with pytest.raises(DataValidationError):
        fit_from_survey(pop.sample(10, random_state=0), outcome="claimed", terms=TERMS)
    with pytest.raises(DataValidationError):
        fit_from_survey(survey.assign(claimed=2.0), outcome="claimed", terms=TERMS)


def test_margins_fit_reproduces_administrative_claims_by_class():
    pop = _population(20_000)
    w = pop["weight"].to_numpy()
    low, kids = (pop["income"] < 20_000).to_numpy(), (pop["kids"] > 0).to_numpy()
    true_p = 1 / (1 + np.exp(-(0.8 - 1.0 * low + 0.7 * kids)))
    margins = [(m, float((w * true_p)[m].sum())) for m in (low, ~low, kids, ~kids)]
    model, table = fit_from_margins(pop, terms=TERMS, eligible_weight=w, margins=margins)
    assert table["relative_miss"].abs().max() < 1e-3
    assert abs(model.coefficients["low_income"] + 1.0) < 0.02 and abs(model.coefficients["has_kids"] - 0.7) < 0.02


def test_pseudo_weights_put_a_lopsided_survey_back_on_the_population():
    pop = _population()
    low = (pop["income"] < 20_000).to_numpy()
    rng = np.random.default_rng(5)
    survey = pop[rng.random(len(pop)) < np.where(low, 0.20, 0.04)]          # low incomes answer five times as often
    pw = pseudo_weights(survey, pop, terms=TERMS)
    np.testing.assert_allclose(pw.sum(), pop["weight"].sum(), rtol=1e-6)
    np.testing.assert_allclose(pw[(survey["income"] < 20_000)].sum(), pop.loc[low, "weight"].sum(), rtol=1e-6)
    # Respondents the population has none of cannot be weighted into it.
    with pytest.raises(DataValidationError):
        pseudo_weights(survey[survey["kids"] == 0], pop, terms=TERMS)


def test_known_answer_passes_a_survey_selected_on_what_the_weights_see():
    pop = _population()
    low = (pop["income"] < 20_000).to_numpy()
    rng = np.random.default_rng(6)
    survey = pop[rng.random(len(pop)) < np.where(low, 0.20, 0.04)].copy()
    survey["pw"] = pseudo_weights(survey, pop, terms=TERMS)
    res = known_answer_test(survey, pop, outcome="snap", terms=(LOW, KIDS, RENT), groups=(KIDS, RENT),
                            survey_weight="pw")
    assert res.passed, res.table
    assert res.overall_gap < 0.02


def test_known_answer_fails_a_survey_selected_on_the_outcome():
    """A survey handed out where SNAP recipients are (a food bank) overstates receipt at every income, and
    weighting on income and children does not undo it."""
    pop = _population()
    rng = np.random.default_rng(7)
    survey = pop[rng.random(len(pop)) < np.where(pop["snap"] > 0, 0.25, 0.04)].copy()
    survey["pw"] = pseudo_weights(survey, pop, terms=TERMS)
    res = known_answer_test(survey, pop, outcome="snap", terms=(LOW, KIDS, RENT), groups=(KIDS, RENT),
                            survey_weight="pw")
    assert not res.passed
    assert res.table.loc[0, "predicted"] > res.table.loc[0, "recorded"] + 0.10
