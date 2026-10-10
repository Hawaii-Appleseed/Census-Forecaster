"""The DOTAX scorecard's table builders on a tiny scored population."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "dotax_scorecard", REPO_ROOT / "scripts" / "dotax_scorecard.py"
)
dotax_scorecard = importlib.util.module_from_spec(_spec)
sys.modules["dotax_scorecard"] = dotax_scorecard
_spec.loader.exec_module(dotax_scorecard)


EDITION = {
    "tax_year": 2099,
    "table_a8_resident_liability": {
        "classes": [
            {"agi_lo": None, "agi_hi": 0.0, "returns": 10, "tax_before_M": 0.0},
            {"agi_lo": 0.0, "agi_hi": 100_000.0, "returns": 100, "tax_before_M": 0.3},
            {"agi_lo": 100_000.0, "agi_hi": 1_000_000.0, "returns": 20, "tax_before_M": 0.4},
            {"agi_lo": 1_000_000.0, "agi_hi": None, "returns": 4, "tax_before_M": 1.0},
        ],
        "total": {"returns": 134, "tax_before_M": 1.7},
    },
    "table_a1_resident_taxable": {
        "taxable_classes": [
            {"agi_lo": 0.0, "agi_hi": 100_000.0, "returns": 80, "agi_M": 4.0},
            {"agi_lo": 100_000.0, "agi_hi": None, "returns": 24, "agi_M": 12.0},
        ],
        "taxable_total": {"returns": 104, "agi_M": 16.0},
    },
    "table_4_filing_status": {
        "resident": {"Married Filing Jointly": 50, "Single": 70, "Married Filing Separately": 4,
                     "Head of Household": 9, "Qualifying Widow(er)": 1},
    },
}


@pytest.fixture
def scored():
    return pd.DataFrame({
        "income": [-5_000.0, 40_000.0, 60_000.0, 250_000.0, 1_500_000.0, 3_000_000.0],
        "weight": [10.0, 60.0, 50.0, 20.0, 2.0, 1.0],
        "filing_status": ["single", "single", "married_filing_jointly", "married_filing_jointly",
                          "married_filing_jointly", "single"],
        # $ per unit; the 60K units owe nothing (not taxable returns in Table A-1)
        "tax_before": [0.0, 2_000.0, 0.0, 15_000.0, 150_000.0, 400_000.0],
    })


def test_a8_counts_tax_and_errors(scored):
    t = dotax_scorecard.a8_table(scored, EDITION).set_index("class")
    assert t.loc["Loss", "returns_model"] == 10
    assert t.loc["$0K-$100K", "returns_model"] == 110
    assert t.loc["$1M+", "returns_model"] == 3
    assert t.loc["Total", "returns_model"] == pytest.approx(143)
    # tax: 60*2000 + 0 = $0.12M in the first class; 2*150K + 400K = $0.7M at $1M+
    assert t.loc["$0K-$100K", "tax_model_M"] == pytest.approx(0.12)
    assert t.loc["$1M+", "tax_model_M"] == pytest.approx(0.7)
    assert t.loc["$1M+", "tax_err_M"] == pytest.approx(-0.3)
    assert t.loc["$1M+", "tax_err_pct"] == pytest.approx(-30.0)
    assert t.loc["$1M+", "returns_err_pct"] == pytest.approx(-25.0)
    assert np.isnan(t.loc["Loss", "tax_err_pct"])         # actual tax is 0


def test_a1_uses_taxable_units_only(scored):
    t = dotax_scorecard.a1_table(scored, EDITION).set_index("class")
    # the 60K units (no tax) are out: 60 returns x $40K = $2.4M
    assert t.loc["$0K-$100K", "returns_model"] == 60
    assert t.loc["$0K-$100K", "agi_model_M"] == pytest.approx(2.4)
    # 20 x 250K + 2 x 1.5M + 1 x 3M = $11M
    assert t.loc["$100K+", "agi_model_M"] == pytest.approx(11.0)
    assert t.loc["Total", "agi_err_pct"] == pytest.approx(100 * (13.4 - 16.0) / 16.0)


def test_status_table_shares_and_unmodeled_widow(scored):
    t = dotax_scorecard.status_table(scored, EDITION).set_index("filing_status")
    assert t.loc["Single", "returns_model"] == 71
    assert t.loc["Married Filing Jointly", "returns_model"] == 72
    assert t.loc["Qualifying Widow(er)", "returns_model"] == 0
    assert t.loc["Total", "returns_actual"] == 134
    assert t.loc["Total", "share_model_pct"] == pytest.approx(100.0)


def test_top_class_decomposition_sums(scored):
    t = dotax_scorecard.top_class_table(scored, EDITION).iloc[0]
    assert t["returns_model"] == 3 and t["returns_actual"] == 4
    assert t["tax_model_M"] == pytest.approx(0.7)
    assert t["count_part_M"] + t["per_filer_part_M"] == pytest.approx(t["tax_err_M"])
    assert t["log_count"] + t["log_per_filer"] == pytest.approx(t["log_err"])
    # count part: one filer short at the actual $250K average
    assert t["count_part_M"] == pytest.approx(-0.25)
    assert t["per_filer_part_M"] == pytest.approx(-0.05)


def test_class_labels():
    assert dotax_scorecard.class_label(None, 0.0) == "Loss"
    assert dotax_scorecard.class_label(50_000.0, 75_000.0) == "$50K-$75K"
    assert dotax_scorecard.class_label(400_000.0, None) == "$400K+"
    assert dotax_scorecard.class_label(1_000_000.0, None) == "$1M+"


def test_render_mentions_every_table(scored):
    tables = {"MID": {"a8": dotax_scorecard.a8_table(scored, EDITION),
                      "a1": dotax_scorecard.a1_table(scored, EDITION),
                      "status": dotax_scorecard.status_table(scored, EDITION),
                      "top": dotax_scorecard.top_class_table(scored, EDITION)}}
    diags = {"MID": {"alpha": 1.5, "top_premium": 0.01, "tail_k": 1.0, "law": "Act 46"}}
    dials = {"MID": {"honolulu_b19013_growth": 1.03, "premium_growth": 1.01,
                     "tail_aging_to_pums_year": 1.05, "cg_growth": 1.02}}
    md = dotax_scorecard.render(tables, diags, dials, base_year=2098, year=2099,
                                base_path=Path("base.pkl"), actuals_path=Path("a.json"), meta={})
    for needle in ("Table A-8", "Table A-1", "Table 4", "$1M+ class", "| $1M+ |", "Dials, TY2098 -> TY2099"):
        assert needle in md


def test_law_for_each_regime():
    assert dotax_scorecard.law_for(2023).name == "2017_system"
    ty24 = dotax_scorecard.law_for(2024)
    assert ty24.bracket_year == 2018 and ty24.standard_deduction_year == 2024
    assert dotax_scorecard.law_for(2027).name == "act46_2027"
