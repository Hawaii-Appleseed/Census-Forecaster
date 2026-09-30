"""Tests for scripts/refresh_national_macro.py — aggregation, parse, merge.

No network: fetchers are monkeypatched.
"""
from __future__ import annotations

import json

import pytest

from census_forecaster.acs.ml_features import (
    NATIONAL_SERIES,
    national_macro_columns,
)
from census_forecaster.scripts import refresh_national_macro as script


# ---------------------------------------------------------------------------
# Registry ↔ column consistency
# ---------------------------------------------------------------------------

def test_registry_produces_22_columns():
    # 14 series: 13 original + migrated national unemployment (level_diff2).
    assert len(national_macro_columns()) == 22
    assert len(NATIONAL_SERIES) == 14


def test_every_series_has_a_known_source_and_policy():
    for s in NATIONAL_SERIES:
        assert s.source in ("CPI_PANEL", "BLS_FETCH", "FRED")
        assert s.col_policy in ("logchange1", "diff1", "level_diff1",
                                "level_diff2")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_aggregate_to_annual_from_bls_rows():
    rows = [{"year": 2020, "period": "M01", "value": 4.0},
            {"year": 2020, "period": "M02", "value": 6.0},
            {"year": 2021, "period": "M01", "value": 5.0}]
    assert script.aggregate_to_annual(rows) == {2020: 5.0, 2021: 5.0}


def test_aggregate_to_annual_from_fred_rows():
    rows = [{"date": "2020-01-01", "value": 3.0},
            {"date": "2020-07-01", "value": 5.0},
            {"date": "2021-01-01", "value": 4.0}]
    assert script.aggregate_to_annual(rows) == {2020: 4.0, 2021: 4.0}


def test_aggregate_partial_year_is_mean_of_available():
    rows = [{"date": "2026-01-01", "value": 6.0},
            {"date": "2026-02-01", "value": 6.4}]
    assert script.aggregate_to_annual(rows) == {2026: 6.2}


def test_resample_monthly_averages_within_month():
    # weekly-ish prints, two in Jan, one in Feb
    rows = [{"date": "2020-01-03", "value": 3.0},
            {"date": "2020-01-31", "value": 3.4},
            {"date": "2020-02-07", "value": 3.8}]
    out = script.resample_monthly(rows)
    assert out == [{"year": 2020, "period": "M01", "value": 3.2},
                   {"year": 2020, "period": "M02", "value": 3.8}]


def test_resample_monthly_passes_bls_rows_through():
    rows = [{"year": 2020, "period": "M03", "value": 62.1}]
    assert script.resample_monthly(rows) == [
        {"year": 2020, "period": "M03", "value": 62.1}]


# ---------------------------------------------------------------------------
# FRED CSV parse
# ---------------------------------------------------------------------------

def test_fetch_fred_csv_parses_and_skips_missing(monkeypatch):
    csv_text = (
        "observation_date,MORTGAGE30US\n"
        "2023-01-05,6.48\n"
        "2023-01-12,.\n"          # FRED missing sentinel → dropped
        "2023-01-19,6.15\n"
        "2023-01-26,\n"           # empty → dropped
    )

    class _Resp:
        text = csv_text
        def raise_for_status(self): pass
    monkeypatch.setattr(script.requests, "get",
                        lambda url, **kw: _Resp())
    rows = script.fetch_fred_csv("MORTGAGE30US")
    assert rows == [{"date": "2023-01-05", "value": 6.48},
                    {"date": "2023-01-19", "value": 6.15}]


# ---------------------------------------------------------------------------
# CPI-panel reader
# ---------------------------------------------------------------------------

def test_read_cpi_panel_series_from_bundle():
    # The bundled panel is committed; the registry's CPI ids must resolve.
    rows = script.read_cpi_panel_series("CUUR0000SA0")
    if not rows:
        pytest.skip("cpi_panel.json not present")
    assert rows[0]["period"].startswith("M")
    assert isinstance(rows[0]["value"], (int, float))


# ---------------------------------------------------------------------------
# macro_monthly merge preserves existing keys
# ---------------------------------------------------------------------------

def test_merge_macro_monthly_preserves_existing(tmp_path, monkeypatch):
    existing = {
        "version": 1, "fetch_date": "2026-01-01",
        "series": {"LNS14000000": [{"year": 2020, "period": "M01", "value": 6.0}],
                   "ZHVI_HONOLULU_MONTHLY": [{"year": 2020, "period": "M01", "value": 8e5}]},
        "sources": {"LNS14000000": "BLS"},
        "limitations": ["existing note"],
    }
    f = tmp_path / "macro_monthly.json"
    f.write_text(json.dumps(existing))
    monkeypatch.setattr(script, "MACRO_MONTHLY_FILE", f)

    script._merge_macro_monthly(
        {"MORTGAGE30US": [{"year": 2020, "period": "M01", "value": 3.1}]})
    out = json.loads(f.read_text())
    # existing keys survive, new key added
    assert "LNS14000000" in out["series"]
    assert "ZHVI_HONOLULU_MONTHLY" in out["series"]
    assert out["series"]["MORTGAGE30US"][0]["value"] == 3.1
    assert "existing note" in out["limitations"]


# ---------------------------------------------------------------------------
# national_macro.json write MERGES into the committed file
#
# Regression: main() used to start from an empty dict and write only what
# fetched, so the monthly CI refreshes of 2026-08-06 and 2026-09-23 each
# deleted mortgage30 / dgs10 / rental_vacancy / homeownership from the
# bundle when FRED was down, despite logging "keeping previous".
# ---------------------------------------------------------------------------

_FRED_NAMES = {s.name for s in NATIONAL_SERIES if s.source == "FRED"}
_PREV_DATE = "2026-01-15"


def _previous_payload(**extra_series) -> dict:
    """A committed national_macro.json holding all 14 registry series at 1.0."""
    series = {s.name: {"2019": 1.0, "2020": 1.0} for s in NATIONAL_SERIES}
    series.update(extra_series)
    return {
        "version": 1, "fetch_date": _PREV_DATE,
        "series": series,
        "sources": {s.name: f"old ({s.series_id})" for s in NATIONAL_SERIES},
        "limitations": [],
    }


@pytest.fixture
def merge_env(tmp_path, monkeypatch):
    """Point both outputs at tmp_path; stub CPI + BLS to return value 9.0."""
    nm = tmp_path / "national_macro.json"
    mm = tmp_path / "macro_monthly.json"
    nm.write_text(json.dumps(_previous_payload()))
    monkeypatch.setattr(script, "NATIONAL_MACRO_FILE", nm)
    monkeypatch.setattr(script, "MACRO_MONTHLY_FILE", mm)
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    rows = [{"year": 2025, "period": "M01", "value": 9.0}]
    monkeypatch.setattr(script, "read_cpi_panel_series", lambda sid: rows)
    monkeypatch.setattr(
        script, "fetch_bls_monthly",
        lambda ids, key, **kw: {sid: list(rows) for sid in ids})
    return nm


def _fred_down(series_id, **kw):
    raise RuntimeError("Read timed out")


def test_failed_fred_keeps_previous_series(merge_env, monkeypatch, capsys):
    monkeypatch.setattr(script, "fetch_fred_csv", _fred_down)
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    assert set(out["series"]) == {s.name for s in NATIONAL_SERIES}
    for s in NATIONAL_SERIES:
        if s.name in _FRED_NAMES:        # carried over, values untouched
            assert out["series"][s.name] == {"2019": 1.0, "2020": 1.0}
            assert out["sources"][s.name] == f"old ({s.series_id})"
            assert out["series_fetch_date"][s.name] == _PREV_DATE
        else:                            # refreshed this run
            assert out["series"][s.name] == {"2025": 9.0}
            assert out["series_fetch_date"][s.name] == out["fetch_date"]
    err = capsys.readouterr().err
    carried = [ln for ln in err.splitlines() if "carried over" in ln]
    assert carried and carried[0].startswith("::warning::")
    for name in _FRED_NAMES:
        assert name in carried[0]


def test_skip_fred_keeps_previous_series(merge_env, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("FRED fetched despite --skip-fred")
    monkeypatch.setattr(script, "fetch_fred_csv", boom)
    assert script.main(["--skip-fred"]) == 0
    out = json.loads(merge_env.read_text())
    assert set(out["series"]) == {s.name for s in NATIONAL_SERIES}
    for name in _FRED_NAMES:
        assert out["series"][name] == {"2019": 1.0, "2020": 1.0}
    assert "carried over" in capsys.readouterr().err


def test_failed_bls_keeps_previous_series(merge_env, monkeypatch):
    def bls_down(*a, **k):
        raise RuntimeError("BLS 503")
    monkeypatch.setattr(script, "fetch_bls_monthly", bls_down)
    monkeypatch.setattr(script, "fetch_fred_csv",
                        lambda sid, **kw: [{"date": "2025-01-01", "value": 7.0}])
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    assert set(out["series"]) == {s.name for s in NATIONAL_SERIES}
    for s in NATIONAL_SERIES:
        want = ({"2019": 1.0, "2020": 1.0} if s.source == "BLS_FETCH"
                else {"2025": 7.0} if s.source == "FRED"
                else {"2025": 9.0})
        assert out["series"][s.name] == want, s.name


def test_fred_zero_rows_warns_and_keeps_previous(merge_env, monkeypatch,
                                                 capsys):
    monkeypatch.setattr(script, "fetch_fred_csv", lambda sid, **kw: [])
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    for name in _FRED_NAMES:
        assert out["series"][name] == {"2019": 1.0, "2020": 1.0}
    err = capsys.readouterr().err
    for s in NATIONAL_SERIES:
        if s.source == "FRED":
            assert (f"::warning::FRED returned no rows for {s.series_id}"
                    in err)


def test_series_dropped_from_registry_is_not_carried(tmp_path, monkeypatch):
    nm = tmp_path / "national_macro.json"
    nm.write_text(json.dumps(_previous_payload(retired={"2020": 5.0})))
    annual, sources, fetched_on = script.read_previous_national_macro(nm)
    assert "retired" not in annual
    assert set(annual) == {s.name for s in NATIONAL_SERIES}
    assert all(isinstance(y, int) for v in annual.values() for y in v)
    assert set(fetched_on.values()) == {_PREV_DATE}


def test_nothing_fetched_and_no_previous_refuses_to_write(tmp_path,
                                                          monkeypatch):
    nm = tmp_path / "national_macro.json"
    monkeypatch.setattr(script, "NATIONAL_MACRO_FILE", nm)
    monkeypatch.setattr(script, "MACRO_MONTHLY_FILE",
                        tmp_path / "macro_monthly.json")
    monkeypatch.setattr(script, "read_cpi_panel_series", lambda sid: [])
    monkeypatch.setattr(script, "fetch_bls_monthly", lambda *a, **k: {})
    monkeypatch.setattr(script, "fetch_fred_csv", _fred_down)
    assert script.main([]) == 2
    assert not nm.exists()


def test_everything_down_still_rewrites_previous_intact(merge_env,
                                                        monkeypatch):
    # Merged result is non-empty, so the write proceeds — with every
    # series carried over and none lost.
    monkeypatch.setattr(script, "read_cpi_panel_series", lambda sid: [])
    monkeypatch.setattr(script, "fetch_bls_monthly", lambda *a, **k: {})
    monkeypatch.setattr(script, "fetch_fred_csv", _fred_down)
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    assert out["series"] == _previous_payload()["series"]
    assert set(out["series_fetch_date"].values()) == {_PREV_DATE}


# ---------------------------------------------------------------------------
# Dry-run writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_no_network_no_write(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("network during --dry-run")
    monkeypatch.setattr(script, "fetch_bls_monthly", boom)
    monkeypatch.setattr(script, "fetch_fred_csv", boom)
    rc = script.main(["--dry-run"])
    assert rc == 0
    assert "dry-run" in capsys.readouterr().err
