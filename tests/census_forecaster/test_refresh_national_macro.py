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


def test_fred_request_carries_no_custom_user_agent(monkeypatch):
    """FRED stalls requests with a custom or browser-like User-Agent when they
    come from a datacenter IP (GitHub runner probe, 2026-10-06: default UA 6/6
    answered in under a second, custom and browser UAs 12/12 read timeouts), so
    every monthly CI refresh since 2026-08-06 lost its four FRED series. It works
    from a residential IP with any UA, so only this assertion catches it."""
    seen = {}

    class _Resp:
        text = "observation_date,DGS10\n2023-01-05,3.5\n"
        def raise_for_status(self): pass

    def fake_get(url, **kw):
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(script.requests, "get", fake_get)
    script.fetch_fred_csv("DGS10")
    headers = {k.lower() for k in (seen.get("headers") or {})}
    assert "user-agent" not in headers, seen.get("headers")


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

# A real refresh returns the whole history plus the newest year, never just
# the newest year, so the stubs cover the committed years (2019, 2020) too.
_YEARS = (2019, 2020, 2025)


def _bls_rows(value):
    return [{"year": y, "period": "M01", "value": value} for y in _YEARS]


def _fred_rows(value):
    return [{"date": f"{y}-01-01", "value": value} for y in _YEARS]


def _at(value):
    """The annual series the stubs above aggregate to."""
    return {str(y): value for y in _YEARS}


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
    rows = _bls_rows(9.0)
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
            assert out["series"][s.name] == _at(9.0)
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
                        lambda sid, **kw: _fred_rows(7.0))
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    assert set(out["series"]) == {s.name for s in NATIONAL_SERIES}
    for s in NATIONAL_SERIES:
        want = ({"2019": 1.0, "2020": 1.0} if s.source == "BLS_FETCH"
                else _at(7.0) if s.source == "FRED"
                else _at(9.0))
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
# A partial fetch must not replace a longer committed series
#
# Regression: fetch_bls_monthly retries a series BLS dropped from the batch
# one year-chunk at a time. If the retry fetched its first chunk and then
# raised (REQUEST_NOT_PROCESSED at the keyless daily limit), the first
# chunk's rows stayed in the result; main() saw a non-empty series, replaced
# the committed 2005-2026 ``unemp`` with 2005-2014, stamped it fetched today
# and left it out of the "carried over" warning. The same happened, with no
# exception at all, when BLS dropped a series from one chunk of the batch.
# ---------------------------------------------------------------------------

_UNEMP = next(s for s in NATIONAL_SERIES if s.name == "unemp")
_LONG_UNEMP = {str(y): 5.0 for y in range(2005, 2027)}
_ARGS = ["--start-year", "2005", "--end-year", "2026"]


def _monthly(ys, ye, value=9.0):
    return [{"year": y, "period": "M01", "value": value}
            for y in range(ys, ye + 1)]


@pytest.fixture
def long_env(tmp_path, monkeypatch):
    """Committed file with a 2005-2026 ``unemp``; CPI and FRED stubbed sane."""
    nm = tmp_path / "national_macro.json"
    mm = tmp_path / "macro_monthly.json"
    nm.write_text(json.dumps(_previous_payload(unemp=_LONG_UNEMP)))
    monkeypatch.setattr(script, "NATIONAL_MACRO_FILE", nm)
    monkeypatch.setattr(script, "MACRO_MONTHLY_FILE", mm)
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    monkeypatch.setattr(script, "read_cpi_panel_series",
                        lambda sid: _bls_rows(9.0))
    monkeypatch.setattr(script, "fetch_fred_csv",
                        lambda sid, **kw: _fred_rows(7.0))
    return nm, mm


def _assert_unemp_carried_over(nm, mm, err):
    out = json.loads(nm.read_text())
    assert out["series"]["unemp"] == _LONG_UNEMP
    assert out["sources"]["unemp"] == f"old ({_UNEMP.series_id})"
    assert out["series_fetch_date"]["unemp"] == _PREV_DATE
    assert out["series_fetch_date"]["ahe"] == out["fetch_date"]  # refreshed
    carried = [ln for ln in err.splitlines() if "carried over" in ln]
    assert carried and carried[0].startswith("::warning::")
    assert "unemp" in carried[0]
    # ...and the truncated rows did not reach the screen file either.
    screen = json.loads(mm.read_text())["series"]
    assert _UNEMP.series_id not in screen
    assert "CES0500000003" in screen


def test_retry_that_dies_after_its_first_chunk_keeps_previous(long_env,
                                                              monkeypatch,
                                                              capsys):
    retry_calls = []

    def fake_fetch(series_ids, start_year=None, end_year=None, api_key=None,
                   **kw):
        if len(series_ids) > 1:          # batch: BLS drops unemp silently
            return {sid: _monthly(start_year, end_year)
                    for sid in series_ids if sid != _UNEMP.series_id}
        assert series_ids == [_UNEMP.series_id]
        retry_calls.append((start_year, end_year))
        if len(retry_calls) == 2:        # daily limit hit mid-retry
            raise RuntimeError("BLS API error: REQUEST_NOT_PROCESSED")
        return {series_ids[0]: _monthly(start_year, end_year)}

    monkeypatch.setattr(script, "fetch_cpi_data", fake_fetch)
    assert script.main(_ARGS) == 0
    assert retry_calls == [(2005, 2014), (2015, 2024)]   # 2nd chunk raised
    nm, mm = long_env
    _assert_unemp_carried_over(nm, mm, capsys.readouterr().err)


def test_fetch_bls_monthly_discards_a_partly_retried_series(monkeypatch):
    sid_ok, sid_bad = "SERIES_OK", "SERIES_BAD"
    calls = {"bad": 0}

    def fake_fetch(series_ids, start_year=None, end_year=None, api_key=None,
                   **kw):
        if len(series_ids) > 1:
            return {sid_ok: _monthly(start_year, end_year)}
        calls["bad"] += 1
        if calls["bad"] == 2:
            raise RuntimeError("REQUEST_NOT_PROCESSED")
        return {sid_bad: _monthly(start_year, end_year)}

    monkeypatch.setattr(script, "fetch_cpi_data", fake_fetch)
    out = script.fetch_bls_monthly([sid_ok, sid_bad], None,
                                   start_year=2005, end_year=2026)
    assert out[sid_bad] == []            # first chunk's rows not kept
    assert {r["year"] for r in out[sid_ok]} == set(range(2005, 2027))


def test_fetch_bls_monthly_keeps_a_fully_retried_series(monkeypatch):
    """Control: the reset above only fires on an exception."""
    sid_ok, sid_bad = "SERIES_OK", "SERIES_BAD"

    def fake_fetch(series_ids, start_year=None, end_year=None, api_key=None,
                   **kw):
        got = [sid_ok] if len(series_ids) > 1 else series_ids
        return {sid: _monthly(start_year, end_year) for sid in got}

    monkeypatch.setattr(script, "fetch_cpi_data", fake_fetch)
    out = script.fetch_bls_monthly([sid_ok, sid_bad], None,
                                   start_year=2005, end_year=2026)
    assert {r["year"] for r in out[sid_bad]} == set(range(2005, 2027))


def test_batch_that_drops_a_series_from_one_chunk_keeps_previous(long_env,
                                                                 monkeypatch,
                                                                 capsys):
    """No exception anywhere: BLS just omits unemp's 2015-2026 rows."""
    def fake_bls(ids, key, **kw):
        return {sid: (_monthly(2005, 2014) if sid == _UNEMP.series_id
                      else _monthly(2005, 2026)) for sid in ids}

    monkeypatch.setattr(script, "fetch_bls_monthly", fake_bls)
    assert script.main(_ARGS) == 0
    nm, mm = long_env
    err = capsys.readouterr().err
    warn = [ln for ln in err.splitlines() if ln.startswith("::warning::unemp")]
    assert warn and "lacks 12 committed year(s) (2015-2026)" in warn[0]
    assert "fetched 2005-2014, committed 2005-2026" in warn[0]
    _assert_unemp_carried_over(nm, mm, err)


def test_a_fetch_with_a_gap_in_the_middle_is_refused(long_env, monkeypatch,
                                                     capsys):
    def fake_bls(ids, key, **kw):
        return {sid: (_monthly(2005, 2009) + _monthly(2013, 2026)
                      if sid == _UNEMP.series_id else _monthly(2005, 2026))
                for sid in ids}

    monkeypatch.setattr(script, "fetch_bls_monthly", fake_bls)
    assert script.main(_ARGS) == 0
    nm, _ = long_env
    assert json.loads(nm.read_text())["series"]["unemp"] == _LONG_UNEMP
    assert "lacks 3 committed year(s) (2010-2012)" in capsys.readouterr().err


def test_a_longer_fetch_still_replaces_the_committed_series(long_env,
                                                            monkeypatch):
    """The guard blocks shrinking, not refreshing: revised values and a new
    year both land, and the series is stamped as fetched today."""
    def fake_bls(ids, key, **kw):
        return {sid: _monthly(2005, 2027, value=6.0) for sid in ids}

    monkeypatch.setattr(script, "fetch_bls_monthly", fake_bls)
    assert script.main(["--start-year", "2005", "--end-year", "2027"]) == 0
    nm, _ = long_env
    out = json.loads(nm.read_text())
    assert out["series"]["unemp"] == {str(y): 6.0 for y in range(2005, 2028)}
    assert out["series_fetch_date"]["unemp"] == out["fetch_date"]


def test_truncated_fred_series_is_refused_and_not_sent_to_the_screen(
        long_env, monkeypatch, capsys):
    monkeypatch.setattr(script, "fetch_fred_csv",
                        lambda sid, **kw: [{"date": "2025-06-01",
                                            "value": 7.0}])
    monkeypatch.setattr(script, "fetch_bls_monthly",
                        lambda ids, key, **kw: {sid: _bls_rows(9.0)
                                                for sid in ids})
    assert script.main([]) == 0
    nm, mm = long_env
    out = json.loads(nm.read_text())
    for name in _FRED_NAMES:
        assert out["series"][name] == {"2019": 1.0, "2020": 1.0}
        assert out["series_fetch_date"][name] == _PREV_DATE
    err = capsys.readouterr().err
    assert "::warning::mortgage30: fetch lacks 2 committed year(s) (2019-2020)" in err
    assert "MORTGAGE30US" not in json.loads(mm.read_text())["series"]


def test_truncated_cpi_panel_series_is_refused(merge_env, monkeypatch,
                                               capsys):
    monkeypatch.setattr(script, "read_cpi_panel_series",
                        lambda sid: [{"year": 2025, "period": "M01",
                                      "value": 9.0}])
    monkeypatch.setattr(script, "fetch_fred_csv",
                        lambda sid, **kw: _fred_rows(7.0))
    assert script.main([]) == 0
    out = json.loads(merge_env.read_text())
    for s in NATIONAL_SERIES:
        if s.source == "CPI_PANEL":
            assert out["series"][s.name] == {"2019": 1.0, "2020": 1.0}
    assert "::warning::cpi_allitems: fetch lacks" in capsys.readouterr().err


def test_year_ranges_compresses_runs():
    assert script._year_ranges([2005, 2006, 2007, 2010]) == "2005-2007, 2010"
    assert script._year_ranges([2012]) == "2012"
    assert script._year_ranges([2003, 2001, 2002, 2003]) == "2001-2003"
    assert script._year_ranges([]) == "none"


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
