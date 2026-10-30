"""Tests for the statute-vs-COR wedge fields on ForwardTargets."""

from __future__ import annotations

import dataclasses

import pytest
from tax_modeler.calibration.forward_targets import build_targets


def test_wedge_fields_default_none():
    fwd = build_targets(2027, include_agi_targets=False)
    assert fwd.statutory_tax_M is None
    assert fwd.statute_vs_cor_wedge is None


def test_wedge_fields_set_via_replace():
    """project_and_recalibrate attaches the wedge with dataclasses.replace —
    the frozen dataclass must support it and preserve everything else."""
    fwd = build_targets(2027, include_agi_targets=False)
    stamped = dataclasses.replace(
        fwd,
        statutory_tax_M=3_100.0,
        statute_vs_cor_wedge=3_100.0 / fwd.aggregate_tax_M,
    )
    assert stamped.statutory_tax_M == 3_100.0
    assert stamped.statute_vs_cor_wedge == pytest.approx(3_100.0 / fwd.aggregate_tax_M)
    # Untouched fields carry over.
    assert stamped.year == fwd.year
    assert stamped.aggregate_tax_M == fwd.aggregate_tax_M
    assert stamped.filer_targets == fwd.filer_targets
    assert stamped.tax_targets == fwd.tax_targets


def test_forward_targets_still_frozen():
    fwd = build_targets(2027, include_agi_targets=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fwd.statutory_tax_M = 1.0  # type: ignore[misc]
