"""scripts/conveyance/maui_sales_extract.py: Maui's sales file and assessment
listing to the forecast's inputs, on a synthetic fixture (tests/fixtures/
maui_extract: TMK zone 9, which Maui does not have), and the committed files."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "maui_extract"
spec = importlib.util.spec_from_file_location("maui_sales_extract",
                                              REPO / "scripts" / "conveyance" / "maui_sales_extract.py")
mx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mx
spec.loader.exec_module(mx)

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def fx():
    par = mx.parcels((FIX / "fullasmt26.txt").read_text(),
                     mx.units_by_tmk(json.loads((FIX / "units_ge5.json").read_text())))
    rows = mx.sales_rows((FIX / "sales.csv").read_text())
    s = mx.sales(mx.documents(rows), par)
    return rows, par, s, mx.tables(s, par)


def _sale(s: pd.DataFrame, price: float) -> pd.Series:
    (i,) = s.index[s["price"] == price]
    return s.loc[i]


def test_fixed_width_rows_and_a_tax_with_a_thousands_separator(fx):
    rows, _, s, _ = fx
    assert len(mx.sales_lines((FIX / "sales.csv").read_text())[1]) == 24       # footer says 24
    assert len(rows) == 22              # no record date: one with no sale date either, one route slip
    r = next(r for r in rows if r["INSTRUNO"] == "A90000001")
    assert r["CONV_TAX"] == "5,243.79" and mx.amount(r["CONV_TAX"]) == 5243.79   # a comma split read $5
    sale = _sale(s, 1_747_930)
    assert sale["tax"] == pytest.approx(5243.79)
    assert sale["category"] == "owner"                                            # 0.3%: schedule (1)


def test_multi_parcel_land_court_deed_counts_once(fx):
    rows, _, s, t = fx
    docs = [d for d in mx.documents(rows) if "910020010002" in d.parids]
    assert len(docs) == 1 and len(docs[0].parids) == 4          # 3 Land Court rows + the INSTRUNO recording
    assert (s["price"] == 12_900_000).sum() == 1
    assert t["maui_sales_over_10m_fy2023_2026.csv"].to_dict("records") == [
        {"fy": 2026, "category": "nonowner", "price": 12_900_000}]
    bv = t["maui_sales_by_value_fy2023_2026.csv"].query("condo == 1")
    assert bv[["count", "sum_price", "sum_value"]].values.tolist() == [[1, 12_900_000, 10_500_000]]
    # Placeholder instrument numbers (0000000000) do not join unrelated documents.
    assert _sale(s, 2_000_000)["category"] == "nonres" and _sale(s, 700_000)["category"] == "owner"


def test_five_unit_building_is_listed_as_multifamily(fx):
    _, par, s, t = fx
    assert par["910010030000"].units == 12 and par["910010030000"].multifamily
    assert t["maui_multifamily_sales_fy2023_2026.csv"].to_dict("records") == [
        {"fy": 2024, "category": "owner", "price": 12_000_000, "units": 12},
        {"fy": 2026, "category": "owner", "price": 3_500_000, "units": 12}]
    bins = t["maui_sales_bins_fy2023_2026.csv"]
    assert bins.query("fy == 2026 and category == 'owner' and bin_lo == 3_500_000")["count"].sum() == 1
    assert not _sale(s, 3_500_000)["used"]                          # not in the sales by value
    assert 5_000_000 not in t["maui_sales_by_value_fy2023_2026.csv"]["sum_value"].to_list()


def test_multifamily_sale_is_in_exactly_one_other_file(fx):
    _, _, _, t = fx
    bins, tops = t["maui_sales_bins_fy2023_2026.csv"], t["maui_sales_over_10m_fy2023_2026.csv"]
    # The $12M sale of the 12-unit building: in the multifamily file only, not the $10M+ list (the
    # forecast adds each multifamily sale as "mf" and takes the under-$10M ones out of their bins).
    assert 12_000_000 not in tops["price"].to_list()
    assert bins.query("fy == 2024 and category == 'owner'")["sum_price"].sum() == 1_100_000 + 1_747_930
    _totals_are_bins_top_and_multifamily_top(t)


def test_schedule_matching():
    assert mx.schedule(1_800, 900_000) == 1                          # 0.2%
    assert mx.schedule(2_250, 900_000) == 2                          # 0.25%
    assert mx.schedule(1_000, 900_000) == 0
    assert mx.schedule(1_800 * 1.019, 900_000) == 1                  # within 2%
    assert mx.schedule(1_800 * 1.021, 900_000) == 0
    assert mx.schedule(2_250 * 0.981, 900_000) == 2
    assert mx.schedule(161_250, 12_900_000) == 2                     # 1.25% at $10M+
    # Near misses of the old 0.5% tolerance: $200 over schedule (2) at $4.55M; 1.19% at $7,560,100.
    assert mx.schedule(38_875, 4_550_000) == 2 and mx.schedule(84_151.1, 7_560_100) == 2
    # The nearer schedule wins: (2)'s rate is at least 20% above (1)'s.
    assert mx.schedule(1.5, 1_000) == 2                              # 0.15% of $1,000
    assert mx.schedule(1, 1) == 1                                    # a $1 deed taxed $1: within $2, (1) first
    assert mx.fit(38_875, 4_550_000) == (pytest.approx(200 / 38_675), 2)


def test_one_prefers_the_price_the_tax_fits():
    assert mx.one([4.2e6, 5.2e6, 4.2e6, 4.2e6], 36_400) == 5.2e6      # 0.7% of $5.2M, not $17.8M
    assert mx.one([4.2e6, 5.2e6, 4.2e6, 4.2e6]) == 17.8e6             # no tax to fit: the sum
    assert mx.one([1.785e6, 17.885e6, 17.885e6, 17.885e6], 7_140) == 1.785e6     # 0.4% of $1.785M
    assert mx.one([735_000, 100], 1_838.5) == 735_100                 # an allocation: the sum fits best
    assert mx.one([500_000, 700_000], 2_400) == 1_200_000             # 0.2% of the sum
    assert mx.one([900_000, 900_000], 1) == 900_000                   # the rows agree
    assert math.isnan(mx.one([math.nan, math.nan]))


def test_differing_prices_near_miss_and_validity(fx):
    rows, _, s, t = fx
    doc = _sale(s, 5_200_000)                                        # four parcels, one of them $5.2M
    assert (doc["fy"], doc["category"], doc["tax"], doc["validity"]) == (2023, "nonres", 36_400, "7")
    assert not (s["price"] >= 17_000_000).any()
    assert t["maui_sales_bins_fy2023_2026.csv"].query("fy == 2023 and category == 'nonres'")["bin_lo"].tolist() == [
        5_000_000]
    near = _sale(s, 4_550_000)
    assert (near["category"], near["used"], near["validity"]) == ("nonowner", True, "8")
    d = next(d for d in mx.documents(rows) if "910030010002" in d.parids)
    assert d.codes == ["7", "7", "0", "7"] and d.validity == "7"


def test_row_without_a_record_date_is_dated_by_its_sale_date(fx):
    rows, _, s, _ = fx
    (r,) = [r for r in rows if not r["RECORDDATE"]]
    assert (r["LANDCOURT_NO"], r["DATE"]) == ("T90000005", "2022/08/31")
    sale = _sale(s, 7_200_000)
    assert (sale["fy"], sale["category"], sale["date"]) == (2023, "nonowner", "2022/08/31")    # 1.1%: schedule (2)
    assert not any(r["PRICE"] == "1012024" for r in rows)            # a route slip: price = tax, not a sale
    assert not any(r["PRICE"] == "500000" for r in rows)             # no record date and no sale date
    doc = next(d for d in mx.documents(rows) if "910030050001" in d.parids)
    assert (doc.date, doc.recorded) == ("2022/08/31", False)


def test_categories_transposed_fields_and_totals(fx):
    _, _, s, t = fx
    assert _sale(s, 800_000)[["tax", "category"]].tolist() == [2_000, "nonowner"]   # recorded as 2,000 / 800,000
    assert _sale(s, 900_000)["category"] == "unmatched"
    tot = t["maui_sales_totals_fy2016_2026.csv"]
    assert tot.query("fy == 2024 and category == 'unmatched'")["count"].item() == 1
    bins = t["maui_sales_bins_fy2023_2026.csv"]
    assert bins.query("fy == 2024 and category == 'nonres' and bin_lo == 800_000")["count"].item() == 1
    assert "unmatched" not in set(bins["category"])
    assert _sale(s, 1_100_000)["category"] == "owner"               # residential on its second listing line


def test_arms_length_filter(fx):
    _, _, s, t = fx
    assert _sale(s, 3_000_000)["category"] == "owner"               # 3x assessed value
    assert not _sale(s, 3_000_000)["used"]
    assert not _sale(s, 700_000)["used"]                            # fully exempt parcel
    bv = t["maui_sales_by_value_fy2023_2026.csv"]
    assert bv["count"].sum() == 6
    assert sorted(bv["sum_price"]) == [800_000, 1_100_000, 1_747_930, 2_600_000, 4_550_000, 12_900_000]


def test_vacant_residential_land_stays_on_schedule_one(fx):
    rows, par, s, t = fx
    assert _sale(s, 1_200_000)["category"] == "nonres"              # schedule (1), no building
    bins = t["maui_sales_bins_fy2023_2026.csv"]
    assert bins.query("fy == 2026 and category == 'nonres' and bin_lo == 1_000_000")["count"].item() == 1
    built = {k: dataclasses.replace(p) for k, p in par.items()}
    built["910010020000"].building = 1.0
    assert _sale(mx.sales(mx.documents(rows), built), 1_200_000)["category"] == "owner"


def test_stock_leaves_out_masters_multifamily_exempt_and_nonresidential(fx):
    _, par, _, t = fx
    assert par["910020010000"].master and par["910020010001"].condo and not par["910020010000"].condo
    st = t["maui_residential_stock_2026.csv"]
    assert list(st.columns) == ["value_lo", "owner_occupied", "improved", "condo", "count", "sum_value"]
    assert st["count"].sum() == 10
    assert st.query("condo == 1")[["value_lo", "count", "sum_value"]].values.tolist() == [[3_500_000, 3, 10_500_000]]
    assert st.query("owner_occupied == 1")["sum_value"].tolist() == [1_700_000, 2_500_000]
    assert st.query("improved == 0")["sum_value"].tolist() == [1_000_000]


def test_homeowner_class_owner_occupied_with_no_exemption_cap(fx):
    # Maui's base homeowner exemption is $300,000; this home carries $400,000 (an add-on).
    _, par, s, t = fx
    p = par["910010090000"]
    assert p.homeowner and p.exempt == 400_000 and p.owner
    assert not par["910010070000"].owner                             # fully exempt, not homeowner class
    st = t["maui_residential_stock_2026.csv"]
    assert st.query("value_lo == 2_500_000")[["owner_occupied", "count"]].values.tolist() == [[1, 1]]
    assert _sale(s, 2_600_000)["owner_occupied_now"] == 1
    bv = t["maui_sales_by_value_fy2023_2026.csv"]
    assert bv.query("sum_price == 2_600_000")[["category", "owner_occupied_now"]].values.tolist() == [["owner", 1]]


def test_used_is_the_sales_by_value_one_row_each(fx):
    _, _, s, t = fx
    u = mx.used(s)
    assert list(u.columns) == mx.USED_COLUMNS
    assert len(u) == t["maui_sales_by_value_fy2023_2026.csv"]["count"].sum() == 6
    assert u.set_index("price").loc[4_550_000, "validity"] == "8"
    assert u.set_index("price").loc[12_900_000, "parids"] == ["910020010001", "910020010002", "910020010003"]
    assert mx.used(mx.sales(mx.documents(fx[0]), fx[1], last_fy=2022)).empty       # FY2023+ only


def _totals_are_bins_top_and_multifamily_top(t: dict[str, pd.DataFrame]) -> None:
    """FY2023+ totals = bins + the $10M+ list + multifamily sales of $10M+."""
    tot = (t["maui_sales_totals_fy2016_2026.csv"].query("fy >= 2023")
           .replace({"category": {"unmatched": "nonres"}}).groupby(["fy", "category"])[["count", "sum_price"]].sum())
    mf = t["maui_multifamily_sales_fy2023_2026.csv"].query("price >= @mx.TOP")
    tops = pd.concat([t["maui_sales_over_10m_fy2023_2026.csv"], mf[["fy", "category", "price"]]])
    parts = pd.concat([t["maui_sales_bins_fy2023_2026.csv"][["fy", "category", "count", "sum_price"]],
                       tops.assign(count=1, sum_price=tops["price"])[["fy", "category", "count", "sum_price"]]])
    parts = parts.groupby(["fy", "category"])[["count", "sum_price"]].sum()
    assert tot["count"].equals(parts["count"])
    assert (tot["sum_price"] - parts["sum_price"]).abs().max() <= 100      # rounding of summed prices


def test_dwelling_units_caches_only_a_complete_pull(tmp_path, monkeypatch):
    class Reply:
        def __init__(self, body: dict) -> None:
            self.body = body

        def json(self) -> dict:
            return self.body

    def serve(pages: list[dict]):
        calls = iter([{"count": 3}, *pages])
        return lambda url, timeout, params: Reply(next(calls))

    feats = [{"attributes": {"OBJECTID": i, "TMK": 291001000 + i, "COUNT_Units": 6}} for i in (1, 2, 3)]
    monkeypatch.setattr(mx, "CACHE", tmp_path)
    monkeypatch.setattr(mx.time, "sleep", lambda s: None)
    error = {"error": {"code": 500, "message": "Error performing query operation"}}
    monkeypatch.setattr(mx.requests, "get", serve([{"features": feats[:2]}, *[error] * 5]))
    with pytest.raises(SystemExit, match="offset 2"):
        mx.dwelling_units(refresh=True)                              # an HTTP-200 error page, retried
    assert not (tmp_path / "units_ge5.json").exists()
    monkeypatch.setattr(mx.requests, "get", serve([{"features": feats[:2]}, {"features": feats[:1]}]))
    with pytest.raises(SystemExit, match="2 unique OBJECTID"):
        mx.dwelling_units(refresh=True)                              # a repeated page
    assert not (tmp_path / "units_ge5.json").exists()
    monkeypatch.setattr(mx.requests, "get", serve([{"features": feats[:2]}, error, {"features": feats[2:]}]))
    path = mx.dwelling_units(refresh=True)
    assert json.loads(path.read_text()) == [{"TMK": 291001000 + i, "COUNT_Units": 6} for i in (1, 2, 3)]


# ─────────────────────────────────────────────────────────────────────────────
# The committed files

def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(mx.OUT / name)


def test_committed_value_price_sums_back_to_sales_by_value():
    keys = ["fy", "category", "owner_occupied_now", "condo", "value_lo"]
    vp = _read("maui_sales_value_price_fy2023_2026.csv").groupby(keys)[["count", "sum_price", "sum_value"]].sum()
    bv = (_read("maui_sales_by_value_fy2023_2026.csv").query("improved == 1")
          .groupby(keys)[["count", "sum_price", "sum_value"]].sum())
    pd.testing.assert_frame_equal(vp.sort_index(), bv.sort_index())
    assert set(_read("maui_sales_value_price_fy2023_2026.csv")["price_lo"]) <= {int(e) for e in mx.PRICE_EDGES}


def test_committed_totals_equal_bins_plus_top_sales():
    _totals_are_bins_top_and_multifamily_top({name: _read(name) for name in (
        "maui_sales_totals_fy2016_2026.csv", "maui_sales_bins_fy2023_2026.csv", "maui_sales_over_10m_fy2023_2026.csv",
        "maui_multifamily_sales_fy2023_2026.csv")})


def test_committed_multifamily_sales_sit_in_their_price_bins():
    bins = _read("maui_sales_bins_fy2023_2026.csv")
    for r in _read("maui_multifamily_sales_fy2023_2026.csv").query("price < @mx.TOP").itertuples():
        lo = int(mx.PRICE_EDGES[sum(e <= r.price for e in mx.PRICE_EDGES) - 1])
        assert r.units >= mx.MULTIFAMILY_UNITS
        same = (bins["fy"] == r.fy) & (bins["category"] == r.category) & (bins["bin_lo"] == lo)
        assert bins.loc[same, "count"].sum() >= 1          # the forecast moves it out of this bin


def test_committed_sources_record_every_raw_file():
    src = json.loads((mx.OUT / "maui_sources.json").read_text())["files"]
    assert set(src) == {"sales", "assessment", "dwelling_units"}
    for f in src.values():
        assert f["url"].startswith("https://") and len(f["sha256"]) == 64 and f["rows"] > 0 and f["obtained"]
    for name in ("sales", "assessment"):                      # not downloaded by the script: say how they were
        assert src[name]["obtained"] == mx.OBTAINED[src[name]["sha256"]] == mx.RESEARCH_ROUND


def test_committed_developer_shares_and_history_are_consistent():
    d = pd.read_csv(mx.OUT / "maui_developer_share_fy2023_2026.csv")
    assert (d["developer_count"] <= d["count"]).all() and (d["developer_price"] <= d["sum_price"]).all()
    assert list(d["band_hi"].iloc[:-1].astype(float)) == list(d["band_lo"].iloc[1:].astype(float))
    h = pd.read_csv(mx.OUT / "maui_sales_history_fy2016_2026.csv")
    tot = pd.read_csv(mx.OUT / "maui_sales_totals_fy2016_2026.csv")
    assert h["count"].sum() == tot["count"].sum()                      # every priced, taxed document, once
    assert set(h["kind"]) == {"bin", "top", "mf"}
    assert (h.loc[h["kind"] == "top", "sum_price"] >= 10_000_000).all()


def test_history_splits_every_document_once_on_the_fixture(fx):
    _, _, s, t = fx
    h = t["maui_sales_history_fy2016_2026.csv"]
    assert h["count"].sum() == len(s) and "unmatched" not in set(h["category"])
    mf = s[s["units"] >= mx.MULTIFAMILY_UNITS]
    top = s[(s["units"] < mx.MULTIFAMILY_UNITS) & (mx._round(s["price"]) >= mx.TOP)]
    assert sorted(h.loc[h["kind"] == "mf", "sum_price"]) == sorted(mx._round(mf["price"]))
    assert (h.loc[h["kind"] == "mf", "units"].to_numpy() >= mx.MULTIFAMILY_UNITS).all()
    assert sorted(h.loc[h["kind"] == "top", "sum_price"]) == sorted(mx._round(top["price"]))
    assert (h.loc[h["kind"] == "bin", "bin_lo"] < mx.TOP).all() and (h["units"] >= 1).all()


def test_developer_share_counts_code_8_home_sales_by_value():
    base = pd.DataFrame({"category": ["owner", "nonowner", "nonowner", "nonres", "nonowner"],
                         "units": [0, 0, 0, 0, 12], "price": [3e6, 5e6, 12e6, 12e6, 12e6],
                         "validity": ["8", "0", "8", "8", "8"]})
    d = mx.developer_share(base).set_index("band_lo")
    assert d.loc[2_000_000, ["count", "developer_count", "developer_price"]].tolist() == [1, 1, 3_000_000]
    assert d.loc[4_000_000, ["count", "developer_count", "developer_price"]].tolist() == [1, 0, 0]
    # $10M+: the home sale counts; the nonresidential sale and the 12-unit building do not
    assert d.loc[10_000_000, ["count", "developer_count", "sum_price", "developer_price"]].tolist() == [1, 1, 12_000_000,
                                                                                                    12_000_000]
