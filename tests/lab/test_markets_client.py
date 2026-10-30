"""Tests for markets/client.py — fetcher, fallback, cache. No network."""
from __future__ import annotations

import json
import urllib.request
from datetime import date

import pytest
from lab.markets import client as mclient
from lab.markets.client import (
    MarketDataError,
    MonthlyBar,
    _drop_incomplete_month,
    _parse_stooq_csv,
    _parse_yahoo_chart,
    fetch_monthly_history,
)

# A canned Stooq monthly CSV (their real header layout).
STOOQ_CSV = b"""Date,Open,High,Low,Close,Volume
2023-11-30,100.0,105.0,99.0,104.20,1000000
2023-12-29,104.5,110.0,104.0,108.90,1100000
2024-01-31,109.0,112.0,107.0,111.30,900000
2024-02-29,111.5,113.0,108.0,109.75,950000
"""

TODAY = date(2024, 3, 15)


# ---------------------------------------------------------------------------
# Stooq CSV parsing
# ---------------------------------------------------------------------------

def test_parse_stooq_csv_basic():
    bars = _parse_stooq_csv(STOOQ_CSV, start_year=2000)
    assert [(b.year, b.month) for b in bars] == [
        (2023, 11), (2023, 12), (2024, 1), (2024, 2),
    ]
    assert bars[0].adj_close == pytest.approx(104.20)
    assert bars[0].volume == pytest.approx(1_000_000)


def test_parse_stooq_csv_respects_start_year():
    bars = _parse_stooq_csv(STOOQ_CSV, start_year=2024)
    assert [(b.year, b.month) for b in bars] == [(2024, 1), (2024, 2)]


def test_parse_stooq_csv_drops_garbage_rows():
    raw = STOOQ_CSV + b"not,a,valid,row\n2024-03-28,x,x,x,notafloat,\n"
    bars = _parse_stooq_csv(raw, start_year=2000)
    assert len(bars) == 4  # garbage silently dropped


def test_parse_stooq_no_data_page_yields_empty():
    assert _parse_stooq_csv(b"No data", start_year=2000) == []


# ---------------------------------------------------------------------------
# Yahoo chart JSON parsing
# ---------------------------------------------------------------------------

# Epoch seconds for month-start sessions (UTC mid-day, so tz-stable):
# 2023-11-01, 2023-12-01, 2024-01-02, 2024-02-01, plus a same-month
# "live" duplicate row Yahoo appends for the current session.
_TS = [1698840000, 1701432000, 1704196800, 1706788800, 1706961600]

YAHOO_PAYLOAD = {
    "chart": {
        "result": [{
            "timestamp": _TS,
            "indicators": {
                "quote": [{
                    "close": [103.0, 107.5, 110.0, 108.4, 108.9],
                    "volume": [1e6, 1.1e6, 9e5, 9.5e5, 1e5],
                }],
                "adjclose": [{
                    "adjclose": [104.2, 108.9, 111.3, 109.75, 110.2],
                }],
            },
        }],
        "error": None,
    },
}


def test_parse_yahoo_chart_prefers_adjclose_and_dedups():
    bars = _parse_yahoo_chart(YAHOO_PAYLOAD, start_year=2000)
    assert [(b.year, b.month) for b in bars] == [
        (2023, 11), (2023, 12), (2024, 1), (2024, 2),
    ]
    # adjclose used, not raw close; the duplicate Feb row was dropped.
    assert bars[0].adj_close == pytest.approx(104.2)
    assert bars[-1].adj_close == pytest.approx(109.75)


def test_parse_yahoo_chart_skips_null_closes_and_start_year():
    payload = json.loads(json.dumps(YAHOO_PAYLOAD))
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][0] = None
    bars = _parse_yahoo_chart(payload, start_year=2024)
    assert [(b.year, b.month) for b in bars] == [(2024, 1), (2024, 2)]


def test_parse_yahoo_chart_error_payload_raises():
    with pytest.raises(MarketDataError, match="malformed"):
        _parse_yahoo_chart(
            {"chart": {"result": None, "error": {"code": "Not Found"}}},
            start_year=2000,
        )


def test_fetch_source_yahoo_chart_forced(tmp_path, monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(YAHOO_PAYLOAD).encode()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp())
    bars, provenance = fetch_monthly_history(
        "SPY", cache_dir=tmp_path, source="yahoo_chart", today=TODAY,
    )
    assert provenance == "yahoo_chart"
    assert [(b.year, b.month) for b in bars][-1] == (2024, 2)


# ---------------------------------------------------------------------------
# Incomplete-month handling
# ---------------------------------------------------------------------------

def test_drop_incomplete_month():
    bars = _parse_stooq_csv(STOOQ_CSV, start_year=2000)
    bars.append(MonthlyBar(year=2024, month=3, adj_close=110.0))
    kept = _drop_incomplete_month(bars, today=TODAY)
    assert (2024, 3) not in [(b.year, b.month) for b in kept]
    assert (2024, 2) in [(b.year, b.month) for b in kept]


# ---------------------------------------------------------------------------
# fetch_monthly_history — source fallback + cache
# ---------------------------------------------------------------------------

@pytest.fixture
def stooq_urlopen(monkeypatch):
    """Route urllib through a canned Stooq response; count calls."""
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return STOOQ_CSV

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url if hasattr(req, "full_url") else req)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_fetch_falls_back_down_the_chain_to_stooq(
        tmp_path, stooq_urlopen, monkeypatch):
    def boom_yf(symbol, start_year):
        raise ImportError("yfinance not installed")

    def boom_yc(symbol, start_year):
        raise MarketDataError("yahoo down")
    monkeypatch.setattr(mclient, "_fetch_yfinance", boom_yf)
    monkeypatch.setattr(mclient, "_fetch_yahoo_chart", boom_yc)

    bars, provenance = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, today=TODAY,
    )
    assert provenance == "stooq"
    assert stooq_urlopen and "spy.us" in stooq_urlopen[0]
    assert [(b.year, b.month) for b in bars][-1] == (2024, 2)


def test_fetch_auto_prefers_yahoo_chart_over_stooq(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mclient, "_fetch_yfinance",
        lambda s, y: (_ for _ in ()).throw(ImportError("nope")),
    )
    monkeypatch.setattr(
        mclient, "_fetch_yahoo_chart",
        lambda s, y: _parse_yahoo_chart(YAHOO_PAYLOAD, y),
    )

    def boom_stooq(sym, y):
        raise AssertionError("stooq must not run when yahoo_chart works")
    monkeypatch.setattr(mclient, "_fetch_stooq", boom_stooq)

    _, provenance = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, today=TODAY)
    assert provenance == "yahoo_chart"


def test_fetch_source_stooq_never_touches_yfinance(
        tmp_path, stooq_urlopen, monkeypatch):
    def boom(symbol, start_year):
        raise AssertionError("yfinance path must not run")
    monkeypatch.setattr(mclient, "_fetch_yfinance", boom)

    _, provenance = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, source="stooq", today=TODAY,
    )
    assert provenance == "stooq"


def test_fetch_raises_when_all_sources_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mclient, "_fetch_yfinance",
        lambda s, y: (_ for _ in ()).throw(ImportError("nope")),
    )
    monkeypatch.setattr(
        mclient, "_fetch_yahoo_chart",
        lambda s, y: (_ for _ in ()).throw(MarketDataError("challenge page")),
    )
    monkeypatch.setattr(
        mclient, "_fetch_stooq",
        lambda s, y: (_ for _ in ()).throw(MarketDataError("down")),
    )
    with pytest.raises(MarketDataError, match="all sources failed"):
        fetch_monthly_history("SPY", "spy.us",
                              cache_dir=tmp_path, today=TODAY)


def test_offline_raises_without_cache(tmp_path):
    with pytest.raises(MarketDataError, match="offline"):
        fetch_monthly_history("SPY", "spy.us", cache_dir=tmp_path,
                              offline=True, today=TODAY)


def test_cache_round_trip_avoids_second_fetch(
        tmp_path, stooq_urlopen, monkeypatch):
    monkeypatch.setattr(
        mclient, "_fetch_yfinance",
        lambda s, y: (_ for _ in ()).throw(ImportError("nope")),
    )
    bars1, prov1 = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, today=TODAY)
    n_network = len(stooq_urlopen)

    # Second call same day: cache hit, no new network traffic —
    # and works offline.
    bars2, prov2 = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, offline=True, today=TODAY)
    assert len(stooq_urlopen) == n_network
    assert prov2 == prov1 == "stooq"
    assert bars2 == bars1


def test_cache_is_dated_so_next_day_refetches(
        tmp_path, stooq_urlopen, monkeypatch):
    monkeypatch.setattr(
        mclient, "_fetch_yfinance",
        lambda s, y: (_ for _ in ()).throw(ImportError("nope")),
    )
    fetch_monthly_history("SPY", "spy.us", cache_dir=tmp_path, today=TODAY)
    n_network = len(stooq_urlopen)
    fetch_monthly_history("SPY", "spy.us", cache_dir=tmp_path,
                          today=date(2024, 3, 16))
    assert len(stooq_urlopen) > n_network  # refetched on the new date


def test_corrupt_cache_falls_through_to_fetch(
        tmp_path, stooq_urlopen, monkeypatch):
    monkeypatch.setattr(
        mclient, "_fetch_yfinance",
        lambda s, y: (_ for _ in ()).throw(ImportError("nope")),
    )
    cache_file = tmp_path / f"SPY_{TODAY.isoformat()}.json"
    cache_file.write_text("{corrupt json!!")
    bars, provenance = fetch_monthly_history(
        "SPY", "spy.us", cache_dir=tmp_path, today=TODAY)
    assert provenance == "stooq"
    assert bars
    # Cache was repaired on the way out.
    assert json.loads(cache_file.read_text())["provenance"] == "stooq"
