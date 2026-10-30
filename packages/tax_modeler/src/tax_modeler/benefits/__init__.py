"""Benefit programs (Phase 3 of the policy-impact extension).

Modules here follow the same shape as :mod:`tax_modeler.credits.eitc` /
:mod:`tax_modeler.credits.ctc`: a parameters dataclass + a per-unit
calculation function + a vectorized DataFrame helper that adds an
amount column.

Programs in scope for Phase 3 (per the approved plan, with TANF dropped
in favor of using the existing EITC/CTC code for refundable-credit
reforms):

  * **SNAP** — :func:`compute_snap_for_units`
  * **Federal SSI** — :func:`compute_ssi_for_units`
  * **Hawaii SSI state supplement** — :func:`compute_ssi_hi_supplement_for_units`

The Reform DSL (``Reform.benefit_overrides``) drives reform scenarios.
Multipliers like ``snap.max_benefit_pct`` are the canonical entry point
for "10% SNAP cut" / "20% SNAP expansion" style analyses.
"""
from __future__ import annotations

# Phase 5 — federal-cost-share + in-kind programs
from .aca_ptc import (
    AcaPtcParameters,
    aca_ptc_parameters,
    compute_aca_ptc_for_units,
)
from .childcare import (
    ChildcareParameters,
    compute_childcare_for_units,
    hawaii_childcare_parameters,
)
from .housing import (
    HousingParameters,
    compute_housing_for_units,
    hawaii_housing_parameters,
)
from .liheap import LiheapParameters, compute_liheap_for_units, hawaii_liheap_parameters
from .medicaid_hi_quest import (
    MedicaidParameters,
    compute_medicaid_for_units,
    hawaii_medicaid_parameters,
)
from .moop import compute_moop_for_units
from .snap import (
    SnapParameters,
    compute_snap_for_units,
    hawaii_snap_parameters,
)
from .ssi import SsiParameters, compute_ssi_for_units, federal_ssi_parameters
from .ssi_hi_supplement import (
    HawaiiSsiSupplementParameters,
    compute_ssi_hi_supplement_for_units,
    hawaii_ssi_supplement_parameters,
)
from .wic import WicParameters, compute_wic_for_units, hawaii_wic_parameters

__all__ = [
    "SnapParameters",
    "compute_snap_for_units",
    "hawaii_snap_parameters",
    "SsiParameters",
    "compute_ssi_for_units",
    "federal_ssi_parameters",
    "HawaiiSsiSupplementParameters",
    "compute_ssi_hi_supplement_for_units",
    "hawaii_ssi_supplement_parameters",
    # Phase 5
    "AcaPtcParameters",
    "aca_ptc_parameters",
    "compute_aca_ptc_for_units",
    "MedicaidParameters",
    "hawaii_medicaid_parameters",
    "compute_medicaid_for_units",
    "WicParameters",
    "hawaii_wic_parameters",
    "compute_wic_for_units",
    "LiheapParameters",
    "hawaii_liheap_parameters",
    "compute_liheap_for_units",
    "ChildcareParameters",
    "hawaii_childcare_parameters",
    "compute_childcare_for_units",
    "HousingParameters",
    "hawaii_housing_parameters",
    "compute_housing_for_units",
    "compute_moop_for_units",
]
