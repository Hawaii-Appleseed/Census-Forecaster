"""Take-up as a probability that differs between units, with its level still set by an administrative total.

The credit steps treat take-up one of two ways: rank the eligible units and cut at an administrative count
(:mod:`.takeup_imputation`), or one factor per credit on every eligible unit
(``chain/forecast_working_family_credits.py``). Either fixes *how many* claim from a source that can be trusted
for it and says nothing measured about *who*. This module separates the two:

* **shape**: a :class:`PropensityModel`, the log-odds of claiming as a sum of coefficients on :class:`Term` s
  (indicators and bands of columns the tax units carry). The coefficients come from wherever there is evidence on
  who claims: a survey with a "did you claim" question (:func:`fit_from_survey`) or an administrative table of
  claims by class (:func:`fit_from_margins`).
* **level**: :func:`claim_probabilities` shifts the intercept until the weighted total (dollars or claims) equals
  the administrative target, exactly as the uniform factor does. With no coefficients it *is* the uniform factor.

A survey nobody sampled at random is only usable for the shape, and only if its respondents resemble the
population once they are weighted to it. Two functions are for that: :func:`pseudo_weights` rakes the survey to
population totals on the columns both have (:mod:`.bounded_raking`), and :func:`known_answer_test` has the survey
predict something the population frame already records (SNAP receipt, tenure, health coverage) and reports how far
off it is. A survey that fails the known-answer test after weighting is selected on things the weights cannot
see, and its coefficients for an unobserved outcome should not be used.

Nothing here is on the published chain: the functions are called by ``analyses/takeup_propensity_sensitivity.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares

from tax_modeler.errors import ConfigError, DataValidationError


@dataclass(frozen=True)
class Term:
    """One column of the design matrix, computed from one frame column.

    ``kind`` ``"band"``: 1 where ``lo <= column < hi`` (either end may be None for open). ``"isin"``: 1 where the
    column is one of ``values``. ``"linear"``: ``(column - center) / scale``, clipped to ``[lo, hi]`` first when
    they are given. Missing values give 0.
    """

    name: str
    column: str
    kind: str = "band"
    lo: float | None = None
    hi: float | None = None
    values: tuple = ()
    center: float = 0.0
    scale: float = 1.0

    def evaluate(self, frame: pd.DataFrame) -> np.ndarray:
        if self.column not in frame.columns:
            raise DataValidationError(f"term {self.name!r} needs column {self.column!r}")
        col = frame[self.column]
        if self.kind == "isin":
            return col.isin(self.values).to_numpy(dtype=float)
        x = pd.to_numeric(col, errors="coerce").to_numpy(dtype=float)
        if self.kind == "band":
            lo = -np.inf if self.lo is None else self.lo
            hi = np.inf if self.hi is None else self.hi
            return ((x >= lo) & (x < hi)).astype(float)
        if self.kind == "linear":
            if self.lo is not None or self.hi is not None:
                x = np.clip(x, self.lo, self.hi)
            return np.nan_to_num((x - self.center) / self.scale, nan=0.0)
        raise ConfigError(f"unknown term kind {self.kind!r}", available=["band", "isin", "linear"])


def design_matrix(frame: pd.DataFrame, terms: Sequence[Term]) -> np.ndarray:
    """(n rows, n terms) matrix of the terms' values on ``frame``."""
    if not terms:
        return np.zeros((len(frame), 0))
    return np.column_stack([t.evaluate(frame) for t in terms])


@dataclass(frozen=True)
class PropensityModel:
    """Log-odds of claiming = ``intercept`` + sum of ``coefficients[term.name]`` x the term.

    ``source`` says where the coefficients came from; ``standard_errors`` are the fit's, where it has them.
    The intercept is the fit's own and is replaced by :func:`claim_probabilities`, which sets the level.
    """

    terms: tuple[Term, ...] = ()
    coefficients: Mapping[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    source: str = "uniform"
    standard_errors: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [t.name for t in self.terms]
        missing = [n for n in names if n not in self.coefficients]
        if missing or len(set(names)) != len(names):
            raise ConfigError(f"every term needs one coefficient; missing or duplicated: {missing or names}")

    @property
    def beta(self) -> np.ndarray:
        return np.array([self.coefficients[t.name] for t in self.terms], dtype=float)

    def linear_predictor(self, frame: pd.DataFrame) -> np.ndarray:
        """The log-odds without the intercept."""
        return design_matrix(frame, self.terms) @ self.beta

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """Probabilities at the model's own intercept (the survey's level, not an administrative one)."""
        return _expit(self.intercept + self.linear_predictor(frame))

    def to_json(self, path: Path) -> None:
        payload = {"source": self.source, "intercept": self.intercept,
                   "terms": [{**{k: v for k, v in vars(t).items() if k != "values"}, "values": list(t.values)}
                             for t in self.terms],
                   "coefficients": dict(self.coefficients), "standard_errors": dict(self.standard_errors)}
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")

    @classmethod
    def from_json(cls, path: Path) -> PropensityModel:
        d = json.loads(Path(path).read_text())
        terms = tuple(Term(**{**t, "values": tuple(t.get("values", ()))}) for t in d["terms"])
        return cls(terms=terms, coefficients=d["coefficients"], intercept=d.get("intercept", 0.0),
                   source=d.get("source", str(path)), standard_errors=d.get("standard_errors", {}))


def _expit(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.tanh(0.5 * np.asarray(x, dtype=float)))


@dataclass(frozen=True)
class ClaimProbabilities:
    """``prob``: each unit's probability of claiming (0 where not eligible). ``scale``: factor on the dollars of
    those who claim, 1 unless the target is more than everyone eligible claiming would give (then ``prob`` is 1 and
    the dollars are scaled, which is what a uniform factor above one means). ``shift``: the intercept that meets the
    target. ``uniform_factor``: target over the eligible total, the factor a uniform take-up would use."""

    prob: np.ndarray
    scale: float
    shift: float
    uniform_factor: float

    @property
    def expected_factor(self) -> np.ndarray:
        """Per-unit factor on the eligible amount whose weighted total is the target (``prob x scale``)."""
        return self.prob * self.scale


def claim_probabilities(model: PropensityModel, frame: pd.DataFrame, *, weighted_amount: np.ndarray,
                        target: float) -> ClaimProbabilities:
    """Probabilities with the model's shape whose total meets ``target``.

    ``weighted_amount``: what each unit adds to the total if it claims (weight x eligible dollars to meet a dollar
    target, weight x eligibility indicator to meet a count); zero marks a unit as not eligible. The intercept is
    moved until ``sum(weighted_amount x prob) == target``. A model with no terms gives every eligible unit
    ``target / sum(weighted_amount)``, the uniform factor, computed as that ratio.
    """
    a = np.asarray(weighted_amount, dtype=float)
    if (a < 0).any() or not np.isfinite(a).all():
        raise DataValidationError("weighted_amount must be finite and non-negative")
    total = float(a.sum())
    if total <= 0 or target <= 0:
        raise DataValidationError(f"nothing to calibrate: eligible total {total}, target {target}")
    eligible = a > 0
    uniform = target / total
    if uniform >= 1.0:
        return ClaimProbabilities(np.where(eligible, 1.0, 0.0), uniform, np.inf, uniform)
    eta = model.linear_predictor(frame)
    if not model.terms or np.ptp(eta[eligible]) == 0.0:
        return ClaimProbabilities(np.where(eligible, uniform, 0.0), 1.0, float(np.log(uniform / (1 - uniform))),
                                  uniform)
    ae, ee = a[eligible], eta[eligible]

    def miss(shift: float) -> float:
        return float((ae * _expit(shift + ee)).sum()) / target - 1.0

    lo, hi = -40.0 - float(ee.max()), 40.0 - float(ee.min())
    shift = brentq(miss, lo, hi, xtol=1e-13, rtol=1e-14, maxiter=500)
    prob = np.zeros(len(a))
    prob[eligible] = _expit(shift + ee)
    return ClaimProbabilities(prob, 1.0, float(shift), uniform)


def probabilities_at(model: PropensityModel, frame: pd.DataFrame, *, shift: float, eligible: np.ndarray) -> np.ndarray:
    """The model's probabilities on another frame at an intercept already found (:attr:`ClaimProbabilities.shift`):
    take-up fitted in the calibration year and held for the years scored."""
    return np.where(np.asarray(eligible, dtype=bool), _expit(shift + model.linear_predictor(frame)), 0.0)


def _fit_logit(X: np.ndarray, y: np.ndarray, w: np.ndarray, ridge: float) -> tuple[float, np.ndarray, np.ndarray]:
    """Weighted logistic regression by Newton's method; ``ridge`` penalises the coefficients (not the intercept).
    Returns intercept, coefficients and the coefficients' standard errors (sandwich, weights as given)."""
    n, k = X.shape
    Z = np.column_stack([np.ones(n), X])
    pen = np.diag([0.0] + [ridge] * k)
    b = np.zeros(k + 1)
    ybar = float((w * y).sum() / w.sum())
    b[0] = np.log(ybar / (1 - ybar))
    for _ in range(200):
        p = _expit(Z @ b)
        grad = Z.T @ (w * (y - p)) - pen @ b
        H = (Z * (w * p * (1 - p))[:, None]).T @ Z + pen
        step = np.linalg.solve(H, grad)
        b = b + step
        if np.abs(step).max() < 1e-10:
            break
    else:
        raise DataValidationError("the logit fit did not converge; the outcome may be separable: raise ridge")
    p = _expit(Z @ b)
    H = (Z * (w * p * (1 - p))[:, None]).T @ Z + pen
    score = Z * (w * (y - p))[:, None]
    Hinv = np.linalg.inv(H)
    cov = Hinv @ (score.T @ score) @ Hinv
    return float(b[0]), b[1:], np.sqrt(np.diag(cov))[1:]


def fit_from_survey(survey: pd.DataFrame, *, outcome: str, terms: Sequence[Term], weight: str | None = None,
                    ridge: float = 1.0, source: str = "survey") -> PropensityModel:
    """Fit the coefficients on survey respondents: ``outcome`` (0/1, e.g. claimed the credit) on ``terms``.

    ``weight``: a column of weights, normally :func:`pseudo_weights`' (scaled here to sum to the number of
    respondents, so ``ridge`` means the same whatever their scale). ``ridge``: a penalty that shrinks coefficients
    toward zero, i.e. toward uniform take-up; the default is a weak prior that matters only where a term has few
    respondents. Standard errors are sandwich errors: they ignore that the weights were themselves fitted and that
    respondents chose to answer, so they understate the uncertainty.
    """
    if outcome not in survey.columns:
        raise DataValidationError(f"survey has no outcome column {outcome!r}")
    y = pd.to_numeric(survey[outcome], errors="coerce").to_numpy(dtype=float)
    keep = np.isfinite(y)
    if not np.isin(y[keep], (0.0, 1.0)).all():
        raise DataValidationError(f"outcome {outcome!r} must be 0/1")
    w = np.ones(len(survey)) if weight is None else survey[weight].to_numpy(dtype=float)
    X = design_matrix(survey, terms)
    X, y, w = X[keep], y[keep], w[keep]
    if len(y) < 5 * (len(terms) + 1) or y.min() == y.max():
        raise DataValidationError(f"{len(y)} usable respondents cannot support {len(terms)} terms")
    w = w * len(w) / w.sum()
    intercept, beta, se = _fit_logit(X, y, w, ridge)
    names = [t.name for t in terms]
    return PropensityModel(terms=tuple(terms), coefficients=dict(zip(names, beta.tolist(), strict=True)),
                           intercept=intercept, source=source,
                           standard_errors=dict(zip(names, se.tolist(), strict=True)))


def fit_from_margins(frame: pd.DataFrame, *, terms: Sequence[Term], eligible_weight: np.ndarray,
                     margins: Sequence[tuple[np.ndarray, float]], ridge: float = 1e-4,
                     source: str = "administrative margins") -> tuple[PropensityModel, pd.DataFrame]:
    """Fit the coefficients to administrative claim counts by class.

    ``eligible_weight``: each unit's weight if eligible, else 0. ``margins``: ``(mask, target)`` pairs, the
    administrative number of claims among the units in ``mask``. Finds the intercept and coefficients whose
    probabilities give ``sum(eligible_weight[mask] x prob[mask])`` nearest each target (least squares on the
    relative misses, with a small ``ridge`` so that redundant terms are settled). Returns the model and a table of
    target against fitted value. A class where even everyone claiming falls short of its target is a shortfall of
    eligible units, not of take-up, and shows as a miss.
    """
    X = design_matrix(frame, terms)
    w = np.asarray(eligible_weight, dtype=float)
    masks = [np.asarray(m, dtype=bool) for m, _ in margins]
    t = np.array([v for _, v in margins], dtype=float)
    if (t <= 0).any():
        raise DataValidationError("every margin needs a positive target")
    Z = np.column_stack([np.ones(len(w)), X])
    sq = np.sqrt(ridge)

    def value(b):
        p = _expit(Z @ b)
        return np.array([(w[m] * p[m]).sum() for m in masks])

    def resid(b):
        return np.concatenate([value(b) / t - 1.0, sq * b[1:]])

    start = np.zeros(Z.shape[1])
    start[0] = np.log(min(max(t.sum() / max(sum(w[m].sum() for m in masks), 1.0), 0.05), 0.95) / 0.5)
    sol = least_squares(resid, start, xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=2000)
    names = [t_.name for t_ in terms]
    model = PropensityModel(terms=tuple(terms), coefficients=dict(zip(names, sol.x[1:].tolist(), strict=True)),
                            intercept=float(sol.x[0]), source=source)
    fitted = value(sol.x)
    table = pd.DataFrame({"target": t, "fitted": fitted, "relative_miss": fitted / t - 1.0,
                          "eligible": [float(w[m].sum()) for m in masks]})
    return model, table


def pseudo_weights(survey: pd.DataFrame, population: pd.DataFrame, *, terms: Sequence[Term],
                   population_weight: str = "weight", bounds: tuple[float, float] = (0.1, 10.0)) -> pd.Series:
    """Weights that make the survey's respondents add up to the population on ``terms``.

    Each term's weighted population total (and the population count) becomes a target the respondents' weights
    must meet, within ``bounds`` of equal weights (:func:`.bounded_raking.calibrate`). This corrects for who
    answered only as far as ``terms`` describe it. Raises when the targets cannot be met inside the bounds: the
    survey has too few respondents of some kind for the weights to stand in for them.
    """
    from .bounded_raking import calibrate

    pw = population[population_weight].to_numpy(dtype=float)
    Xs = np.column_stack([np.ones(len(survey)), design_matrix(survey, terms)])
    Xp = np.column_stack([np.ones(len(population)), design_matrix(population, terms)])
    targets = pw @ Xp
    names = ["count", *[t.name for t in terms]]
    w0 = np.full(len(survey), pw.sum() / len(survey))
    res = calibrate(w0, Xs, targets, names=names, bounds=bounds)
    if res.dropped:
        raise DataValidationError(f"the survey has no respondent for {res.dropped}, which the population has")
    if not res.exact:
        bad = res.table.loc[res.table["relative_miss"].abs() > res.tol, ["constraint", "relative_miss"]]
        raise DataValidationError(
            "the survey cannot be weighted to the population on these terms within "
            f"{bounds}: {bad.to_dict('records')} (screened: {res.screened})")
    return pd.Series(w0 * res.ratios, index=survey.index, name="pseudo_weight")


@dataclass(frozen=True)
class KnownAnswer:
    """``table``: one row per check group with the population's recorded rate, the rate the survey predicts for the
    same units and the gap. ``overall_gap``, ``max_group_gap`` (percentage points / 100). ``passed``: both within
    the tolerances given."""

    table: pd.DataFrame
    overall_gap: float
    max_group_gap: float
    passed: bool


def known_answer_test(survey: pd.DataFrame, population: pd.DataFrame, *, outcome: str, terms: Sequence[Term],
                      groups: Sequence[Term], survey_weight: str | None = None, population_weight: str = "weight",
                      ridge: float = 1.0, overall_tolerance: float = 0.03, group_tolerance: float = 0.06,
                      min_group_share: float = 0.02) -> KnownAnswer:
    """Have the survey predict an outcome the population frame records, and compare.

    ``outcome`` is a 0/1 column present in both frames with the same meaning (asked the way the ACS asks it).
    The survey fits ``outcome`` on ``terms`` (:func:`fit_from_survey`, at the survey's own level: here the level is
    part of the test), the fit is applied to the population, and the predicted rate is set against the recorded
    one overall and inside each of ``groups``. Use groups that are *not* among the terms: agreement inside the
    terms' own cells is built in. Groups under ``min_group_share`` of the population weight are reported and not
    counted toward the verdict. The tolerances are in rate units (0.03 = three percentage points).
    """
    if outcome not in population.columns:
        raise DataValidationError(f"population has no column {outcome!r} to test against")
    model = fit_from_survey(survey, outcome=outcome, terms=terms, weight=survey_weight, ridge=ridge,
                            source="known-answer test")
    pred = model.predict(population)
    truth = pd.to_numeric(population[outcome], errors="coerce").to_numpy(dtype=float)
    w = population[population_weight].to_numpy(dtype=float) * np.isfinite(truth)
    truth = np.nan_to_num(truth)
    rows = [{"group": "all", "share": 1.0, "recorded": float((w * truth).sum() / w.sum()),
             "predicted": float((w * pred).sum() / w.sum())}]
    for g in groups:
        m = g.evaluate(population) > 0
        if w[m].sum() <= 0:
            continue
        rows.append({"group": g.name, "share": float(w[m].sum() / w.sum()),
                     "recorded": float((w[m] * truth[m]).sum() / w[m].sum()),
                     "predicted": float((w[m] * pred[m]).sum() / w[m].sum())})
    table = pd.DataFrame(rows)
    table["gap"] = table["predicted"] - table["recorded"]
    counted = table[(table["group"] != "all") & (table["share"] >= min_group_share)]
    overall = float(abs(table.loc[0, "gap"]))
    worst = float(counted["gap"].abs().max()) if len(counted) else 0.0
    return KnownAnswer(table, overall, worst, overall <= overall_tolerance and worst <= group_tolerance)


__all__ = ["Term", "PropensityModel", "ClaimProbabilities", "KnownAnswer", "design_matrix", "claim_probabilities",
           "probabilities_at",
           "fit_from_survey", "fit_from_margins", "pseudo_weights", "known_answer_test"]
