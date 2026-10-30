"""Smoke tests for distributional and poverty metrics (Phase 2).

Validates that:

* ``weighted_ntile_labels`` partitions into equal-weight bins.
* ``decile_summary`` produces expected shapes and conservation properties
  (shares sum to 1, totals reconcile to grand total).
* ``gini`` is bounded in [0, 1] and zero on perfect equality.
* ``palma`` / ``atkinson`` are well-defined on the synthetic fixture.
* ``poverty_rate``, ``poverty_depth``, ``poverty_gap`` agree on hand-
  worked cases (single-row examples).
"""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler import (
    atkinson,
    decile_summary,
    gini,
    palma,
    poverty_depth,
    poverty_gap,
    poverty_rate,
    weighted_ntile_labels,
)
from tax_modeler.errors import DataValidationError

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Quantile labels
# ---------------------------------------------------------------------------


def test_decile_labels_partition_population():
    values = np.arange(100)
    weights = np.ones(100)
    labels = weighted_ntile_labels(values, weights, n=10)
    assert set(labels) == {f"D{i}" for i in range(1, 11)}
    counts = labels.value_counts()
    # With uniform weights and 100 rows, each decile has ~10 rows
    assert counts.min() >= 8 and counts.max() <= 12


def test_quintile_labels_with_weights():
    # 4 rows, each weight 25 → quintiles each ~20 weight
    values = np.array([10, 20, 30, 40], dtype=float)
    weights = np.array([25, 25, 25, 25], dtype=float)
    labels = weighted_ntile_labels(values, weights, n=5, label_prefix="Q")
    assert set(labels).issubset({f"Q{i}" for i in range(1, 6)})


def test_ntile_labels_validate():
    with pytest.raises(DataValidationError):
        weighted_ntile_labels([1, 2, 3], [1, 1, 1], n=1)
    with pytest.raises(DataValidationError):
        weighted_ntile_labels([1, 2, 3], [1, -1, 1], n=2)


# ---------------------------------------------------------------------------
# Decile summary
# ---------------------------------------------------------------------------


def test_decile_summary_shares_sum_to_one():
    rng = np.random.default_rng(42)
    v = rng.exponential(50_000, size=500)
    w = np.ones_like(v)
    d = decile_summary(v, w)
    assert len(d) == 10
    assert d["share"].sum() == pytest.approx(1.0, abs=1e-6)


def test_decile_summary_total_reconciles():
    v = np.arange(1000, dtype=float)
    w = np.ones_like(v)
    d = decile_summary(v, w)
    assert d["total"].sum() == pytest.approx(v.sum(), rel=1e-6)


def test_decile_summary_extra_columns():
    v = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], dtype=float)
    w = np.ones_like(v)
    d = decile_summary(v, w, extra={"tax_paid": v * 0.1})
    assert "tax_paid" in d.columns
    # Tax-paid total reconciles
    assert d["tax_paid"].sum() == pytest.approx((v * 0.1).sum(), rel=1e-6)


# ---------------------------------------------------------------------------
# Inequality indices
# ---------------------------------------------------------------------------


def test_gini_zero_on_equality():
    v = np.full(100, 1000.0)
    w = np.ones(100)
    assert gini(v, w) == pytest.approx(0.0, abs=1e-6)


def test_gini_in_unit_interval_on_random():
    rng = np.random.default_rng(0)
    v = rng.lognormal(mean=10, sigma=0.8, size=1000)
    w = np.ones_like(v)
    g = gini(v, w)
    assert 0.0 < g < 1.0


def test_gini_rejects_negative_values():
    with pytest.raises(DataValidationError):
        gini([-1, 1, 2], [1, 1, 1])


def test_palma_on_synthetic_fixture(taxed_units):
    p = palma(taxed_units["income"], taxed_units["weight"])
    # Hawaii inequality is real but not extreme — palma typically 1-3
    assert 0.0 < p < 100.0


def test_atkinson_zero_on_equality():
    v = np.full(50, 5000.0)
    w = np.ones(50)
    assert atkinson(v, w, eps=0.5) == pytest.approx(0.0, abs=1e-9)


def test_atkinson_eps_one_path():
    rng = np.random.default_rng(7)
    v = rng.lognormal(mean=10, sigma=0.5, size=200)
    w = np.ones_like(v)
    a = atkinson(v, w, eps=1.0)
    assert 0.0 < a < 1.0


# ---------------------------------------------------------------------------
# Poverty metrics
# ---------------------------------------------------------------------------


def test_poverty_rate_handworked():
    # 4 units of weight 1 each; 2 below threshold of 100 → rate = 0.5
    res = np.array([50, 80, 120, 200], dtype=float)
    thr = 100.0
    w = np.ones_like(res)
    assert poverty_rate(res, thr, w) == pytest.approx(0.5)


def test_poverty_rate_with_weights():
    # Heavier weight on the poor side
    res = np.array([50, 80, 120, 200], dtype=float)
    thr = 100.0
    w = np.array([3, 3, 1, 1], dtype=float)
    # Poor weight 6 / total 8 = 0.75
    assert poverty_rate(res, thr, w) == pytest.approx(0.75)


def test_poverty_depth_handworked():
    # Single poor unit at 50 vs threshold 100 → normalized gap 0.5
    res = np.array([50, 200], dtype=float)
    thr = 100.0
    w = np.ones_like(res)
    assert poverty_depth(res, thr, w) == pytest.approx(0.5)


def test_poverty_gap_handworked():
    res = np.array([50, 80, 120, 200], dtype=float)
    thr = 100.0
    w = np.array([2, 1, 1, 1], dtype=float)
    # Shortfalls: 50*2 + 20*1 = 120
    assert poverty_gap(res, thr, w) == pytest.approx(120.0)


def test_poverty_rate_no_poor_returns_zero():
    res = np.array([200, 300, 400], dtype=float)
    thr = 100.0
    w = np.ones_like(res)
    assert poverty_rate(res, thr, w) == 0.0
    assert poverty_depth(res, thr, w) == 0.0
    assert poverty_gap(res, thr, w) == 0.0


def test_poverty_threshold_per_unit():
    # Different threshold per row (e.g. household-size-adjusted)
    res = np.array([50, 100, 150], dtype=float)
    thr = np.array([60, 90, 200], dtype=float)
    w = np.ones_like(res)
    # Below: row 0 (50<60), row 2 (150<200) → 2/3
    assert poverty_rate(res, thr, w) == pytest.approx(2 / 3)


def test_poverty_validates_threshold_positive():
    with pytest.raises(DataValidationError):
        poverty_rate([100], 0.0, [1])
    with pytest.raises(DataValidationError):
        poverty_rate([100], -1.0, [1])
