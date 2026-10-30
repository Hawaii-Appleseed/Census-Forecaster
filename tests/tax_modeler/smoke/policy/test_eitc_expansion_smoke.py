"""Phase 6 policy-level smoke test: EITC expansion → poverty falls."""
from __future__ import annotations

import pytest
from tax_modeler import (
    Reform,
    apply_reform,
    compute_hi_eitc_for_units,
    compute_spm_resources,
    hawaii_spm_threshold,
    poverty_rate,
)

pytestmark = pytest.mark.smoke


def test_federal_eitc_doubled_raises_q1_resources(taxed_units):
    if (taxed_units["eitc_amount"] * taxed_units["weight"]).sum() == 0:
        pytest.skip("no federal EITC recipients on fixture")
    base_resources, _ = compute_spm_resources(taxed_units)

    reform = Reform(
        name="eitc_doubled",
        benefit_overrides={"eitc": {"amount_pct": 2.0}},
    )
    result = apply_reform(taxed_units, reform, year=2026)
    cf_resources, _ = compute_spm_resources(result.counterfactual_units)

    assert result.benefit_flow_deltas_millions["eitc"] > 0
    assert (cf_resources["spm_resources"].sum()
            > base_resources["spm_resources"].sum())


def test_combined_federal_and_hi_eitc_expansion(taxed_units):
    if (taxed_units["eitc_amount"] * taxed_units["weight"]).sum() == 0:
        pytest.skip("no federal EITC recipients on fixture")

    units = compute_hi_eitc_for_units(taxed_units)
    base_resources, _ = compute_spm_resources(units)

    reform = Reform(
        name="combined_eitc_expansion",
        benefit_overrides={
            "eitc": {"amount_pct": 1.50},
            "hi_eitc": {"rate_of_federal": 0.80},  # 40% → 80%
        },
    )
    result = apply_reform(units, reform, year=2026)
    cf_resources, _ = compute_spm_resources(result.counterfactual_units)

    deltas = result.benefit_flow_deltas_millions
    assert deltas["eitc"] > 0
    assert deltas["hi_eitc"] > 0
    # Total SPM resources strictly increase
    assert (cf_resources["spm_resources"].sum()
            > base_resources["spm_resources"].sum())


def test_eitc_expansion_weakly_lowers_poverty(taxed_units):
    if (taxed_units["eitc_amount"] * taxed_units["weight"]).sum() == 0:
        pytest.skip("no federal EITC recipients on fixture")

    units = compute_hi_eitc_for_units(taxed_units)
    threshold = hawaii_spm_threshold(2024)
    base_resources, _ = compute_spm_resources(units)
    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )

    reform = Reform(
        name="eitc_tripled",
        benefit_overrides={"eitc": {"amount_pct": 3.0}, "hi_eitc": {"rate_of_federal": 1.0}},
    )
    result = apply_reform(units, reform, year=2026)
    cf_resources, _ = compute_spm_resources(result.counterfactual_units)
    cf_rate = poverty_rate(
        cf_resources["spm_resources"], threshold, cf_resources["weight"]
    )
    # EITC expansion can only weakly lower poverty
    assert cf_rate <= base_rate
