"""scripts/conveyance/mls_sales_by_band.py: the pure steps behind the MLS calibration table (coverage multipliers,
fiscal-year bucketing, band mapping, the $10M+, $6M-split and Oʻahu $2-3M steps), on small synthetic inputs and
the committed luxury_report_counts.csv. Needs neither PyMuPDF nor the Title Guaranty report cache."""
from __future__ import annotations

import importlib.util
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "conveyance" / "mls_sales_by_band.py"
spec = importlib.util.spec_from_file_location("mls_sales_by_band", SCRIPT)
mls = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mls
spec.loader.exec_module(mls)

pytestmark = pytest.mark.smoke

KEYS = [k for k, _, _ in mls.BANDS]
COUNTIES = list(mls.COUNTY.values())
HALVES = ["2022H2", "2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
QUARTERS = [f"{y}Q{q}" for y in range(2022, 2027) for q in range(1, 5) if "2022Q3" <= f"{y}Q{q}" <= "2026Q2"]


def _lx(rows: list[tuple]) -> pd.DataFrame:
    """A luxury_report_counts-shaped frame, with the fiscal year the script adds."""
    lx = pd.DataFrame(rows, columns=["source", "period", "county", "type", "band_lo", "band_hi", "count", "basis"])
    return lx.assign(fy=lx["period"].map(mls.fiscal_year))


def _committed_luxury() -> pd.DataFrame:
    lx = pd.read_csv(mls.LUXURY, dtype={"period": str})
    return lx.assign(fy=lx["period"].map(mls.fiscal_year))


# --------------------------------------------------------------------------- import, files, terms

def test_module_imports_without_pymupdf():
    assert "fitz" not in vars(mls)          # imported only inside _open(), when a report is read


def test_opening_a_report_without_pymupdf_exits_with_the_same_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "fitz", None)                     # makes `import fitz` raise ImportError
    with pytest.raises(SystemExit, match=r"PyMuPDF \(fitz\) is required; the repo .venv lacks it"):
        mls._open(mls.eoy_file("Maui", 2025))


def test_the_cache_needs_170_reports_named_without_links():
    files = mls.needed()
    assert len(files) == len(set(files)) == 3 * 54 + 4 * 2
    assert all(f.endswith(".pdf") and "/" not in f and ":" not in f for f in files)
    assert mls.monthly_file("Oahu", 2024, 3) == "Residential-Sales-Report-Oahu-Island-3.2024.pdf"
    assert mls.monthly_file("Maui", 2023, 11) == "Residential-Sales-Report-Maui-Islandv2-11.2023.pdf"
    assert mls.monthly_file("Maui", 2025, 6) == "Residential-Sales-Report-Maui-Island-6.2025-Nicole.pdf"
    assert mls.monthly_file("Hawaii", 2022, 8) == "Residential-Sales-Report-Hawaii-Island-8.2022-1.pdf"
    assert mls.monthly_file("Hawaii", 2026, 3) == "Residential-Sales-Report-Hawaii-Island-3.2026.pdf"
    assert mls.monthly_file("Hawaii", 2026, 4) == "Residential-Sales-Report-Hawaii-4.2026.pdf"
    assert mls.eoy_file("Kauai", 2025) == "RSR-Kauai-EOY-2025.pdf"


def test_the_script_carries_no_link_to_title_guaranty():
    assert not hasattr(mls, "TG_URL")
    assert "tghawaii" not in SCRIPT.read_text(encoding="utf-8").lower()


def test_missing_reports_are_named_not_linked(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "runs" / "conveyance_sb3028" / "mls_cache"
    cache.mkdir(parents=True)
    have = mls.eoy_file("Oahu", 2024)
    (cache / have).write_bytes(b"")
    monkeypatch.setattr(mls, "REPO", tmp_path)
    monkeypatch.setattr(mls, "CACHE", cache)
    monkeypatch.setattr(sys, "argv", ["mls_sales_by_band.py"])
    with pytest.raises(SystemExit) as exc:
        mls.main()
    msg, err = str(exc.value.code), capsys.readouterr().err
    assert msg.startswith("169 of 170 Title Guaranty reports")
    assert err.split() == [f for f in mls.needed() if f != have]
    for text in (msg, err):
        assert "http" not in text and "download" not in text.lower() and "by hand" not in text


# --------------------------------------------------------------------------- fiscal years, bands

@pytest.mark.parametrize("period, fy", [("2022H2", 2023), ("2025H1", 2025), ("2025H2", 2026), ("2026H1", 2026),
                                        ("2022Q3", 2023), ("2023Q2", 2023), ("2025Q3", 2026), ("2026Q2", 2026)])
def test_fiscal_year_of_a_half_or_quarter(period, fy):
    assert mls.fiscal_year(period) == fy


def test_a_calendar_year_has_no_fiscal_year():
    assert math.isnan(mls.fiscal_year("2025"))


def test_months_bucket_into_july_june_fiscal_years():
    assert mls.month_fy(2025, 6) == 2025 and mls.month_fy(2025, 7) == 2026 and mls.month_fy(2026, 6) == 2026
    ms = mls.months()
    assert ms[0] == (2022, 1) and ms[-1] == (2026, 6)
    assert Counter(mls.month_fy(y, m) for y, m in ms) == {2022: 6, 2023: 12, 2024: 12, 2025: 12, 2026: 12}
    frame = pd.DataFrame(ms, columns=["year", "month"])
    assert mls.month_fy(frame.year, frame.month).tolist() == [mls.month_fy(y, m) for y, m in ms]


def test_fy_blend_weights_calendar_years_by_their_months_in_fy2023_26():
    cy = pd.Series({2022: 8.0, 2023: 16.0, 2024: 24.0, 2025: 32.0})
    assert mls.fy_blend(cy) == pytest.approx((4 + 16 + 24 + 32 + 16) / 4)     # H1 2026 = half of 2025
    assert mls.fy_blend(cy, h1_2026=10.0) == pytest.approx((4 + 16 + 24 + 32 + 10) / 4)
    assert mls.fy_blend(pd.Series(7.0, index=mls.YEARS)) == pytest.approx(7.0)


def test_model_bands_are_contiguous_and_every_group_has_parameters():
    assert KEYS == ["1_3", "3_6", "6_10", "10p"]
    edges = [(lo, hi) for _, lo, hi in mls.BANDS]
    assert edges[0][0] == 1e6 and np.isinf(edges[-1][1])
    assert all(a[1] == b[0] for a, b in zip(edges, edges[1:]))
    groups = {mls.GROUP[k] for k in KEYS}
    assert groups == set(mls.MAUI_COV) == set(mls.VACANT_SHARE) == set(mls.COUNT_ERR)
    assert set(mls.OAHU_ADJ) == {"1_2", "2_3", "3_6", "6_10"}


def test_maui_recorded_sales_fall_in_the_model_bands(tmp_path, monkeypatch):
    pd.DataFrame([(2024, "owner", 600_000, 50), (2024, "owner", 1_000_000, 10), (2024, "nonowner", 2_900_000, 2),
                  (2024, "nonres", 1_500_000, 7), (2024, "owner", 3_000_000, 3), (2024, "nonowner", 9_900_000, 1),
                  (2025, "owner", 1_000_000, 4)],
                 columns=["fy", "category", "bin_lo", "count"]).assign(sum_price=0).to_csv(
        tmp_path / "maui_sales_bins_fy2023_2026.csv", index=False)
    pd.DataFrame([(2024, "owner", 10_000_000), (2024, "nonowner", 25_000_000), (2024, "nonres", 12_000_000)],
                 columns=["fy", "category", "price"]).to_csv(tmp_path / "maui_sales_over_10m_fy2023_2026.csv",
                                                             index=False)
    monkeypatch.setattr(mls, "RAW", tmp_path)
    rec = mls.maui_recorded()
    assert list(rec.columns) == KEYS
    assert rec.loc[2024].tolist() == [12, 3, 1, 2]          # under $1M and nonresidential left out; $3M, $10M up
    assert rec.loc[2025, "1_3"] == 4


# --------------------------------------------------------------------------- coverage

@pytest.mark.parametrize("key", KEYS)
def test_maui_and_kauai_coverage_is_mauis_own_range(key):
    cov, lo, hi = mls.MAUI_COV[mls.GROUP[key]]
    for county in ("Maui", "Kauai"):
        assert mls.coverage(county, key, 0.3) == pytest.approx((1.0, lo / cov, hi / cov))


def test_hawaii_high_end_is_raised_10_percent_at_3m_plus_only():
    assert mls.coverage("Hawaii", "1_3", 0.3) == pytest.approx((1.0, 1.095 / 1.137, 1.17 / 1.137))
    for key in ("3_6", "6_10"):
        assert mls.coverage("Hawaii", key, 0.3) == pytest.approx((1.0, 1.128 / 1.23, 1.36 / 1.23 * 1.10))
    assert mls.coverage("Hawaii", "10p", 0.3) == pytest.approx((1.0, 1.06 / 1.27, 1.45 / 1.27 * 1.10))


def test_vacant_rescale_is_the_change_in_mauis_vacant_share():
    assert mls._vacant_rescale("1_3") == pytest.approx((1 - 0.028) / (1 - 0.037))
    assert mls._vacant_rescale("3_6") == mls._vacant_rescale("6_10") == pytest.approx((1 - 0.075) / (1 - 0.083))
    assert mls._vacant_rescale("10p") == pytest.approx((1 - 0.034) / (1 - 0.16))
    assert all(mls._vacant_rescale(k) > 1 for k in KEYS)    # Maui's current extract counts fewer vacant lots


@pytest.mark.parametrize("key, adj", [("3_6", (1.05, 0.94, 1.15)), ("6_10", (0.94, 0.88, 1.02))])
def test_oahu_3_to_10m_multipliers_rescaled_once(key, adj):
    v = (1 - 0.075) / (1 - 0.083)
    assert mls.coverage("Honolulu", key, 0.3) == pytest.approx(
        (adj[0] * v, 1.128 / 1.23 * adj[1] / adj[0], 1.36 / 1.23 * adj[2] / adj[0]))


def test_oahu_10m_plus_is_its_own_ratio_over_mauis():
    v = (1 - 0.034) / (1 - 0.16)
    assert mls.coverage("Honolulu", "10p", 0.3) == pytest.approx((1.06 / 1.27 * v, 1.0 / 1.06, 1.2 / 1.06))


@pytest.mark.parametrize("share", [0.0, 0.25, 1.0])
def test_oahu_1_3m_weights_its_1_2m_and_2_3m_multipliers_by_mls_count(share):
    adj = (1 - share) * np.array([0.97, 0.93, 1.03]) + share * np.array([1.10, 0.97, 1.22])
    v = (1 - 0.028) / (1 - 0.037)
    assert mls.coverage("Honolulu", "1_3", share) == pytest.approx(
        (adj[0] * v, 1.095 / 1.137 * adj[1] / adj[0], 1.17 / 1.137 * adj[2] / adj[0]))


@pytest.mark.parametrize("county", COUNTIES)
@pytest.mark.parametrize("key", KEYS)
def test_coverage_range_brackets_the_central_value(county, key):
    for share in (0.0, 0.12, 1.0):
        rel, lo, hi = mls.coverage(county, key, share)
        assert rel > 0 and lo <= 1 <= hi


def test_coverage_reproduces_the_committed_oahu_values():
    rel = pd.read_csv(mls.OUT).query("county == 'Honolulu'").set_index("band_lo")["coverage_rel_maui"]
    for band_lo, key in ((3_000_000, "3_6"), (6_000_000, "6_10"), (10_000_000, "10p")):
        assert round(mls.coverage("Honolulu", key, 0.0)[0], 3) == rel[band_lo]
    assert mls.coverage("Honolulu", "1_3", 0.0)[0] < rel[1_000_000] < mls.coverage("Honolulu", "1_3", 1.0)[0]


# --------------------------------------------------------------------------- $10M+, $6M split, Oʻahu $2-3M

def test_ten_plus_scales_hawaii_2025_houses_and_takes_oahu_houses_from_list_sothebys():
    hawaii_houses, hawaii_condos = [1, 1, 2, 2, 3, 4, 8, 1], [0, 0, 0, 1, 0, 0, 0, 0]
    rows = ([("hawaii_life", p, "Hawaii", "house", 1e7, np.inf, n, "chart") for p, n in zip(HALVES, hawaii_houses)]
            + [("hawaii_life", p, "Hawaii", "condo", 1e7, np.inf, n, "chart") for p, n in zip(HALVES, hawaii_condos)]
            + [("hawaii_life", p, "Honolulu", t, 1e7, np.inf, n, "chart") for p in HALVES
               for t, n in (("house", 2), ("condo", 1))]
            + [("list_sothebys", q, "Honolulu", "house", 1e7, np.inf, 1, "text") for q in QUARTERS if q != "2026Q1"]
            + [("hawaii_life", "2025", "Hawaii", "house", 1e7, np.inf, 17, "text")])       # not a chart row
    used, alt = mls.ten_plus(_lx(rows))
    assert list(used.index) == list(mls.FYS)
    assert used["Hawaii"].tolist() == [2, 5, 3 + 4 * 0.75, 8 * 0.75 + 1]
    assert alt["Hawaii"].tolist() == [2, 5, 7, 9]
    assert used["Honolulu"].tolist() == [4 + 2, 4 + 2, 4 + 2, 3 + mls.OAHU_Q1_2026_HOUSES_10M + 2]
    assert alt["Honolulu"].tolist() == [6, 6, 6, 6]


def test_10m_plus_rows_follow_from_the_committed_luxury_counts_and_coverage():
    ten, alt = mls.ten_plus(_committed_luxury())
    table = pd.read_csv(mls.OUT).set_index(["county", "band_lo"])
    for county in COUNTIES:
        row = table.loc[(county, 10_000_000)]
        t10, t_alt = ten[county].mean(), alt[county].mean()
        if county == "Maui":
            expect, rel = (t10, ten[county].min(), ten[county].max()), 1.0
        else:
            rel, r_lo, r_hi = mls.coverage(county, "10p", 0.0)
            expect = (t10, min(t10, t_alt) * r_lo, max(t10, t_alt) * r_hi)
        assert row[["mls_per_year", "mls_low", "mls_high"]].tolist() == pytest.approx(expect, abs=0.05 + 1e-9)
        assert row["coverage_rel_maui"] == pytest.approx(rel, abs=5e-4 + 1e-9)


def test_share_6_10_is_pooled_with_the_lowest_and_highest_report():
    rows = [("hawaii_life", p, "Maui", "all", lo, hi, n, "text") for p, a, b in
            (("2022", 8, 2), ("2025", 6, 4), ("2026H1", 9, 1)) for lo, hi, n in ((3e6, 6e6, a), (6e6, 1e7, b))]
    rows += [("hawaii_life", "2025", "Maui", "all", 1e7, np.inf, 50, "text"),         # $10M+: left out
             ("hawaii_life", "2025", "Maui", "house", 6e6, 1e7, 50, "text")]          # one type: left out
    assert mls.share_6_10(_lx(rows))["Maui"] == pytest.approx((7 / 30, 0.1, 0.4))


def test_share_6_10_pooled_share_lies_within_the_reports_on_the_committed_counts():
    for county, (pooled, lo, hi) in mls.share_6_10(_committed_luxury()).items():
        assert county in COUNTIES and 0 < lo <= pooled <= hi < 1


def test_oahu_2_3m_adds_a_pareto_interpolated_2_2_5m_condo_count():
    rows = [("list_sothebys", q, "Honolulu", t, lo, hi, n, "text") for q in QUARTERS for t, lo, hi, n in
            (("house", 2_000_000, 3_000_000, 5), ("condo", 1_000_000, 2_500_000, 15),
             ("condo", 2_500_000, np.inf, 10))]
    rows.append(("list_sothebys", "2022Q2", "Honolulu", "house", 2_000_000, 3_000_000, 100, "text"))  # FY2022
    typ = pd.DataFrame([{"island": "Oahu", "year": y, "type": "condo", 0: 0, 1: 0, 2: 0, 3: 0, mls.B3P: 12}
                        for y in mls.YEARS]).set_index(["island", "year", "type"])
    # houses 20 a year; condos at $2.5M+ 40 a year, less 12 at $3M+ (TG); $1M+ / $2.5M+ = 2.5, so the Pareto
    # alpha is 1 and $2M+ is half of $1M+: (400 / 2 - 160) over 16 quarters = 10 a year at $2-2.5M
    assert mls.oahu_2_3m(_lx(rows), typ) == pytest.approx(20 + 40 - 12 + 10)


# --------------------------------------------------------------------------- chart helpers, committed table

def test_clusters_average_values_within_the_gap():
    assert mls._clusters([10.0, 1.0, 2.0, 11.0, 30.0], 3) == [1.5, 10.5, 30.0]


def test_num_matches_printed_counts_only():
    word = (0.0, 0.0, 1.0, 1.0)
    assert mls._num(word + ("1,234",)) and mls._num(word + ("7",))
    assert not mls._num(word + ("$3M+",)) and not mls._num(word + ("12%",))


def test_committed_table_has_every_county_and_band_in_range():
    t = pd.read_csv(mls.OUT)
    assert len(t) == 16 and set(zip(t.county, t.band_lo)) == {(c, int(lo)) for c in COUNTIES for _, lo, _ in mls.BANDS}
    assert (t.mls_low <= t.mls_per_year).all() and (t.mls_per_year <= t.mls_high).all()
    assert (t.loc[t.county != "Honolulu", "coverage_rel_maui"] == 1.0).all()
