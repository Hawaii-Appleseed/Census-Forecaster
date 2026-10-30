"""The sign check: does a pass move the way its mechanism predicts?

A Granger F-test is direction-blind. On 2026-08-07 US_JETFUEL →
HI_VISITORS cleared BH at all three lags while moving OPPOSITE to its
written mechanism — a "pass" that was never evidence for the claim. The
check exists so that class of error is caught automatically.

Two implementation bugs were hit while building it and are pinned here,
because both made the check actively harmful rather than merely weak:
scoring the direction at the peak-|r| lead (a seasonal echo at lead 12
outvoted the real signal at lead 3, accusing the best-verified finding
in the screen), and having no materiality floor (a mean r of +0.009
reported as "contradicting", burying real violations in noise).
"""

from __future__ import annotations

import pytest
from lab.markets.screen import (
    _AMBIGUOUS_SIGNS,
    EXPECTED_SIGN,
    HYPOTHESIS_PAIRS,
    LeadCorr,
    assert_sign_coverage,
    expected_sign,
    mean_lead_corr,
    sign_matches,
    sign_materiality,
)


def _xc(by_lead: dict[int, float], n: int = 400) -> list[LeadCorr]:
    return [LeadCorr(lead=k, r=r, n=n) for k, r in sorted(by_lead.items())]


# ---------------------------------------------------------------------------
# Coverage: no pair may skip the check silently
# ---------------------------------------------------------------------------

def test_every_registered_pair_declares_a_direction():
    assert_sign_coverage()


def test_unregistered_pair_is_an_error_not_a_silent_skip():
    """The failure mode this guard exists for: a new pair added without
    a directional claim reports sign='n/a' and looks checked."""
    with pytest.raises(ValueError, match="missing a directional claim"):
        assert_sign_coverage([("BRAND_NEW", "HI_UNEMPLOYMENT")])


def test_pair_cannot_be_both_signed_and_ambiguous():
    """A pair claiming a direction AND claiming to be two-sided is a
    contradiction — whichever table was edited second is a mistake."""
    overlap = ("HI_UI_CLAIMS", "HI_UNEMPLOYMENT")
    assert overlap in EXPECTED_SIGN and overlap not in _AMBIGUOUS_SIGNS
    assert_sign_coverage([overlap])          # fine as-is
    _AMBIGUOUS_SIGNS[overlap] = "temporary, for the test"
    try:
        with pytest.raises(ValueError, match="BOTH sign tables"):
            assert_sign_coverage([overlap])
    finally:
        del _AMBIGUOUS_SIGNS[overlap]


def test_ambiguous_entries_carry_a_written_reason():
    for pair, reason in _AMBIGUOUS_SIGNS.items():
        assert len(reason) > 40, f"{pair} needs a real justification"


# ---------------------------------------------------------------------------
# The statistic: leads 1..k, not the peak
# ---------------------------------------------------------------------------

def test_lead_zero_is_excluded():
    """Lead 0 is co-movement, not prediction; it must not decide the
    direction of a claim about leading."""
    xc = _xc({0: +0.9, 1: -0.2, 2: -0.2, 3: -0.2})
    assert mean_lead_corr(xc, 3) == pytest.approx(-0.2)


def test_mean_is_taken_over_the_tested_window_only():
    xc = _xc({1: -0.3, 2: -0.3, 3: -0.3, 12: +9.0})
    assert mean_lead_corr(xc, 3) == pytest.approx(-0.3)   # lead 12 ignored


def test_seasonal_echo_at_lead_12_cannot_outvote_the_signal():
    """The real HI_PRICE_CUTS → HONOLULU_SF_MEDIAN profile (2020
    excluded). Peak |r| sits at lead 12 (+0.322) — a 12-month offset
    between two non-seasonally-adjusted series, i.e. seasonality — while
    the mechanism's horizon, lead 3, reads -0.272 and is where the
    BH-passing test lives. Scoring the peak marked the screen's
    best-verified finding as contradicting itself."""
    profile = {0: +0.195, 1: +0.038, 2: -0.054, 3: -0.272, 4: +0.140,
               5: -0.028, 6: -0.132, 7: -0.081, 8: +0.025, 9: -0.017,
               10: +0.113, 11: -0.176, 12: +0.322}
    xc = _xc(profile, n=100)
    assert max(profile, key=lambda k: abs(profile[k])) == 12   # the trap
    assert mean_lead_corr(xc, 3) < 0                           # correct sign
    assert sign_matches("HI_PRICE_CUTS", "HONOLULU_SF_MEDIAN", xc, 3,
                        nobs=400) is True


# ---------------------------------------------------------------------------
# Materiality floor
# ---------------------------------------------------------------------------

def test_threshold_tightens_as_the_sample_grows():
    assert sign_materiality(100) == pytest.approx(0.10)
    assert sign_materiality(400) == pytest.approx(0.05)
    assert sign_materiality(None) == float("inf")
    assert sign_materiality(0) == float("inf")


def test_noise_around_zero_is_not_a_contradiction():
    """+0.009 against a negative prediction is not evidence of anything;
    reporting it as a violation is how a check stops being read."""
    xc = _xc({1: +0.009, 2: +0.009, 3: +0.009}, n=400)
    assert sign_matches("HI_PAYROLLS", "HI_UNEMPLOYMENT", xc, 3,
                        nobs=400) is None


def test_material_wrong_direction_is_flagged():
    xc = _xc({1: +0.30, 2: +0.30, 3: +0.30}, n=400)
    assert sign_matches("HI_PAYROLLS", "HI_UNEMPLOYMENT", xc, 3,
                        nobs=400) is False


def test_material_right_direction_passes():
    xc = _xc({1: -0.30, 2: -0.30, 3: -0.30}, n=400)
    assert sign_matches("HI_PAYROLLS", "HI_UNEMPLOYMENT", xc, 3,
                        nobs=400) is True


# ---------------------------------------------------------------------------
# None means "no verdict", never "passed"
# ---------------------------------------------------------------------------

def test_two_sided_pair_is_exempt_not_failed():
    xc = _xc({1: +0.5, 2: +0.5, 3: +0.5}, n=400)
    assert ("HI_PERMIT_UNITS", "HONOLULU_ZHVI") in _AMBIGUOUS_SIGNS
    assert sign_matches("HI_PERMIT_UNITS", "HONOLULU_ZHVI", xc, 3,
                        nobs=400) is None


def test_descriptive_rows_carry_no_verdict():
    """lags=0 rows are the mom12 cross-correlations — no test, so there
    is nothing for a sign to contradict."""
    xc = _xc({1: +0.5}, n=400)
    assert sign_matches("HI_PAYROLLS", "HI_UNEMPLOYMENT", xc, 0,
                        nobs=400) is None


def test_expected_sign_lookup():
    assert expected_sign("HI_UI_CLAIMS", "HI_UNEMPLOYMENT") == +1
    assert expected_sign("HI_PRICE_CUTS", "HONOLULU_SF_MEDIAN") == -1
    assert expected_sign("HI_PERMIT_UNITS", "HONOLULU_ZHVI") is None


def test_the_pair_that_motivated_the_check_is_registered_negative():
    """US_JETFUEL -> HI_VISITORS: fuel up, long-haul capacity cut,
    fewer arrivals. Observed positive; that is the whole point."""
    assert expected_sign("US_JETFUEL", "HI_VISITORS") == -1
    assert ("US_JETFUEL", "HI_VISITORS") in HYPOTHESIS_PAIRS
