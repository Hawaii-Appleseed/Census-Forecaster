"""Structure tests for the experimental attention-term universe.

Network-dependent fetching is deliberately untested (unofficial
endpoints); these pin the pre-registration contract only.
"""
from __future__ import annotations

from lab.markets.attention import DEFAULT_TIME_WINDOW, TERMS
from lab.markets.screen import MONTHLY_TARGETS


def test_terms_are_preregistered_with_hypotheses():
    assert len(TERMS) >= 3
    for spec in TERMS:
        assert spec.term and spec.hypothesis
        assert spec.geo in ("US", "US-HI")


def test_monthly_target_affinities_resolve():
    """Every declared target must exist in the screen's registry —
    a dangling affinity would silently drop out of any future screen."""
    for spec in TERMS:
        for target in spec.monthly_targets:
            assert target in MONTHLY_TARGETS, (spec.term, target)


def test_time_window_is_pinned():
    """The fetch window is a fixed constant — normalization is only
    window-stable if every fetch uses the same window."""
    start, _, end = DEFAULT_TIME_WINDOW.partition(" ")
    assert start == "2010-01-01"
    assert len(end) == 10
