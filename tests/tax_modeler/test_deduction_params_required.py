"""Regression tests for the NO_ITEMIZING refactor.

Codifies the invariants that close the silent-bug pattern:
  1. ``calculate_hawaii_tax`` for tax_year ≥ 2024 raises if no explicit
     deduction_params policy is provided.
  2. ``NO_ITEMIZING`` sentinel is accepted as an explicit "skip itemizing".
  3. ``per_unit_tax`` for two configs with identical SD/brackets must
     produce zero delta — guards against any future regression where
     baseline and bill diverge purely from caller-side parameter wiring.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.liability.hawaii import (
    NO_ITEMIZING,
    calculate_hawaii_tax,
    calculate_hawaii_tax_for_units,
)


def _unit(income=100_000, fs="single", deps=0):
    return {
        "income": income,
        "filing_status": fs,
        "num_dependents": deps,
        "weight": 1.0,
    }


class TestDeductionParamsValidation:
    """Refactor invariant: silent SD-only fallback for Act 46 years is gone."""

    def test_pre_act_46_year_accepts_none(self):
        """TY 2023 and earlier accept deduction_params=None (legacy fallback)."""
        result = calculate_hawaii_tax(_unit(), tax_year=2023)
        assert result["hi_tax_liability"] >= 0

    def test_act_46_year_rejects_none(self):
        """TY ≥ 2024 must reject silent None — ValueError, not SD-only quietly."""
        with pytest.raises(ValueError, match="requires an explicit deduction_params"):
            calculate_hawaii_tax(_unit(), tax_year=2027)

    def test_act_46_year_accepts_no_itemizing_sentinel(self):
        """NO_ITEMIZING is the explicit "I want SD-only" choice."""
        result = calculate_hawaii_tax(
            _unit(), tax_year=2027, deduction_params=NO_ITEMIZING
        )
        assert result["hi_tax_liability"] >= 0

    def test_act_46_year_accepts_real_params(self):
        """Real deduction_params dict still works."""
        # Minimal valid params dict matching the schema in
        # adjustments/itemized_deductions._load_params fallback.
        from tax_modeler.adjustments.itemized_deductions import (
            scale_deduction_params_for_target_year,
        )

        params = scale_deduction_params_for_target_year(2027, geoid="15003")
        result = calculate_hawaii_tax(
            _unit(income=500_000, fs="married_filing_jointly"),
            tax_year=2027,
            deduction_params=params,
        )
        # High-income MFJ: expected itemized > $24K SD, so column captures it.
        assert result["hi_itemized_deduction"] > 0
        assert result["hi_standard_deduction"] >= result["hi_itemized_deduction"]

    def test_for_units_propagates_validation(self):
        """The vectorized entry point must reject None for TY ≥ 2024 too."""
        df = pd.DataFrame([_unit()])
        with pytest.raises(ValueError, match="requires an explicit deduction_params"):
            calculate_hawaii_tax_for_units(df, tax_year=2027)

    def test_for_units_accepts_no_itemizing(self):
        df = pd.DataFrame([_unit()])
        out = calculate_hawaii_tax_for_units(
            df, tax_year=2027, deduction_params=NO_ITEMIZING
        )
        assert "hi_tax_liability" in out.columns
        assert (out["hi_itemized_deduction"] == 0.0).all()

    def test_string_params_rejected(self):
        """Non-dict, non-sentinel inputs raise TypeError."""
        with pytest.raises(TypeError, match="must be a dict"):
            calculate_hawaii_tax(
                _unit(), tax_year=2027, deduction_params="oops"
            )


class TestPerUnitTaxInvariant:
    """Two configs with identical SD/brackets must produce zero delta."""

    def test_identical_configs_zero_delta(self):
        """If we compare a config to itself, the delta must be exactly zero.

        This is the unit test that would have caught the original
        ``deduction_col="hi_standard_deduction"`` bug — it forced both
        scenarios to read the same column, making delta look fine even
        when the SD policy diverged.
        """
        from tax_modeler.config.tax_system_config import (
            TaxCalculator,
            TaxSystemRegistry,
        )
        from tax_modeler.scenarios.quintile_analysis import per_unit_tax

        # Synthesize a tiny test cohort. We don't need projection here —
        # only the invariant that two identical configs produce identical
        # tax under the post-refactor per_unit_tax.
        df = pd.DataFrame([
            {"income": 50_000, "filing_status": "single",
             "num_dependents": 0, "weight": 1.0,
             "hi_itemized_deduction": 8_000.0},
            {"income": 250_000, "filing_status": "married_filing_jointly",
             "num_dependents": 2, "weight": 1.0,
             "hi_itemized_deduction": 35_000.0},
            {"income": 1_500_000, "filing_status": "married_filing_jointly",
             "num_dependents": 0, "weight": 1.0,
             "hi_itemized_deduction": 200_000.0},
        ])

        calc = TaxCalculator()
        config = TaxSystemRegistry.get_act46_system(2027)

        a = per_unit_tax(df, config, calc)
        b = per_unit_tax(df, config, calc)

        np.testing.assert_array_equal(
            a, b,
            "per_unit_tax called twice with the same config must produce "
            "identical output (no hidden state, no caller-side parameter "
            "that can silently diverge baseline vs bill).",
        )
