"""Tests for markets/signals.py — no-peeking cutoff + screen gating."""
from __future__ import annotations

import math

import pytest
from lab.markets.client import MonthlyBar
from lab.markets.panel import PricesPanel
from lab.markets.signals import (
    build_signals_payload,
    derive_annual_signals,
    surviving_tickers,
)


def _bars(monthly_prices: dict[tuple[int, int], float]) -> list[MonthlyBar]:
    return [MonthlyBar(year=y, month=m, adj_close=p)
            for (y, m), p in sorted(monthly_prices.items())]


def _selected(*tickers, robust=True):
    return {
        "generated": "2026-07-14",
        "signals": [
            {"ticker": t, "robust_to_2020_exclusion": robust}
            for t in tickers
        ],
    }


def _flat_panel(symbols, years=range(2019, 2022), price=100.0):
    prices = {(y, m): price for y in years for m in range(1, 13)}
    return PricesPanel(series={s: _bars(prices) for s in symbols})


# ---------------------------------------------------------------------------
# Screen gating
# ---------------------------------------------------------------------------

def test_surviving_tickers_requires_robust_by_default():
    sel = {"signals": [
        {"ticker": "XLE", "robust_to_2020_exclusion": True},
        {"ticker": "SPY", "robust_to_2020_exclusion": False},
    ]}
    assert surviving_tickers(sel) == {"XLE"}
    assert surviving_tickers(sel, require_robust=False) == {"XLE", "SPY"}


def test_channel_emitted_only_for_surviving_tickers():
    panel = _flat_panel(["XLE", "MATX", "XLRE", "VNQ"])
    signals = derive_annual_signals(panel, _selected("XLE", "VNQ"))
    assert set(signals) == {"mkt_energy_mom", "mkt_reit_mom"}
    # MATX not a survivor → no shipping channel even though data exists.
    assert "mkt_shipping_mom" not in signals


def test_reit_channel_averages_only_surviving_members():
    # XLRE rallies, VNQ flat. Only VNQ survives → channel == VNQ's 0.
    prices_flat = {(y, m): 100.0 for y in (2019, 2020) for m in range(1, 13)}
    prices_up = {(y, m): 100.0 * 1.02 ** ((y - 2019) * 12 + m)
                 for y in (2019, 2020) for m in range(1, 13)}
    panel = PricesPanel(series={"VNQ": _bars(prices_flat),
                                "XLRE": _bars(prices_up)})
    signals = derive_annual_signals(panel, _selected("VNQ"))
    assert signals["mkt_reit_mom"][2020] == pytest.approx(0.0, abs=1e-12)


def test_no_survivors_yields_no_signals():
    panel = _flat_panel(["XLE"])
    assert derive_annual_signals(panel, _selected("XLE", robust=False)) == {}


# ---------------------------------------------------------------------------
# June-cutoff no-peeking
# ---------------------------------------------------------------------------

def test_july_shock_does_not_move_that_years_signal():
    """A price shock in July of year Y must not change year-Y's signal."""
    base = {(y, m): 100.0 for y in (2019, 2020, 2021) for m in range(1, 13)}
    panel_calm = PricesPanel(series={"XLE": _bars(base)})

    shocked = dict(base)
    for m in range(7, 13):          # crash starts July 2020...
        shocked[(2020, m)] = 40.0
    for m in range(1, 7):           # ...and persists through June 2021
        shocked[(2021, m)] = 40.0
    panel_shock = PricesPanel(series={"XLE": _bars(shocked)})

    sel = _selected("XLE")
    calm = derive_annual_signals(panel_calm, sel)["mkt_energy_mom"]
    shock = derive_annual_signals(panel_shock, sel)["mkt_energy_mom"]
    assert shock[2020] == calm[2020]           # cutoff holds
    assert shock[2021] != calm[2021]           # ...but next year sees it


def test_signal_is_june_to_june_momentum():
    # 1% monthly growth: Jun→Jun = 12 months of log(1.01).
    prices = {}
    p = 100.0
    for y in (2019, 2020):
        for m in range(1, 13):
            prices[(y, m)] = p
            p *= 1.01
    panel = PricesPanel(series={"MATX": _bars(prices)})
    signals = derive_annual_signals(panel, _selected("MATX"))
    assert signals["mkt_shipping_mom"][2020] == pytest.approx(
        12 * math.log(1.01))


def test_missing_june_endpoint_omits_year():
    prices = {(2019, m): 100.0 for m in range(1, 13)}
    prices.update({(2020, m): 100.0 for m in (1, 2, 3)})  # stops in March
    panel = PricesPanel(series={"XLE": _bars(prices)})
    signals = derive_annual_signals(panel, _selected("XLE"))
    assert 2020 not in signals.get("mkt_energy_mom", {})


# ---------------------------------------------------------------------------
# Payload schema
# ---------------------------------------------------------------------------

def test_payload_schema_round_trips_through_loader(tmp_path, monkeypatch):
    panel = _flat_panel(["XLE"])
    sel = _selected("XLE")
    signals = derive_annual_signals(panel, sel)
    payload = build_signals_payload(signals, sel, last_refresh="2026-07")

    assert payload["geography"] == "national"
    assert payload["as_of_month"] == 6
    assert payload["screen_generated"] == "2026-07-14"
    assert "mkt_energy_mom" in payload["signals"]

    # Round-trip through the ml_features loader.
    import json

    import census_forecaster.acs.ml_features as mf
    li_dir = tmp_path / "data" / "leading_indicators"
    li_dir.mkdir(parents=True)
    (li_dir / "market_signals.json").write_text(json.dumps(payload))
    monkeypatch.setattr(
        mf, "__file__", str(tmp_path / "acs" / "ml_features.py"))
    loaded = mf.load_market_signals_data()
    assert loaded is not None
    assert loaded["mkt_energy_mom"] == {
        int(y): v for y, v in payload["signals"]["mkt_energy_mom"].items()}
