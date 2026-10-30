"""
Ensemble growth projection system for Hawaii tax revenue forecasting.

This module provides income projection capabilities that combine:
- ACS demographic and income trends
- BLS OES occupation-specific wage growth
- Hierarchical matching strategies with confidence scoring
"""

from __future__ import annotations

from .acs_supplement_forecast import (
    AcsSupplement,
    project_acs_supplement,
    scale_mortgage_deductions,
)
from .demographic import (
    DemographicProjectionResult,
    hawaii_demographic_targets,
    hawaii_disability_share,
    hawaii_hh_size_dist,
    hawaii_senior_share,
    project_demographics_forward,
)
from .ensemble import EnsembleProjector
from .occupation_matcher import OccupationMatcher
from .tax_unit_projector import project_tax_units_forward

__all__ = [
    'EnsembleProjector',
    'OccupationMatcher',
    'project_tax_units_forward',
    'AcsSupplement',
    'project_acs_supplement',
    'scale_mortgage_deductions',
    # Phase 8: ACS demographic projection
    'DemographicProjectionResult',
    'hawaii_senior_share',
    'project_demographics_forward',
    # Phase 9b: multi-marginal projection targets
    'hawaii_hh_size_dist',
    'hawaii_disability_share',
    'hawaii_demographic_targets',
]
