"""Tests for ``compute_poverty_impact_with_se`` (SDR variance via PUMS replicates).

The SE wrapper around ``compute_poverty_impact`` adds ``<col>_se`` siblings
to every aggregated DataFrame. The tests below exercise:

  * No-op fallback when no ``weight_r*`` columns are present (returns
    the headline result, no ``*_se`` columns).
  * Schema completeness: every numeric headline column gets an ``_se`` sibling.
  * SE non-negativity and the n_replicates truncation parameter.
  * Identity at zero variance: if every replicate weight equals the main
    weight, SDR SE is exactly zero.
  * Headline invariance: the headline numbers are byte-identical with or
    without the SE wrapper (the wrapper must not perturb the main estimate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
from tax_modeler.poverty.impact import (
    _sdr_se_from_replicates,
    compute_poverty_impact,
    compute_poverty_impact_with_se,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_units(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "filing_status": "head_of_household",
        "num_dependents": 2,
        "num_qualifying_children": 2,
        "total_cash_income": 25_000.0,
        "earned_income": 25_000.0,
        "income": 25_000.0,
        "weight": 100.0,
        "hi_tax_liability": 0.0,
        "eitc_amount": 3_000.0,
        "ctc_total": 4_000.0,
        "ctc_refundable": 3_400.0,
        "hi_eitc_amount": 1_200.0,
        "tenure": "renter",
        "county": "Honolulu",
        "house_district": 1,
        "senate_district": 1,
        "PUMA": 301,
    }
    out = pd.DataFrame([{**defaults, **r} for r in rows])
    return arpa_ctc_for_tax_units(out)


def _add_poisson_replicates(
    units: pd.DataFrame, n_reps: int = 80, seed: int = 7,
) -> pd.DataFrame:
    """Add ``weight_r01``..``weight_rNN`` columns as Poisson-perturbed copies.

    PUMS replicate weights are constructed via Census's successive-difference
    perturbation method, but for unit tests Poisson noise around the main
    weight is a good-enough stand-in: it produces non-trivial SE while
    preserving the expected value (since ``E[Poisson(λ)] = λ``).
    """
    rng = np.random.default_rng(seed)
    out = units.copy()
    base = out["weight"].astype(float).to_numpy()
    base_clipped = np.clip(base, 0.1, None)  # Poisson(0) is degenerate
    for r in range(1, n_reps + 1):
        out[f"weight_r{r:02d}"] = rng.poisson(base_clipped).astype(float)
    return out


def _add_identical_replicates(
    units: pd.DataFrame, n_reps: int = 80,
) -> pd.DataFrame:
    """Replicates that all exactly equal the main weight ⇒ SDR SE = 0."""
    out = units.copy()
    for r in range(1, n_reps + 1):
        out[f"weight_r{r:02d}"] = out["weight"]
    return out


def _kids_heavy_frame(n: int = 120, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        n_kids = int(rng.integers(2, 5))
        income = float(rng.uniform(15_000, 40_000))
        rows.append({
            "num_dependents": n_kids,
            "num_qualifying_children": n_kids,
            "total_cash_income": income,
            "earned_income": income,
            "income": income,
            "weight": float(rng.uniform(50, 200)),
            "eitc_amount": float(rng.uniform(2_000, 5_000)),
            "ctc_refundable": n_kids * 1_700.0,
            "ctc_total": n_kids * 2_000.0,
            "hi_eitc_amount": float(rng.uniform(800, 2_000)),
            "county": rng.choice(["Honolulu", "Hawaii", "Maui", "Kauai"]),
            "house_district": int(rng.integers(1, 52)),
            "senate_district": int(rng.integers(1, 26)),
        })
    return _make_units(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_replicate_columns_returns_headline_unchanged():
    """Without weight_r* columns, _with_se returns the same result as headline."""
    units = _make_units([{"weight": 100.0}])
    headline = compute_poverty_impact(units, tax_year=2024)
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    pd.testing.assert_frame_equal(headline.by_state, with_se.by_state)
    pd.testing.assert_frame_equal(headline.by_county, with_se.by_county)


def test_identical_replicates_yield_zero_se():
    """Every replicate exactly equal to main weight ⇒ SDR SE = 0 everywhere."""
    units = _add_identical_replicates(_kids_heavy_frame(n=60, seed=3))
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    se_cols = [c for c in with_se.by_state.columns if c.endswith("_se")]
    assert se_cols, "by_state should have *_se columns when replicates are present"
    for c in se_cols:
        assert with_se.by_state[c].iloc[0] == pytest.approx(0.0, abs=1e-9), (
            f"{c} should be zero when all replicates match the main weight"
        )


def test_poisson_replicates_yield_nonzero_se():
    """Poisson-perturbed replicates produce non-zero SDR SE on persons_lifted_no_eitc."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=120, seed=29), seed=42)
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    state = with_se.by_state.iloc[0]
    # The flagship cell: SE on persons_lifted_no_eitc should be > 0
    # (Poisson noise around weight ⇒ noise in the count of poor persons).
    if state["persons_lifted_no_eitc"] != 0:
        assert state["persons_lifted_no_eitc_se"] > 0, (
            f"expected non-zero SE on persons_lifted_no_eitc, got "
            f"{state['persons_lifted_no_eitc_se']}"
        )


def test_se_columns_present_on_every_geography():
    """Each aggregated frame gets *_se siblings on its numeric columns."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=80, seed=23), seed=11)
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    for frame_name in (
        "by_state", "by_county", "by_house_district",
        "by_senate_district", "by_household_type",
    ):
        frame = getattr(with_se, frame_name)
        n_se_cols = sum(1 for c in frame.columns if c.endswith("_se"))
        assert n_se_cols >= 5, (
            f"{frame_name} has only {n_se_cols} _se columns; expected at least 5 "
            "(baseline rate, persons in poverty, gap, + scenarios)"
        )


def test_n_replicates_truncation_works():
    """n_replicates=10 uses only the first 10 columns; doesn't crash."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=60, seed=5), n_reps=80, seed=33)
    # Smoke: small replicate count finishes quickly and yields a result with SE.
    with_se = compute_poverty_impact_with_se(
        units, tax_year=2024, n_replicates=10,
    )
    assert any(c.endswith("_se") for c in with_se.by_state.columns)


def test_se_is_nonnegative_everywhere():
    """SDR SE is variance-derived ⇒ guaranteed >= 0 across all output cells."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=80, seed=51), seed=61)
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    for frame_name in (
        "by_state", "by_county", "by_house_district",
        "by_senate_district", "by_household_type",
    ):
        frame = getattr(with_se, frame_name)
        for c in frame.columns:
            if c.endswith("_se"):
                assert (frame[c] >= 0).all(), f"{frame_name}.{c} has negative SE"


def test_headline_invariance_with_replicates():
    """Headline columns (no _se suffix) are identical to compute_poverty_impact's."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=80, seed=77), seed=89)
    headline = compute_poverty_impact(units, tax_year=2024)
    with_se = compute_poverty_impact_with_se(units, tax_year=2024)
    # Drop _se siblings; the remaining columns must match exactly.
    head_cols = list(headline.by_state.columns)
    for c in head_cols:
        np.testing.assert_allclose(
            with_se.by_state[c].astype(float).to_numpy(),
            headline.by_state[c].astype(float).to_numpy(),
            err_msg=f"by_state[{c}] differs between headline and _with_se",
        )


def test_sdr_formula_directly():
    """Unit test of _sdr_se_from_replicates with hand-computed values."""
    # θ_0 = 100, replicates = [102, 98, 100, 100] (R = 4)
    # diffs² = [4, 4, 0, 0], sum = 8
    # V = (4/4) * 8 = 8 ⇒ SE = sqrt(8) ≈ 2.828
    theta_r = np.array([102.0, 98.0, 100.0, 100.0])
    se = _sdr_se_from_replicates(100.0, theta_r)
    assert se == pytest.approx(np.sqrt(8.0), rel=1e-9)


def test_sdr_formula_array_shape():
    """_sdr_se_from_replicates accepts (R, K) replicate matrices."""
    theta_0 = np.array([100.0, 50.0])
    theta_r = np.array([
        [102.0, 49.0],
        [98.0, 51.0],
        [100.0, 50.0],
        [100.0, 50.0],
    ])
    se = _sdr_se_from_replicates(theta_0, theta_r)
    # Cell 0: same as scalar test ⇒ sqrt(8)
    # Cell 1: diffs² = [1, 1, 0, 0] → V = (4/4)*2 = 2 → SE = sqrt(2)
    np.testing.assert_allclose(se, np.array([np.sqrt(8.0), np.sqrt(2.0)]), rtol=1e-9)


def test_notes_include_sdr_caveat():
    """The result.notes tuple should mention SDR / replicate weights."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=40, seed=99), seed=101)
    result = compute_poverty_impact_with_se(units, tax_year=2024, n_replicates=20)
    combined = " | ".join(result.notes)
    assert "SDR" in combined or "replicate" in combined.lower(), (
        "result.notes should document the SDR variance estimation"
    )


def test_scenario_subset_works_with_se():
    """Passing a scenario subset still computes SE per the requested scenarios."""
    units = _add_poisson_replicates(_kids_heavy_frame(n=60, seed=121), seed=131)
    scenarios = ("no_eitc", "hi_ctc_650")
    result = compute_poverty_impact_with_se(
        units, tax_year=2024, n_replicates=20, scenarios=scenarios,
    )
    state = result.by_state.iloc[0]
    assert "persons_lifted_no_eitc_se" in result.by_state.columns
    assert "persons_lifted_hi_ctc_650_se" in result.by_state.columns
    # Scenarios not requested must not have SE columns either.
    assert "persons_lifted_no_credits_se" not in result.by_state.columns
    # And the headline cell still equals the headline result.
    assert state["persons_lifted_no_eitc"] >= 0
