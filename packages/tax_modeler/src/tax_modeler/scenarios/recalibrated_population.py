"""The MID population on the recalibration path (``project_and_recalibrate``).

``chain/forecast_sb3125_vs_fy26base.py`` and ``chain/forecast_cg_rate_options.py``
score this population rather than the Act 24 page's
(:mod:`tax_modeler.scenarios.act24_population`): each year is projected with
forward targets, the SOI anchor for the $1M+ tiers, CBO component aging and
the top-income premium, then re-scored on that year's itemized deductions.
Both steps used to spell out the same ten projection arguments; they live
here once.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tax_modeler.scenarios.act24_population import CalibratedBase


def build_units(base: CalibratedBase, *, alpha: float) -> tuple[pd.DataFrame, float]:
    """The calibrated base plus the synthetic $1M+ tail: ``(units, tail_k)``.

    The tail is not aged to the PUMS dollar year: :func:`project`'s SOI anchor
    zeroes every $1M+ unit and replaces them with SOI tiers aged from TY2022,
    so its level is moot."""
    from tax_modeler.base import build_tail_units

    return build_tail_units(base.frame, alpha=alpha, ded_params=base.ded_params,
                            cal_tax_year=base.tax_year)


def project(units: pd.DataFrame, *, year: int, top_premium: float):
    """``project_and_recalibrate`` to ``year``: ``(projected, forward_targets)``."""
    from tax_modeler.calibration.year_recalibrator import project_and_recalibrate

    return project_and_recalibrate(
        units,
        target_year=year,
        use_forward_targets=True,
        use_soi_anchor=True,
        soi_year=2022,
        hawaii_capgain_adjustment=0.95,
        use_cbo_aging=True,
        cbo_vintage="2025-01",
        top_premium_pct=top_premium,
        top_bracket_differential=0.025,
        method="ensemble",
    )


def rescore(projected: pd.DataFrame, *, year: int) -> pd.DataFrame:
    """A copy of ``projected`` re-scored with TY-``year`` itemized-deduction
    params (the mortgage-interest tiers scale with projected home values)."""
    from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year
    from tax_modeler.pipeline import compute_base_tax

    ded_params = scale_deduction_params_for_target_year(year, geoid="15003")
    return compute_base_tax(projected.copy(), tax_year=year, deduction_params=ded_params)
