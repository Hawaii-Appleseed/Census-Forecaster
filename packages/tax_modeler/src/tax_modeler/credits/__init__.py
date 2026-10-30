"""
Tax Credits Module

This module provides functionality for calculating various tax credits
including the Child Tax Credit (CTC) and Earned Income Tax Credit (EITC).
"""

from __future__ import annotations

from .arpa_ctc import arpa_ctc_for_tax_units
from .ctc import (
    calculate_ctc,
    calculate_ctc_for_tax_units,
    ctc_parameters_for_year,
)
from .eitc import (
    calculate_eitc,
    calculate_eitc_for_tax_units,
    eitc_parameters_for_year,
)

# Hawaii state-level refundable credits (Reform-DSL-friendly)
from .hi_eitc import (
    HawaiiEitcParameters,
    compute_hi_eitc_for_units,
    hawaii_eitc_parameters,
)
from .hi_food_excise import (
    HawaiiFoodExciseParameters,
    compute_hi_food_excise_for_units,
    hawaii_food_excise_parameters,
)
from .hi_renters import (
    HawaiiRentersCreditParameters,
    compute_hi_renters_for_units,
    hawaii_renters_credit_parameters,
)

__all__ = [
    'calculate_eitc',
    'calculate_eitc_for_tax_units',
    'eitc_parameters_for_year',
    'calculate_ctc',
    'calculate_ctc_for_tax_units',
    'ctc_parameters_for_year',
    'arpa_ctc_for_tax_units',
    # Hawaii state credits
    'HawaiiEitcParameters',
    'hawaii_eitc_parameters',
    'compute_hi_eitc_for_units',
    'HawaiiFoodExciseParameters',
    'hawaii_food_excise_parameters',
    'compute_hi_food_excise_for_units',
    'HawaiiRentersCreditParameters',
    'hawaii_renters_credit_parameters',
    'compute_hi_renters_for_units',
]
