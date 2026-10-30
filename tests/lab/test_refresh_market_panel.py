"""Tests for scripts/refresh_market_panel.py — CLI, failure posture, schemas.

No network: fetchers are monkeypatched at the script's import site.
"""
from __future__ import annotations

import json

import pytest

from lab.markets.client import MarketDataError, MonthlyBar
from lab.markets.panel import PricesPanel
from lab.markets.universe import TICKERS
from lab.scripts import refresh_market_panel as script


def _bars(n=6, start_year=2023):
    return [MonthlyBar(year=start_year + (i // 12), month=(i % 12) + 1,
                       adj_close=100.0 + i) for i in range(n)]


@pytest.fixture
def fake_fetch_ok(monkeypatch):
    def fake(symbol, stooq_symbol=None, *, start_year=2005, source="auto",
             **kwargs):
        return _bars(), "stooq"
    monkeypatch.setattr(script, "fetch_monthly_history", fake)


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_makes_no_network_calls(
        tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("network call during --dry-run")
    monkeypatch.setattr(script, "fetch_monthly_history", boom)
    monkeypatch.setattr(script, "fetch_zillow_county_csv", boom)
    monkeypatch.setattr(script, "fetch_cpi_data", boom)

    rc = script.main(["--dry-run", "--out", str(tmp_path),
                      "--anchors-out", str(tmp_path / "anchors")])
    assert rc == 0
    assert list(tmp_path.rglob("*.json")) == []
    err = capsys.readouterr().err
    assert "dry-run" in err and "SPY" in err


# ---------------------------------------------------------------------------
# Panel writing
# ---------------------------------------------------------------------------

def test_writes_panel_and_manifest(tmp_path, fake_fetch_ok):
    rc = script.main(["--skip-macro", "--out", str(tmp_path),
                      "--anchors-out", str(tmp_path / "anchors")])
    assert rc == 0

    panel = json.loads((tmp_path / "prices_panel.json").read_text())
    assert panel["version"] == 1
    assert panel["n_series"] == len(TICKERS)
    assert panel["provenance"]["SPY"] == "stooq"
    assert panel["series"]["SPY"][0]["period"] == "M01"

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["n_tickers"] == len(TICKERS)
    spy = next(t for t in manifest["tickers"] if t["symbol"] == "SPY")
    assert spy["tier"] == "broad"
    assert spy["hypothesis"]
    assert spy["n_obs"] == 6
    # No macro/anchor files when --skip-macro.
    assert not (tmp_path / "macro_monthly.json").exists()
    assert not (tmp_path / "anchors" / "bls_national_unemployment.json").exists()


def test_keep_last_committed_on_per_ticker_failure(tmp_path, monkeypatch):
    # Seed a previously committed panel holding stale SPY data.
    prev = PricesPanel(series={"SPY": _bars(3)}, provenance={"SPY": "stooq"},
                       fetch_date="2024-01-05")
    (tmp_path / "prices_panel.json").write_text(json.dumps(prev.to_payload()))

    def fake(symbol, stooq_symbol=None, **kwargs):
        if symbol == "SPY":
            raise MarketDataError("both sources down")
        return _bars(), "stooq"
    monkeypatch.setattr(script, "fetch_monthly_history", fake)

    rc = script.main(["--skip-macro", "--tolerate-failures",
                      "--out", str(tmp_path),
                      "--anchors-out", str(tmp_path / "anchors")])
    assert rc == 0
    panel = json.loads((tmp_path / "prices_panel.json").read_text())
    assert len(panel["series"]["SPY"]) == 3          # stale data retained
    assert panel["provenance"]["SPY"] == "stooq+stale"
    assert len(panel["series"]["QQQ"]) == 6          # fresh data elsewhere


def test_without_tolerate_failures_a_ticker_error_aborts(
        tmp_path, monkeypatch):
    def fake(symbol, stooq_symbol=None, **kwargs):
        if symbol == "SPY":
            raise MarketDataError("down")
        return _bars(), "stooq"
    monkeypatch.setattr(script, "fetch_monthly_history", fake)

    rc = script.main(["--skip-macro", "--out", str(tmp_path),
                      "--anchors-out", str(tmp_path / "anchors")])
    assert rc == 2
    assert not (tmp_path / "prices_panel.json").exists()


def test_all_tickers_failing_never_writes_empty_panel(tmp_path, monkeypatch):
    def fake(*a, **k):
        raise MarketDataError("everything is down")
    monkeypatch.setattr(script, "fetch_monthly_history", fake)

    rc = script.main(["--skip-macro", "--tolerate-failures",
                      "--out", str(tmp_path),
                      "--anchors-out", str(tmp_path / "anchors")])
    assert rc == 2
    assert not (tmp_path / "prices_panel.json").exists()


# ---------------------------------------------------------------------------
# Macro block + national-unemployment anchor
# ---------------------------------------------------------------------------

def _fake_unemp(api_key, *, start_year, end_year):
    return {
        script.NATIONAL_UNEMP_SID: [
            {"year": 2023, "period": f"M{m:02d}", "value": 3.6 + 0.01 * m}
            for m in range(1, 13)
        ],
        script.HAWAII_UNEMP_SID: [
            {"year": 2023, "period": f"M{m:02d}", "value": 3.0}
            for m in range(1, 13)
        ],
    }


def _fake_zillow(url):
    return {"15003": {"2023-01-31": 850000.0, "2023-02-28": 851000.0}}


def test_macro_block_writes_monthly_series_and_anchor(
        tmp_path, fake_fetch_ok, monkeypatch):
    monkeypatch.setenv("BLS_API_KEY", "test-key")
    monkeypatch.setattr(script, "fetch_unemployment_monthly", _fake_unemp)
    monkeypatch.setattr(script, "fetch_zillow_county_csv", _fake_zillow)

    anchors = tmp_path / "anchors"
    rc = script.main(["--out", str(tmp_path), "--anchors-out", str(anchors)])
    assert rc == 0

    macro = json.loads((tmp_path / "macro_monthly.json").read_text())
    assert len(macro["series"][script.NATIONAL_UNEMP_SID]) == 12
    assert macro["series"]["ZHVI_HONOLULU_MONTHLY"] == [
        {"year": 2023, "period": "M01", "value": 850000.0},
        {"year": 2023, "period": "M02", "value": 851000.0},
    ]

    anchor = json.loads(
        (anchors / "bls_national_unemployment.json").read_text())
    # Standard anchor schema fields (matches data/anchors/README.md).
    for field in ("source", "series_id", "title", "frequency", "units",
                  "geography", "last_refresh", "limitations",
                  "values_by_year"):
        assert field in anchor, field
    assert anchor["geography"] == "national"
    expected = sum(3.6 + 0.01 * m for m in range(1, 13)) / 12
    assert anchor["values_by_year"]["2023"] == pytest.approx(expected, abs=1e-3)


def test_macro_block_fetches_keylessly_without_key(
        tmp_path, fake_fetch_ok, monkeypatch, capsys):
    """Without BLS_API_KEY the unemployment block runs keylessly."""
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    monkeypatch.setattr(script, "fetch_unemployment_monthly", _fake_unemp)
    monkeypatch.setattr(script, "fetch_zillow_county_csv", _fake_zillow)

    anchors = tmp_path / "anchors"
    rc = script.main(["--out", str(tmp_path), "--anchors-out", str(anchors)])
    assert rc == 0
    macro = json.loads((tmp_path / "macro_monthly.json").read_text())
    assert script.NATIONAL_UNEMP_SID in macro["series"]
    assert (anchors / "bls_national_unemployment.json").exists()
    assert "keyless" in capsys.readouterr().err


def test_macro_block_degrades_when_unemployment_fetch_fails(
        tmp_path, fake_fetch_ok, monkeypatch, capsys):
    def boom(api_key, *, start_year, end_year):
        raise RuntimeError("BLS is down")
    monkeypatch.setattr(script, "fetch_unemployment_monthly", boom)
    monkeypatch.setattr(script, "fetch_zillow_county_csv", _fake_zillow)

    anchors = tmp_path / "anchors"
    rc = script.main(["--out", str(tmp_path), "--anchors-out", str(anchors)])
    assert rc == 0                                        # never fails CI
    macro = json.loads((tmp_path / "macro_monthly.json").read_text())
    assert script.NATIONAL_UNEMP_SID not in macro["series"]
    assert "ZHVI_HONOLULU_MONTHLY" in macro["series"]     # Zillow still ran
    assert not (anchors / "bls_national_unemployment.json").exists()
    assert "unemployment fetch failed" in capsys.readouterr().err


def test_fetch_unemployment_chunks_10yr_windows_when_keyless(monkeypatch):
    calls = []

    def fake_cpi(series_ids, start_year, end_year, api_key=None, **kw):
        calls.append((start_year, end_year, api_key))
        return {sid: [{"year": start_year, "period": "M01", "value": 4.0}]
                for sid in series_ids}
    monkeypatch.setattr(script, "fetch_cpi_data", fake_cpi)

    script.fetch_unemployment_monthly(None, start_year=2005, end_year=2026)
    assert calls == [(2005, 2014, None), (2015, 2024, None),
                     (2025, 2026, None)]

    calls.clear()
    script.fetch_unemployment_monthly("key", start_year=2005, end_year=2026)
    assert calls == [(2005, 2024, "key"), (2025, 2026, "key")]


def test_build_national_unemployment_anchor_partial_year():
    monthly = [{"year": 2024, "period": f"M{m:02d}", "value": 4.0}
               for m in range(1, 7)]  # six months only
    anchor = script.build_national_unemployment_anchor(monthly)
    assert anchor["values_by_year"] == {"2024": 4.0}


# ---------------------------------------------------------------------------
# Smoke — bundled data, end-to-end report (skips before first real fetch)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_smoke_tracker_report_on_bundled_panel(tmp_path):
    from lab.markets.panel import PRICES_PANEL_PATH
    from lab.markets.report import main as report_main
    if not PRICES_PANEL_PATH.exists():
        pytest.skip("bundled prices panel not yet committed")
    rc = report_main(["--csv-dir", str(tmp_path)])
    assert rc == 0
    header = (tmp_path / "tracker_status.csv").read_text().splitlines()[0]
    assert header.startswith("symbol,name,tier,last_month")
