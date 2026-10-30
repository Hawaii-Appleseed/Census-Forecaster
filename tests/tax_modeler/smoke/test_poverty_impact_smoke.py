"""End-to-end smoke test for poverty_impact_report on the synthetic fixture.

Skipped if the synthetic fixture is missing. Real-Hawaii-PUMS verification
is done manually via `python scripts/poverty_impact_report.py ...` since
the PUMS files are gitignored.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
PERSONS = FIXTURE_DIR / "synthetic_pums_persons.parquet"
HOUSEHOLDS = FIXTURE_DIR / "synthetic_pums_households.parquet"


@pytest.fixture(scope="module")
def fixture_units():
    if not PERSONS.exists() or not HOUSEHOLDS.exists():
        pytest.skip(f"synthetic PUMS fixture missing at {FIXTURE_DIR}")
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from poverty_impact_report import (
        _apply_arpa_ctc,
        _apply_hi_eitc,
        _build_units_for_tax_year,
        _load_units,
    )
    units, _persons = _load_units(None, use_fixture=True)
    units = _build_units_for_tax_year(units, tax_year=2024, project=True)
    units = _apply_hi_eitc(units, tax_year=2024)
    units = _apply_arpa_ctc(units)
    return units


def test_impact_runs_end_to_end(fixture_units):
    from tax_modeler.poverty.impact import compute_poverty_impact

    result = compute_poverty_impact(fixture_units, tax_year=2024)
    assert result.tax_year == 2024
    # State-level row must exist and carry plausible columns.
    s = result.by_state.iloc[0]
    assert 0.0 <= s["poverty_rate_baseline"] <= 1.0
    assert s["weighted_persons"] > 0
    # Sum across counties (if county column is non-null) should be ≤ state.
    if not result.by_county["county"].isna().all():
        counties = result.by_county.dropna(subset=["county"])
        assert counties["weighted_persons"].sum() == pytest.approx(s["weighted_persons"])


def test_at_least_one_scenario_strictly_changes_resources(fixture_units):
    """At least one scenario must produce different SPM resources from baseline.

    Defends against silent bugs where the scenario application is a no-op
    (e.g. wrong column name).
    """
    from tax_modeler.poverty.impact import _DEFAULT_SCENARIOS, compute_poverty_impact

    result = compute_poverty_impact(fixture_units, tax_year=2024)
    df = result.units
    baseline = df["spm_resources"].to_numpy()
    changed = []
    for scn in _DEFAULT_SCENARIOS:
        cf = df[f"spm_resources_{scn}"].to_numpy()
        if not (cf == baseline).all():
            changed.append(scn)
    assert len(changed) >= 3, (
        f"Only {len(changed)} scenarios changed resources: {changed}"
    )


def test_gap_closed_by_combined_credits_positive(fixture_units):
    """Removing all credits should produce a wider poverty gap (gap_closed > 0
    for the no_credits scenario, since gap_closed_no_credits = gap_no_credits
    - gap_baseline, and removing money makes the gap wider)."""
    from tax_modeler.poverty.impact import compute_poverty_impact

    result = compute_poverty_impact(fixture_units, tax_year=2024)
    s = result.by_state.iloc[0]
    assert s["gap_closed_no_credits_$"] >= 0, (
        f"Removing credits should widen the gap; got "
        f"gap_closed_no_credits = {s['gap_closed_no_credits_$']}"
    )
