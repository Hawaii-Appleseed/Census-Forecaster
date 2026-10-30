"""Phase 6 policy-level smoke test: combined tax + benefit reforms."""
from __future__ import annotations

import pytest

from tax_modeler import (
    AdminCaseload,
    Reform,
    TaxSystemRegistry,
    apply_reform,
    compute_hi_eitc_for_units,
    compute_snap_for_units,
    compute_spm_resources,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
)
from lab.validation import validate_against_admin_caseload


pytestmark = pytest.mark.smoke


def test_combined_tax_and_benefit_reform_round_trip(taxed_units):
    """Tax cut + benefit cut + EITC expansion in a single Reform."""
    units = compute_snap_for_units(taxed_units)
    units = compute_ssi_for_units(units)
    units = compute_ssi_hi_supplement_for_units(units)
    units = compute_hi_eitc_for_units(units)

    reform = Reform(
        name="combined_tax_and_benefit_reform",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
        benefit_overrides={
            "snap":    {"max_benefit_pct": 0.90},
            "hi_eitc": {"rate_of_federal": 0.80},
            "eitc":    {"amount_pct": 1.50},
        },
    )
    result = apply_reform(units, reform, year=2027)

    # Both tax + benefit deltas populated
    assert isinstance(result.revenue_delta_millions, float)
    assert {"snap", "hi_eitc", "eitc"}.issubset(set(result.benefit_flow_deltas_millions))
    assert result.counterfactual_units is not None
    # Counterfactual frame retains baseline shape
    assert len(result.counterfactual_units) == len(units)


def test_validation_against_admin_caseload(taxed_units):
    """Run the policy-crosscheck helper end-to-end on the synthetic fixture.

    The fixture is tiny (50 households scaled to ~5K weighted units), so
    we expect the synthetic SNAP/SSI totals to fall well short of the
    real Hawaii caseload. The point of the test is that the validator
    *runs*, returns a typed report, and doesn't crash on missing /
    out-of-range data.
    """
    units = compute_snap_for_units(taxed_units)
    units = compute_ssi_for_units(units)
    units = compute_ssi_hi_supplement_for_units(units)

    caseload = AdminCaseload.load()
    # Generous tolerances because synthetic fixture is small
    report = validate_against_admin_caseload(
        units, caseload=caseload, year=2024,
        count_tolerance_pct=2.0, dollar_tolerance_pct=2.0,
    )
    assert len(report.checks) == 3
    assert all(c.program in {"snap", "ssi", "ssi_hi_supplement"} for c in report.checks)
    # Summary string is non-empty and includes one line per program
    summary = report.summary()
    for prog in ("snap", "ssi", "ssi_hi_supplement"):
        assert prog in summary
