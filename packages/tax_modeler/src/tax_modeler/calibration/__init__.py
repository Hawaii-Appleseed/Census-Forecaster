"""Tax calibration modules."""

from __future__ import annotations

import importlib

# Eager: joint_ipf is both a submodule and a function exported under the
# same name, which a lazy load could resolve to the module.
from .joint_ipf import (
    Dimension,
    JointDistributionDrift,
    JointIpfResult,
    joint_ipf,
    validate_joint_distribution,
)

# Exported name -> the module that defines it, imported on first use (__getattr__).
_LAZY = {
    "CalibrationOrchestrator": ".orchestrator",
    "apply_systematic_calibration": ".orchestrator",
    "IPFCalibrationOrchestrator": ".ipf_orchestrator",
    "apply_ipf_calibration": ".ipf_orchestrator",
    "apply_ipf_calibration_via_rake": ".ipf_orchestrator",
    "DOTAXSOIParser": ".dotax_soi_parser",
    "IPFCalibrator": ".ipf_calibration",
    "create_benchmarks_from_dotax": ".ipf_calibration",
    "calibrate_pums_with_ipf": ".ipf_calibration",
    "IRSBracketCalibrator": ".irs_bracket_calibration",
    "calibrate_with_irs_brackets": ".irs_bracket_calibration",
    "HighIncomeEnhancer": ".high_income_enhancement",
    "enhance_high_income": ".high_income_enhancement",
    "IncomeSourceSplitter": ".income_source_split",
    "split_income_sources": ".income_source_split",
    "WageGrowthAdjuster": ".wage_growth_adjustment",
    "generate_growth_rate_report": ".wage_growth_adjustment",
    "SOICalibrator": ".soi_calibration",
    "AdminCaseload": ".admin_caseload",
    "CaseloadTarget": ".admin_caseload",
    "calibrate_benefits": ".takeup_imputation",
    "impute_takeup": ".takeup_imputation",
    "scale_benefit_to_dollar_target": ".takeup_imputation",
    "TakeupEstimate": ".hi_eitc_takeup_estimate",
    "estimate_hi_eitc_takeup": ".hi_eitc_takeup_estimate",
    "AlphaCalibration": ".eitc_alpha_calibration",
    "calibrate_eitc_poverty_alpha": ".eitc_alpha_calibration",
    "write_calibration_artifact": ".eitc_alpha_calibration",
    "reweight_eitc_eligibles_by_children": ".eitc_reweight",
    "DonorMatcher": ".donor_match",
    "impute_childcare_expense": ".donor_match",
    "impute_moop": ".donor_match",
    "impute_work_expense": ".donor_match",
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


__all__ = [
    'CalibrationOrchestrator',
    'apply_systematic_calibration',
    'IPFCalibrationOrchestrator',
    'apply_ipf_calibration',
    'apply_ipf_calibration_via_rake',
    'DOTAXSOIParser',
    'IPFCalibrator',
    'create_benchmarks_from_dotax',
    'calibrate_pums_with_ipf',
    'IRSBracketCalibrator',
    'calibrate_with_irs_brackets',
    'HighIncomeEnhancer',
    'enhance_high_income',
    'IncomeSourceSplitter',
    'split_income_sources',
    'WageGrowthAdjuster',
    'generate_growth_rate_report',
    'SOICalibrator',
    # Phase 4: take-up imputation
    'AdminCaseload',
    'CaseloadTarget',
    'calibrate_benefits',
    'impute_takeup',
    'scale_benefit_to_dollar_target',
    # 2026-Q2: Hawaii-empirical HI EITC take-up anchor (HI CTC sweep prior)
    'TakeupEstimate',
    'estimate_hi_eitc_takeup',
    # 2026-Q2: Empirical α calibration for scale_eitc_for_poverty
    'AlphaCalibration',
    'calibrate_eitc_poverty_alpha',
    'write_calibration_artifact',
    # Lever 3a: surgical EITC by-children reweight
    'reweight_eitc_eligibles_by_children',
    # Phase 8: donor-matching imputation (CPS-ASEC → ACS)
    'DonorMatcher',
    'impute_moop',
    # Phase 9a: extended donor-match coverage
    'impute_childcare_expense',
    'impute_work_expense',
    # Phase 10: joint multi-dimensional IPF
    'Dimension',
    'JointIpfResult',
    'JointDistributionDrift',
    'joint_ipf',
    'validate_joint_distribution',
]
