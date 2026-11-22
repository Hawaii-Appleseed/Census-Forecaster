"""Every constant that means "the DOTAX base edition's year" is that one constant (calibration.dotax_base.BASE_YEAR),
so a new edition moves them together. Until 2026-11-21 there were separate literals, two of them tied by tests."""
from __future__ import annotations

import pytest
from tax_modeler import base
from tax_modeler.calibration import dotax_base
from tax_modeler.calibration import irs_soi_hi_table2 as irs
from tax_modeler.units import dependencies


def test_the_anchor_year_has_one_definition():
    assert base.BASE_TAX_YEAR == dotax_base.BASE_YEAR
    assert irs.BASE_YEAR == dotax_base.BASE_YEAR
    assert dependencies._DEFAULT_DEP_TAX_YEAR == dotax_base.BASE_YEAR


def test_the_tables_keyed_by_it_cover_it():
    assert dotax_base.BASE_YEAR in dependencies._QR_GROSS_INCOME_LIMIT_BY_YEAR
    assert irs.values("returns", dotax_base.BASE_YEAR)


def test_the_credit_baselines_keep_their_own_vintage():
    """sb3125_cd1_credits.BASE_YEAR is the year of its hand-typed credit baselines, which do not move with a new
    individual-income edition; it is deliberately not tied to dotax_base.BASE_YEAR."""
    from tax_modeler.scenarios import sb3125_cd1_credits

    assert sb3125_cd1_credits.BASE_YEAR == 2023


def test_the_synthesizer_has_no_stale_default_targets():
    import pandas as pd
    from tax_modeler.adjustments.ultra_high_income_synthesizer_v2 import (
        UltraHighIncomeSynthesizerV2,
        apply_ultra_high_income_synthesis_v2,
    )

    with pytest.raises(TypeError):
        UltraHighIncomeSynthesizerV2()
    with pytest.raises(TypeError):
        UltraHighIncomeSynthesizerV2(total_1m_filers=1_000).calibrate(pd.DataFrame())
    with pytest.raises(TypeError):
        apply_ultra_high_income_synthesis_v2(pd.DataFrame())
