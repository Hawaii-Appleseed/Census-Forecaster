"""tax_modeler — Hawaii income-tax + federal EITC microsimulation.

A self-contained microsimulation package for state-level income-tax
forecasting.  Currently configured for Hawaii (Act 46, SB 3125 CD1, HB 2306,
etc.) but structured so a future state plug-in is a config-file change, not
a refactor (see :class:`StateConfig`).

Quick start
-----------

::

    from tax_modeler import run_pipeline

    result = run_pipeline(target_year=2026)
    print(result.state_summary["hi_net_tax_revenue"])
    print(result.by_county)

Stable public API
-----------------

Use :data:`PUBLIC_API` to discover the supported surface.  Anything not in
that list is internal and may change without notice.  The most common
entry points:

- :func:`run_pipeline` — end-to-end, returns :class:`PipelineResult`
- :func:`enrich_for_credits` / :func:`compute_base_tax` / :func:`calibrate`
  — individual stages for advanced workflows
- :class:`TaxCalculator`, :class:`TaxSystemRegistry`, :func:`compare_systems`
  — bracket-comparison primitives
- :class:`RevenueEstimator` — population-weighted aggregation
- :class:`StateConfig`, :data:`HAWAII` — state-level constants

Errors
------

Boundary failures raise typed exceptions from :mod:`tax_modeler.errors`:
:class:`MissingDataError`, :class:`DataValidationError`, :class:`ConfigError`,
:class:`CalibrationError`.  All inherit from :class:`TaxModelerError` and
the appropriate Python builtin (``FileNotFoundError`` / ``ValueError`` /
``RuntimeError``) for backwards-compatible ``except`` blocks.
"""
from __future__ import annotations

import importlib
import logging

# units.base configures logging when it is imported; the eager imports ran it
# as soon as tax_modeler was, so do the same here.
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Exported name -> the module that defines it, imported on first use (__getattr__).
_LAZY = {
    # --- Errors -----------------------------------------------------------------
    "CalibrationError": ".errors",
    "ConfigError": ".errors",
    "DataValidationError": ".errors",
    "MissingDataError": ".errors",
    "TaxModelerError": ".errors",
    # --- Core domain types -------------------------------------------------------
    "FILING_STATUS": ".units.base",
    "TaxUnitConstructor": ".units.base",
    "HawaiiTaxParameters": ".liability.hawaii",
    # --- Tax liability + credits + deductions ------------------------------------
    "calculate_hawaii_tax": ".liability",
    "calculate_hawaii_tax_for_units": ".liability",
    "HawaiiEitcParameters": ".credits",
    "HawaiiFoodExciseParameters": ".credits",
    "HawaiiRentersCreditParameters": ".credits",
    "calculate_eitc": ".credits",
    "calculate_eitc_for_tax_units": ".credits",
    "compute_hi_eitc_for_units": ".credits",
    "compute_hi_food_excise_for_units": ".credits",
    "compute_hi_renters_for_units": ".credits",
    "hawaii_eitc_parameters": ".credits",
    "hawaii_food_excise_parameters": ".credits",
    "hawaii_renters_credit_parameters": ".credits",
    "HawaiiTaxCalculator": ".brackets",
    "load_tax_data": ".brackets",
    "DeductionPolicy": ".deductions",
    "TaxableIncomeCalculator": ".deductions",
    "parse_deduction_benchmarks": ".deductions",
    "parse_exemption_benchmarks": ".deductions",
    # --- Tax-system orchestration ------------------------------------------------
    "HAWAII": ".config",
    "StateConfig": ".config",
    "TaxCalculator": ".config",
    "TaxSystemConfig": ".config",
    "TaxSystemRegistry": ".config",
    "compare_systems": ".config",
    # --- Calibration -------------------------------------------------------------
    "AdminCaseload": ".calibration",
    "CalibrationOrchestrator": ".calibration",
    "CaseloadTarget": ".calibration",
    "DOTAXSOIParser": ".calibration",
    "Dimension": ".calibration",
    "DonorMatcher": ".calibration",
    "IPFCalibrationOrchestrator": ".calibration",
    "IPFCalibrator": ".calibration",
    "JointDistributionDrift": ".calibration",
    "JointIpfResult": ".calibration",
    "apply_ipf_calibration": ".calibration",
    "apply_ipf_calibration_via_rake": ".calibration",
    "apply_systematic_calibration": ".calibration",
    "calibrate_benefits": ".calibration",
    "impute_childcare_expense": ".calibration",
    "impute_moop": ".calibration",
    "impute_takeup": ".calibration",
    "scale_benefit_to_dollar_target": ".calibration",
    "impute_work_expense": ".calibration",
    "joint_ipf": ".calibration",
    "validate_joint_distribution": ".calibration",
    # --- Adjustments / credits ---------------------------------------------------
    "calculate_hawaii_credits": ".adjustments",
    "estimate_agi_from_total_income": ".adjustments",
    "estimate_deduction": ".adjustments",
    "scale_deduction_params_for_target_year": ".adjustments",
    "scale_eitc_for_poverty": ".adjustments",
    "HawaiiTaxCredits": ".adjustments.hawaii_credits",
    "ParetoIncomeCalibrator": ".adjustments.pareto_calibration",
    "UltraHighIncomeSynthesizer": ".adjustments.ultra_high_income_synthesizer",
    # --- Validation --------------------------------------------------------------
    "DotaxTable12AValidator": ".validation",
    "apply_hybrid_tax_calibration": ".validation",
    "validate_against_table_12a": ".validation",
    "validate_hybrid_calibration": ".validation",
    # --- Revenue / analysis ------------------------------------------------------
    "RevenueEstimator": ".revenue",
    "estimate_revenue": ".revenue",
    "GeographicAnalyzer": ".analysis",
    "assign_geography": ".analysis",
    "load_crosswalk": ".analysis",
    # --- End-to-end pipeline -----------------------------------------------------
    "PipelineResult": ".pipeline",
    "calibrate": ".pipeline",
    "compute_base_tax": ".pipeline",
    "enrich_for_benefits": ".pipeline",
    "enrich_for_credits": ".pipeline",
    "enrich_with_spm_unit_id": ".pipeline",
    "run_pipeline": ".pipeline",
    # --- Policy-impact extension (Phase 1: entities + reform DSL) ----------------
    "EntityGraph": ".entities",
    "Household": ".entities",
    "Person": ".entities",
    "SPMUnit": ".entities",
    "TaxUnit": ".entities",
    "TAX_SYSTEM_FACTORY_REGISTRY": ".reform",
    "Reform": ".reform",
    "ReformResult": ".reform",
    "apply_reform": ".reform",
    "register_tax_system": ".reform",
    "BehavioralParams": ".scenarios",
    "ForecastScenario": ".scenarios",
    "MacroScenario": ".scenarios",
    "TopIncomeParams": ".scenarios",
    # --- Policy-impact extension (Phase 2: distribution + poverty metrics) -------
    "atkinson": ".metrics",
    "decile_summary": ".metrics",
    "distribution_by_geography": ".metrics",
    "gini": ".metrics",
    "palma": ".metrics",
    "poverty_by_geography": ".metrics",
    "poverty_depth": ".metrics",
    "poverty_gap": ".metrics",
    "poverty_rate": ".metrics",
    "weighted_ntile_labels": ".metrics",
    "compute_spm_resources": ".poverty",
    "hawaii_spm_threshold": ".poverty",
    "spm_threshold_table": ".poverty",
    "threshold_for_units": ".poverty",
    # --- Policy-impact extension (Phase 3 + 5: benefit modules) ------------------
    "HawaiiSsiSupplementParameters": ".benefits",
    "SnapParameters": ".benefits",
    "SsiParameters": ".benefits",
    "compute_snap_for_units": ".benefits",
    "compute_ssi_for_units": ".benefits",
    "compute_ssi_hi_supplement_for_units": ".benefits",
    "federal_ssi_parameters": ".benefits",
    "hawaii_snap_parameters": ".benefits",
    "hawaii_ssi_supplement_parameters": ".benefits",
    "AcaPtcParameters": ".benefits",
    "ChildcareParameters": ".benefits",
    "HousingParameters": ".benefits",
    "LiheapParameters": ".benefits",
    "MedicaidParameters": ".benefits",
    "WicParameters": ".benefits",
    "aca_ptc_parameters": ".benefits",
    "compute_aca_ptc_for_units": ".benefits",
    "compute_childcare_for_units": ".benefits",
    "compute_housing_for_units": ".benefits",
    "compute_liheap_for_units": ".benefits",
    "compute_medicaid_for_units": ".benefits",
    "compute_wic_for_units": ".benefits",
    "hawaii_childcare_parameters": ".benefits",
    "hawaii_housing_parameters": ".benefits",
    "hawaii_liheap_parameters": ".benefits",
    "hawaii_medicaid_parameters": ".benefits",
    "hawaii_wic_parameters": ".benefits",
    # --- PUMS / projection (two entry points each) ------------------------------
    # Simple "scalar / DataFrame" helpers — bridge to monorepo workspace packages.
    "load_hawaii_pums": ".pums_adapter",
    "project_income_growth": ".projection_adapter",
    # Full-fat loaders kept from the original ctc-and-eitc — needed for batched
    # population loading and tax-unit-vectorized income projection.
    "PUMSDataLoader": ".loaders.pums_loader",
    "load_pums_data": ".loaders.pums_loader",
    "AcsSupplement": ".projection",
    "DemographicProjectionResult": ".projection",
    "EnsembleProjector": ".projection",
    "OccupationMatcher": ".projection",
    "hawaii_demographic_targets": ".projection",
    "hawaii_disability_share": ".projection",
    "hawaii_hh_size_dist": ".projection",
    "hawaii_senior_share": ".projection",
    "project_acs_supplement": ".projection",
    "project_demographics_forward": ".projection",
    "project_tax_units_forward": ".projection",
    "scale_mortgage_deductions": ".projection",
}


def __getattr__(name: str):
    """Import an exported name's module the first time the name is used
    (PEP 562), so importing one submodule no longer runs every other one.
    Any other name is tried as a submodule, as the eager imports used to
    leave every submodule set on the package."""
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name], __name__), name)
        globals()[name] = value
        return value
    if not name.startswith("__"):
        try:
            return importlib.import_module(f".{name}", __name__)
        except ModuleNotFoundError as e:
            if e.name != f"{__name__}.{name}":
                raise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})



__version__ = "0.3.0.dev0"


# ----------------------------------------------------------------------------
# Public API surface
#
# These are the symbols users should import.  They have stable signatures and
# documented behavior.  Everything else re-exported below is "internal but
# importable" — fine for power users who pin a version, but no compatibility
# guarantee across releases.
# ----------------------------------------------------------------------------

PUBLIC_API = (
    # End-to-end entry point
    "run_pipeline",
    "PipelineResult",
    # Pipeline stages (formerly underscore-private; underscore aliases remain
    # as deprecated shims in tax_modeler.pipeline).
    "enrich_for_credits",
    "enrich_for_benefits",
    "enrich_with_spm_unit_id",
    "compute_base_tax",
    "calibrate",
    # Tax math primitives
    "TaxUnitConstructor",
    "TaxCalculator",
    "TaxSystemConfig",
    "TaxSystemRegistry",
    "compare_systems",
    # Aggregation
    "RevenueEstimator",
    # State configuration
    "StateConfig",
    "HAWAII",
    # Policy-impact: reform DSL + entity layer (v0.3.0+)
    "Reform",
    "ReformResult",
    "apply_reform",
    "register_tax_system",
    "TAX_SYSTEM_FACTORY_REGISTRY",
    "EntityGraph",
    "Person",
    "TaxUnit",
    "SPMUnit",
    "Household",
    # Phase 11: typed scenario-parameter dataclasses
    "BehavioralParams",
    "TopIncomeParams",
    "MacroScenario",
    "ForecastScenario",
    # Policy-impact: distribution + poverty metrics (v0.3.0+)
    "decile_summary",
    "gini",
    "palma",
    "atkinson",
    "weighted_ntile_labels",
    "poverty_rate",
    "poverty_depth",
    "poverty_gap",
    "compute_spm_resources",
    "hawaii_spm_threshold",
    "spm_threshold_table",
    "threshold_for_units",
    # Policy-impact: SNAP + SSI benefit modules (v0.3.0+)
    "compute_snap_for_units",
    "compute_ssi_for_units",
    "compute_ssi_hi_supplement_for_units",
    "SnapParameters",
    "SsiParameters",
    "HawaiiSsiSupplementParameters",
    "hawaii_snap_parameters",
    "federal_ssi_parameters",
    "hawaii_ssi_supplement_parameters",
    # Policy-impact: take-up imputation against administrative caseload (Phase 4)
    "AdminCaseload",
    "CaseloadTarget",
    "calibrate_benefits",
    "impute_takeup",
    "scale_benefit_to_dollar_target",
    # Phase 8: ACS demographic projection + donor-match + geographic stratification
    "project_demographics_forward",
    "hawaii_senior_share",
    "hawaii_hh_size_dist",
    "hawaii_disability_share",
    "hawaii_demographic_targets",
    "DemographicProjectionResult",
    "DonorMatcher",
    "impute_moop",
    "impute_childcare_expense",
    "impute_work_expense",
    "poverty_by_geography",
    "distribution_by_geography",
    # Phase 10: joint multi-dim IPF
    "Dimension",
    "JointIpfResult",
    "JointDistributionDrift",
    "joint_ipf",
    "validate_joint_distribution",
    # Policy-impact: Phase 5 federal-cost-share + in-kind programs
    "compute_aca_ptc_for_units",
    "compute_medicaid_for_units",
    "compute_wic_for_units",
    "compute_liheap_for_units",
    "compute_childcare_for_units",
    "compute_housing_for_units",
    "AcaPtcParameters",
    "MedicaidParameters",
    "WicParameters",
    "LiheapParameters",
    "ChildcareParameters",
    "HousingParameters",
    "aca_ptc_parameters",
    "hawaii_medicaid_parameters",
    "hawaii_wic_parameters",
    "hawaii_liheap_parameters",
    "hawaii_childcare_parameters",
    "hawaii_housing_parameters",
    # Hawaii state-level refundable credits
    "compute_hi_eitc_for_units",
    "compute_hi_food_excise_for_units",
    "compute_hi_renters_for_units",
    "HawaiiEitcParameters",
    "HawaiiFoodExciseParameters",
    "HawaiiRentersCreditParameters",
    "hawaii_eitc_parameters",
    "hawaii_food_excise_parameters",
    "hawaii_renters_credit_parameters",
    # Errors (catch by intent)
    "TaxModelerError",
    "MissingDataError",
    "DataValidationError",
    "ConfigError",
    "CalibrationError",
)


__all__ = [
    # ── Stable public surface ─────────────────────────────────────────────
    *PUBLIC_API,
    # ── Internal but importable (no stability guarantee) ─────────────────
    "FILING_STATUS",
    "HawaiiTaxParameters",
    "calculate_hawaii_tax",
    "calculate_hawaii_tax_for_units",
    "calculate_eitc",
    "calculate_eitc_for_tax_units",
    "HawaiiTaxCalculator",
    "load_tax_data",
    "DeductionPolicy",
    "TaxableIncomeCalculator",
    "parse_deduction_benchmarks",
    "parse_exemption_benchmarks",
    "CalibrationOrchestrator",
    "apply_systematic_calibration",
    "IPFCalibrationOrchestrator",
    "apply_ipf_calibration",
    "apply_ipf_calibration_via_rake",
    "DOTAXSOIParser",
    "IPFCalibrator",
    "estimate_agi_from_total_income",
    "calculate_hawaii_credits",
    "estimate_deduction",
    "scale_deduction_params_for_target_year",
    "scale_eitc_for_poverty",
    "HawaiiTaxCredits",
    "ParetoIncomeCalibrator",
    "UltraHighIncomeSynthesizer",
    "DotaxTable12AValidator",
    "validate_against_table_12a",
    "apply_hybrid_tax_calibration",
    "validate_hybrid_calibration",
    "estimate_revenue",
    "GeographicAnalyzer",
    "assign_geography",
    "load_crosswalk",
    "load_hawaii_pums",
    "PUMSDataLoader",
    "load_pums_data",
    "project_income_growth",
    "EnsembleProjector",
    "OccupationMatcher",
    "project_tax_units_forward",
    "AcsSupplement",
    "project_acs_supplement",
    "scale_mortgage_deductions",
]
