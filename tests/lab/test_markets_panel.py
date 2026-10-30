"""Tests for markets/panel.py — schema round-trip + transform math."""
from __future__ import annotations

import math

import pytest
from lab.markets.client import MonthlyBar
from lab.markets.panel import PricesPanel, load_prices_panel


def _bars(prices: list[float], start=(2020, 1)) -> list[MonthlyBar]:
    """Consecutive monthly bars from a price list."""
    year, month = start
    out = []
    for p in prices:
        out.append(MonthlyBar(year=year, month=month, adj_close=p))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


@pytest.fixture
def panel() -> PricesPanel:
    # 14 months: Jan 2020 – Feb 2021, exact 1% monthly growth.
    prices = [100.0 * 1.01 ** i for i in range(14)]
    return PricesPanel(
        series={"SPY": _bars(prices)},
        provenance={"SPY": "stooq"},
        fetch_date="2024-03-05",
        start_year=2020,
    )


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------

def test_payload_round_trip(panel, tmp_path):
    payload = panel.to_payload()
    assert payload["version"] == 1
    assert payload["n_series"] == 1
    assert payload["series"]["SPY"][0] == {
        "year": 2020, "period": "M01", "adj_close": 100.0, "volume": None,
    }
    restored = PricesPanel.from_payload(payload)
    assert restored.series["SPY"] == panel.series["SPY"]
    assert restored.provenance == panel.provenance
    assert restored.fetch_date == panel.fetch_date


def test_load_prices_panel_from_file(panel, tmp_path):
    import json
    p = tmp_path / "prices_panel.json"
    p.write_text(json.dumps(panel.to_payload()))
    loaded = load_prices_panel(p)
    assert loaded.series["SPY"] == panel.series["SPY"]


def test_load_missing_panel_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="refresh_market_panel"):
        load_prices_panel(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Transform math
# ---------------------------------------------------------------------------

def test_log_returns_constant_growth(panel):
    returns = panel.log_returns("SPY")
    assert len(returns) == 13
    for _, _, r in returns:
        assert r == pytest.approx(math.log(1.01))


def test_log_returns_do_not_chain_across_gaps():
    bars = [
        MonthlyBar(2020, 1, 100.0),
        MonthlyBar(2020, 2, 101.0),
        MonthlyBar(2020, 5, 130.0),  # Mar+Apr missing
        MonthlyBar(2020, 6, 131.0),
    ]
    p = PricesPanel(series={"X": bars})
    returns = p.log_returns("X")
    assert [(y, m) for y, m, _ in returns] == [(2020, 2), (2020, 6)]


def test_momentum_12m(panel):
    # Feb 2021 vs Feb 2020: 12 months of 1% growth.
    mom = panel.momentum("SPY", 12)
    assert mom == pytest.approx(12 * math.log(1.01))


def test_momentum_as_of(panel):
    mom = panel.momentum("SPY", 3, as_of=(2020, 6))
    assert mom == pytest.approx(3 * math.log(1.01))


def test_momentum_missing_endpoint_returns_none(panel):
    assert panel.momentum("SPY", 60) is None          # start before history
    assert panel.momentum("SPY", 3, as_of=(2025, 1)) is None


def test_annualized_vol_zero_for_constant_growth(panel):
    # Constant log-return series has zero variance.
    vol = panel.annualized_vol("SPY", window=12)
    assert vol == pytest.approx(0.0, abs=1e-12)


def test_annualized_vol_known_alternation():
    # Alternating +5%/-5% log returns: sd = 0.05 · sqrt(n/(n-1)) around 0.
    prices, p = [100.0], 100.0
    for i in range(24):
        p *= math.exp(0.05 if i % 2 == 0 else -0.05)
        prices.append(p)
    panel = PricesPanel(series={"X": _bars(prices)})
    vol = panel.annualized_vol("X", window=24)
    expected_sd = math.sqrt(sum((0.05 - 0.0) ** 2 for _ in range(24)) / 23)
    assert vol == pytest.approx(expected_sd * math.sqrt(12), rel=1e-9)


def test_annualized_vol_insufficient_data_returns_none():
    panel = PricesPanel(series={"X": _bars([100, 101, 102, 103])})
    assert panel.annualized_vol("X") is None


def test_to_projection_points_shape(panel):
    pts = panel.to_projection_points("SPY")
    assert pts[0] == {"year": 2020, "period": "M01", "value": 100.0}
    assert all(set(p) == {"year", "period", "value"} for p in pts)
    # This is exactly the shape bls.projection.project_forward_full eats.


def test_unknown_symbol_raises(panel):
    with pytest.raises(KeyError, match="not in panel"):
        panel.bars("NOPE")
