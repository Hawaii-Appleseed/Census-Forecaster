"""Smoke tests for scripts/eitc_ctc_geo_report.py.

Verifies the report script's CLI runs end-to-end on the synthetic PUMS
fixture for both backtest (TY 2022, no projection) and projection
(TY 2025) modes, and that all four CBPP-comparable CSVs land in the
output directory with the expected schema.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eitc_ctc_geo_report.py"


@pytest.fixture(scope="module")
def report_module():
    """Import scripts/eitc_ctc_geo_report.py directly (it lives outside the package)."""
    spec = importlib.util.spec_from_file_location("eitc_ctc_geo_report", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eitc_ctc_geo_report"] = mod
    spec.loader.exec_module(mod)
    return mod


_EXPECTED_COLS = {
    "weighted_filers",
    "filers_receiving_eitc",
    "total_eitc_dollars",
    "filers_receiving_ctc",
    "total_ctc_dollars",
    "refundable_ctc_dollars",
    "hi_eitc_dollars",
}


def test_report_2022_backtest_runs(report_module, tmp_path):
    out_dir = tmp_path / "ty2022"
    rc = report_module.main([
        "--tax-year", "2022",
        "--out", str(out_dir),
        "--use-fixture",
    ])
    assert rc == 0

    for fname in ("by_state.csv", "by_county.csv",
                  "by_house_district.csv", "by_senate_district.csv"):
        path = out_dir / fname
        assert path.exists(), f"missing output {fname}"
        df = pd.read_csv(path)
        assert _EXPECTED_COLS.issubset(df.columns), f"{fname} missing columns: {_EXPECTED_COLS - set(df.columns)}"

    state = pd.read_csv(out_dir / "by_state.csv")
    assert len(state) == 1
    assert state["weighted_filers"].iloc[0] > 0


def test_report_2025_projection_runs(report_module, tmp_path):
    out_dir = tmp_path / "ty2025"
    rc = report_module.main([
        "--tax-year", "2025",
        "--out", str(out_dir),
        "--use-fixture",
    ])
    assert rc == 0
    state = pd.read_csv(out_dir / "by_state.csv")
    assert len(state) == 1
    assert state["weighted_filers"].iloc[0] > 0


def test_report_rejects_unsupported_year(report_module, tmp_path):
    out_dir = tmp_path / "bad"
    rc = report_module.main([
        "--tax-year", "2019",
        "--out", str(out_dir),
        "--use-fixture",
    ])
    assert rc == 2  # CLI returns 2 for unsupported years


def test_report_2022_with_compare_cbpp_no_network(report_module, tmp_path, monkeypatch):
    """When CBPP fetch fails / cache absent, the script must succeed without the comparison CSV."""
    out_dir = tmp_path / "ty2022_cbpp"
    # Force the fetch helper to return None (simulates offline + no cache).
    monkeypatch.setattr(report_module, "_try_fetch_cbpp_table367", lambda _path: None)
    rc = report_module.main([
        "--tax-year", "2022",
        "--out", str(out_dir),
        "--use-fixture",
        "--compare-cbpp",
    ])
    assert rc == 0
    # Geographic CSVs are still emitted even without CBPP data.
    assert (out_dir / "by_state.csv").exists()
    assert not (out_dir / "cbpp_vs_modeler_2022.csv").exists()
