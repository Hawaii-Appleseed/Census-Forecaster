"""pums-estimator: county-level distribution estimation via PUMS microdata reweighting.

Estimates statistics not directly published by the Census Bureau — such as
the distribution of vehicle types by county — by reweighting ACS PUMS
microdata to match county-level control totals from census_forecaster.

Quick start
-----------
    >>> from pums_estimator import (
    ...     PumsRecord, CountyControls, SyntheticEstimatePoint,
    ...     fetch_pums, load_crosswalk, estimate_county_distribution,
    ...     controls_from_acs_table, scale_margins,
    ... )

Statistical contract
--------------------
Results are *model-based estimates*, not direct survey estimates.  They
carry the uncertainty of both the PUMS sampling design and the raking
assumptions.  Do not treat them as equivalent to published ACS tables.
"""
from .controls import controls_from_acs_table, scale_margins
from .estimation.rake import rake
from .estimation.synthetic import estimate_county_distribution
from .models import CountyControls, PumsRecord, SyntheticEstimatePoint
from .pums.client import fetch_pums
from .pums.crosswalk import load_crosswalk, pumas_for_county

__version__ = "0.1.0"

__all__ = [
    "PumsRecord",
    "CountyControls",
    "SyntheticEstimatePoint",
    "fetch_pums",
    "load_crosswalk",
    "pumas_for_county",
    "rake",
    "estimate_county_distribution",
    "controls_from_acs_table",
    "scale_margins",
]
