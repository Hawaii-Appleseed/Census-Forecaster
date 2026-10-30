"""Tests for the generic successive-difference-replication SDR engine."""
from __future__ import annotations

import math

import numpy as np
import pytest
from tax_modeler.uncertainty import (
    ACS_REPLICATES,
    ACS_SDR_FACTOR,
    Z_90,
    SDREstimate,
    estimate_ratio,
    estimate_total,
    sdr_se,
    sdr_variance,
    summarize,
    weighted_ratio_replicates,
    weighted_total_replicates,
)


def test_acs_constants():
    assert ACS_REPLICATES == 80
    assert ACS_SDR_FACTOR == pytest.approx(4.0 / 80)
    assert Z_90 == 1.645


def test_sdr_variance_default_factor_is_four_over_r():
    # 80 replicates each 1 unit above the point: variance = (4/80)*80*1 = 4.
    reps = [11.0] * 80
    assert sdr_variance(10.0, reps) == pytest.approx(4.0)
    assert sdr_se(10.0, reps) == pytest.approx(2.0)


def test_sdr_variance_explicit_factor_overrides_default():
    reps = [1.0, -1.0]  # squared deviations from 0 sum to 2
    # default factor 4/2 = 2 -> 4.0; explicit factor 1.0 -> 2.0
    assert sdr_variance(0.0, reps) == pytest.approx(4.0)
    assert sdr_variance(0.0, reps, factor=1.0) == pytest.approx(2.0)


def test_sdr_variance_requires_1d():
    with pytest.raises(ValueError):
        sdr_variance(0.0, np.zeros((2, 2)))


def test_resolve_factor_rejects_empty():
    with pytest.raises(ValueError):
        sdr_variance(0.0, [])


def test_summarize_ci_is_point_plus_minus_z_se():
    reps = [11.0] * 80  # se = 2.0
    est = summarize(10.0, reps)
    assert isinstance(est, SDREstimate)
    assert est.point == pytest.approx(10.0)
    assert est.se == pytest.approx(2.0)
    assert est.ci_low == pytest.approx(10.0 - 1.645 * 2.0)
    assert est.ci_high == pytest.approx(10.0 + 1.645 * 2.0)
    assert est.moe == pytest.approx(1.645 * 2.0)
    assert est.cv == pytest.approx(2.0 / 10.0)


def test_sdrestimate_cv_nan_when_point_zero():
    est = SDREstimate(point=0.0, se=1.0, ci_low=-1.0, ci_high=1.0)
    assert math.isnan(est.cv)


def test_sdrestimate_as_dict_keys():
    est = SDREstimate(point=1.0, se=0.5, ci_low=0.0, ci_high=2.0)
    d = est.as_dict("metric")
    assert d == {
        "metric": 1.0,
        "metric_se": 0.5,
        "metric_ci90_low": 0.0,
        "metric_ci90_high": 2.0,
    }


def test_weighted_total_replicates_column_zero_is_point():
    values = np.array([1.0, 2.0, 3.0])
    # column 0 = full-sample weight; 2 replicate columns.
    w = np.array([
        [10.0, 11.0, 9.0],
        [10.0, 10.0, 10.0],
        [10.0, 9.0, 11.0],
    ])
    totals = weighted_total_replicates(values, w)
    assert totals.shape == (3,)
    assert totals[0] == pytest.approx(60.0)  # 1*10+2*10+3*10
    assert totals[1] == pytest.approx(1 * 11 + 2 * 10 + 3 * 9)  # 58
    assert totals[2] == pytest.approx(1 * 9 + 2 * 10 + 3 * 11)  # 62


def test_weighted_ratio_replicates_nan_on_zero_denominator():
    num = np.array([1.0, 1.0])
    den = np.array([0.0, 0.0])  # zero denominator -> nan
    w = np.array([[5.0, 5.0], [5.0, 5.0]])
    ratios = weighted_ratio_replicates(num, den, w)
    assert np.isnan(ratios).all()


def test_estimate_total_known_answer():
    # point total = 20; replicate totals 21 and 19 -> var = (4/2)*(1+1)=4, se=2.
    values = np.array([1.0, 1.0])
    w = np.array([
        [10.0, 11.0, 9.0],
        [10.0, 10.0, 10.0],
    ])
    est = estimate_total(values, w)
    assert est.point == pytest.approx(20.0)
    assert est.se == pytest.approx(2.0)
    assert est.ci_low == pytest.approx(20.0 - 1.645 * 2.0)
    assert est.ci_high == pytest.approx(20.0 + 1.645 * 2.0)


def test_estimate_ratio_known_answer():
    # num=[1,0], den=[1,1]. col0 ratio 0.5; col1 0.25; col2 0.75.
    # var = (4/2)*((0.25-0.5)^2+(0.75-0.5)^2)=2*0.125=0.25 -> se=0.5.
    num = np.array([1.0, 0.0])
    den = np.array([1.0, 1.0])
    w = np.array([
        [10.0, 10.0, 30.0],
        [10.0, 30.0, 10.0],
    ])
    est = estimate_ratio(num, den, w)
    assert est.point == pytest.approx(0.5)
    assert est.se == pytest.approx(0.5)


def test_as_matrix_rejects_too_few_columns():
    with pytest.raises(ValueError):
        # only a point column, no replicate column
        weighted_total_replicates(np.array([1.0]), np.array([[10.0]]))


def test_as_matrix_rejects_row_mismatch():
    with pytest.raises(ValueError):
        weighted_total_replicates(np.array([1.0, 2.0]), np.array([[10.0, 9.0]]))
