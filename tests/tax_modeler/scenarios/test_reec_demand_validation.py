"""Tests for the DPP solar-permit REEC demand validation (July 2026)."""
from __future__ import annotations

import pytest
from tax_modeler.scenarios.reec_demand_validation import (
    compare_with_model,
    empirical_demand_factors,
    load_dpp_solar,
)


def test_bundle_loads_with_expected_shape():
    d = load_dpp_solar()
    assert d["dataset_id"] == "4vab-c87q"
    assert "2023" in d["annual"] and "2024" in d["annual"]
    assert d["annual"]["2023"]["res_n"] > 1000
    assert d["h1_residential"]["2023"]["res_n"] > 0
    assert any("publication lag" in lim for lim in d["limitations"])


def test_base_year_factor_is_one():
    for basis in ("value", "count"):
        f = empirical_demand_factors(basis)
        assert f[2023] == pytest.approx(1.0)


def test_partial_year_uses_h1_window():
    """The dataset's partial final year must be an H1/H1 ratio, never a
    half-year over full-year mix."""
    d = load_dpp_solar()
    partial = int(d["max_issuedate"][:4])
    h1 = d["h1_residential"]
    expected = h1[str(partial)]["res_val"] / h1["2023"]["res_val"]
    f = empirical_demand_factors("value")
    assert f[partial] == pytest.approx(expected, rel=1e-12)


def test_complete_year_uses_full_year_ratio():
    d = load_dpp_solar()
    f = empirical_demand_factors("value")
    expected = d["annual"]["2024"]["res_val"] / d["annual"]["2023"]["res_val"]
    assert f[2024] == pytest.approx(expected, rel=1e-12)


def test_compare_rows_carry_model_columns():
    rows = compare_with_model(years=(2024,))
    (row,) = rows
    assert row["year"] == 2024
    assert row["empirical_value_factor"] is not None
    assert row["income_growth_g"] > 1.0
    assert "model_gxd_obbba_mid" in row and "d_obbba_mid" in row
    # 2024: every scenario pins d=1.0, so g*d == g.
    assert row["model_gxd_pre_obbba"] == pytest.approx(
        row["income_growth_g"], abs=1e-4)


def test_invalid_basis_raises():
    with pytest.raises(ValueError):
        empirical_demand_factors("permits")
