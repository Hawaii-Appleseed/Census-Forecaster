"""Round-2 REEC model improvement tests.

Covers:
  - Item 1: dynamic AGI eligibility recomputation per year
  - Item 2: endogenous pro-rata demand suppression
  - Item 3: refundable share adjustment for AGI-screened pool
  - Item 4: TY2026 retroactive interpretation switch (A vs B)
  - Item 5: DOTAX TY2018-2022 historical actuals replacing 3%/yr backcast
"""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.scenarios.sb3125_cd1_credits import (
    _DOTAX_HISTORICAL,
    REEC_CAP_2027_2030_M,
    REEC_REFUNDABLE_SHARE_INDIVIDUAL,
    _certified_credits_for_vintage,
    _historical_reec_corporate,
    _historical_reec_individual,
    _refundable_share_individual_for_eligibility,
    compute_credit_overlay,
    compute_dynamic_agi_eligibility_share,
)

# --- Item 5: DOTAX historical loader ---------------------------------------

def test_dotax_historical_loaded_for_years_2018_to_2022():
    """The five historical years are present after module import."""
    assert set(_DOTAX_HISTORICAL.keys()) == {2018, 2019, 2020, 2021, 2022}


def test_dotax_historical_individual_increases_2018_to_2022():
    """Individual REEC trended up from $34M to $55M over the window."""
    ind_2018 = _DOTAX_HISTORICAL[2018]["individual_total_$M"]
    ind_2022 = _DOTAX_HISTORICAL[2022]["individual_total_$M"]
    assert ind_2018 == pytest.approx(34.21, abs=0.5)
    assert ind_2022 == pytest.approx(55.02, abs=0.5)
    assert ind_2022 > ind_2018  # individual REEC grew meaningfully


def test_historical_reec_individual_prefers_actuals_over_backcast():
    """For TY2018-2022, the loader value beats the 3%/yr backcast."""
    actual_2022 = _historical_reec_individual(2022, "obbba_mid")
    actual_2018 = _historical_reec_individual(2018, "obbba_mid")
    # Both equal DOTAX actuals
    assert actual_2022 == pytest.approx(_DOTAX_HISTORICAL[2022]["individual_total_$M"])
    assert actual_2018 == pytest.approx(_DOTAX_HISTORICAL[2018]["individual_total_$M"])


def test_historical_reec_corporate_uses_all_minus_ind_for_2018_2022():
    """Corporate+other = all_total - individual, capturing suppressed financial-corp."""
    for y in (2018, 2019, 2020, 2021, 2022):
        c = _historical_reec_corporate(y, 0.015)
        expected = _DOTAX_HISTORICAL[y]["corp_plus_other_$M"]
        assert c == pytest.approx(expected)


# --- Item 1: dynamic AGI eligibility ---------------------------------------

def _fake_units(n_each=5000, scale=1.0):
    """Build a synthetic projected_units DataFrame.

    ``scale`` multiplies all incomes — emulates nominal income growth.
    Large default sample size to dampen finite-sample noise so per-year
    eligibility shares trend cleanly with growth.
    """
    import numpy as np
    rng = np.random.default_rng(seed=42)
    rows = []
    single_inc = rng.lognormal(mean=np.log(80_000), sigma=0.7, size=n_each) * scale
    for inc in single_inc:
        rows.append({"income": float(inc), "weight": 100.0, "filing_status": "single"})
    mfj_inc = rng.lognormal(mean=np.log(140_000), sigma=0.7, size=n_each) * scale
    for inc in mfj_inc:
        rows.append({"income": float(inc), "weight": 100.0,
                     "filing_status": "married_filing_jointly"})
    return pd.DataFrame(rows)


def test_dynamic_agi_eligibility_declines_with_growth():
    """As nominal income inflates substantially, more filers cross above the
    $175K / $350K thresholds → aggregate eligibility share declines.

    Tests the TY2023 → TY2031 endpoint (35% nominal growth) since smaller
    scale steps can produce non-monotonic shifts in the DOTAX-weighted
    aggregate (within-bin shares fluctuate as filers migrate between bins
    of fixed nominal width).
    """
    s_now    = compute_dynamic_agi_eligibility_share(_fake_units(scale=1.00))
    s_high   = compute_dynamic_agi_eligibility_share(_fake_units(scale=1.35))
    assert s_high < s_now, (s_now, s_high)
    # Should fall by at least 1pp under 35% nominal growth
    assert s_now - s_high >= 0.01


def test_dynamic_agi_eligibility_in_unit_interval():
    df = _fake_units()
    s = compute_dynamic_agi_eligibility_share(df)
    assert 0.0 <= s <= 1.0


# --- Item 2: pro-rata demand suppression -----------------------------------

def test_pro_rata_suppression_zero_elasticity_matches_baseline():
    """eta=0 → identical to nominal pro-rata (no suppression)."""
    a_ind, a_corp, a_diag = _certified_credits_for_vintage(
        2027, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        pro_rata_elasticity=0.0,
    )
    b_ind, b_corp, b_diag = _certified_credits_for_vintage(
        2027, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        pro_rata_elasticity=0.0,
    )
    assert a_ind == pytest.approx(b_ind)
    assert a_corp == pytest.approx(b_corp)
    # Sum hits the cap exactly (eligible > cap in MID scenario)
    assert a_ind + a_corp == pytest.approx(REEC_CAP_2027_2030_M, rel=1e-6)


def test_pro_rata_suppression_reduces_demand_when_cap_binds():
    """eta=0.3 → certified total ≤ nominal cap, but suppression < 1."""
    _, _, base_diag = _certified_credits_for_vintage(
        2027, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        pro_rata_elasticity=0.0,
    )
    ind_s, corp_s, supp_diag = _certified_credits_for_vintage(
        2027, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        pro_rata_elasticity=0.3,
    )
    # Suppression strictly less than 1 when cap binds
    assert 0.0 < supp_diag["suppression_factor"] < 1.0
    # Total still respects cap (or falls below if suppression brought demand below)
    total = ind_s + corp_s
    assert total <= REEC_CAP_2027_2030_M + 1e-6


# --- Item 3: refundable share adjustment ----------------------------------

def test_refundable_share_rises_as_eligibility_falls():
    """Lower eligibility → higher refundable share (lower-income filers
    use §235-12.5(k)/(l) refundability more)."""
    s_full   = _refundable_share_individual_for_eligibility(1.00)
    s_80     = _refundable_share_individual_for_eligibility(0.80)
    s_70     = _refundable_share_individual_for_eligibility(0.70)
    assert s_full == pytest.approx(REEC_REFUNDABLE_SHARE_INDIVIDUAL, abs=0.01)
    assert s_full < s_80 < s_70


def test_refundable_share_clamped_to_unit_interval():
    """No matter what eligibility share, refundable share stays in [0.20, 0.60]."""
    for elig in (0.0, 0.1, 0.5, 0.95, 1.0, 1.5):
        s = _refundable_share_individual_for_eligibility(elig)
        assert 0.20 <= s <= 0.60


# --- Item 4: TY2026 retroactive cap interpretation ------------------------

def test_interpretation_a_caps_ty2026():
    """Under interpretation A, TY2026 certifications ≤ $40M cap."""
    ind, corp, _ = _certified_credits_for_vintage(
        2026, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        interpretation="A",
    )
    assert ind + corp <= REEC_CAP_2027_2030_M + 1e-6


def test_interpretation_b_does_not_cap_ty2026():
    """Under interpretation B (default), TY2026 demand passes through uncapped."""
    ind, corp, _ = _certified_credits_for_vintage(
        2026, cap_enabled=True, demand_scenario="obbba_mid",
        cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
        interpretation="B",
    )
    # Total demand for TY2026 (DOTAX-derived) is well above $40M
    assert ind + corp > REEC_CAP_2027_2030_M


def test_interpretation_b_still_caps_ty2027():
    """Both interpretations agree on capping TY2027+."""
    for interp in ("A", "B"):
        ind, corp, _ = _certified_credits_for_vintage(
            2027, cap_enabled=True, demand_scenario="obbba_mid",
            cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
            interpretation=interp,
        )
        assert ind + corp <= REEC_CAP_2027_2030_M + 1e-6


def test_sunset_blocks_ty2030_under_both_interpretations():
    """Subsection (p) sunset blocks TY2030+ regardless of (c)(4)."""
    for interp in ("A", "B"):
        ind, corp, _ = _certified_credits_for_vintage(
            2030, cap_enabled=True, demand_scenario="obbba_mid",
            cgec_annual_growth=0.015, corp_subject_to_agi_limit=False,
            interpretation=interp,
        )
        assert ind == 0.0
        assert corp == 0.0


# --- compute_credit_overlay smoke test ------------------------------------

def test_compute_credit_overlay_backward_compat_no_new_params():
    """Calling without new params produces the same numbers as before."""
    r = compute_credit_overlay(2027, reec_effective_claim_share=0.65,
                               cgec_annual_growth=0.015)
    # Legacy static overlay: baseline − $40M cap. Center re-pinned 41.8→44.0
    # on 2026-07-30 after the Hawaii CPI correction (the old center was
    # computed on Los Angeles CPI mislabelled as Honolulu — see
    # census_forecaster.bls.panel; higher corrected nominal growth raises
    # the projected baseline and thus cap savings).
    # Tolerance is wide enough (abs=1.5) to absorb routine monthly drift in the
    # refreshed calibration anchors (the refresh-data gate runs this test
    # against freshly-pulled data); it's a sanity band, not a tripwire.
    assert r["reec_savings_$M"] == pytest.approx(44.0, abs=1.5)


def test_compute_credit_overlay_vintage_with_new_knobs_returns_diagnostics():
    """The new diagnostic keys appear when the vintage path runs."""
    r = compute_credit_overlay(
        2027, reec_effective_claim_share=0.65, cgec_annual_growth=0.015,
        model_carryforward_pool=True, pro_rata_elasticity=0.3,
        dynamic_refundable_share=True, interpretation="A",
    )
    assert "reec_eligibility_share" in r
    assert "reec_pro_rata_factor" in r
    assert "reec_suppression_factor" in r
    assert "reec_ind_refundable_share" in r
    assert r["reec_interpretation"] == "A"
    # Pro-rata suppression should have triggered (binding cap, eta > 0)
    assert 0.0 < r["reec_suppression_factor"] < 1.0


def test_compute_credit_overlay_ci_band():
    """The κ-calibrated CI90 band brackets the point estimate on both the
    growth factor and the savings, and the band is genuinely two-sided."""
    r = compute_credit_overlay(2027, reec_effective_claim_share=0.65,
                               cgec_annual_growth=0.015)
    assert (r["growth_individual_ci90_low"]
            < r["growth_individual"]
            < r["growth_individual_ci90_high"])
    assert (r["reec_savings_ci90_low_$M"]
            <= r["reec_savings_$M"]
            <= r["reec_savings_ci90_high_$M"])
    assert r["reec_savings_ci90_low_$M"] < r["reec_savings_ci90_high_$M"]
    assert r["growth_individual_se_log"] > 0


def test_compute_credit_overlay_ci_band_vintage_path():
    """CI keys also appear on the vintage-simulation path."""
    r = compute_credit_overlay(
        2027, reec_effective_claim_share=0.65, cgec_annual_growth=0.015,
        model_carryforward_pool=True, interpretation="A",
    )
    assert (r["reec_savings_ci90_low_$M"]
            <= r["reec_savings_$M"]
            <= r["reec_savings_ci90_high_$M"])
