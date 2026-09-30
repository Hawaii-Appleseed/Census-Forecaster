"""Regression tests for ``forecast_sb3125_vs_fy26base.py``'s regime scoring
(pipeline audit, September 29, 2026).

The TY2026-frozen baseline was scored on a frame rebuilt with
``scale_deduction_params_for_target_year(2026)`` while the other two regimes
used the target year's params. That function only moves the mortgage-interest
tiers with projected home values, an economic input rather than statute, so
the frozen regime saw TY-yr incomes with TY2026 mortgage interest. In the CD2
table that put -$32.4M into TY2027's SD-expansion effect, which is 0 by
statute (Act 46's TY2027 SD equals its TY2026 SD), and -$346.3M into the
5-year total. Every regime is now scored on one TY-yr frame; only statute
differs between them.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.adjustments.itemized_deductions import scale_deduction_params_for_target_year
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.pipeline import compute_base_tax

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fy26():
    """The script as a module. It silences warnings and logging at import;
    that is undone here so it cannot leak into the rest of the session."""
    saved_disable = logging.root.manager.disable
    sys.path.insert(0, str(REPO))  # for _forecast_common
    try:
        with warnings.catch_warnings():
            spec = importlib.util.spec_from_file_location(
                "forecast_sb3125_vs_fy26base", REPO / "forecast_sb3125_vs_fy26base.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    finally:
        logging.disable(saved_disable)
        sys.path.remove(str(REPO))
    return mod


@pytest.fixture(scope="module")
def calc():
    return TaxCalculator()


# A standard-deduction filer and itemizers with mortgage interest at every
# income level the itemized-deduction tiers distinguish.
UNITS = pd.DataFrame([
    {"income": 60_000, "filing_status": "single", "num_dependents": 0, "weight": 1.0},
    {"income": 120_000, "filing_status": "head_of_household", "num_dependents": 1, "weight": 1.0},
    {"income": 180_000, "filing_status": "married_filing_jointly", "num_dependents": 2, "weight": 1.0},
    {"income": 400_000, "filing_status": "married_filing_jointly", "num_dependents": 1, "weight": 1.0},
    {"income": 2_000_000, "filing_status": "married_filing_jointly", "num_dependents": 0, "weight": 1.0},
])


def _score(fy26, calc, yr):
    systems = fy26.regime_systems(yr, TaxSystemRegistry.get_sb3125_cd2_system)
    return fy26.score_regimes(UNITS, yr, systems, calc)


def test_sd_expansion_is_zero_when_act46_sd_is_unchanged(fy26, calc):
    """Act 46's TY2027 SD equals its TY2026 SD, so the frozen baseline and
    'frozen brackets + Act 46 TY2027 SDs' are the same law: every unit's
    SD-expansion effect must be exactly 0. Scoring the frozen regime on
    TY2026 itemized amounts made it -$4 to -$1,460 a unit here."""
    _, (tax_a, tax_b, _) = _score(fy26, calc, 2027)
    np.testing.assert_array_equal(tax_b - tax_a, 0.0)


def test_itemizers_bear_no_sd_expansion(fy26, calc):
    """By TY2031 the joint SD is $24,000 against the frozen $16,000. A unit
    itemizing more than both deducts the same under either law, so only
    standard-deduction filers see the expansion."""
    yr = 2031
    proj, (tax_a, tax_b, _) = _score(fy26, calc, yr)
    sd_frozen = np.array([calc.standard_deduction_for(TaxSystemRegistry.get_hb2306_orig_system(yr), fs)
                          for fs in proj["filing_status"]])
    sd_yr = np.array([calc.standard_deduction_for(TaxSystemRegistry.get_act46_system(yr), fs)
                      for fs in proj["filing_status"]])
    itemized = proj["hi_itemized_deduction"].to_numpy(dtype=float)
    itemizes_under_both = itemized >= np.maximum(sd_frozen, sd_yr)
    assert itemizes_under_both.any() and (~itemizes_under_both).any()
    np.testing.assert_array_equal((tax_b - tax_a)[itemizes_under_both], 0.0)
    assert ((tax_b - tax_a)[~itemizes_under_both] < 0).all()


def test_every_regime_reads_the_target_year_frame(fy26, calc):
    """The frame the regimes share carries TY-yr itemized amounts, and the
    frozen baseline's tax is its tax on that frame."""
    yr = 2029
    proj, (tax_a, _, _) = _score(fy26, calc, yr)
    expected = compute_base_tax(
        UNITS.copy(), tax_year=yr,
        deduction_params=scale_deduction_params_for_target_year(yr, geoid="15003"),
    )
    np.testing.assert_array_equal(
        proj["hi_itemized_deduction"].to_numpy(), expected["hi_itemized_deduction"].to_numpy(),
    )
    frozen_2026 = compute_base_tax(
        UNITS.copy(), tax_year=2026,
        deduction_params=scale_deduction_params_for_target_year(2026, geoid="15003"),
    )
    # The test population does itemize differently on the two bases, so the
    # check above has teeth.
    assert (proj["hi_itemized_deduction"] > frozen_2026["hi_itemized_deduction"]).any()
    cfg_a = TaxSystemRegistry.get_hb2306_orig_system(yr)
    np.testing.assert_array_equal(tax_a, calc.unit_liabilities(expected, cfg_a)["net"])


def test_regimes_differ_only_in_statute(fy26):
    """Regime b is the frozen baseline with Act 46's TY-yr standard deduction,
    so a - b isolates the SD expansion and b - c the bill's brackets."""
    for yr in (2027, 2029, 2031):
        cfg_a, cfg_b, cfg_c = fy26.regime_systems(yr, TaxSystemRegistry.get_sb3125_cd2_system)
        assert cfg_a == TaxSystemRegistry.get_hb2306_orig_system(yr)
        assert cfg_c == TaxSystemRegistry.get_sb3125_cd2_system(yr)
        assert dataclasses.replace(
            cfg_b, name=cfg_a.name, description=cfg_a.description, standard_deduction_year=2026,
        ) == cfg_a
        assert cfg_b.standard_deduction_year == cfg_c.standard_deduction_year == yr
