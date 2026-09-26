"""scripts/conveyance/fetch_parcel_stock.py: county housing-stock classifiers, on small synthetic rolls."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_parcel_stock", REPO / "scripts" / "conveyance" / "fetch_parcel_stock.py")
fps = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fps
spec.loader.exec_module(fps)

pytestmark = pytest.mark.smoke

NO_UNITS = pd.DataFrame({"TMK": pd.Series(dtype="int64"), "county": pd.Series(dtype=str),
                         "COUNT_Units": pd.Series(dtype="int64")})


def _honolulu_roll(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["parid", "taxratecode", "ovrclass", "landvalue", "buildingvalue",
                                       "landexemption", "buildingexemption"])


def test_cpr_master_leaves_the_stock_before_the_multifamily_and_large_tests():
    df = _honolulu_roll([
        ("111111110000", "1", None, 1_000_000, 2_000_000, 0, 0),         # master of the two units below
        ("111111110001", "1", None, 500_000, 1_000_000, 0, 160_000),
        ("111111110002", "1", None, 500_000, 1_000_000, 0, 0),
        ("222222220000", "1", None, 20_000_000, 5_000_000, 0, 0),        # master worth $25M: not large_other
        ("222222220001", "1", None, 10_000_000, 2_000_000, 0, 0),
        ("333333330000", "1", None, 3_000_000, 4_000_000, 0, 0),         # master of a 12-unit project: not multifamily
        ("333333330001", "1", None, 250_000, 300_000, 0, 0),
        ("444444440000", "1", None, 800_000, 400_000, 0, 120_000),       # a house
        ("555555550000", "1", None, 20_000_000, 6_000_000, 0, 0),        # a $26M non-condo parcel
    ])
    units = pd.DataFrame({"TMK": [133333333, 144444444], "county": "Honolulu", "COUNT_Units": [12, 1]})
    s = fps.honolulu(df, units)
    assert s["group"].tolist() == ["other", "residential", "residential", "other", "residential", "other",
                                   "residential", "residential", "large_other"]
    assert s["condo"].tolist() == [0, 1, 1, 0, 1, 0, 1, 0, 0]
    assert s["owner"].tolist() == [False, True, False, False, False, False, False, True, False]


def _hawaii_roll(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["tmk", "pittcode", "homeowner", "landvalue", "bldgvalue", "landexempt", "bldgexempt"])


def test_hawaii_owner_units_follow_each_projects_home_exemptions():
    raw = _hawaii_roll([
        (311001001, 100, "Y", 300_000, 400_000, 150_000, 0),       # home exemptions average $160,000 ...
        (311001002, 100, "Y", 300_000, 400_000, 170_000, 0),
        (311001003, 100, "Y", 20_000, 30_000, 50_000, 0),         # ... not counting a fully exempt home
        (311001004, 200, "Y", 2_000_000, 8_000_000, 480_000, 0),  # 10 units, 3 home exemptions
        (311001005, 200, "Y", 1_000_000, 3_000_000, 1_000_000, 0),  # 4 units, more exemptions than units
        (311001006, 200, "N", 1_000_000, 4_000_000, 320_000, 0),  # 5 units, not flagged homeowner: none
    ])
    hu = pd.DataFrame({"TMK": [311001004, 311001005, 311001006], "COUNT_Units": [10, 4, 5]})
    assert fps.home_exemption(raw) == pytest.approx(160_000)
    out = fps.split_condo_masters(fps.county_gis(raw, NO_UNITS, "Hawaii"), raw, hu)
    cu = out[out["group"] == "condo_unit"]
    assert (cu["condo"] == 1).all() and cu["improved"].all()
    assert cu.groupby("tmk")["owner"].sum().to_dict() == {311001004: 3, 311001005: 4, 311001006: 0}
    assert cu.groupby("tmk").size().to_dict() == {311001004: 10, 311001005: 4, 311001006: 5}
    assert cu.groupby("tmk")["value"].sum().to_dict() == pytest.approx({311001004: 10e6, 311001005: 4e6, 311001006: 5e6})
    assert (out["group"] != "condo_master").all()


def test_cap_factor_is_the_within_plat_ratio_and_uncapping_removes_it():
    rows = ([(311001000 + i, 100, "Y", 300_000, 500_000, 0, 0) for i in range(30)]          # zone 1: owners at 0.8x
            + [(311001100 + i, 100, "N", 400_000, 600_000, 0, 0) for i in range(30)]
            + [(311002000 + i, 100, "Y", 20_000, 100_000, 0, 0) for i in range(2)]          # too few owners: ignored
            + [(311002100 + i, 100, "N", 400_000, 600_000, 0, 0) for i in range(5)]
            + [(321001000 + i, 100, "Y", 200_000, 300_000, 0, 0) for i in range(3)]          # zone 2: owners at 0.5x,
            + [(321001100 + i, 100, "N", 400_000, 600_000, 0, 0) for i in range(3)])         # weight 3: takes the pool
    raw = _hawaii_roll(rows)
    f = fps.cap_factors(raw).set_index("zone")
    assert f.loc[1, "factor"] == pytest.approx(0.8, abs=1e-4) and f.loc[1, "weight"] == 30
    assert f.loc[2, "factor"] == pytest.approx(np.exp((30 * np.log(0.8) + 3 * np.log(0.5)) / 33), abs=1e-4)
    assert f.loc[2, "weight"] == 33
    s = fps.uncap(fps.county_gis(raw, NO_UNITS, "Hawaii"), f.reset_index())
    assert s["value"].iloc[:30].to_numpy() == pytest.approx(1e6, rel=1e-4)                  # uncapped to market
    assert s["value"].iloc[30:60].eq(1e6).all()                                              # non-owners untouched
    x = s.iloc[:60]
    assert fps.within_plat_ratio(raw["tmk"].iloc[:60] // 1000, x["owner"], x["value"])[0] == pytest.approx(1.0, abs=1e-4)


def test_uncap_scales_a_condo_project_by_its_owner_share():
    raw = _hawaii_roll([
        (311001001, 100, "Y", 300_000, 500_000, 100_000, 0),       # home exemptions average $100,000
        (311001002, 500, "Y", 400_000, 500_000, 100_000, 0),       # an ag dwelling
        # 8 units at a $1M market value, 2 of them owner-occupied and capped at 0.8:
        # the master holds 2 x $800,000 + 6 x $1M, and two home exemptions.
        (311001003, 200, "Y", 1_600_000, 6_000_000, 200_000, 0),
        (311001004, 200, "N", 1_000_000, 4_000_000, 0, 0),         # 5 units, no owners: untouched
    ])
    hu = pd.DataFrame({"TMK": [311001003, 311001004], "COUNT_Units": [8, 5]})
    factors = pd.DataFrame({"zone": [1, 1], "pittcode": [100, 500], "factor": [0.8, 0.9], "weight": [100, 100]})
    s = fps.split_condo_masters(fps.county_gis(raw, NO_UNITS, "Hawaii"), raw, hu)
    out = fps.uncap(s, factors)
    cu = out[out["group"] == "condo_unit"]
    assert cu.groupby("tmk")["owner"].mean().to_dict() == {311001003: 0.25, 311001004: 0.0}
    assert cu.loc[cu["tmk"] == 311001003, "value"].to_numpy() == pytest.approx(1e6)       # owner or not: market
    assert cu.loc[cu["tmk"] == 311001004, "value"].to_numpy() == pytest.approx(1e6)
    assert out.loc[out["group"] == "residential", "value"].iloc[0] == pytest.approx(800_000 / 0.8)
    assert out.loc[out["group"] == "ag_dwelling", "value"].iloc[0] == pytest.approx(900_000 / 0.9)


class _FakeArcgis:
    """requests.get stand-in for a paged ArcGIS layer of `rows` records."""
    def __init__(self, n: int, count: int | None = None, fail_at: int | None = None, fail_times: int = 99,
                 dup: bool = False):
        self.oids = [1, 1, *range(3, n + 1)] if dup else list(range(1, n + 1))
        self.count, self.fail_at, self.fail_left = n if count is None else count, fail_at, fail_times

    def __call__(self, url: str, timeout: int, params: dict):
        if not url.endswith("/query"):
            d = {"fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}, {"name": "a", "type": "esriFieldTypeInteger"}]}
        elif params.get("returnCountOnly"):
            d = {"count": self.count}
        elif params["resultOffset"] == self.fail_at and self.fail_left > 0:
            self.fail_left -= 1
            d = {"error": {"code": 500, "message": "Error performing query operation"}}
        else:
            o, k = params["resultOffset"], params["resultRecordCount"]
            d = {"features": [{"attributes": {"OBJECTID": i, "a": 10 * i}} for i in self.oids[o:o + k]],
                 "exceededTransferLimit": o + k < len(self.oids)}
        return type("R", (), {"json": lambda _self: d})()


@pytest.fixture
def arcgis(monkeypatch, tmp_path):
    monkeypatch.setattr(fps, "CACHE", tmp_path)
    monkeypatch.setattr(fps, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setitem(fps.SOURCES, "t", ("https://example.test/FeatureServer/0", "a", 2))
    def use(fake: _FakeArcgis) -> None:
        monkeypatch.setattr(fps.requests, "get", fake)
    return use


def test_fetch_caches_a_complete_pull_and_retries_an_error_page(arcgis, tmp_path):
    arcgis(_FakeArcgis(5, fail_at=2, fail_times=1))
    df = fps.fetch("t", refresh=True)
    assert df["a"].tolist() == [10, 20, 30, 40, 50] and df.columns.tolist() == ["a"]
    assert pd.read_parquet(tmp_path / "t.parquet").equals(df)


@pytest.mark.parametrize("fake, msg", [
    (dict(n=5, fail_at=2), "failed at offset 2: {'code': 500"),   # an error page that never clears
    (dict(n=5, count=7), "5 rows (5 unique OBJECTID) of 7"),     # the service stops short
    (dict(n=5, dup=True), "5 rows (4 unique OBJECTID) of 5"),    # a page repeated
])
def test_fetch_refuses_to_cache_an_incomplete_pull(arcgis, tmp_path, fake, msg):
    arcgis(_FakeArcgis(**fake))
    with pytest.raises(SystemExit, match=re.escape(msg)):
        fps.fetch("t", refresh=True)
    assert not (tmp_path / "t.parquet").exists()


def test_kauai_classifier():
    cols = ["PARTXT", "TMK", "TYPE", "CLASS", "OVRCLASS", "TAXCLASS", "MODTOT", "ASSDTOT", "TOTEXEMPT", "TAXABLE",
            "RESTRICT3", "TAXYR"]
    res, vr, ag = "1:NON-OWNER-OCCUPIED RESIDENTIAL", "2:VACATION RENTAL", "5:AGRICULTURAL"
    df = pd.DataFrame([
        ("410010010000", 441001001, "TMK Parcel", res, None, res, 1_200_000, 1_200_000, 0, 1_200_000, " ", 2026),
        ("410010020000", 441001002, "TMK Parcel", res, "8:OWNER-OCCUPIED", "8:OWNER-OCCUPIED",
         1_000_000, 740_000, 200_000, 540_000, None, 2026),                                  # market value, not capped
        ("410010030003", 441001003, "CPRX Multi-Unit Complex", vr, None, vr, 900_000, 900_000, 0, 900_000, None, 2026),
        ("410010030000", 441001003, "TMK Parcel", vr, None, vr, 5_000_000, 5_000_000, 0, 0,
         "H:NON-TAXABLE CONDO MASTER", 2026),
        ("410010040000", 441001004, "TMK Parcel", res, None, res, 500_000, 500_000, 500_000, 0, None, 2026),  # fully exempt
        ("410010050000", 441001005, "TMK Parcel", res, None, res, 3_000_000, 3_000_000, 0, 3_000_000, None, 2026),  # 6 units
        ("410010060000", 441001006, "TMK Parcel", res, None, res, 25_000_000, 25_000_000, 0, 25_000_000, None, 2026),
        ("410010070000", 441001007, "TMK Parcel", ag, None, ag, 2_000_000, 2_000_000, 0, 2_000_000, None, 2026),
        ("410010080000", 441001008, "TMK Parcel", res, None, res, 700_000, 700_000, 0, 700_000, None, 2025),  # old roll
        ("410010090001", 441001009, "CPR Unit", res, "10:OWNER-OCCUPIED MIXED-USE", "10:OWNER-OCCUPIED MIXED-USE",
         1_500_000, 1_100_000, 150_000, 950_000, None, 2026),
    ], columns=cols)
    bldg = pd.DataFrame({"PARTXT": ["410010010000", "410010020000", "410010070000", "410010090001"],
                         "FLAG": [0, 50, 0, 60], "TABLE_": ["DWELDAT", "DWELDAT", "DWELDAT", None]})
    units = pd.DataFrame({"TMK": [441001005], "county": ["Kauai"], "COUNT_Units": [6]})
    s = fps.kauai(df, bldg, units)
    assert len(s) == 9                                                                     # TY2025 row dropped
    assert s["group"].tolist() == ["residential", "residential", "residential", "other", "other", "multifamily",
                                   "large_other", "ag_dwelling", "residential"]
    assert s["owner"].tolist() == [False, True, False, False, False, False, False, False, True]
    assert s["improved"].tolist() == [True, False, True, False, False, False, False, True, True]
    assert s["condo"].tolist() == [0, 0, 1, 0, 0, 0, 0, 0, 1]
    assert s["value"].iloc[1] == 1_000_000


def test_binned_schema_and_totals():
    s = pd.DataFrame({"value": [250_000, 2_100_000, 2_200_000, 9e6], "owner": [True, False, False, False],
                      "improved": [True, True, True, False], "condo": [0, 1, 1, 0],
                      "group": ["residential", "residential", "residential", "other"]})
    b = fps.binned("Kauai", s)
    assert b.columns.tolist() == fps.COLUMNS
    assert b["count"].sum() == 3 and b["sum_value"].sum() == 4_550_000
    top = b[b["value_lo"] == 2_000_000]
    assert top[["condo", "count", "sum_value"]].values.tolist() == [[1, 2, 4_300_000]]
