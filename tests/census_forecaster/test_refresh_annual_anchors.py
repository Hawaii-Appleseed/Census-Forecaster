"""Tests for scripts/refresh_annual_anchors.py — parse, guard, failure posture.

No network: every fetcher is monkeypatched.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from census_forecaster.acs.sources.base import AnchorSource
from census_forecaster.scripts import refresh_annual_anchors as ra
from census_forecaster.scripts import refresh_national_macro as nm

SPECS = {s["filename"]: s for s in ra.ANCHOR_SPECS}


# ---------------------------------------------------------------------------
# Value builders
# ---------------------------------------------------------------------------

def test_fred_annual_keeps_years_from_first_year_and_rounds():
    rows = [{"date": "2009-01-01", "value": 80.0},
            {"date": "2010-01-01", "value": 87.4211},
            {"date": "2011-01-01", "value": 89.7}]
    assert ra.annual_from_fred_annual(rows, first_year=2010) == {2010: 87.421, 2011: 89.7}


def test_fred_quarterly_is_the_mean_of_four_quarters_complete_years_only():
    rows = [{"date": f"2023-{m:02d}-01", "value": v}
            for m, v in ((1, 100.0), (4, 110.0), (7, 120.0), (10, 130.0))]
    rows += [{"date": "2024-01-01", "value": 140.0},   # 2024 has one quarter: dropped
             {"date": "2022-01-01", "value": 1.0}]      # 2022 has one quarter: dropped
    assert ra.annual_from_fred_quarterly(rows, first_year=2010) == {2023: 115.0}


def test_fred_quarterly_with_unexpected_months_raises():
    rows = [{"date": f"2023-{m:02d}-01", "value": 1.0} for m in (2, 5, 8, 11)]
    with pytest.raises(ValueError, match="layout change"):
        ra.annual_from_fred_quarterly(rows)


def test_bls_annual_takes_only_a01_periods():
    pts = [{"year": 2024, "period": "A01", "value": 67366.0},
           {"year": 2024, "period": "Q04", "value": 63090.0},
           {"year": 2024, "period": "M03", "value": 632283.0},
           {"year": 2009, "period": "A01", "value": 40000.0}]
    assert ra.annual_from_bls(pts, first_year=2010) == {2024: 67366.0}


def test_implausible_values_raise():
    # Employment levels (~600,000) where pay (~65,000) was expected: the wrong
    # QCEW data type, which is exactly what the file's old series id pointed at.
    with pytest.raises(ValueError, match="outside"):
        ra.check_plausible("qcew_hawaii_wages.json", {2024: 632283.0})
    ra.check_plausible("qcew_hawaii_wages.json", {2024: 67366.0})


def test_years_lost_names_committed_years_the_fetch_lacks():
    assert ra.years_lost({2022: 1.0, 2023: 1.0, 2024: 1.0}, {2022: 2.0, 2023: 2.0}) == [2024]
    assert ra.years_lost({2022: 1.0}, {2022: 9.0, 2025: 9.0}) == []


# ---------------------------------------------------------------------------
# Payload schema (what AnchorSource.from_file reads)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SPECS))
def test_payload_loads_through_anchor_source(tmp_path, name):
    spec = SPECS[name]
    lo, hi = ra._RANGES[name]
    values = {y: lo + (hi - lo) * 0.1 * (1.03 ** (y - 2010)) for y in range(2010, 2026)}
    payload = ra.build_payload(spec, values, today=date(2026, 10, 7))
    assert payload["last_refresh"] == "2026-10"
    assert list(payload["values_by_year"]) == [str(y) for y in range(2010, 2026)]
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    src = AnchorSource.from_file(path, publication_lag_years=1,
                                 indicator_affinity=("B19013_001E",))
    assert src.metadata["source"] == spec["source"]


# ---------------------------------------------------------------------------
# BLS chunking
# ---------------------------------------------------------------------------

def test_bls_fetch_chunks_by_window_and_concatenates(monkeypatch):
    calls = []

    def fake(series_ids, start_year=None, end_year=None, api_key=None, **kw):
        calls.append((start_year, end_year))
        return {series_ids[0]: [{"year": start_year, "period": "A01", "value": 1.0}]}

    monkeypatch.setattr(ra, "fetch_cpi_data", fake)
    pts = ra.fetch_bls_annual("S", None, first_year=2010, last_year=2026)
    assert calls == [(2010, 2019), (2020, 2026)] and len(pts) == 2     # keyless: 10-year windows
    calls.clear()
    ra.fetch_bls_annual("S", "KEY", first_year=2010, last_year=2026)
    assert calls == [(2010, 2026)]                                      # keyed: 20-year windows


# ---------------------------------------------------------------------------
# main(): writes, and the failure posture
# ---------------------------------------------------------------------------

def _good(spec_values):
    def fake_fetch_values(spec, api_key, first_year=ra.FIRST_YEAR):
        return dict(spec_values[spec["filename"]])
    return fake_fetch_values


def _values_for(name, last_year=2025):
    lo, hi = ra._RANGES[name]
    return {y: lo + (hi - lo) * 0.1 for y in range(2010, last_year + 1)}


def _old_file(tmp_path, name, last_year=2024, last_refresh="2026-01", value=1.0):
    spec = SPECS[name]
    p = ra.build_payload(spec, {y: value for y in range(2010, last_year + 1)},
                         today=date(2026, 1, 15))
    p["last_refresh"] = last_refresh
    (tmp_path / name).write_text(json.dumps(p))


def test_main_writes_every_anchor_with_todays_last_refresh(tmp_path, monkeypatch):
    for name in SPECS:
        _old_file(tmp_path, name)
    monkeypatch.setattr(ra, "fetch_values", _good({n: _values_for(n) for n in SPECS}))
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    assert ra.main(["--out", str(tmp_path)]) == 0
    for name in SPECS:
        d = json.loads((tmp_path / name).read_text())
        assert d["last_refresh"] == date.today().strftime("%Y-%m")
        assert sorted(d["values_by_year"]) == [str(y) for y in range(2010, 2026)]   # 2025 added


def test_a_failed_fetch_leaves_the_file_and_its_last_refresh(tmp_path, monkeypatch, capsys):
    for name in SPECS:
        _old_file(tmp_path, name)
    before = (tmp_path / "qcew_hawaii_wages.json").read_text()
    good = _good({n: _values_for(n) for n in SPECS})

    def fake(spec, api_key, first_year=ra.FIRST_YEAR):
        if spec["filename"] == "qcew_hawaii_wages.json":
            raise RuntimeError("BLS API error: REQUEST_NOT_PROCESSED")
        return good(spec, api_key, first_year)

    monkeypatch.setattr(ra, "fetch_values", fake)
    assert ra.main(["--out", str(tmp_path)]) == 0                       # degrade, don't fail CI
    assert (tmp_path / "qcew_hawaii_wages.json").read_text() == before  # untouched, still "2026-01"
    assert json.loads((tmp_path / "pce_deflator.json").read_text())["last_refresh"] != "2026-01"
    err = capsys.readouterr().err
    assert "::warning::qcew_hawaii_wages.json: fetch failed" in err
    assert "1 of 3 anchors not refreshed: qcew_hawaii_wages.json" in err
    # --strict turns the same failure into a non-zero exit
    assert ra.main(["--out", str(tmp_path), "--strict"]) == 1


def test_a_fetch_that_lacks_committed_years_is_refused(tmp_path, monkeypatch, capsys):
    name = "fred_hi_hpi.json"
    _old_file(tmp_path, name, last_year=2024)
    before = (tmp_path / name).read_text()
    short = {y: _values_for(name)[y] for y in range(2010, 2022)}        # lacks 2022-2024
    monkeypatch.setattr(ra, "ANCHOR_SPECS", (SPECS[name],))
    monkeypatch.setattr(ra, "fetch_values", lambda spec, key, first_year=ra.FIRST_YEAR: short)
    assert ra.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / name).read_text() == before
    assert "lacks 3 committed year(s) (2022-2024)" in capsys.readouterr().err


def test_dry_run_does_no_network_and_writes_nothing(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network during --dry-run")
    monkeypatch.setattr(ra, "fetch_values", boom)
    assert ra.main(["--out", str(tmp_path), "--dry-run"]) == 0
    assert list(tmp_path.iterdir()) == []


def test_fred_fetch_is_the_shared_ua_free_fetcher():
    """FRED stalls custom User-Agents from datacenter IPs (see
    refresh_national_macro.fetch_fred_csv). Reusing that function is what keeps
    these anchors off the same trap; a private copy could reintroduce it."""
    assert ra.fetch_fred_csv is nm.fetch_fred_csv


# ---------------------------------------------------------------------------
# The bundled files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SPECS))
def test_bundled_anchor_is_contiguous_plausible_and_current_enough(name):
    path = ra.ANCHORS_DIR / name
    d = json.loads(path.read_text())
    years = sorted(int(y) for y in d["values_by_year"])
    assert years[0] == ra.FIRST_YEAR
    assert years == list(range(years[0], years[-1] + 1)), "gap in values_by_year"
    ra.check_plausible(name, {int(y): float(v) for y, v in d["values_by_year"].items()})
    assert years[-1] >= 2024
