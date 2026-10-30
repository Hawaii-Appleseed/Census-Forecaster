"""Tests for the reverse-direction (fundamentals -> ticker) experiment.

Structural pins only — the empirical outcome lives in METHODOLOGY (it
was a clean EMH null on the 2026-07 panels) and the bundled data
refreshes monthly, so tests assert the contract, not the verdict.
"""
from __future__ import annotations

import json
from importlib.resources import files

import pytest

from lab.markets.fundamentals import (
    FUNDAMENTALS,
    REVERSE_PAIRS,
    run_reverse_screen,
    walkforward_return_ablation,
)
from lab.markets.panel import load_prices_panel


@pytest.fixture(scope="module")
def macro():
    return json.loads(
        (files("census_forecaster") / "data" / "markets"
         / "macro_monthly.json").read_text())["series"]


def test_registry_resolves(macro):
    """Every fundamental maps to a bundled macro series, every pair to a
    panel ticker, and availability lags are declared."""
    panel = load_prices_panel()
    fund_names = {s.name for s in FUNDAMENTALS}
    for spec in FUNDAMENTALS:
        assert spec.macro_series_id in macro
        assert spec.availability_lag_months >= 1
        assert spec.revision_caveat
    for fund, ticker, hypothesis in REVERSE_PAIRS:
        assert fund in fund_names
        assert ticker in panel.symbols()
        assert hypothesis


def test_reverse_screen_structure(macro):
    rep = run_reverse_screen(macro)
    assert rep["n_tests"] > 0
    base = [t for t in rep["candidates"] if not t["exclude_2020"]]
    assert any(t["granger_p"] is not None for t in base)
    for t in rep["candidates"]:
        assert isinstance(t["bh_pass"], bool)
    # Every base test has a matching 2020-exclusion rerun.
    excl = [t for t in rep["candidates"] if t["exclude_2020"]]
    assert len(excl) == len(base)


def test_ablation_uses_availability_lags(macro):
    rows = walkforward_return_ablation(macro)
    assert len(rows) == len(REVERSE_PAIRS)
    ran = [r for r in rows if r.get("n_forecasts", 0) > 0]
    assert ran, "no pair had enough overlap to ablate"
    for r in ran:
        assert r["availability_lag_months"] >= 1
        assert r["rmse_zero"] > 0 and r["rmse_signal"] > 0
        assert isinstance(r["signal_beats_both"], bool)
