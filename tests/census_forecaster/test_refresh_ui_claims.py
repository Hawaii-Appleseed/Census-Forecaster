"""Tests for the DOL ETA-539 UI-claims fetcher (network-free)."""

from __future__ import annotations

import json
from datetime import date

from census_forecaster.markets.screen import HAWAII_PREDICTORS, HYPOTHESIS_PAIRS
from census_forecaster.scripts import refresh_ui_claims as ui

CSV_HEAD = '"st","rptdate","c1","c2","c3","c4","c5"\n'


def test_parses_hi_rows_only():
    text = (CSV_HEAD
            + 'HI,2026-07-25,29,2026-07-18,978,3,10\n'
            + 'CA,2026-07-25,29,2026-07-18,40000,3,10\n'
            + 'HI,2026-08-01,30,2026-07-25,1005,0,2\n')
    weekly = ui.parse_hi_weekly(text)
    assert weekly == [(date(2026, 7, 18), 978.0), (date(2026, 7, 25), 1005.0)]


def test_us_date_format_and_bad_rows():
    text = (CSV_HEAD
            + 'HI,1/8/1990,1,1/6/1990,"1,234",0,0\n'   # US date + comma value
            + 'HI,x,1,not-a-date,50,0,0\n'             # bad date -> skipped
            + 'HI,x,1,1/13/1990,abc,0,0\n'             # bad value -> skipped
            + 'HI,x,1,1/20/1990,-5,0,0\n')             # negative -> skipped
    weekly = ui.parse_hi_weekly(text)
    assert weekly == [(date(1990, 1, 6), 1234.0)]


def test_monthly_mean_not_sum():
    """4- and 5-week months must stay on the same scale."""
    weekly = [(date(2026, 1, d), 1000.0) for d in (3, 10, 17, 24, 31)]  # 5 wks
    weekly += [(date(2026, 2, d), 1000.0) for d in (7, 14, 21, 28)]     # 4 wks
    rows = ui.weekly_to_monthly_mean(weekly)
    assert rows == [
        {"year": 2026, "period": "M01", "value": 1000.0},
        {"year": 2026, "period": "M02", "value": 1000.0},
    ]


def test_month_assignment_by_week_ending():
    rows = ui.weekly_to_monthly_mean([
        (date(2026, 1, 31), 900.0),
        (date(2026, 2, 7), 1100.0),
    ])
    assert [r["period"] for r in rows] == ["M01", "M02"]


def test_merge_is_additive(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"series": {"KEEP": [1]}, "sources": {},
                                "limitations": []}))
    merged = ui.merge_into_macro_monthly(
        [{"year": 2026, "period": "M07", "value": 1033.33}], path=path)
    assert merged["series"]["KEEP"] == [1]
    assert merged["series"][ui.SERIES_ID][0]["value"] == 1033.33
    assert "DOL_ETA539" in merged["sources"]


def test_screen_wiring():
    assert HAWAII_PREDICTORS["HI_UI_CLAIMS"] == ui.SERIES_ID
    assert ("HI_UI_CLAIMS", "HI_UNEMPLOYMENT") in HYPOTHESIS_PAIRS
    assert ("HI_PAYROLLS", "HI_UNEMPLOYMENT") in HYPOTHESIS_PAIRS
    assert HAWAII_PREDICTORS["HI_PAYROLLS"] == "SMS15000000000000001"


# ---------------------------------------------------------------------------
# Per-state annual channel (ML STATE_SERIES feed)
# ---------------------------------------------------------------------------

def test_parses_every_state_in_one_pass():
    text = (CSV_HEAD
            + 'HI,2026-07-25,29,2026-07-18,978,3,10\n'
            + 'CA,2026-07-25,29,2026-07-18,40000,3,10\n'
            + 'HI,2026-08-01,30,2026-07-25,1005,0,2\n')
    by_state = ui.parse_weekly_by_state(text)
    assert set(by_state) == {"HI", "CA"}
    assert by_state["CA"] == [(date(2026, 7, 18), 40000.0)]
    # The Hawaii-only helper is now a thin view over the same parse.
    assert by_state["HI"] == ui.parse_hi_weekly(text)


def test_annual_mean_not_sum():
    """52- and 53-week years must stay on the same scale."""
    weekly = [(date(2025, 1, 4), 1000.0), (date(2025, 6, 7), 2000.0),
              (date(2026, 3, 7), 500.0)]
    assert ui.weekly_to_annual_mean(weekly) == {2025: 1500.0, 2026: 500.0}


def test_state_annual_is_keyed_by_fips_and_drops_territories():
    by_state = {
        "HI": [(date(2025, 1, 4), 1000.0)],
        "CA": [(date(2025, 1, 4), 40000.0)],
        "PR": [(date(2025, 1, 4), 700.0)],   # territory → no panel counties
        "US": [(date(2025, 1, 4), 9e5)],     # aggregate row, not a state
    }
    annual = ui.build_state_annual(by_state)
    assert set(annual) == {"15", "06"}
    assert annual["15"] == {2025: 1000.0}


def test_postal_map_covers_the_calibration_panel_states():
    from census_forecaster.scripts.build_calibration_panel import STATE_FIPS
    assert set(ui.POSTAL_TO_FIPS.values()) == set(STATE_FIPS)


def test_state_payload_stringifies_years_and_carries_metadata():
    payload = ui.build_state_payload({"15": {2024: 1080.85, 2025: 1025.56}})
    assert payload["series_id"] == ui.STATE_SERIES_ID
    assert payload["geography"] == "state"
    assert payload["values_by_state_year"]["15"] == {
        "2024": 1080.85, "2025": 1025.56}
    # The LAUS collinearity caveat must survive edits to this file: it is
    # the reason the channel is not an independent signal.
    assert any("LAUS" in lim for lim in payload["limitations"])


def test_state_payload_round_trips_through_the_feature_loader(tmp_path,
                                                              monkeypatch):
    """What the fetcher writes is exactly what ml_features reads back.

    Points the loader's package-data root at a temp tree (it resolves
    ``Path(__file__).parent.parent / "data" / ...``) so the real reader
    runs against a real file the real writer produced — the two halves
    of this channel are only useful if their formats agree.
    """
    import json as _json

    from census_forecaster.acs import ml_features as mf

    fake_module = tmp_path / "pkg" / "acs" / "ml_features.py"
    data_file = tmp_path / "pkg" / "data" / "leading_indicators" / "ui_claims.json"
    data_file.parent.mkdir(parents=True)
    data_file.write_text(_json.dumps(
        ui.build_state_payload({"15": {2024: 1080.85}, "06": {2024: 4e4}})))
    monkeypatch.setattr(mf, "Path", lambda _p: fake_module)

    assert mf.load_state_data() == {
        "ui_claims": {"15": {2024: 1080.85}, "06": {2024: 40000.0}}}
