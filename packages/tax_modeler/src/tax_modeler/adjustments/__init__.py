"""
Income adjustments and tax credits for Hawaii tax calculations
"""

from __future__ import annotations

from .agi_adjustments import estimate_agi_from_total_income
from .eitc_poverty_scaling import scale_eitc_for_poverty
from .hawaii_credits import calculate_hawaii_credits
from .itemized_deductions import estimate_deduction, scale_deduction_params_for_target_year

__all__ = [
    'estimate_agi_from_total_income',
    'calculate_hawaii_credits',
    'estimate_deduction',
    'scale_deduction_params_for_target_year',
    'scale_eitc_for_poverty',
]
