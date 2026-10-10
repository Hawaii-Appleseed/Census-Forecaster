"""Tax calibration modules."""

from __future__ import annotations

from .orchestrator import (
    CalibrationOrchestrator,
    apply_systematic_calibration
)
from .ipf_orchestrator import (
    IPFCalibrationOrchestrator,
    apply_ipf_calibration,
    apply_ipf_calibration_via_rake,
)
from .report import (
    CalibrationReport,
    ClipEvent,
    DroppedBin,
    MarginResidual,
    WeightDiagnostics,
)
from .dotax_soi_parser import DOTAXSOIParser
from .ipf_calibration import IPFCalibrator, create_benchmarks_from_dotax, calibrate_pums_with_ipf
from .irs_bracket_calibration import IRSBracketCalibrator, calibrate_with_irs_brackets
from .high_income_enhancement import HighIncomeEnhancer, enhance_high_income
from .income_source_split import IncomeSourceSplitter, split_income_sources
from .wage_growth_adjustment import WageGrowthAdjuster, generate_growth_rate_report
from .soi_calibration import SOICalibrator
from .admin_caseload import AdminCaseload, CaseloadTarget
from .takeup_imputation import calibrate_benefits, impute_takeup, scale_benefit_to_dollar_target
from .hi_eitc_takeup_estimate import TakeupEstimate, estimate_hi_eitc_takeup
from .eitc_alpha_calibration import (
    AlphaCalibration,
    calibrate_eitc_poverty_alpha,
    write_calibration_artifact,
)
from .eitc_reweight import reweight_eitc_eligibles_by_children
from .donor_match import (
    DonorMatcher,
    impute_childcare_expense,
    impute_moop,
    impute_work_expense,
)
from .joint_ipf import (
    Dimension,
    JointDistributionDrift,
    JointIpfResult,
    joint_ipf,
    validate_joint_distribution,
)

__all__ = [
    'CalibrationOrchestrator',
    'apply_systematic_calibration',
    'IPFCalibrationOrchestrator',
    'apply_ipf_calibration',
    'apply_ipf_calibration_via_rake',
    'CalibrationReport',
    'ClipEvent',
    'DroppedBin',
    'MarginResidual',
    'WeightDiagnostics',
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
