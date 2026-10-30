"""Tests for the empirical α calibration of ``scale_eitc_for_poverty``.

Covers:
  * Synthetic-panel fit recovers a known α exactly.
  * Real-panel fit lands in the plausible band (currently α ≈ 0.71 from
    a 2-year-pair stable window).
  * ``write_calibration_artifact`` round-trips with ``from_dict``-style
    reconstruction.
  * ``_build_year_pairs`` only emits consecutive years.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration.eitc_alpha_calibration import (
    AlphaCalibration,
    _build_year_pairs,
    calibrate_eitc_poverty_alpha,
    write_calibration_artifact,
)

# ---------------------------------------------------------------------------
# Synthetic-panel recovery
# ---------------------------------------------------------------------------

def _build_synthetic_panel(
    *, alpha_true: float, n_pairs: int, seed: int = 0,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build IRS + ACS series consistent with a known α.

    Each year-pair has b19013_factor and poverty_factor drawn iid from
    log-normal(±5%). Observed EITC growth is constructed to satisfy
    ``log(eitc_growth) = log(b19013_factor) + α · log(poverty_factor)``
    exactly, so the OLS fit must recover α with zero residuals.
    """
    rng = np.random.default_rng(seed)
    years = list(range(2010, 2010 + n_pairs + 1))
    b19013 = pd.Series(
        [40_000.0 * np.exp(rng.normal(0, 0.05)) for _ in years],
        index=years,
    )
    s1701 = pd.Series(
        [10.0 * np.exp(rng.normal(0, 0.05)) for _ in years],
        index=years,
    )
    # Build EITC counts consistent with α exactly
    eitc = [100_000.0]
    for i in range(1, len(years)):
        b_fac = b19013.iloc[i] / b19013.iloc[i - 1]
        p_fac = s1701.iloc[i] / s1701.iloc[i - 1]
        eitc.append(eitc[-1] * b_fac * (p_fac ** alpha_true))
    irs = pd.DataFrame({"year": years, "eitc_returns": eitc})
    return irs, b19013, s1701


def test_synthetic_panel_recovers_known_alpha():
    """OLS fit recovers a known α to ≤1e-9 on a noise-free synthetic panel."""
    alpha_true = 0.42
    irs, b19013, s1701 = _build_synthetic_panel(alpha_true=alpha_true, n_pairs=8)
    pairs = _build_year_pairs(irs=irs, b19013=b19013, s1701=s1701)
    assert len(pairs) == 8, f"expected 8 year-pairs, got {len(pairs)}"

    y = np.log(np.array([p["eitc_factor"] / p["b19013_factor"] for p in pairs]))
    x = np.log(np.array([p["poverty_factor"] for p in pairs]))
    alpha = float(np.sum(x * y) / np.sum(x ** 2))
    assert alpha == pytest.approx(alpha_true, abs=1e-9), (
        f"expected α={alpha_true}, got {alpha}"
    )


def test_year_pairs_skip_non_consecutive():
    """``_build_year_pairs`` only emits pairs where year_2 = year_1 + 1."""
    # IRS panel skips 2016 (e.g., when the IRS XLSX was unavailable)
    irs = pd.DataFrame({
        "year": [2015, 2017, 2018, 2019],
        "eitc_returns": [100_000.0, 102_000.0, 95_000.0, 96_000.0],
    })
    b19013 = pd.Series([40_000, 41_000, 42_000, 43_000],
                       index=[2015, 2017, 2018, 2019])
    s1701 = pd.Series([10.0, 9.5, 9.7, 9.3],
                      index=[2015, 2017, 2018, 2019])
    pairs = _build_year_pairs(irs=irs, b19013=b19013, s1701=s1701)
    pair_years = [p["year_pair"] for p in pairs]
    # 2015→2016 missing, 2016→2017 missing; 2017→2018 and 2018→2019 OK
    assert pair_years == [(2017, 2018), (2018, 2019)]


# ---------------------------------------------------------------------------
# Real-panel fit
# ---------------------------------------------------------------------------

def test_real_panel_alpha_in_plausible_band():
    """The bundled IRS + ACS panel yields α in [0.0, 1.5] (sanity).

    The fit currently lands ~0.71 on the n=2 stable window, but we
    keep the assertion band wide since the precision is limited and
    future data may shift the value. Anything outside [0, 1.5] would
    suggest a regression spec or data-pipeline bug.
    """
    cal = calibrate_eitc_poverty_alpha()
    assert 0.0 <= cal.alpha <= 1.5, (
        f"α={cal.alpha:.4f} outside the plausible band [0, 1.5]"
    )
    # RMSE should be small on the stable subset (we excluded the shock years)
    assert cal.rmse < 0.1, f"RMSE={cal.rmse:.4f} suspiciously high"
    assert cal.n_year_pairs >= 2, (
        f"expected ≥ 2 year-pairs; got {cal.n_year_pairs}"
    )


def test_real_panel_excludes_arpa_years_by_default():
    cal = calibrate_eitc_poverty_alpha()
    arpa_years = {(2019, 2020), (2020, 2021), (2021, 2022)}
    assert not (arpa_years & set(cal.year_pairs)), (
        f"ARPA / COVID years leaked into fit: {set(cal.year_pairs) & arpa_years}"
    )


def test_keep_all_pairs_changes_fit():
    """Passing ``exclude_pairs=()`` should change α (often dramatically)
    because the ARPA / COVID shocks dominate the regression signal."""
    cal_stable = calibrate_eitc_poverty_alpha()
    cal_all = calibrate_eitc_poverty_alpha(exclude_pairs=())
    assert cal_stable.n_year_pairs < cal_all.n_year_pairs
    # The two should yield meaningfully different α (otherwise the
    # exclusion isn't doing what we think it is).
    assert abs(cal_stable.alpha - cal_all.alpha) > 0.5, (
        f"stable α={cal_stable.alpha:.3f}, all α={cal_all.alpha:.3f} — "
        "exclusion should produce a large shift"
    )


# ---------------------------------------------------------------------------
# Artifact serialization
# ---------------------------------------------------------------------------

def test_write_calibration_artifact_round_trips(tmp_path: Path):
    cal = AlphaCalibration(
        alpha=0.71,
        rmse=0.034,
        n_year_pairs=2,
        year_pairs=((2017, 2018), (2018, 2019)),
        residuals=(-0.022, -0.043),
        geoid_used="15003",
        vintage="1y",
        irs_panel_years=(2015, 2017, 2018, 2019, 2020, 2021, 2022),
        notes=("test note",),
    )
    out = write_calibration_artifact(cal, out_path=tmp_path / "fit.json")
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["alpha"] == 0.71
    assert payload["rmse"] == 0.034
    assert payload["n_year_pairs"] == 2
    assert payload["year_pairs"] == [[2017, 2018], [2018, 2019]]
    assert payload["geoid_used"] == "15003"
    assert payload["notes"] == ["test note"]
