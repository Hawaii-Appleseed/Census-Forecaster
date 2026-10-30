"""Tests for the DOTAX monthly-collections validation (July 2026)."""
from __future__ import annotations

import pytest
from tax_modeler.projection.dotax_validation import (
    compare_with_model,
    latest_complete_windows,
    load_dotax_collections,
    yoy_window_growth,
)


def test_bundle_loads_with_expected_series():
    d = load_dotax_collections()
    for key in ("ind_wh", "ge_use", "ge_allocated", "tat", "total",
                "general_fund", "county_surcharge"):
        assert key in d["series_keys"]
    assert len(d["monthly"]) >= 18
    assert any("Act 46" in lim for lim in d["limitations"])


def test_spot_value_matches_source_report():
    """Pin one value against a manual read of the Dec-2025 workbook."""
    d = load_dotax_collections()
    assert d["monthly"]["2025-12"]["ind_wh"] == pytest.approx(247_800_220.74)
    assert d["monthly"]["2024-12"]["tat"] == pytest.approx(59_099_700.08)


def test_yoy_window_growth_basic():
    g = yoy_window_growth("ge_use", ["2025-10", "2025-11", "2025-12"])
    assert g is not None
    assert -0.5 < g < 0.5


def test_yoy_window_growth_strict_on_missing_months():
    """A window touching a month absent from the bundle returns None —
    never a silently shrunken comparison."""
    d = load_dotax_collections()
    assert "2025-05" not in d["monthly"] or "ge_use" not in d["monthly"].get("2025-05", {})
    assert yoy_window_growth("ge_use", ["2025-05", "2025-06"]) is None


def test_latest_complete_windows_shapes():
    w = latest_complete_windows()
    assert "ge_allocated" in w and "ind_wh" in w
    for rec in w.values():
        assert rec["n_months"] >= 3
        assert ".." in rec["window"]


def test_compare_with_model_carries_note_and_years():
    r = compare_with_model()
    assert r["windows"]
    assert r["model_implied_annual"]
    assert "Act 46" in r["note"]
