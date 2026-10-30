"""Tests for the Betson SPM 3-parameter equivalence scale in thresholds.py."""
from __future__ import annotations

import pytest
from tax_modeler.poverty.thresholds import _equivalence_scale, hawaii_spm_threshold


def test_reference_family_scale_is_one():
    """2 adults + 2 children must equal 1.0 by construction."""
    assert _equivalence_scale(n_adults=2, n_children=2) == pytest.approx(1.0)


def test_one_adult_no_children_uses_adults_only_scale():
    """One-adult unit: scale = 1^0.5 / 3^0.7 ≈ 0.463."""
    s = _equivalence_scale(n_adults=1, n_children=0)
    assert s == pytest.approx(1.0 / (3 ** 0.7), rel=1e-6)


def test_two_adults_no_children_uses_adults_only_scale():
    """Two-adult unit: scale = 2^0.5 / 3^0.7 ≈ 0.655."""
    s = _equivalence_scale(n_adults=2, n_children=0)
    assert s == pytest.approx((2 ** 0.5) / (3 ** 0.7), rel=1e-6)


def test_single_parent_one_child_uses_first_child_weight():
    """Single parent + 1 child: scale = (1 + 0.8)^0.7 / 3^0.7."""
    s = _equivalence_scale(n_adults=1, n_children=1)
    assert s == pytest.approx((1.8 ** 0.7) / (3 ** 0.7), rel=1e-6)


def test_single_parent_three_children_uses_betson_weights():
    """Single parent + 3 children: scale = (1 + 0.8 + 0.5×2)^0.7 / 3^0.7."""
    s = _equivalence_scale(n_adults=1, n_children=3)
    # 1 + 0.8 + 0.5 + 0.5 = 2.8
    assert s == pytest.approx((2.8 ** 0.7) / (3 ** 0.7), rel=1e-6)


def test_two_adults_three_children_uses_half_children_weight():
    """2 adults + 3 children: scale = (2 + 0.5×3)^0.7 / 3^0.7."""
    s = _equivalence_scale(n_adults=2, n_children=3)
    # 2 + 1.5 = 3.5
    assert s == pytest.approx((3.5 ** 0.7) / (3 ** 0.7), rel=1e-6)


def test_thresholds_monotone_in_children():
    """Adding a child should raise the threshold for any composition."""
    base = hawaii_spm_threshold(2024, n_adults=2, n_children=0)
    plus_one = hawaii_spm_threshold(2024, n_adults=2, n_children=1)
    plus_two = hawaii_spm_threshold(2024, n_adults=2, n_children=2)
    plus_three = hawaii_spm_threshold(2024, n_adults=2, n_children=3)
    assert base < plus_one < plus_two < plus_three


def test_thresholds_monotone_in_adults():
    """Adding an adult should raise the threshold for any composition."""
    one_no_kids = hawaii_spm_threshold(2024, n_adults=1, n_children=0)
    two_no_kids = hawaii_spm_threshold(2024, n_adults=2, n_children=0)
    one_two_kids = hawaii_spm_threshold(2024, n_adults=1, n_children=2)
    two_two_kids = hawaii_spm_threshold(2024, n_adults=2, n_children=2)
    assert one_no_kids < two_no_kids
    assert one_two_kids < two_two_kids


def test_single_parent_scale_higher_than_two_adult_no_kids():
    """A single parent with 2 kids should equal a higher threshold than 2 adults
    no kids — under Betson, the first child carries 0.8 weight."""
    sp_2 = _equivalence_scale(n_adults=1, n_children=2)
    two_a_0c = _equivalence_scale(n_adults=2, n_children=0)
    # Single-parent + 2 kids = (1 + 0.8 + 0.5)^0.7 = 2.3^0.7 ≈ 1.78
    # Two adults no kids = 2^0.5 ≈ 1.41
    # The first is bigger.
    assert sp_2 > two_a_0c
