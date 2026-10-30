"""Tests for the ticker forecast board + EWMA vol option (July 2026)."""
from __future__ import annotations

import json

import pytest
from lab.markets.forecaster import (
    DEFAULT_HORIZONS,
    forecast_board,
    main,
)
from lab.markets.panel import load_prices_panel
from lab.markets.trend import _monthly_vol, forecast_ticker


@pytest.fixture(scope="module")
def panel():
    return load_prices_panel()


def test_ewma_vol_differs_from_rolling(panel):
    bars = panel.bars("SPY")
    r = _monthly_vol(bars, method="rolling")
    e = _monthly_vol(bars, method="ewma")
    assert r and e and r > 0 and e > 0
    assert r != pytest.approx(e)


def test_forecast_ticker_default_unchanged(panel):
    """Back-compat: the default vol method is still 'rolling' — existing
    callers' numbers must not move."""
    from datetime import date
    bars = panel.bars("SPY")
    a = forecast_ticker(bars, date(2027, 6, 28))
    b = forecast_ticker(bars, date(2027, 6, 28), vol_method="rolling")
    assert a.monthly_vol == pytest.approx(b.monthly_vol)
    assert a.lo90 == pytest.approx(b.lo90)


def test_unknown_vol_method_raises(panel):
    with pytest.raises(ValueError):
        _monthly_vol(panel.bars("SPY"), method="garch")


def test_board_structure(panel):
    rows = forecast_board(panel, horizons=(3, 12))
    symbols = {r.symbol for r in rows}
    assert len(symbols) >= 9
    for r in rows:
        assert r.lo90 <= r.point <= r.hi90
        assert r.vol_method == "ewma"
        assert r.vol_regime in ("elevated", "normal", "calm")
        assert r.horizon_months in (3, 12)
        assert isinstance(r.leading_indicator, bool)
    # The forward-screen survivors are flagged on the board.
    flagged = {r.symbol for r in rows if r.leading_indicator}
    assert flagged, "no leading-indicator survivors flagged"


def test_cli_smoke_writes_json(tmp_path):
    out = tmp_path / "board.json"
    rc = main(["--horizons", "3", "--json", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["vol_method"] == "ewma"
    assert "not trading advice" in payload["disclaimer"]
    assert payload["rows"] and payload["rows"][0]["horizon_months"] == 3


def test_default_horizons_pinned():
    assert DEFAULT_HORIZONS == (1, 3, 6, 12)
